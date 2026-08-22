"""UI-origin proposals and the confirmation endpoint (SPEC §3, §2.5).

The whole write surface of the service is here, and all of it is two steps:
``POST /book/edits`` produces a proposal, ``POST /proposals/{id}`` applies one.
No third path exists. That is ADR-0029 and SPEC §5-F2 as code rather than as a
convention — see ``trials/t13_no_unproposed_mutation.py``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, status as http_status
from pydantic import BaseModel, Field

from .. import proposals as proposal_store
from ..deps import BookDep, ClockDep, ConnDep, SettingsDep
from ..envelope import Envelope, envelope
from ..errors import bad_request, conflict, not_found
from ..ops.dryrun import dry_run
from ..ops.schema import HOST_OPS, PROPOSABLE_OPS, MutationOp
from ..proposals import ProposalOut, ProposalResponse, Stamp, _row_to_out
from ..reads import read_context

router = APIRouter(tags=["proposals"])

Origin = Literal["turn", "cell_edit", "onboarding", "import", "settings", "button"]


class EditsRequest(BaseModel):
    """``POST /book/edits`` — UI-origin proposals, no model call.

    ``context`` carries the record-actual channel marker. It is the same field
    ``POST /turns`` carries, and it feeds the same discriminator, so the rule of
    SPEC §5-F5 has exactly one implementation.
    """

    ops: list[MutationOp] = Field(min_length=1)
    origin: Origin = "cell_edit"
    scenario: str | None = None
    context: Literal["actuals_record"] | None = None


@router.post("/book/edits", status_code=http_status.HTTP_201_CREATED)
async def create_edit_proposal(
    body: EditsRequest,
    request: Request,
    book: BookDep,
    clock: ClockDep,
    conn: ConnDep,
    settings: SettingsDep,
) -> ProposalResponse:
    operations = [op.model_dump(mode="json") for op in body.ops]
    unproposable = sorted({o["op"] for o in operations} - PROPOSABLE_OPS)
    if unproposable:
        raise bad_request(
            "NOT_PROPOSABLE",
            f"{', '.join(unproposable)} is not a change to the working overlay.",
        )

    async with read_context(request, book, clock, body.scenario) as ctx:
        proposal_id, result = await proposal_store.create(
            conn,
            kit=ctx.kit,
            book_id=book.id,
            origin=body.origin,
            scenario=ctx.scenario,
            operations=operations,
            as_of=ctx.as_of,
            clock=clock,
            settings=settings,
            context=body.context,
        )
        env = ctx.envelope(pending=True)

    if proposal_id is None:
        # A clarification is not a pending change; nothing was stored.
        return ProposalResponse(
            **env.model_dump(), kind="clarification", clarification=result.clarification
        )

    row = await proposal_store.load(conn, proposal_id)
    return ProposalResponse(**env.model_dump(), kind="proposal", proposal=_row_to_out(row))


class ProposalAction(BaseModel):
    action: Literal["accept", "discard"]


class AcceptResponse(Envelope):
    """The answer to a confirmation.

    ``applied`` when the change landed, ``refreshed`` when the ground had moved
    and the service re-ran the dry-run — the old card is superseded and the new
    one needs confirming again (SPEC §2.5). ``discarded`` speaks for itself.
    """

    kind: Literal["applied", "refreshed", "discarded"]
    proposal: ProposalOut
    superseded: list[str] = []
    diagnostics: list = []


@router.post("/proposals/{proposal_id}")
async def resolve_proposal(
    proposal_id: uuid.UUID,
    body: ProposalAction,
    request: Request,
    book: BookDep,
    clock: ClockDep,
    conn: ConnDep,
    settings: SettingsDep,
) -> AcceptResponse:
    """ADR-0029's confirmation step.

    Accept re-checks the §2.5 staleness fingerprint first. On a mismatch it does
    NOT apply: it re-runs the dry-run against the book as it is now and hands
    back a refreshed proposal. Applying blind is the failure this check exists
    to prevent.
    """
    from ..serialize import diagnostics_out

    row = await proposal_store.load(conn, proposal_id)
    if row is None or row.book_id != book.id:
        raise not_found("NO_PROPOSAL", "No such proposal.")
    if row.status != "pending":
        raise conflict(
            "PROPOSAL_RESOLVED",
            f"This proposal is already {row.status}.",
            proposal_status=row.status,
        )
    if row.expires_at <= clock.now():
        # The refusal aborts this request, which rolls its transaction back, so
        # the expiry is recorded on its own connection. A proposal that timed
        # out must stay timed out.
        async with request.app.state.db.connect() as own:
            await proposal_store.mark(own, row.id, "expired", clock=clock)
        raise conflict("PROPOSAL_EXPIRED", "This proposal expired. Make the change again.")

    async with read_context(request, book, clock, row.scenario) as ctx:
        if body.action == "discard":
            await proposal_store.mark(conn, row.id, "discarded", clock=clock)
            refreshed = await proposal_store.load(conn, row.id)
            return AcceptResponse(
                **ctx.envelope().model_dump(), kind="discarded", proposal=_row_to_out(refreshed)
            )

        stored = Stamp(revision=row.base_revision, fingerprint=row.overlay_fingerprint)
        current = Stamp.of(ctx.kit)
        if not stored.matches(current):
            await proposal_store.mark(conn, row.id, "superseded", clock=clock)
            new_id, result = await proposal_store.create(
                conn,
                kit=ctx.kit,
                book_id=book.id,
                origin=row.origin,
                scenario=row.scenario,
                operations=list(row.ops),
                as_of=ctx.as_of,
                clock=clock,
                settings=settings,
                context=row.context,
                turn_id=row.turn_id,
                supersedes=row.id,
            )
            if new_id is None:
                raise conflict("PROPOSAL_STALE", result.clarification or "The book moved on.")
            fresh = await proposal_store.load(conn, new_id)
            return AcceptResponse(
                **ctx.envelope(pending=True).model_dump(),
                kind="refreshed",
                proposal=_row_to_out(fresh),
                diagnostics=[d.model_dump() for d in _row_to_out(fresh).diagnostics],
            )

        # The stamp matched, so this is the card that was confirmed. Before
        # touching the real book, run the operations once more on a throwaway
        # copy. If they fail there they would fail here, and a half-applied
        # change is the one outcome a confirmation must never produce — several
        # SDK verbs persist as they go, so "try and see" would leave the book
        # holding part of a change nobody accepted.
        rehearsal = dry_run(
            ctx.kit, list(row.ops), scenario=row.scenario, as_of=ctx.as_of, context=row.context
        )
        if not rehearsal.ok:
            raise conflict(
                "APPLY_REFUSED",
                "; ".join(d.message for d in rehearsal.diagnostics if d.severity == "error")
                or "This change cannot be applied.",
                diagnostics=[d.model_dump() for d in rehearsal.diagnostics],
            )

        applied = _apply(ctx.kit, row, as_of=ctx.as_of)
        errors = [d for d in applied if d.severity == "error"]
        if errors:  # pragma: no cover — the rehearsal above just succeeded
            raise conflict(
                "APPLY_FAILED",
                "; ".join(d.message for d in errors),
                diagnostics=[d.model_dump() for d in applied],
            )
        ctx.kit.save()
        await proposal_store.mark(conn, row.id, "accepted", clock=clock)
        superseded = await proposal_store.supersede_pending(
            conn, book_id=book.id, clock=clock, keep=row.id
        )
        state = ctx.kit.status()
        env = envelope(
            as_of=ctx.as_of,
            scenario=row.scenario,
            revision=state.revision,
            clean=state.clean,
            request_id=ctx.request_id,
        )
        resolved = await proposal_store.load(conn, row.id)
        return AcceptResponse(
            **env.model_dump(),
            kind="applied",
            proposal=_row_to_out(resolved),
            superseded=[str(p) for p in superseded],
            diagnostics=diagnostics_out(applied),
        )


def _apply(kit, row, *, as_of) -> list:
    """Apply a confirmed proposal's operations to the working overlay."""
    from ..ops.applier import apply_op

    diagnostics: list = []
    for index, operation in enumerate(row.ops):
        result = apply_op(
            kit, operation, scenario=row.scenario, as_of=as_of, context=row.context, seq=index
        )
        diagnostics.extend(result.diagnostics)
        if not result.ok:
            break
    return diagnostics


# --- save and discard (M9 / revert) --------------------------------------- #


class SaveRequest(BaseModel):
    message: str = Field(min_length=1)


class SaveResponse(Envelope):
    committed: bool
    superseded: list[str] = []
    diagnostics: list = []


@router.post("/book/save")
async def save_book(
    body: SaveRequest,
    request: Request,
    book: BookDep,
    clock: ClockDep,
    conn: ConnDep,
) -> SaveResponse:
    """M9 — commit the working overlay (SPEC §2.4).

    Committing is not a change to the overlay, so it needs no proposal of its
    own: it records changes the user already confirmed one card at a time. It
    does move the revision, so every pending card is superseded (SPEC §2.5).
    """
    from ..serialize import diagnostics_out

    async with read_context(request, book, clock) as ctx:
        report = ctx.kit.commit(body.message, author="user")
        state = ctx.kit.status()
        env = envelope(
            as_of=ctx.as_of,
            scenario=ctx.scenario,
            revision=state.revision,
            clean=state.clean,
            request_id=ctx.request_id,
        )
    superseded = await proposal_store.supersede_pending(conn, book_id=book.id, clock=clock)
    return SaveResponse(
        **env.model_dump(),
        committed=report.revision is not None,
        superseded=[str(p) for p in superseded],
        diagnostics=diagnostics_out(report.diagnostics),
    )


class DiscardResponse(Envelope):
    discarded: bool
    superseded: list[str] = []
    diagnostics: list = []


@router.post("/book/discard")
async def discard_working_overlay(
    request: Request, book: BookDep, clock: ClockDep, conn: ConnDep
) -> DiscardResponse:
    """Reload the working overlay from HEAD (SPEC §2.4).

    The ledger is untouched: it is append-only and shared by every scenario, so
    "discard my uncommitted plan changes" never un-records something that
    happened (ADR-0012).
    """
    from ..serialize import diagnostics_out

    runtime = request.app.state.books
    async with runtime.acquire(book.id, book.storage_path) as kit:
        report = kit.discard()
        kit.save()
        state = kit.status()
        env = envelope(
            as_of=clock.today(),
            scenario=book.active_scenario,
            revision=state.revision,
            clean=state.clean,
            request_id=getattr(request.state, "request_id", ""),
        )
    superseded = await proposal_store.supersede_pending(conn, book_id=book.id, clock=clock)
    return DiscardResponse(
        **env.model_dump(),
        discarded=True,
        superseded=[str(p) for p in superseded],
        diagnostics=diagnostics_out(report.diagnostics),
    )
