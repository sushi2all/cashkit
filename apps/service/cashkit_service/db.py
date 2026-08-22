"""Postgres access — the app database, never book content.

SQLAlchemy Core over asyncpg. Everything is async because the whole service
runs on the event-loop thread: a kit instance is thread-bound (SPEC §2.2,
proto findings §4), so a blocking driver in a threadpool would be the one
pattern that breaks it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

metadata = sa.MetaData()


def _uuid_col() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


users = sa.Table(
    "users",
    metadata,
    _uuid_col(),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deleted_at", sa.DateTime(timezone=True)),
)

login_tokens = sa.Table(
    "login_tokens",
    metadata,
    _uuid_col(),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consumed_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

sessions = sa.Table(
    "sessions",
    metadata,
    _uuid_col(),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
)

books = sa.Table(
    "books",
    metadata,
    _uuid_col(),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
    sa.Column("storage_path", sa.Text, nullable=False),
    sa.Column("active_scenario", sa.Text, nullable=False, server_default="base"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

turns = sa.Table(
    "turns",
    metadata,
    _uuid_col(),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("input_text", sa.Text, nullable=False),
    sa.Column("kind", sa.Text),
    sa.Column("context", sa.Text),
    sa.Column("intents", JSONB),
    sa.Column("model", sa.Text),
    sa.Column("prompt_tokens", sa.Integer),
    sa.Column("completion_tokens", sa.Integer),
    sa.Column("cost", sa.Numeric(12, 6)),
    sa.Column("latency_ms", sa.Integer),
    sa.Column("outcome", sa.Text),
    sa.Column("diagnostics", JSONB),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

llm_calls = sa.Table(
    "llm_calls",
    metadata,
    _uuid_col(),
    sa.Column("turn_id", UUID(as_uuid=True), sa.ForeignKey("turns.id", ondelete="CASCADE"), nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("purpose", sa.Text, nullable=False),
    sa.Column("request", JSONB),
    sa.Column("response", JSONB),
    sa.Column("prompt_tokens", sa.Integer),
    sa.Column("completion_tokens", sa.Integer),
    sa.Column("cost", sa.Numeric(12, 6)),
    sa.Column("latency_ms", sa.Integer),
    sa.Column("error", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

proposals = sa.Table(
    "proposals",
    metadata,
    _uuid_col(),
    sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    sa.Column("turn_id", UUID(as_uuid=True), sa.ForeignKey("turns.id", ondelete="SET NULL")),
    sa.Column("origin", sa.Text, nullable=False),
    sa.Column("context", sa.Text),
    sa.Column("scenario", sa.Text, nullable=False),
    sa.Column("ops", JSONB, nullable=False),
    sa.Column("deltas", JSONB, nullable=False),
    sa.Column("base_revision", sa.Text),
    sa.Column("overlay_fingerprint", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("supersedes", UUID(as_uuid=True), sa.ForeignKey("proposals.id", ondelete="SET NULL")),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("resolved_at", sa.DateTime(timezone=True)),
)

import_jobs = sa.Table(
    "import_jobs",
    metadata,
    _uuid_col(),
    sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("report", JSONB),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


class Database:
    """Owns the connection pool and hands out transactional connections."""

    def __init__(self, url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True, future=True)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[AsyncConnection]:
        """One connection inside one transaction; commits on clean exit."""
        async with self._engine.begin() as conn:
            yield conn

    async def dispose(self) -> None:
        await self._engine.dispose()
