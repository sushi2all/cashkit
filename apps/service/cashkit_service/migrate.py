"""Migration runner.

Plain SQL files applied in filename order, each recorded in
``schema_migrations``. The MLP has one deployment and one writer; a migration
framework would be weight without a job to do (D-MLP-09).

    uv run python -m cashkit_service.migrate
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa

from .config import Settings, get_settings
from .db import Database

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_TRACKING_TABLE = sa.text(
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        name       text PRIMARY KEY,
        applied_at timestamptz NOT NULL DEFAULT now()
    )
    """
)


async def _execute_script(conn, sql: str) -> None:
    """Run a multi-statement script.

    asyncpg prepares every statement it is given, and a prepared statement is
    one statement. A migration file is a script, so it goes to the driver
    connection, which speaks the simple query protocol.
    """
    raw = await conn.get_raw_connection()
    await raw.driver_connection.execute(sql)


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def apply_migrations(db: Database) -> list[str]:
    """Apply every unapplied migration. Returns the names it applied."""
    applied: list[str] = []
    async with db.connect() as conn:
        await conn.execute(_TRACKING_TABLE)
        rows = await conn.execute(sa.text("SELECT name FROM schema_migrations"))
        done = {r[0] for r in rows}
    for path in migration_files():
        if path.name in done:
            continue
        # One transaction per migration: a failure leaves the database at the
        # last complete step rather than half-way through this one.
        async with db.connect() as conn:
            await _execute_script(conn, path.read_text())
            await conn.execute(
                sa.text("INSERT INTO schema_migrations (name) VALUES (:n)"),
                {"n": path.name},
            )
        applied.append(path.name)
    return applied


async def _main(settings: Settings) -> None:
    db = Database(settings.database_url)
    try:
        applied = await apply_migrations(db)
        print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(_main(get_settings()))
