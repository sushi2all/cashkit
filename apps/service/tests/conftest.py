"""Test fixtures.

Two things every test here gets for free:

* **A real Postgres.** The schema uses ``jsonb``, ``uuid`` and cascades; a
  SQLite stand-in would test a different database. The fixture starts the
  compose file itself when nothing is listening, so
  ``uv run pytest apps/service/tests`` is one command.
* **A frozen clock.** ``as_of``, session expiry and proposal expiry are all
  read from it, so no assertion in this suite depends on the day it runs
  (D-MLP-12).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import os
import socket
import subprocess
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from cashkit_service.app import create_app
from cashkit_service.books import BookRuntime
from cashkit_service.clock import FixedClock
from cashkit_service.config import Settings
from cashkit_service.db import Database
from cashkit_service.mail import CapturingMailer
from cashkit_service.migrate import apply_migrations

COMPOSE_FILE = Path(__file__).resolve().parents[1] / "docker-compose.dev.yml"
DB_HOST, DB_PORT = "localhost", 55432
ADMIN_URL = f"postgresql+asyncpg://cashkit:cashkit@{DB_HOST}:{DB_PORT}/cashkit"

#: Every test runs "today" here. A Tuesday inside the fixture book's horizon.
FROZEN_NOW = _dt.datetime(2026, 3, 17, 9, 30, tzinfo=_dt.timezone.utc)


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_postgres() -> None:
    if _port_open(DB_HOST, DB_PORT):
        return
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.exit(
            "No Postgres on localhost:55432 and the container would not start: "
            f"{exc}\nStart it by hand:\n"
            f"  docker compose -f {COMPOSE_FILE} up -d --wait",
            returncode=1,
        )


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def postgres() -> None:
    _ensure_postgres()


@pytest_asyncio.fixture
async def database(postgres) -> Database:
    """A private database per test, so nothing leaks between them."""
    name = f"ck_test_{uuid.uuid4().hex[:16]}"
    admin = Database(ADMIN_URL + "?prepared_statement_cache_size=0")
    raw_engine = admin.engine.execution_options(isolation_level="AUTOCOMMIT")
    async with raw_engine.connect() as conn:
        await conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()

    db = Database(f"postgresql+asyncpg://cashkit:cashkit@{DB_HOST}:{DB_PORT}/{name}")
    await apply_migrations(db)
    try:
        yield db
    finally:
        await db.dispose()
        admin = Database(ADMIN_URL)
        raw_engine = admin.engine.execution_options(isolation_level="AUTOCOMMIT")
        async with raw_engine.connect() as conn:
            await conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            await conn.execute(sa.text(f'DROP DATABASE "{name}"'))
        await admin.dispose()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(FROZEN_NOW)


@pytest.fixture
def mailer() -> CapturingMailer:
    return CapturingMailer()


@pytest.fixture
def books_root(tmp_path: Path) -> Path:
    root = tmp_path / "books"
    root.mkdir()
    return root


@pytest.fixture
def settings(books_root: Path) -> Settings:
    return Settings(
        database_url="unused://",
        books_root=books_root,
        # SPEC §10 contract tests: the §3 response invariants are checked by
        # middleware in test mode.
        check_response_invariants=True,
    )


@pytest.fixture
def app(settings: Settings, clock: FixedClock, mailer: CapturingMailer, database: Database, books_root: Path):
    return create_app(
        settings=settings,
        clock=clock,
        mailer=mailer,
        database=database,
        book_runtime=BookRuntime(books_root),
    )


@pytest_asyncio.fixture
async def client(app) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.state.books.close_all()


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient, mailer: CapturingMailer) -> AsyncClient:
    """A client carrying a live session for ``user@example.com``."""
    email = "user@example.com"
    await client.post("/auth/link", json={"email": email, "platform": "web"})
    link = mailer.last_for(email)
    response = await client.post("/auth/verify", json={"token": link.token, "platform": "web"})
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


@pytest_asyncio.fixture
async def book_client(auth_client: AsyncClient) -> AsyncClient:
    """A client whose account owns an empty book over 2026.

    The horizon brackets :data:`FROZEN_NOW`, so ``as_of`` always falls inside
    it and "today" means something in every assertion.
    """
    response = await auth_client.post(
        "/books",
        json={
            "horizon_start": "2026-01-01",
            "horizon_end": "2027-01-01",
            "opening_balance": "2500.00",
        },
    )
    assert response.status_code == 201, response.text
    return auth_client
