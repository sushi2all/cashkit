"""``POST /import`` and ``GET /imports/{id}/stream`` (SPEC §3, §7).

The endpoint is thin, like ``POST /turns`` and for the same reason: everything
that matters is in :mod:`cashkit_service.imports`. In particular **this module
contains no branch that applies anything**. An import produces one proposal and
a reconciliation report; the book changes when the user posts
``POST /proposals/{id}``, and nowhere else (ADR-0029, SPEC §7.4).

Two decisions visible here:

* **The target is decided before the first model call**, under the book lock,
  so the answer to `POST /import` already says whether this is going into base
  or into a fresh fork (SPEC §7.3). The user learns where their file is going
  before it costs anything.
* **A SPEC §8 limit is a sentence on a 200**, not a status code — the same
  shape a refused turn takes (D-MLP-24). Being over today's five imports is
  something the user reads, not an infrastructure error.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..agent import budget
from ..agent.transport import Transport
from ..deps import BookDep, ClockDep, ConnDep, DatabaseDep, PrincipalDep, SettingsDep
from ..db import import_jobs
from ..errors import ServiceError, bad_request, not_found
from ..imports.jobs import ImportRegistry
from ..imports.loop import decide_target
from ..imports.runner import ImportDone, ImportStarted, run_job, target_payload
from ..imports.sheets import UnreadableWorkbook, parse
from ..reads import read_context

router = APIRouter(tags=["import"])

STREAM_PATH = "/imports/{job_id}/stream"


def _registry(request: Request) -> ImportRegistry:
    return request.app.state.imports


@router.post("/import")
async def start_import(
    request: Request,
    principal: PrincipalDep,
    book: BookDep,
    clock: ClockDep,
    conn: ConnDep,
    database: DatabaseDep,
    settings: SettingsDep,
    file: UploadFile = File(..., description="An .xlsx workbook."),
) -> ImportStarted:
    """Start an import. It applies nothing; it produces a card and a report."""
    transport: Transport | None = getattr(request.app.state, "transport", None)
    if transport is None:
        raise ServiceError(
            503, "MODEL_UNCONFIGURED", "The assistant is not configured on this service."
        )

    data = await file.read()
    if not data:
        raise bad_request("EMPTY_UPLOAD", "That file is empty.")
    if len(data) > settings.import_max_bytes:
        raise bad_request(
            "UPLOAD_TOO_LARGE",
            f"That file is larger than {settings.import_max_bytes // (1024 * 1024)} MB.",
        )
    try:
        parse(data)
    except UnreadableWorkbook as exc:
        raise bad_request(
            "UNREADABLE_WORKBOOK",
            "That file could not be read as a spreadsheet. Save it as .xlsx and try again.",
            detail_text=str(exc)[:300],
        ) from exc

    filename = file.filename or "import.xlsx"

    # SPEC §7.3, decided once, under the lock, before anything is spent.
    async with read_context(request, book, clock) as ctx:
        target = decide_target(ctx.kit, filename)

    refusal = await budget.check_import(
        conn, book_id=book.id, clock=clock, settings=settings
    ) or await budget.check_turn(
        conn, user_id=principal.user_id, clock=clock, settings=settings
    )
    if refusal is not None:
        return ImportStarted(
            kind="refusal",
            job_id="",
            status="refused",
            stream="",
            target=target_payload(target),
            call_cap=settings.import_max_llm_calls,
            reply=refusal.reply,
            retry_after_seconds=refusal.retry_after_seconds,
        )

    registry = _registry(request)
    job = registry.create(book_id=book.id, user_id=principal.user_id, filename=filename)
    # On its own connection, so the row is committed before this request
    # answers: the next request's SPEC §8 rate check has to see it, and the
    # background task must not depend on a transaction that could still roll
    # back (the journal's precedent, D-MLP-29).
    async with database.connect() as own:
        await own.execute(
            import_jobs.insert().values(
                id=job.id, book_id=book.id, status="running", report=None,
                created_at=clock.now(),
            )
        )

    job.task = asyncio.create_task(
        run_job(
            job=job,
            books=request.app.state.books,
            storage_path=book.storage_path,
            database=database,
            clock=clock,
            settings=settings,
            transport=transport,
            request_id=getattr(request.state, "request_id", ""),
            data=data,
            target=target,
        )
    )
    return ImportStarted(
        job_id=str(job.id),
        status="running",
        stream=STREAM_PATH.format(job_id=job.id),
        target=target_payload(target),
        call_cap=settings.import_max_llm_calls,
    )


@router.get(
    STREAM_PATH,
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Progress as it happens: stage, section, and every reconciliation "
                "check passing or failing, ending with the `done` event whose data "
                "is an ImportDone."
            ),
        }
    },
)
async def stream_import(
    job_id: uuid.UUID, request: Request, principal: PrincipalDep
) -> StreamingResponse:
    """Server-sent progress for one import (SPEC §3, §6-S14).

    Everything already emitted is replayed before the stream waits, so a
    listener that arrives late — or reconnects — sees the whole run.
    """
    registry = _registry(request)
    job = registry.get(job_id)
    if job is None or job.user_id != principal.user_id:
        raise not_found("NO_IMPORT", "No such import.")
    return StreamingResponse(
        registry.stream(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/imports/{job_id}")
async def read_import(
    job_id: uuid.UUID, request: Request, principal: PrincipalDep
) -> ImportDone:
    """The terminal payload, for a client that cannot read a stream.

    The stream is the primary surface and it replays, so this adds no
    capability the stream lacks; what it adds is a plain JSON reading of the
    same payload for a platform without streaming ``fetch`` (D-MLP-77). It is
    a read: it starts nothing and it changes nothing.
    """
    registry = _registry(request)
    job = registry.get(job_id)
    if job is None or job.user_id != principal.user_id:
        raise not_found("NO_IMPORT", "No such import.")
    terminal = next(
        (e for e in reversed(job.events) if e.get("stage") in ("done", "failed")), None
    )
    if terminal is None:
        raise ServiceError(
            409, "IMPORT_RUNNING", "That import is still running. Read the stream."
        )
    if terminal.get("stage") == "failed":
        raise ServiceError(
            409,
            "IMPORT_FAILED",
            str(terminal.get("error") or "That import did not finish."),
        )
    return ImportDone.model_validate(terminal)
