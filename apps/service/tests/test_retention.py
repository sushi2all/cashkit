"""The retention jobs, driven with a frozen clock (SPEC §9, §11).

*Log purge jobs verified* is a gate clause, and "verified" cannot mean "the
code exists". Every job here is run against rows and files that are genuinely
old, with the clock supplied rather than waited for (D-MLP-12), and each is
checked in both directions: what must go goes, and what must stay stays.

The second half is the one that matters. A purge that deletes everything
passes a naive test perfectly.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from cashkit_service.config import Settings
from cashkit_service.db import books, deletions, llm_calls, login_tokens, turns, users
from cashkit_service.retention import (
    close_backup_windows,
    overdue_backup_windows,
    purge_llm_payloads,
    purge_login_tokens,
    purge_request_logs,
    run_sweep,
)

NOW = dt.datetime(2026, 8, 23, 12, 0, tzinfo=dt.timezone.utc)


def days_ago(n: int) -> dt.datetime:
    return NOW - dt.timedelta(days=n)


async def _account_with_calls(database, ages: list[int]) -> list[uuid.UUID]:
    """One account, one book, one turn, and a model call at each given age."""
    user_id, book_id, turn_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids: list[uuid.UUID] = []
    async with database.connect() as conn:
        await conn.execute(users.insert().values(id=user_id, email="r@example.com", created_at=days_ago(400)))
        await conn.execute(
            books.insert().values(
                id=book_id, user_id=user_id, storage_path="/tmp/nope", created_at=days_ago(400)
            )
        )
        await conn.execute(
            turns.insert().values(
                id=turn_id,
                user_id=user_id,
                book_id=book_id,
                request_id="r",
                input_text="x",
                created_at=days_ago(400),
            )
        )
        for seq, age in enumerate(ages):
            call_id = uuid.uuid4()
            ids.append(call_id)
            await conn.execute(
                llm_calls.insert().values(
                    id=call_id,
                    turn_id=turn_id,
                    seq=seq,
                    purpose="interpret",
                    request={"messages": [{"role": "user", "content": "my rent is 912.50"}]},
                    response={"reply": "understood"},
                    prompt_tokens=100,
                    completion_tokens=20,
                    cost=Decimal_("0.000123"),
                    latency_ms=1200,
                    created_at=days_ago(age),
                )
            )
    return ids


def Decimal_(value: str):  # tiny helper so the insert reads like the schema
    from decimal import Decimal

    return Decimal(value)


# --- llm_calls payloads --------------------------------------------------- #


async def test_model_payloads_older_than_thirty_days_are_blanked(database):
    fresh, stale = await _account_with_calls(database, [29, 31])

    async with database.connect() as conn:
        purged = await purge_llm_payloads(conn, now=NOW, days=30)
    assert purged == 1

    async with database.connect() as conn:
        rows = {
            r.id: r
            for r in (await conn.execute(sa.select(llm_calls))).all()
        }
    assert rows[fresh].request is not None and rows[fresh].response is not None
    assert rows[stale].request is None and rows[stale].response is None


async def test_the_numeric_columns_survive_the_purge(database):
    """SPEC §4: the payloads purge, the numbers do not.

    The cost and repair-rate history is what the §11 alarms are computed from,
    so a purge that took the row would blind them a month at a time.
    """
    _fresh, stale = await _account_with_calls(database, [29, 31])
    async with database.connect() as conn:
        await purge_llm_payloads(conn, now=NOW, days=30)
        row = (await conn.execute(sa.select(llm_calls).where(llm_calls.c.id == stale))).one()
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 20
    assert str(row.cost) == "0.000123"
    assert row.latency_ms == 1200
    assert row.purpose == "interpret"


async def test_a_second_sweep_reports_nothing_to_do(database):
    """Idempotence, and the reason it is asserted: the count is a metric.

    `cashkit_retention_llm_payloads_purged_total` is an operator's evidence
    that the job ran. A job that re-counts rows it already blanked would show
    a purge backlog that does not exist.
    """
    await _account_with_calls(database, [31, 40])
    async with database.connect() as conn:
        assert await purge_llm_payloads(conn, now=NOW, days=30) == 2
        assert await purge_llm_payloads(conn, now=NOW, days=30) == 0


# --- login tokens --------------------------------------------------------- #


async def test_dead_link_tokens_are_swept_and_live_ones_are_not(database):
    async with database.connect() as conn:
        for name, expires in (("old", days_ago(3)), ("recent", days_ago(0)), ("live", NOW + dt.timedelta(minutes=10))):
            await conn.execute(
                login_tokens.insert().values(
                    id=uuid.uuid4(),
                    email=f"{name}@example.com",
                    token_hash=name,
                    expires_at=expires,
                    created_at=days_ago(3),
                )
            )
        removed = await purge_login_tokens(conn, now=NOW, days=1)
        left = (await conn.execute(sa.select(login_tokens.c.email))).scalars().all()
    assert removed == 1
    assert sorted(left) == ["live@example.com", "recent@example.com"]


# --- request logs --------------------------------------------------------- #


def test_rotated_request_logs_older_than_ninety_days_go(tmp_path: Path):
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "request.log").write_text("{}\n")  # the live file, never touched
    for age in (10, 89, 91, 400):
        stamp = (NOW - dt.timedelta(days=age)).date().isoformat()
        (directory / f"request.log.{stamp}").write_text("{}\n")
    (directory / "notes.txt").write_text("not a log")

    removed = purge_request_logs(directory, now=NOW, days=90)

    left = sorted(p.name for p in directory.iterdir())
    assert removed == 2
    assert "request.log" in left, "the live file is never deleted"
    assert "notes.txt" in left, "only rotated request logs are in scope"
    assert f"request.log.{(NOW - dt.timedelta(days=89)).date()}" in left
    assert f"request.log.{(NOW - dt.timedelta(days=91)).date()}" not in left


def test_a_missing_log_directory_is_not_an_error(tmp_path: Path):
    """A development run has no log directory; a sweep must not care."""
    assert purge_request_logs(tmp_path / "absent", now=NOW, days=90) == 0


def test_the_file_date_decides_not_the_mtime(tmp_path: Path):
    """A restore or a copy rewrites every mtime; retention is not about that."""
    directory = tmp_path / "logs"
    directory.mkdir()
    stamp = (NOW - dt.timedelta(days=200)).date().isoformat()
    old = directory / f"request.log.{stamp}"
    old.write_text("{}\n")
    import os

    os.utime(old, (NOW.timestamp(), NOW.timestamp()))  # freshly touched, still old
    assert purge_request_logs(directory, now=NOW, days=90) == 1


# --- the backup window ---------------------------------------------------- #


async def _deletion(database, *, deleted_days_ago: int) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with database.connect() as conn:
        await conn.execute(
            deletions.insert().values(
                user_id=user_id,
                deleted_at=days_ago(deleted_days_ago),
                backup_purge_due_at=days_ago(deleted_days_ago) + dt.timedelta(days=30),
            )
        )
    return user_id


async def test_a_window_closes_only_when_the_bucket_can_no_longer_hold_it(database):
    """The honest rule: closed against what is retained, not against elapsed time.

    The account deleted 40 days ago cannot be in any backup that was written
    35 days ago or later, so its window closes. The account deleted 20 days ago
    can be, so its window stays open — even though a naive "30 days have
    passed" test would have closed neither and a naive "mark them all" job
    would have closed both.
    """
    old = await _deletion(database, deleted_days_ago=40)
    recent = await _deletion(database, deleted_days_ago=20)

    async with database.connect() as conn:
        closed = await close_backup_windows(
            conn, now=NOW, oldest_retained_backup_at=days_ago(35)
        )
        rows = {r.user_id: r for r in (await conn.execute(sa.select(deletions))).all()}
    assert closed == 1
    assert rows[old].backups_purged_at == NOW
    assert rows[recent].backups_purged_at is None


async def test_an_empty_bucket_closes_every_window(database):
    """Nothing retained can be holding anything."""
    await _deletion(database, deleted_days_ago=1)
    async with database.connect() as conn:
        assert await close_backup_windows(conn, now=NOW, oldest_retained_backup_at=None) == 1


async def test_an_overdue_window_is_countable_because_it_is_an_alarm(database):
    """Above zero is a §9 breach in progress, so §11 has a rule for it."""
    await _deletion(database, deleted_days_ago=31)  # due yesterday, still open
    await _deletion(database, deleted_days_ago=5)  # not due yet
    async with database.connect() as conn:
        assert await overdue_backup_windows(conn, now=NOW) == 1
        await close_backup_windows(conn, now=NOW, oldest_retained_backup_at=None)
        assert await overdue_backup_windows(conn, now=NOW) == 0


# --- the sweep ------------------------------------------------------------ #


async def test_the_sweep_runs_every_job_and_reports_each(database, tmp_path: Path):
    await _account_with_calls(database, [31])
    await _deletion(database, deleted_days_ago=45)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / f"request.log.{(NOW - dt.timedelta(days=120)).date()}").write_text("{}\n")
    async with database.connect() as conn:
        await conn.execute(
            login_tokens.insert().values(
                id=uuid.uuid4(),
                email="sweep@example.com",
                token_hash="sweep",
                expires_at=days_ago(9),
                created_at=days_ago(9),
            )
        )

    settings = Settings(database_url="unused://", request_log_dir=logs)
    report = await run_sweep(
        database, settings, now=NOW, oldest_retained_backup_at=days_ago(40)
    )

    assert report.as_dict() == {
        "llm_payloads_purged": 1,
        "login_tokens_purged": 1,
        "request_logs_purged": 1,
        "backup_windows_closed": 1,
        "backup_windows_overdue": 0,
    }


async def test_the_retention_defaults_are_the_numbers_the_policy_states(database):
    """One source of truth for §9's three clocks.

    The privacy policy states 30 days, 90 days and 30 days. A separate test
    (`test_compliance.py`) reads the policy file and compares it against these
    same settings, so neither the code nor the prose can move alone.
    """
    settings = Settings(database_url="unused://")
    assert settings.llm_payload_retention_days == 30
    assert settings.request_log_retention_days == 90
    assert settings.backup_retention_days == 30
