"""The FastAPI application.

Every route is ``async def`` on purpose (SPEC §2.2): the kit and its SQLite
ledger are thread-bound, so the whole request must stay on the event-loop
thread. A ``def`` handler here would be handed to a threadpool and would break
book access under load — the proto's finding §4, made structural.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .agent.transport import OpenRouterTransport, Transport
from .books import BookRuntime
from .clock import Clock, SystemClock
from .config import Settings, get_settings
from .db import Database
from .imports.jobs import ImportRegistry
from .mail import ConsoleMailer, Mailer
from .metrics import MetricsRegistry
from .middleware import RequestIdMiddleware, ResponseInvariantMiddleware
from .observability import install_sentry
from .requestlog import RequestLogMiddleware
from .routers import auth as auth_router
from .routers import book_edits
from .routers import book_read
from .routers import books as books_router
from .routers import export as export_router
from .routers import imports as imports_router
from .routers import scenarios as scenarios_router
from .routers import me as me_router
from .routers import ops as ops_router
from .routers import turns as turns_router

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
    transport: Transport | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.imports.close_all()
        app.state.books.close_all()
        if app.state.transport is not None and app.state.owns_transport:
            await app.state.transport.aclose()
        if app.state.owns_database:
            await app.state.db.dispose()

    app = FastAPI(title=TITLE, description=DESCRIPTION, version="1", lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock or SystemClock()
    app.state.mailer = mailer or ConsoleMailer()
    app.state.owns_database = database is None
    app.state.db = database or Database(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
    )
    app.state.books = book_runtime or BookRuntime(
        settings.books_root, lock_timeout=settings.book_lock_timeout_seconds
    )
    # In-process, single-node, and deliberate: an import's progress lives with
    # the task producing it (SPEC §12 is one VM). The registry is cleared on
    # shutdown, and a job whose process is gone is gone — a half-finished
    # import applied nothing, so there is nothing to recover.
    app.state.imports = ImportRegistry()
    # The model transport is optional: without a key the service is the whole
    # deterministic core and `POST /turns` answers 503. Nothing else changes,
    # which is what keeps S1's surface runnable with no model at all.
    app.state.owns_transport = transport is None
    app.state.transport = transport or (
        OpenRouterTransport(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
        )
        if settings.llm_api_key
        else None
    )

    # SPEC §11: metrics are content-free by construction (`metrics.py` declares
    # every label value a metric may carry). Switching them off leaves
    # `/metrics` answering 404 rather than serving an empty document, so a
    # scrape target that is off looks off rather than looks healthy.
    app.state.metrics = MetricsRegistry() if settings.metrics_enabled else None
    if app.state.metrics is not None:
        from cashkit import __version__ as engine_version

        app.state.metrics.gauge(
            "cashkit_build_info",
            1.0,
            version=app.version,
            engine_version=str(engine_version),
            environment=settings.environment,
        )
    install_sentry(settings)

    app.add_middleware(ResponseInvariantMiddleware, enabled=settings.check_response_invariants)
    # Order matters: Starlette runs the last-added middleware outermost, so
    # RequestId runs first and the request log sees the id it minted. The log
    # line is emitted in a `finally`, so a 500 still produces one.
    app.add_middleware(RequestLogMiddleware, enabled=settings.request_log_enabled)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(ops_router.router)
    app.include_router(auth_router.router)
    app.include_router(me_router.router)
    app.include_router(books_router.router)
    app.include_router(book_read.router)
    app.include_router(book_edits.router)
    app.include_router(scenarios_router.router)
    app.include_router(turns_router.router)
    app.include_router(export_router.router)
    app.include_router(imports_router.router)
    return app


app = create_app()
