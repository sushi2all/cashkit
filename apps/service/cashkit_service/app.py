"""The FastAPI application.

Every route is ``async def`` on purpose (SPEC §2.2): the kit and its SQLite
ledger are thread-bound, so the whole request must stay on the event-loop
thread. A ``def`` handler here would be handed to a threadpool and would break
book access under load — the proto's finding §4, made structural.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .books import BookRuntime
from .clock import Clock, SystemClock
from .config import Settings, get_settings
from .db import Database
from .mail import ConsoleMailer, Mailer
from .middleware import RequestIdMiddleware, ResponseInvariantMiddleware
from .routers import auth as auth_router
from .routers import book_edits
from .routers import book_read
from .routers import books as books_router
from .routers import export as export_router
from .routers import scenarios as scenarios_router
from .routers import me as me_router

TITLE = "CashKit MLP service"
DESCRIPTION = (
    "Deterministic service core for the CashKit consumer MLP "
    "(SPEC-mlp-consumer.md). Every money figure is an engine number, "
    "serialized once, and every payload that carries one carries its "
    "provenance with it."
)


def create_app(
    *,
    settings: Settings | None = None,
    clock: Clock | None = None,
    mailer: Mailer | None = None,
    database: Database | None = None,
    book_runtime: BookRuntime | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.books.close_all()
        if app.state.owns_database:
            await app.state.db.dispose()

    app = FastAPI(title=TITLE, description=DESCRIPTION, version="1", lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock or SystemClock()
    app.state.mailer = mailer or ConsoleMailer()
    app.state.owns_database = database is None
    app.state.db = database or Database(settings.database_url)
    app.state.books = book_runtime or BookRuntime(
        settings.books_root, lock_timeout=settings.book_lock_timeout_seconds
    )

    app.add_middleware(ResponseInvariantMiddleware, enabled=settings.check_response_invariants)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(auth_router.router)
    app.include_router(me_router.router)
    app.include_router(books_router.router)
    app.include_router(book_read.router)
    app.include_router(book_edits.router)
    app.include_router(scenarios_router.router)
    app.include_router(export_router.router)
    return app


app = create_app()
