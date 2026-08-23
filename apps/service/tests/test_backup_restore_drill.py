"""The restore drill (SPEC §8, and the S6 gate clause "restore-from-backup
executed once successfully").

**A restore procedure nobody ran is not a backup.** So this is not a test of a
backup module — there is no backup module. It runs the production shell
scripts, in the production container image, against real Postgres containers
and a real S3-compatible object store (MinIO), and then opens the restored
books **with the engine** and compares every closing balance string against
what the original computed before the backup was taken.

Nothing here is a stand-in for the thing it tests:

* `ops/backup/backup.sh` and `restore.sh` are the files the VM runs. The only
  difference from production is `S3_ENDPOINT` and two containers standing in
  for two moments in time.
* MinIO speaks the S3 API the `aws s3` calls make. Hetzner Object Storage is
  the production endpoint; neither is mocked.
* The books are real books built with the SDK, with real commits, a real
  ledger, and — deliberately — **uncommitted working-overlay changes**, which
  a `git bundle` alone cannot carry. A backup that silently dropped them would
  pass a lazier drill and would lose whatever the user had not saved.
* The verification is the engine's own numbers, string-equal, not a file hash.
  Identical bytes prove a copy; identical figures prove a book.

Marked `drill` and excluded from the per-commit run, because it builds an
image and starts four containers::

    uv run pytest apps/service/tests/test_backup_restore_drill.py -m drill -q
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from cashkit_service.db import Database, books, llm_calls, turns, users
from cashkit_service.migrate import apply_migrations

pytestmark = pytest.mark.drill

COMPOSE = Path(__file__).resolve().parents[3] / "ops" / "backup" / "docker-compose.drill.yml"
SRC_DB = "postgresql+asyncpg://cashkit:cashkit@localhost:55433/cashkit"
DST_DB = "postgresql+asyncpg://cashkit:cashkit@localhost:55434/cashkit_restored"


def run(*args: str, env: dict[str, str] | None = None, timeout: int = 900) -> str:
    """A shell step, with its output attached to the failure if it fails."""
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(args, capture_output=True, text=True, env=merged, timeout=timeout)
    if result.returncode != 0:
        raise AssertionError(
            f"$ {' '.join(args)}\nexit {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return result.stdout


def compose(*args: str, env: dict[str, str] | None = None, timeout: int = 900) -> str:
    return run("docker", "compose", "-f", str(COMPOSE), *args, env=env, timeout=timeout)


# --- the world the drill loses and gets back ------------------------------ #


def author_book(root: Path, *, opening: str, salary: str, rent: str) -> None:
    """A real book: two committed revisions, a ledger row, and a dirty overlay.

    The last part is the point. `add_item` saves to the working tree without
    committing (D-S55-04), so the third item exists in the overlay and in no
    commit — exactly the state SPEC §2.4's dirty indicator is about, and
    exactly what a `git bundle` cannot carry on its own.
    """
    from cashkit.model import (
        Amount, Event, Grain, Item, PeriodRange, Recurrence, Segment, Settlement,
    )
    from cashkit.sdk import CashKit, create_book

    create_book(
        root,
        id=root.name,
        horizon=PeriodRange(start=dt.date(2026, 1, 1), end=dt.date(2027, 1, 1)),
        opening_balance=Decimal(opening),
        grain=Grain.MONTH,
    )
    kit, diagnostics = CashKit.open(root)
    assert kit is not None, diagnostics

    kit.add_item(
        Item(
            id="salary", name="Salary", kind="flow", direction="in", tags={"cat": "income"},
            segments=[Segment(
                start=dt.date(2026, 1, 1),
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal(salary)),
            )],
            settlement=Settlement.immediate(),
        )
    )
    kit.commit("salary")

    kit.add_item(
        Item(
            id="rent", name="Rent", kind="flow", direction="out", tags={"cat": "housing"},
            segments=[Segment(
                start=dt.date(2026, 1, 1),
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal(rent)),
            )],
            settlement=Settlement.immediate(),
        )
    )
    kit.add_event(
        Event(
            id="jan-actual", item="rent", date=dt.date(2026, 1, 5),
            amount=Decimal(rent), status="actual", source="drill",
        )
    )
    kit.commit("rent and one actual")

    # Uncommitted from here: the working overlay a bundle cannot carry.
    kit.add_item(
        Item(
            id="unsaved_gym", name="Gym", kind="flow", direction="out", tags={"cat": "health"},
            segments=[Segment(
                start=dt.date(2026, 2, 1),
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal("-49.90")),
            )],
            settlement=Settlement.immediate(),
        )
    )


def fingerprint(root: Path) -> dict[str, object]:
    """What the engine says about this book, as strings.

    Every closing balance in the horizon, the item ids the overlay holds, the
    ledger rows, and the revision at HEAD. A restore that produced a different
    one of these has restored a different book, whatever the bytes say.
    """
    from cashkit.sdk import CashKit

    from cashkit_service.money import money
    from cashkit_service.serialize import closing_series

    kit, diagnostics = CashKit.open(root)
    assert kit is not None, diagnostics
    # The service's own serializer, so "identical" means what an endpoint
    # would have said and not what a private helper thinks.
    run = kit.run()
    closings = [money(v).exact for v in closing_series(run)]
    items = sorted(run.book.items)
    table = kit.query_events(include_voided=True)
    events = sorted(repr(row) for row in table.rows)
    history = kit.history(limit=50)
    return {
        "closings": closings,
        "items": items,
        "events": events,
        "revisions": [h.id for h in history],
        "messages": [h.message for h in history],
    }


async def seed_source_database(db: Database) -> dict[str, object]:
    """Rows in every table that matters, and the markers to check them by."""
    await apply_migrations(db)
    user_id, book_id, turn_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with db.connect() as conn:
        await conn.execute(
            users.insert().values(
                id=user_id, email="drill@example.com", created_at=dt.datetime.now(dt.timezone.utc)
            )
        )
        await conn.execute(
            books.insert().values(
                id=book_id, user_id=user_id, storage_path="/books/drill",
                created_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        await conn.execute(
            turns.insert().values(
                id=turn_id, user_id=user_id, book_id=book_id, request_id="drill-req",
                input_text="a sentence only the source database has",
                kind="answer", cost=Decimal("0.000411"),
                created_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        await conn.execute(
            llm_calls.insert().values(
                id=uuid.uuid4(), turn_id=turn_id, seq=0, purpose="interpret",
                request={"messages": [{"role": "user", "content": "drill"}]},
                response={"reply": "ok"}, prompt_tokens=11, completion_tokens=3,
                created_at=dt.datetime.now(dt.timezone.utc),
            )
        )
    return {"user_id": user_id, "turn_id": turn_id}


# --- fixtures ------------------------------------------------------------- #


@pytest.fixture(scope="module")
def drill_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("drill")
    for name in ("books", "restore", "age", "marker"):
        (root / name).mkdir()
    # Docker Desktop on macOS shares /tmp and /private/tmp; resolve so the bind
    # mount is the path the daemon can actually see.
    return root.resolve()


@pytest.fixture(scope="module")
def stack(drill_root: Path):
    """Build the image, mint a keypair, start MinIO and both databases."""
    env = {"CASHKIT_DRILL_ROOT": str(drill_root)}
    compose("build", "backup", env=env)
    # The recipient must exist before the stack starts, since it travels as an
    # environment variable. age-keygen writes the identity and prints the
    # public half on stderr; the identity file carries it as a comment too.
    compose(
        "run", "--rm", "--no-deps", "--entrypoint", "sh", "backup",
        "-c", "age-keygen -o /age/identity.txt 2>/dev/null",
        env=env,
    )
    identity = (drill_root / "age" / "identity.txt").read_text()
    recipient = next(
        line.split(": ", 1)[1].strip()
        for line in identity.splitlines()
        if line.startswith("# public key:")
    )
    env["CASHKIT_DRILL_AGE_RECIPIENT"] = recipient
    compose("up", "-d", "--wait", env=env)
    compose(
        "exec", "-T", "backup", "sh", "-c",
        "aws --endpoint-url http://minio:9000 s3 mb s3://cashkit-backups || true",
        env=env,
    )
    try:
        yield env
    finally:
        compose("down", "-v", "--remove-orphans", env=env, timeout=300)


# --- the drill ------------------------------------------------------------ #


async def test_restore_from_backup(drill_root: Path, stack: dict[str, str]):
    """Back up, lose everything, restore, and check the engine's own numbers."""
    books_dir = drill_root / "books"
    ids = ["book_alpha", "book_beta"]
    author_book(books_dir / ids[0], opening="2500.00", salary="2617.33", rent="-912.50")
    author_book(books_dir / ids[1], opening="410.05", salary="1980.00", rent="-1240.75")
    truth = {book_id: fingerprint(books_dir / book_id) for book_id in ids}
    assert truth[ids[0]]["closings"], "the source book computes something"
    assert "unsaved_gym" in truth[ids[0]]["items"], "the overlay carries an uncommitted item"
    assert len(truth[ids[0]]["revisions"]) >= 2, "there is history to bundle"

    src = Database(SRC_DB)
    try:
        markers = await seed_source_database(src)
        async with src.connect() as conn:
            source_counts = {
                table.name: (
                    await conn.execute(sa.select(sa.func.count()).select_from(table))
                ).scalar_one()
                for table in (users, books, turns, llm_calls)
            }
    finally:
        await src.dispose()

    # --- back up ---------------------------------------------------------- #
    output = compose("exec", "-T", "backup", "/usr/local/bin/backup.sh", env=stack)
    assert "books 2" in output or "2 book(s)" in output, output

    # --- lose everything -------------------------------------------------- #
    shutil.rmtree(books_dir)
    books_dir.mkdir()
    assert list(books_dir.iterdir()) == []

    # --- restore ---------------------------------------------------------- #
    restored_root = drill_root / "restore" / "books"
    compose(
        "exec", "-T", "-e", "PGHOST=postgres-dst", "backup",
        "/usr/local/bin/restore.sh", "latest", "/restore/books", "cashkit_restored",
        env=stack,
    )

    # --- the books, by the engine's own figures --------------------------- #
    for book_id in ids:
        restored = fingerprint(restored_root / book_id)
        assert restored["closings"] == truth[book_id]["closings"], (
            f"{book_id}: the restored book computes different closing balances"
        )
        assert restored["items"] == truth[book_id]["items"]
        assert restored["events"] == truth[book_id]["events"]
        assert restored["revisions"] == truth[book_id]["revisions"], (
            f"{book_id}: the revision history did not survive the bundle"
        )
        assert restored["messages"] == truth[book_id]["messages"]
        assert "unsaved_gym" in restored["items"], (
            f"{book_id}: the uncommitted working overlay was lost — a bundle "
            "carries commits only, which is why the tree archive exists"
        )

    # --- the app database ------------------------------------------------- #
    dst = Database(DST_DB)
    try:
        async with dst.connect() as conn:
            restored_counts = {
                table.name: (
                    await conn.execute(sa.select(sa.func.count()).select_from(table))
                ).scalar_one()
                for table in (users, books, turns, llm_calls)
            }
            row = (
                await conn.execute(sa.select(turns).where(turns.c.id == markers["turn_id"]))
            ).one()
    finally:
        await dst.dispose()
    assert restored_counts == source_counts
    assert row.input_text == "a sentence only the source database has"
    assert str(row.cost) == "0.000411", "a Decimal column survived as a Decimal"


def test_a_backup_is_unreadable_without_the_identity(drill_root: Path, stack: dict[str, str]):
    """Encryption at rest, checked by trying to read it (SPEC §9).

    The backup container holds the **public** key only: it can write a backup
    it cannot read. That is the property worth having — a compromised backup
    sidecar leaks nothing — and it is only worth having if it is true, so the
    drill downloads an object and confirms it is neither the plaintext dump nor
    decryptable without the identity file.
    """
    listing = compose(
        "exec", "-T", "backup", "sh", "-c",
        "aws --endpoint-url http://minio:9000 s3 ls --recursive s3://cashkit-backups/",
        env=stack,
    )
    assert ".age" in listing, listing
    assert "pg.dump.age" in listing
    # Nothing is stored in the clear except the manifest and the marker.
    stored = [line.split()[-1] for line in listing.strip().splitlines()]
    clear = [
        name for name in stored
        if not name.endswith(".age") and not name.endswith(("MANIFEST", "COMPLETE"))
    ]
    assert clear == [], f"objects stored unencrypted: {clear}"

    probe = compose(
        "exec", "-T", "backup", "sh", "-c",
        "aws --endpoint-url http://minio:9000 s3 cp "
        "$(aws --endpoint-url http://minio:9000 s3 ls --recursive s3://cashkit-backups/ "
        "| grep pg.dump.age | head -1 | awk '{print \"s3://cashkit-backups/\" $4}') /tmp/probe.age "
        "--only-show-errors && head -c 21 /tmp/probe.age && echo "
        "&& (age -d -o /tmp/probe.out /tmp/probe.age </dev/null 2>&1 || true)",
        env=stack,
    )
    assert probe.splitlines()[0] == "age-encryption.org/v1", probe
    assert "PGDMP" not in probe, "the dump is stored in the clear"
    # The container that wrote it cannot read it: it holds the recipient, not
    # the identity. `restore.sh` requires BACKUP_AGE_IDENTITY_FILE for exactly
    # this reason, and the drill supplies it only there.
    assert "no identity matched" in probe, probe


def test_prune_keeps_the_window_and_publishes_what_is_left(
    drill_root: Path, stack: dict[str, str]
):
    """Retention, and the marker the §9 deletion window is closed against.

    Three snapshots are planted by hand: one inside the window, one outside it
    with a COMPLETE marker, and one outside it **without** one. The prune must
    delete exactly the second. Deleting the third would be a system that throws
    away the evidence of its own failed run; keeping the second would be a
    system whose retention policy is a comment.

    Afterwards the marker file must name the oldest snapshot still in the
    bucket, because that timestamp is what `close_backup_windows()` decides
    against (D-MLP-99).
    """
    old_complete = "2020-01-01T03-15-00Z"
    old_incomplete = "2020-02-01T03-15-00Z"
    plant = "; ".join(
        [
            "S=http://minio:9000",
            "echo x > /tmp/o",
            f"aws --endpoint-url $S s3 cp /tmp/o s3://cashkit-backups/drill/{old_complete}/pg.dump.age --only-show-errors",
            f"aws --endpoint-url $S s3 cp /tmp/o s3://cashkit-backups/drill/{old_complete}/COMPLETE --only-show-errors",
            f"aws --endpoint-url $S s3 cp /tmp/o s3://cashkit-backups/drill/{old_incomplete}/pg.dump.age --only-show-errors",
        ]
    )
    compose("exec", "-T", "backup", "sh", "-c", plant, env=stack)

    output = compose("exec", "-T", "backup", "/usr/local/bin/prune.sh", env=stack)
    assert f"deleted {old_complete}" in output, output
    assert "KEEPING incomplete snapshot" in output or old_incomplete in output

    listing = compose(
        "exec", "-T", "backup", "sh", "-c",
        "aws --endpoint-url http://minio:9000 s3 ls s3://cashkit-backups/drill/",
        env=stack,
    )
    assert old_complete not in listing, "a complete snapshot past retention survived"
    assert old_incomplete in listing, (
        "an incomplete snapshot was deleted; a half-written run is the evidence "
        "of a failure, not a backup to reclaim space from"
    )

    marker = (drill_root / "marker" / "backup-oldest.txt").read_text().strip()
    assert marker.startswith("2020-02-01T03:15:00"), marker
    dt.datetime.fromisoformat(marker)  # the retention sweep parses it with this
