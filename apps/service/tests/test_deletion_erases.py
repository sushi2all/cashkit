"""`DELETE /me` verifiably erases (SPEC §9, the S6 gate clause).

The gate's wording is *verifiably erases Postgres rows (turns + llm_calls
included) and the book directory*, and the operative word is **verifiably**.
S1's `test_me.py` proves the sessions go and the `users` row goes; that is a
test of the two tables the endpoint names in its own code. This file takes the
other approach on purpose: it **seeds every table the schema has**, deletes,
and then asserts the whole database is empty — table by table, by walking the
metadata rather than by listing the tables a reader happened to think of.

That difference matters. A per-table assertion passes for ever once written;
a metadata walk fails the day someone adds a table without a cascade, which is
exactly the failure this clause exists to catch. `login_tokens` is the proof
that the failure is real rather than theoretical: it has no `user_id`, no
cascade reaches it, and until this session an unconsumed row left the address
in the database after the account was erased.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from cashkit_service.db import (
    books,
    deletions,
    import_jobs,
    llm_calls,
    login_tokens,
    metadata,
    proposals,
    sessions,
    turns,
    users,
)

#: Tables a deleted account must leave nothing in. Everything the schema has,
#: minus the deletion receipt itself (which is the record *of* the deletion and
#: carries no personal data) and the migration bookkeeping table.
KEPT_BY_DESIGN = {"deletions", "schema_migrations"}


async def _seed_everything(client, database, books_root: Path) -> dict[str, object]:
    """Build an account that has touched every table, and return its markers.

    Nothing here is a shortcut around a service path that has one: the book is
    created through `POST /books` and the proposal through `POST /book/edits`,
    so the rows are the rows the product writes. `turns`, `llm_calls` and
    `import_jobs` have no model-free service path, so they are inserted
    directly — the point of this test is the cascade, not the writer.
    """
    email = "erase.me@example.com"
    await client.post("/auth/link", json={"email": email, "platform": "web"})
    from cashkit_service.mail import CapturingMailer

    mailer: CapturingMailer = client._mailer  # type: ignore[attr-defined]
    link = mailer.last_for(email)
    verified = await client.post("/auth/verify", json={"token": link.token, "platform": "web"})
    token = verified.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"

    # A second, unconsumed link for the same address: the row no cascade reaches.
    await client.post("/auth/link", json={"email": email, "platform": "web"})

    created = await client.post(
        "/books",
        json={
            "horizon_start": "2026-01-01",
            "horizon_end": "2027-01-01",
            "opening_balance": "1000.00",
        },
    )
    assert created.status_code == 201, created.text
    book_id = uuid.UUID((await client.get("/me")).json()["book_id"])

    # A real proposal through the real pipeline.
    proposed = await client.post(
        "/book/edits",
        json={
            "origin": "settings",
            "ops": [{"op": "set_opening_balance", "amount": "1234.00"}],
        },
    )
    assert proposed.status_code == 201, proposed.text

    async with database.connect() as conn:
        user_id = (
            await conn.execute(sa.select(users.c.id).where(users.c.email == email))
        ).scalar_one()
        turn_id = uuid.uuid4()
        await conn.execute(
            turns.insert().values(
                id=turn_id,
                user_id=user_id,
                book_id=book_id,
                request_id="req-erase",
                input_text="what my money is doing",
                kind="answer",
                created_at=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
            )
        )
        await conn.execute(
            llm_calls.insert().values(
                id=uuid.uuid4(),
                turn_id=turn_id,
                seq=0,
                purpose="interpret",
                request={"messages": [{"role": "user", "content": "my salary is 2617.33"}]},
                response={"reply": "understood"},
                created_at=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
            )
        )
        await conn.execute(
            import_jobs.insert().values(
                id=uuid.uuid4(),
                book_id=book_id,
                status="done",
                report={"rows": []},
                created_at=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
            )
        )

    storage = Path((await _storage_path(database, book_id)))
    assert storage.is_dir(), "the book directory exists before deletion"
    return {"email": email, "user_id": user_id, "book_id": book_id, "storage": storage}


async def _storage_path(database, book_id: uuid.UUID) -> str:
    async with database.connect() as conn:
        return (
            await conn.execute(sa.select(books.c.storage_path).where(books.c.id == book_id))
        ).scalar_one()


@pytest.fixture
def client_with_mailer(client, mailer):
    client._mailer = mailer
    return client


async def test_delete_me_leaves_nothing_in_any_table(client_with_mailer, database, books_root):
    """The whole database is empty afterwards, by metadata walk.

    Not "the tables I remembered are empty" — every table the schema defines.
    """
    seeded = await _seed_everything(client_with_mailer, database, books_root)

    # Everything is there first, or the assertion below would pass vacuously.
    async with database.connect() as conn:
        for table in metadata.sorted_tables:
            if table.name in KEPT_BY_DESIGN:
                continue
            count = (
                await conn.execute(sa.select(sa.func.count()).select_from(table))
            ).scalar_one()
            assert count > 0, f"{table.name} was not seeded; this test would prove nothing"

    assert (await client_with_mailer.delete("/me")).status_code == 204

    async with database.connect() as conn:
        remaining = {}
        for table in metadata.sorted_tables:
            if table.name in KEPT_BY_DESIGN:
                continue
            count = (
                await conn.execute(sa.select(sa.func.count()).select_from(table))
            ).scalar_one()
            if count:
                remaining[table.name] = count
    assert remaining == {}, f"rows survived the deletion: {remaining}"

    assert not seeded["storage"].exists(), "the book directory survived the deletion"


async def test_turns_and_llm_calls_go_by_name(client_with_mailer, database, books_root):
    """The gate names two tables explicitly, so they are asserted explicitly.

    A metadata walk is the stronger test and could regress into a weaker one if
    a table were ever added to `KEPT_BY_DESIGN` by mistake. These two never can
    be: they hold the user's words and the raw model payloads.
    """
    await _seed_everything(client_with_mailer, database, books_root)
    await client_with_mailer.delete("/me")
    async with database.connect() as conn:
        assert (await conn.execute(sa.select(sa.func.count()).select_from(turns))).scalar_one() == 0
        assert (
            await conn.execute(sa.select(sa.func.count()).select_from(llm_calls))
        ).scalar_one() == 0
        assert (
            await conn.execute(sa.select(sa.func.count()).select_from(proposals))
        ).scalar_one() == 0
        assert (
            await conn.execute(sa.select(sa.func.count()).select_from(import_jobs))
        ).scalar_one() == 0
        assert (
            await conn.execute(sa.select(sa.func.count()).select_from(sessions))
        ).scalar_one() == 0


async def test_no_link_token_for_the_address_survives(client_with_mailer, database, books_root):
    """The row no cascade reaches.

    `login_tokens` keys on the email, not on the user, because a link can be
    requested before an account exists. An unconsumed row therefore outlived
    the account until this session, leaving the one genuinely identifying
    column in the database after the account holding it was erased.
    """
    seeded = await _seed_everything(client_with_mailer, database, books_root)
    async with database.connect() as conn:
        before = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(login_tokens)
                .where(login_tokens.c.email == seeded["email"])
            )
        ).scalar_one()
    assert before >= 1, "an unconsumed link exists before the deletion"

    await client_with_mailer.delete("/me")

    async with database.connect() as conn:
        rows = (await conn.execute(sa.select(login_tokens))).all()
    assert rows == [], f"a link token survived the deletion: {rows}"


async def test_the_deletion_receipt_carries_no_personal_data(
    client_with_mailer, database, books_root, clock
):
    """§9's 30-day backup obligation needs a row; that row names nobody.

    A hard delete destroys the thing the obligation was attached to. The
    receipt is what carries it forward, and its whole content is a uuid that
    now references nothing plus two timestamps — no email, no book path, no
    text the user typed.
    """
    seeded = await _seed_everything(client_with_mailer, database, books_root)
    await client_with_mailer.delete("/me")

    async with database.connect() as conn:
        row = (await conn.execute(sa.select(deletions))).one()
    assert row.user_id == seeded["user_id"]
    assert row.deleted_at == clock.now()
    assert row.backup_purge_due_at == clock.now() + dt.timedelta(days=30)
    assert row.backups_purged_at is None, "nothing has proved the backups are gone yet"

    columns = {c.name for c in deletions.columns}
    assert columns == {"user_id", "deleted_at", "backup_purge_due_at", "backups_purged_at"}
    blob = repr(dict(row._mapping)).lower()
    assert seeded["email"] not in blob
    assert "storage" not in blob and "/books" not in blob


async def test_the_users_table_has_no_deleted_at_column(database):
    """D-MLP-22 resolved: the column is gone, not merely unused.

    A nullable column nothing ever sets is a promise the schema makes and the
    code does not keep. Whoever reads `users` next should not have to grep the
    service to learn that soft deletion is not a thing here.
    """
    async with database.connect() as conn:
        found = (
            await conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users'"
                )
            )
        ).scalars().all()
    assert "deleted_at" not in found
    assert "deleted_at" not in {c.name for c in users.columns}


async def test_export_carries_the_account_before_it_is_erased(client_with_mailer, database, books_root):
    """The other half of §9's pair: deletion and export.

    A user who deletes should have been able to take everything with them
    first, so the archive is checked against the same seeded account — the
    book directory included, since a book is the data, not an internal format.
    """
    import io
    import json
    import zipfile

    await _seed_everything(client_with_mailer, database, books_root)
    response = await client_with_mailer.get("/me/export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = zf.namelist()
        turn_rows = json.loads(zf.read("account/turns.json"))
        call_rows = json.loads(zf.read("account/llm_calls.json"))
    assert any(n.startswith("book/") for n in names), "the book directory is in the archive"
    assert turn_rows and turn_rows[0]["input_text"] == "what my money is doing"
    assert call_rows and call_rows[0]["request"], "the raw model payloads are the user's too"
    assert not any("token_hash" in n for n in names)
