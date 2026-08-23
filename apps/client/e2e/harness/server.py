"""The web E2E harness: the real service, behind a scripted model provider.

What this process is, and what it deliberately is not:

* It runs the **real** ``cashkit_service`` app — the real routers, the real
  guard, the real dry-run, the real proposal store, a real Postgres and a real
  book on disk. Nothing in the request path is a stand-in.
* The **model provider** is scripted, and only the provider. This is the
  D-MLP-34 precedent: invariants have to hold against a model that misbehaves
  on cue, and a browser test cannot spend money or tolerate a flaky provider on
  every run. The live model is exercised by ``apps/service/trials`` on the
  pinned model, which is where model *behaviour* is measured.
* It serves the exported web app and forwards ``/api/*`` into the service
  in-process, so the browser talks to one origin. That mirrors the SPEC §12
  deployment (Caddy in front of the service) and means no CORS policy has to
  be invented for a test.
* The control routes live under ``/__control`` on **this** app, never on the
  service app. The service gains no route: reading a magic link or scripting a
  model answer is the harness's business, and ``POST /turns`` stays the only
  way into the turn pipeline. T13's route inventory keeps that honest.

Run it directly for a manual poke:

    uv run python apps/client/e2e/harness/server.py --port 8099
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVICE_ROOT = REPO_ROOT / "apps" / "service"
sys.path.insert(0, str(SERVICE_ROOT))
sys.path.insert(0, str(SERVICE_ROOT / "tests"))

from cashkit_service.app import create_app  # noqa: E402
from cashkit_service.books import BookRuntime  # noqa: E402
from cashkit_service.clock import FixedClock  # noqa: E402
from cashkit_service.config import Settings  # noqa: E402
from cashkit_service.db import Database  # noqa: E402
from cashkit_service.mail import CapturingMailer  # noqa: E402
from cashkit_service.migrate import apply_migrations  # noqa: E402
from fake_model import ScriptedTransport  # noqa: E402
from workbooks import export_like, messy_family_budget  # noqa: E402

#: The same frozen instant the service suites use, so a figure asserted in a
#: browser test matches a figure asserted in a Python test (D-MLP-12).
FROZEN_NOW = _dt.datetime(2026, 3, 17, 9, 30, tzinfo=_dt.timezone.utc)

ADMIN_URL = "postgresql+asyncpg://cashkit:cashkit@localhost:55432/cashkit"
DIST = REPO_ROOT / "apps" / "client" / "dist"


class ScriptRequest(BaseModel):
    """Queue what the scripted provider answers, in order."""

    responses: list[Any]
    replace: bool = True


async def build() -> tuple[FastAPI, dict[str, Any]]:
    name = f"ck_e2e_{uuid.uuid4().hex[:12]}"
    admin = Database(ADMIN_URL + "?prepared_statement_cache_size=0")
    engine = admin.engine.execution_options(isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()

    database = Database(f"postgresql+asyncpg://cashkit:cashkit@localhost:55432/{name}")
    await apply_migrations(database)

    books_root = Path(tempfile.mkdtemp(prefix="cashkit-e2e-books-"))
    settings = Settings(
        database_url="unused://",
        books_root=books_root,
        # The browser test drives the same response invariants the service
        # suites check, so leave the middleware on: a payload that carries a
        # money figure without its provenance fails the page, not just a test.
        check_response_invariants=True,
        web_app_url="http://127.0.0.1:8099",
    )
    mailer = CapturingMailer()
    transport = ScriptedTransport(script=[])
    clock = FixedClock(FROZEN_NOW)

    service = create_app(
        settings=settings,
        clock=clock,
        mailer=mailer,
        database=database,
        book_runtime=BookRuntime(books_root),
        transport=transport,
    )
    return service, {
        "database": database,
        "database_name": name,
        "books_root": books_root,
        "mailer": mailer,
        "transport": transport,
    }


def make_harness(service: FastAPI, ctx: dict[str, Any]) -> FastAPI:
    app = FastAPI(title="CashKit web E2E harness")
    mailer: CapturingMailer = ctx["mailer"]
    transport: ScriptedTransport = ctx["transport"]
    forwarder = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service), base_url="http://service"
    )

    # --- control surface (harness only; the service gains no route) -------- #

    @app.get("/__control/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "books_root": str(ctx["books_root"]), "database": ctx["database_name"]}

    @app.post("/__control/script")
    async def script(body: ScriptRequest) -> dict[str, int]:
        """Queue the provider's answers. Only the provider is replaced."""
        if body.replace:
            transport.script.clear()
        transport.script.extend(body.responses)
        return {"queued": len(transport.script)}

    @app.get("/__control/calls")
    async def calls() -> dict[str, int]:
        return {"calls": len(transport.calls)}

    @app.get("/__control/workbook")
    async def workbook(kind: str = "simple") -> Response:
        """The T16 workbooks, so the browser test uploads the same bytes the
        service suite imports (`apps/service/tests/workbooks.py`)."""
        if kind == "messy":
            data = messy_family_budget()
        elif kind == "decimals":
            # Sheet figures with cents in them, so the money invariant has
            # something money-shaped to look at that is NOT an engine figure.
            data = export_like(rows=[("Salary", "flow", [Decimal("2617.33")] * 12)])
        else:
            data = export_like()
        return Response(
            content=data,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

    @app.get("/__control/link")
    async def link(email: str) -> dict[str, str]:
        """The last magic link for an address.

        The service never returns a link token in any response, in any mode —
        a debug flag that did would defeat the whole flow. The harness reads it
        out of the test mailer, which is where the mail would have gone.
        """
        try:
            found = mailer.last_for(email)
        except AssertionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"url": found.url, "token": found.token, "email": found.email}

    # --- API forwarding ---------------------------------------------------- #

    EXCLUDED_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def forward(path: str, request: Request) -> Response:
        """One origin for the browser, exactly as the deployment has."""
        body = await request.body()
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length", "connection"}
        }

        if path.endswith("/stream"):
            # An import's progress is a stream, and buffering it here would
            # turn "watch it happen" into "wait, then see everything at once" —
            # which is the one behaviour the S14 screen exists to have. Caddy
            # passes SSE through in the deployment (SPEC §12); so does this.
            return await _forward_stream(request.method, path, body, headers)

        upstream = await forwarder.request(
            request.method,
            f"/{path}",
            content=body or None,
            headers=headers,
            params=dict(request.query_params),
        )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers={
                k: v for k, v in upstream.headers.items() if k.lower() not in EXCLUDED_HEADERS
            },
            media_type=upstream.headers.get("content-type"),
        )

    async def _forward_stream(
        method: str, path: str, body: bytes, headers: dict[str, str]
    ) -> Response:
        """Pass a server-sent-event stream through frame by frame."""
        opened = forwarder.stream(
            method, f"/{path}", content=body or None, headers=headers
        )
        upstream = await opened.__aenter__()

        async def frames():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await opened.__aexit__(None, None, None)

        return StreamingResponse(
            frames(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- the exported web app ---------------------------------------------- #

    if DIST.is_dir():
        app.mount("/_expo", StaticFiles(directory=DIST / "_expo"), name="expo")

        @app.get("/{path:path}")
        async def spa(path: str) -> Response:
            candidate = DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            index = DIST / "index.html"
            if not index.is_file():
                return JSONResponse({"detail": "web app not exported"}, status_code=503)
            return FileResponse(index)

    return app


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()

    service, ctx = await build()
    harness = make_harness(service, ctx)

    config = uvicorn.Config(harness, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    print(json.dumps({"ready": True, "port": args.port, "database": ctx["database_name"]}), flush=True)
    try:
        await server.serve()
    finally:
        service.state.books.close_all()
        await ctx["database"].dispose()
        shutil.rmtree(ctx["books_root"], ignore_errors=True)
        admin = Database(ADMIN_URL)
        engine = admin.engine.execution_options(isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": ctx["database_name"]},
            )
            await conn.execute(sa.text(f'DROP DATABASE "{ctx["database_name"]}"'))
        await admin.dispose()


if __name__ == "__main__":
    os.environ.setdefault("CASHKIT_DATABASE_URL", ADMIN_URL)
    asyncio.run(main())
