"""Running one import as a background task, and the payloads it produces.

`POST /import` answers as soon as the job exists; the work outlives the
request, so it needs its own database connection and its own error handling.
Everything a listener ever sees is emitted through the job — including the
failure cases, because an import that dies silently is worse than one that
fails loudly.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from ..agent.journal import TurnJournal, log_chain
from ..agent.transport import ModelUnavailable, Transport
from ..books import BASE_SCENARIO, BookRuntime
from ..clock import Clock
from ..config import Settings
from ..db import Database, import_jobs
from ..envelope import Envelope, envelope
from ..proposals import ProposalOut, _row_to_out, load as load_proposal
from ..serialize import DiagnosticOut
from .checks import ReconciliationReport
from .jobs import ImportJob
from .loop import ImportLoop, Target
from .sheets import UnreadableWorkbook


class ImportTarget(BaseModel):
    """Where an import lands, and why (SPEC §7.3)."""

    scenario: str
    reason: str
    created_fork: bool
    message: str


class ImportStarted(BaseModel):
    """The answer to ``POST /import``.

    It carries no computed figure — the job has not run yet — so it carries no
    provenance envelope either. The figures arrive on the stream, stamped.
    """

    kind: str = "started"
    job_id: str
    status: str
    stream: str
    target: ImportTarget
    call_cap: int
    #: Present instead of a job when a SPEC §8 limit stopped the import. It is
    #: a sentence the user reads on a 200, exactly as a turn refusal is
    #: (D-MLP-24), not a status code.
    reply: str = ""
    retry_after_seconds: int | None = None


class ImportDone(Envelope):
    """The terminal payload: the report, the one card, and what went wrong.

    The figures in the report are dry-run figures for the target scenario, so
    the envelope's ``what_if`` is stamped — SPEC §2.4 admits no exception for a
    number that came out of an import.
    """

    job_id: str
    status: str
    kind: str = "done"
    report: ReconciliationReport
    proposal: ProposalOut | None = None
    diagnostics: list[DiagnosticOut] = Field(default_factory=list)
    error: str | None = None


def target_payload(target: Target) -> ImportTarget:
    return ImportTarget(
        scenario=target.scenario,
        reason=target.reason,
        created_fork=target.created_fork,
        message=(
            f"This book already has a plan, so the import goes into a new scenario "
            f"called {target.scenario!r}. Base is left exactly as it is."
            if target.created_fork
            else "This book is empty, so the import goes into the plan itself."
        ),
    )


async def run_job(
    *,
    job: ImportJob,
    books: BookRuntime,
    storage_path: str,
    database: Database,
    clock: Clock,
    settings: Settings,
    transport: Transport,
    request_id: str,
    data: bytes,
    target: Target,
) -> None:
    """Run one import to completion and emit its terminal event.

    Nothing raises out of here: the task has no caller to catch it, so every
    failure becomes an emitted ``failed`` event and a stored job row.
    """

    def kit_lock():
        return books.acquire(job.book_id, storage_path)

    journal = await TurnJournal.open(
        database,
        clock=clock,
        user_id=job.user_id,
        book_id=job.book_id,
        request_id=request_id,
        text=f"import {job.filename}",
        context="import",
        model=transport.model,
    )

    status = "failed"
    payload: dict[str, Any]
    try:
        loop = ImportLoop(
            kit_lock=kit_lock,
            database=database,
            book_id=job.book_id,
            clock=clock,
            settings=settings,
            transport=transport,
            journal=journal,
            emit=job.emit,
            filename=job.filename,
            data=data,
            request_id=request_id,
            target=target,
        )
        outcome = await loop.run()
        proposal = None
        if outcome.proposal_id is not None:
            async with database.connect() as conn:
                row = await load_proposal(conn, outcome.proposal_id)
                proposal = None if row is None else _row_to_out(row)
        async with kit_lock() as kit:
            state = kit.status()
            env = envelope(
                as_of=loop.as_of,
                scenario=BASE_SCENARIO,
                revision=state.revision,
                clean=state.clean,
                request_id=request_id,
                # Every figure in the report is a dry-run figure for a change
                # nobody has applied. SPEC §2.4 stamps it.
                pending=True,
            )
        status = outcome.status
        job.report = outcome.report
        job.proposal_id = outcome.proposal_id
        payload = ImportDone(
            **env.model_dump(),
            job_id=str(job.id),
            status=status,
            report=outcome.report,
            proposal=proposal,
            diagnostics=outcome.diagnostics,
        ).model_dump(mode="json")
        payload["stage"] = "done"
    except UnreadableWorkbook as exc:
        payload = _failure(job, "That file could not be read as a spreadsheet.", str(exc))
    except ModelUnavailable as exc:
        payload = _failure(
            job, "The assistant could not be reached, so nothing was imported.", str(exc)
        )
    except Exception as exc:  # noqa: BLE001 — a background task has no caller
        payload = _failure(job, "The import stopped before it finished.", f"{type(exc).__name__}: {exc}")

    try:
        async with database.connect() as conn:
            await conn.execute(
                import_jobs.update()
                .where(import_jobs.c.id == job.id)
                .values(status=status, report=payload)
            )
    except Exception:  # noqa: BLE001 — the stream still gets the answer
        log_chain("import.row_unwritable", request_id=request_id, turn_id=journal.turn_id)

    await journal.close(
        kind="import",
        outcome=status,
        intents=[],
        diagnostics=list(payload.get("diagnostics") or []),
        proposal_id=job.proposal_id,
        latency_ms=journal.latency_ms,
    )
    job.emit(payload)
    job.finish(status)


def _failure(job: ImportJob, message: str, detail: str) -> dict[str, Any]:
    return {
        "stage": "failed",
        "kind": "failed",
        "job_id": str(job.id),
        "status": "failed",
        "error": message,
        "detail": detail[:600],
    }


__all__ = [
    "ImportDone",
    "ImportStarted",
    "ImportTarget",
    "run_job",
    "target_payload",
]
