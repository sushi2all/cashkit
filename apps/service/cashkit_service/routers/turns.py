"""``POST /turns`` — the chat turn (SPEC §3, §2.3).

The endpoint is deliberately thin. It authenticates, hands the sentence to the
pipeline, and shapes the answer; every rule that matters lives in
:mod:`cashkit_service.agent`. In particular this module contains no branch that
applies anything: a turn reaches the book only through ``proposals.create()``,
the same call ``POST /book/edits`` makes, and applying stays with
``POST /proposals/{id}`` (ADR-0029).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..agent.pipeline import TurnResult, run_turn
from ..agent.tools import Receipt
from ..agent.transport import ModelUnavailable, Transport
from ..deps import BookDep, ClockDep, ConnDep, DatabaseDep, PrincipalDep, SettingsDep
from ..envelope import Envelope, envelope
from ..errors import ServiceError
from ..proposals import ProposalOut, _row_to_out, load as load_proposal
from ..serialize import DiagnosticOut, diagnostics_out

router = APIRouter(tags=["turns"])


class TurnRequest(BaseModel):
    """``POST /turns {text, scenario?, context?}``.

    ``context: "actuals_record"`` marks the record-actual channel (SPEC §5-F5).
    It is set by the interface on the Actuals record flow and passed straight
    through to the discriminator, which the model never influences.
    """

    text: str = Field(min_length=1, max_length=8000)
    scenario: str | None = None
    context: Literal["actuals_record"] | None = None


class TurnResponse(Envelope):
    """``{kind, reply, receipts[], proposal?}`` plus the SPEC §11 chain.

    ``kind`` has four values, not three: SPEC §3 lists ``answer``,
    ``proposal`` and ``clarification``, and SPEC §8 requires a turn over the
    daily budget to "refuse politely with a retry-tomorrow message". That
    refusal is a turn outcome the user reads as a sentence, not an
    infrastructure error, so it is a fourth kind rather than a status code
    (D-MLP-24).
    """

    turn_id: str
    kind: Literal["answer", "proposal", "clarification", "refusal"]
    reply: str
    receipts: list[Receipt] = []
    proposal: ProposalOut | None = None
    clarification: str | None = None
    diagnostics: list[DiagnosticOut] = []
    #: Present on a refusal only: when the user may try again.
    retry_after_seconds: int | None = None
    #: SPEC §11: how many model calls this turn made. The rows are in
    #: ``llm_calls``, keyed by this turn id.
    llm_calls: int = 0


@router.post("/turns")
async def create_turn(
    body: TurnRequest,
    request: Request,
    principal: PrincipalDep,
    book: BookDep,
    clock: ClockDep,
    conn: ConnDep,
    database: DatabaseDep,
    settings: SettingsDep,
) -> TurnResponse:
    transport: Transport | None = getattr(request.app.state, "transport", None)
    if transport is None:
        raise ServiceError(
            503,
            "MODEL_UNCONFIGURED",
            "The assistant is not configured on this service.",
        )

    try:
        result = await run_turn(
            request,
            book=book,
            user_id=principal.user_id,
            conn=conn,
            database=database,
            clock=clock,
            settings=settings,
            transport=transport,
            text=body.text,
            scenario=body.scenario,
            context=body.context,
        )
    except ModelUnavailable as exc:
        raise ServiceError(
            502,
            "MODEL_UNAVAILABLE",
            "The assistant could not be reached. Try again in a moment.",
            detail_text=str(exc)[:400],
        ) from exc

    proposal = None
    if result.proposal_id is not None:
        row = await load_proposal(conn, result.proposal_id)
        proposal = None if row is None else _row_to_out(row)

    return TurnResponse(
        **_envelope(result).model_dump(),
        turn_id=str(result.turn_id) if result.turn_id else "",
        kind=result.kind,  # type: ignore[arg-type]
        reply=result.reply,
        receipts=result.receipts,
        proposal=proposal,
        clarification=result.clarification,
        diagnostics=diagnostics_out(result.diagnostics),
        retry_after_seconds=result.retry_after_seconds,
        llm_calls=result.llm_calls,
    )


def _envelope(result: TurnResult) -> Envelope:
    """The SPEC §2.4 stamp for a turn.

    A proposal's figures are a dry-run including pending changes, so the turn
    that carries one is stamped ``pending``. An R1 hypothetical is computed on
    a throwaway overlay, so a turn quoting one is stamped ``overlay``. Both are
    "any figure NOT from the committed state of base", and both are stamped.
    """
    return envelope(
        as_of=result.as_of,
        scenario=result.scenario,
        revision=result.revision,
        clean=result.clean,
        request_id=result.request_id,
        pending=result.kind == "proposal",
        overlay=result.hypothetical,
    )
