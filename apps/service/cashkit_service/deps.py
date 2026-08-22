"""FastAPI dependencies — the service's whole wiring surface.

Every collaborator a handler needs (settings, clock, database, mailer, book
runtime) arrives through one of these. Tests replace them with
``app.dependency_overrides``; nothing reaches for a module global.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncConnection

from .auth import Principal, authenticate
from .clock import Clock
from .config import Settings
from .db import Database, books
from .errors import no_book, unauthorized
from .mail import Mailer


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_clock_dep(request: Request) -> Clock:
    return request.app.state.clock


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def get_books(request: Request):
    """The :class:`~cashkit_service.books.BookRuntime` registry."""
    return request.app.state.books


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ClockDep = Annotated[Clock, Depends(get_clock_dep)]
DatabaseDep = Annotated[Database, Depends(get_db)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]


async def get_conn(db: DatabaseDep):
    """One transactional connection per request."""
    async with db.connect() as conn:
        yield conn


ConnDep = Annotated[AsyncConnection, Depends(get_conn)]


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized()
    token = authorization[7:].strip()
    if not token:
        raise unauthorized()
    return token


async def current_principal(
    conn: ConnDep,
    settings: SettingsDep,
    clock: ClockDep,
    token: Annotated[str, Depends(bearer_token)],
) -> Principal:
    return await authenticate(conn, token=token, clock=clock, settings=settings)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


class BookRow:
    """The caller's book row, as the service needs it."""

    __slots__ = ("id", "user_id", "storage_path", "active_scenario")

    def __init__(self, id: uuid.UUID, user_id: uuid.UUID, storage_path: str, active_scenario: str) -> None:
        self.id = id
        self.user_id = user_id
        self.storage_path = storage_path
        self.active_scenario = active_scenario


async def load_book_row(conn: AsyncConnection, user_id: uuid.UUID) -> BookRow | None:
    row = (
        await conn.execute(
            sa.select(books.c.id, books.c.user_id, books.c.storage_path, books.c.active_scenario).where(
                books.c.user_id == user_id
            )
        )
    ).first()
    if row is None:
        return None
    return BookRow(row.id, row.user_id, row.storage_path, row.active_scenario)


async def current_book(conn: ConnDep, principal: PrincipalDep) -> BookRow:
    row = await load_book_row(conn, principal.user_id)
    if row is None:
        raise no_book()
    return row


BookDep = Annotated[BookRow, Depends(current_book)]
