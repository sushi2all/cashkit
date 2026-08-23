"""Retention: the jobs that make SPEC §9's promises true over time.

SPEC §9 states three clocks in the privacy policy, and a clock stated but not
run is a lie with a timestamp on it:

* **raw model payloads purge after 30 days** — ``llm_calls.request`` and
  ``llm_calls.response`` carry the user's financial data verbatim (SPEC §4).
  The numeric columns survive, so the cost and latency history stays intact.
* **request logs purge after 90 days** — the structured JSON access log of
  SPEC §11. It carries no user identifier by construction (see
  :mod:`cashkit_service.requestlog`), but it is still a log of a person's
  activity and it still has a stated life.
* **account deletion cascades through all of it, backups included, within 30
  days.** ``DELETE /me`` erases the live rows and the book directory
  immediately (D-MLP-22). Backups are the part that cannot be immediate: an
  object already written to the bucket holds the account until it ages out.

The last one is the reason this module has a notion of *proof* rather than of
elapsed time. The naive implementation marks a deletion "purged" thirty days
later and hopes. :func:`close_backup_windows` instead takes the timestamp of
the **oldest backup still in the bucket** and closes only those deletions that
happened before it — because every object still retained was written after the
account's rows were already gone. That is checkable rather than asserted, and
:func:`overdue_backup_windows` is what an alarm reads when it is not true.

Every function takes ``now`` explicitly. The clock is a dependency
(D-MLP-12), so a retention test is a test and not a wait.

Run them all::

    uv run python -m cashkit_service.retention
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from .clock import Clock, SystemClock
from .config import Settings, get_settings
from .db import Database, deletions, llm_calls, login_tokens

log = logging.getLogger("cashkit.retention")

#: A rotated request log, as :class:`logging.handlers.TimedRotatingFileHandler`
#: names it: ``request.log.2026-08-23``.
ROTATED = re.compile(r"^request\.log\.(\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class RetentionReport:
    """What one sweep did. Every number is a row or a file, never an estimate."""

    llm_payloads_purged: int = 0
    login_tokens_purged: int = 0
    request_logs_purged: int = 0
    backup_windows_closed: int = 0
    backup_windows_overdue: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "llm_payloads_purged": self.llm_payloads_purged,
            "login_tokens_purged": self.login_tokens_purged,
            "request_logs_purged": self.request_logs_purged,
            "backup_windows_closed": self.backup_windows_closed,
            "backup_windows_overdue": self.backup_windows_overdue,
        }


# --- Postgres ------------------------------------------------------------- #


async def purge_llm_payloads(
    conn: AsyncConnection, *, now: dt.datetime, days: int
) -> int:
    """Blank the raw request/response on model calls older than ``days``.

    The row stays: SPEC §4 keeps the numeric columns, which is what the spend
    and repair-rate history is made of. Only the two payload columns go, and
    only where there is something to remove — so a second run over the same
    window reports zero rather than counting the same rows again.
    """
    cutoff = now - dt.timedelta(days=days)
    result = await conn.execute(
        llm_calls.update()
        .where(
            llm_calls.c.created_at < cutoff,
            sa.or_(llm_calls.c.request.isnot(None), llm_calls.c.response.isnot(None)),
        )
        # `sa.null()`, not `None`: a Python `None` handed to a JSONB column is
        # the JSON value `null`, which is a stored document rather than an
        # absent one. The data is gone either way, but `IS NULL` would stay
        # false — so the job would re-count the same rows for ever and the
        # purge metric would show a backlog that does not exist.
        .values(request=sa.null(), response=sa.null())
    )
    return result.rowcount or 0


async def purge_login_tokens(
    conn: AsyncConnection, *, now: dt.datetime, days: int
) -> int:
    """Delete magic-link tokens that expired more than ``days`` ago.

    A link token row is an email address plus a hash. Its TTL is fifteen
    minutes (SPEC §3), so a row that has been dead for a day is an address
    kept for no reason at all.
    """
    cutoff = now - dt.timedelta(days=days)
    result = await conn.execute(
        login_tokens.delete().where(login_tokens.c.expires_at < cutoff)
    )
    return result.rowcount or 0


async def close_backup_windows(
    conn: AsyncConnection, *, now: dt.datetime, oldest_retained_backup_at: dt.datetime | None
) -> int:
    """Close every deletion the bucket can no longer be holding.

    ``oldest_retained_backup_at`` is the timestamp of the oldest object the
    backup prune left in place. Every retained backup was therefore written
    after that instant, so an account deleted before it cannot appear in any
    of them. With an empty bucket there is nothing left to hold anything, so
    every open window closes.

    This is the one honest way to mark a backup purge done: it is a statement
    about what is in the bucket, not about how much time has passed.
    """
    predicate = deletions.c.backups_purged_at.is_(None)
    if oldest_retained_backup_at is not None:
        predicate = sa.and_(predicate, deletions.c.deleted_at < oldest_retained_backup_at)
    result = await conn.execute(
        deletions.update().where(predicate).values(backups_purged_at=now)
    )
    return result.rowcount or 0


async def overdue_backup_windows(conn: AsyncConnection, *, now: dt.datetime) -> int:
    """How many deletions are past their 30-day backup window and still open.

    Anything above zero is a §9 breach in progress, which is why it is on the
    alarm list (`cashkit_deletion_backup_windows_overdue`, SPEC §11) rather
    than in a report nobody reads.
    """
    result = await conn.execute(
        sa.select(sa.func.count())
        .select_from(deletions)
        .where(
            deletions.c.backups_purged_at.is_(None),
            deletions.c.backup_purge_due_at <= now,
        )
    )
    return int(result.scalar_one())


# --- the request log ------------------------------------------------------ #


def purge_request_logs(directory: Path, *, now: dt.datetime, days: int) -> int:
    """Delete rotated request-log files older than ``days``.

    The handler rotates daily and keeps a bounded number of files, so this is
    the second belt rather than the only one — but it is the one a test can
    drive with a frozen clock, and it is what catches a backup count that was
    widened without the policy being widened with it.

    The file's own date suffix decides, not its mtime: an mtime is whatever
    the last copy or restore made it, and this is a retention decision.
    """
    if not directory.is_dir():
        return 0
    cutoff = (now - dt.timedelta(days=days)).date()
    removed = 0
    for path in sorted(directory.iterdir()):
        match = ROTATED.match(path.name)
        if match is None:
            continue
        try:
            stamped = dt.date.fromisoformat(match.group(1))
        except ValueError:  # pragma: no cover - the pattern already matched
            continue
        if stamped < cutoff:
            path.unlink()
            removed += 1
    return removed


# --- the sweep ------------------------------------------------------------ #


async def run_sweep(
    db: Database,
    settings: Settings,
    *,
    now: dt.datetime,
    oldest_retained_backup_at: dt.datetime | None = None,
) -> RetentionReport:
    """Every retention job, once, in one transaction per job."""
    async with db.connect() as conn:
        payloads = await purge_llm_payloads(
            conn, now=now, days=settings.llm_payload_retention_days
        )
        tokens = await purge_login_tokens(
            conn, now=now, days=settings.login_token_retention_days
        )
        closed = await close_backup_windows(
            conn, now=now, oldest_retained_backup_at=oldest_retained_backup_at
        )
        overdue = await overdue_backup_windows(conn, now=now)
    logs = purge_request_logs(
        settings.request_log_dir, now=now, days=settings.request_log_retention_days
    )
    report = RetentionReport(
        llm_payloads_purged=payloads,
        login_tokens_purged=tokens,
        request_logs_purged=logs,
        backup_windows_closed=closed,
        backup_windows_overdue=overdue,
    )
    log.info("retention sweep %s", report.as_dict())
    return report


async def _main(settings: Settings, clock: Clock) -> RetentionReport:
    db = Database(settings.database_url)
    try:
        marker = settings.backup_marker_file
        oldest: dt.datetime | None = None
        if marker.is_file():
            # The backup prune writes the oldest object it left behind here,
            # so the two jobs need no shared client and no shared credentials.
            oldest = dt.datetime.fromisoformat(marker.read_text().strip())
        return await run_sweep(db, settings, now=clock.now(), oldest_retained_backup_at=oldest)
    finally:
        await db.dispose()


if __name__ == "__main__":  # pragma: no cover - operational entry point
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = asyncio.run(_main(get_settings(), SystemClock()))
    print(report.as_dict())


__all__ = [
    "RetentionReport",
    "close_backup_windows",
    "overdue_backup_windows",
    "purge_llm_payloads",
    "purge_login_tokens",
    "purge_request_logs",
    "run_sweep",
]
