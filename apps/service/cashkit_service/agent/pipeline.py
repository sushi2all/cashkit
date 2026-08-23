"""The turn pipeline: interpret → guard → propose → verify (SPEC §2.3).

One function, :func:`run_turn`, and it is the whole model-facing behaviour of
the service. The order of its phases is the SPEC's, and the boundaries between
them are the invariants:

1. **Guardrails** (SPEC §8) — the spend and rate checks run before the first
   model call, so a refused turn costs nothing.
2. **Snapshot** — the compact book state plus the engine's own results, built
   while the book lock is held.
3. **Interpret** (one call) — the lock is *released* first. A model call never
   holds the book lock (SPEC §2.2).
4. **Guard** (ADR-0029, structural) — read operations and change operations are
   separated on the artifact. Change operations are never applied here, and the
   model can never name an operation the interface reserves.
5. **Reads** execute immediately and become receipts; a question that needs more
   than the snapshot enters the bounded Q&A loop, which is read-only.
6. **Changes** are dry-run and stored as a proposal — one card, one turn. At
   most one repair round from diagnostics, and one bounded verification call
   when a macro is involved.

Every phase that touches the book takes the lock; every phase that talks to the
model does not hold it. The two never overlap.
"""

from __future__ import annotations

import datetime as _dt
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from cashkit.model import Diagnostic
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import proposals as proposal_store
from ..clock import Clock
from ..config import Settings
from ..db import Database
from ..deps import BookRow
from ..ops.applier import CK_E902, app_diagnostic
from ..ops.dryrun import DryRun, dry_run
from ..reads import read_context
from . import budget, prompts, snapshot as snapshot_module, verify
from .guard import Guarded, guard
from .journal import TurnJournal, log_chain, record_refusal
from .tools import Receipt, execute_reads, receipts_for_model
from .transport import ModelUnavailable, Transport


@dataclass
class TurnResult:
    """Everything the endpoint needs to answer, and the journal to close."""

    kind: str
    reply: str
    scenario: str
    as_of: _dt.date
    revision: str | None
    clean: bool
    request_id: str
    turn_id: uuid.UUID | None = None
    receipts: list[Receipt] = field(default_factory=list)
    proposal_id: uuid.UUID | None = None
    clarification: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    retry_after_seconds: int | None = None
    llm_calls: int = 0
    #: True when a figure in the receipts came from a throwaway overlay — an
    #: R1 hypothetical. SPEC §2.4 stamps it, so the envelope must know.
    hypothetical: bool = False


async def run_turn(
    request: Request,
    *,
    book: BookRow,
    user_id: uuid.UUID,
    conn: AsyncConnection,
    database: Database,
    clock: Clock,
    settings: Settings,
    transport: Transport,
    text: str,
    scenario: str | None = None,
    context: str | None = None,
) -> TurnResult:
    """Run one turn. The only path from a sentence to a proposal."""
    started = time.monotonic()
    request_id = getattr(request.state, "request_id", "")

    # --- 1. guardrails, before any spend (SPEC §8) ------------------------ #
    refusal = await budget.check_turn(
        conn, user_id=user_id, clock=clock, settings=settings
    )
    if refusal is not None:
        turn_id = await record_refusal(
            conn,
            clock=clock,
            user_id=user_id,
            book_id=book.id,
            request_id=request_id,
            text=text,
            context=context,
            outcome=refusal.outcome,
        )
        async with read_context(request, book, clock, scenario) as ctx:
            return TurnResult(
                kind="refusal",
                reply=refusal.reply,
                scenario=ctx.scenario,
                as_of=ctx.as_of,
                revision=ctx.revision,
                clean=ctx.clean,
                request_id=request_id,
                turn_id=turn_id,
                retry_after_seconds=refusal.retry_after_seconds,
            )

    # --- 2. snapshot, under the lock -------------------------------------- #
    async with read_context(request, book, clock, scenario) as ctx:
        state = snapshot_module.build(ctx.kit, scenario=ctx.scenario, as_of=ctx.as_of)
        target, as_of = ctx.scenario, ctx.as_of
        revision, clean = ctx.revision, ctx.clean
    snapshot_json = snapshot_module.compact(state)

    journal = await TurnJournal.open(
        database,
        clock=clock,
        user_id=user_id,
        book_id=book.id,
        request_id=request_id,
        text=text,
        context=context,
        # The transport names what actually answers; the setting only names
        # what was asked for, and a provider may serve a variant of it.
        model=transport.model,
    )

    result = TurnResult(
        kind="answer",
        reply="",
        scenario=target,
        as_of=as_of,
        revision=revision,
        clean=clean,
        request_id=request_id,
        turn_id=journal.turn_id,
    )

    try:
        await _pipeline(
            request,
            book=book,
            conn=conn,
            clock=clock,
            settings=settings,
            transport=transport,
            journal=journal,
            snapshot_json=snapshot_json,
            text=text,
            context=context,
            scenario=scenario,
            result=result,
        )
    except ModelUnavailable as exc:
        await journal.close(
            kind="error",
            outcome="model_unavailable",
            diagnostics=[{"code": "MODEL_UNAVAILABLE", "message": str(exc)[:800]}],
            latency_ms=_elapsed_ms(started),
        )
        raise

    result.llm_calls = journal.seq
    await journal.close(
        kind=result.kind,
        outcome=_outcome(result),
        intents=[r.request for r in result.receipts]
        + ([] if result.proposal_id is None else await _stored_ops(conn, result.proposal_id)),
        diagnostics=[d.model_dump() for d in result.diagnostics],
        proposal_id=result.proposal_id,
        latency_ms=_elapsed_ms(started),
    )
    return result


async def _pipeline(
    request: Request,
    *,
    book: BookRow,
    conn: AsyncConnection,
    clock: Clock,
    settings: Settings,
    transport: Transport,
    journal: TurnJournal,
    snapshot_json: str,
    text: str,
    context: str | None,
    scenario: str | None,
    result: TurnResult,
) -> None:
    # --- 3. interpret: one call, no lock held ----------------------------- #
    messages = prompts.interpret_messages(snapshot_json, text)
    parsed = await ask_json(
        transport, journal, messages, purpose="interpret", settings=settings
    )
    result.reply = _reply_of(parsed)
    declared = parsed.get("kind")

    # --- 4. guard: structural, post-interpretation (ADR-0029) ------------- #
    guarded = guard(parsed.get("intents"))
    result.diagnostics.extend(guarded.diagnostics)
    log_chain(
        "turn.guarded",
        request_id=result.request_id,
        turn_id=journal.turn_id,
        reads=len(guarded.reads),
        mutations=len(guarded.mutations),
        deferred=len(guarded.deferred),
        dropped=len(guarded.diagnostics),
    )

    # --- 5. reads execute now; the Q&A loop stays read-only --------------- #
    if guarded.reads:
        await _read_phase(
            request,
            book=book,
            clock=clock,
            settings=settings,
            transport=transport,
            journal=journal,
            snapshot_json=snapshot_json,
            text=text,
            scenario=scenario,
            guarded=guarded,
            result=result,
        )

    # --- 6. changes are held as a proposal, never applied ----------------- #
    if guarded.mutations:
        await _change_phase(
            request,
            book=book,
            conn=conn,
            clock=clock,
            settings=settings,
            transport=transport,
            journal=journal,
            snapshot_json=snapshot_json,
            text=text,
            context=context,
            scenario=scenario,
            operations=list(guarded.mutations),
            result=result,
        )
        return

    # A turn with no changes is an answer, or the model's own question.
    if declared == "clarification" or (not guarded.reads and not result.reply):
        result.kind = "clarification"
        result.clarification = result.reply or "What would you like to know?"
        result.reply = result.clarification


async def _read_phase(
    request: Request,
    *,
    book: BookRow,
    clock: Clock,
    settings: Settings,
    transport: Transport,
    journal: TurnJournal,
    snapshot_json: str,
    text: str,
    scenario: str | None,
    guarded: Guarded,
    result: TurnResult,
) -> None:
    """Execute reads, then let the model quote them. Bounded, read-only."""
    pending = list(guarded.reads)
    conversation: list[dict[str, str]] = [
        {"role": "system", "content": prompts.qa_system(snapshot_json)},
        {"role": "user", "content": text},
    ]

    for _round in range(settings.llm_qa_max_calls):
        async with read_context(request, book, clock, scenario) as ctx:
            receipts, diagnostics = execute_reads(
                ctx.kit, pending, scenario=ctx.scenario, as_of=ctx.as_of
            )
        result.receipts.extend(receipts)
        result.diagnostics.extend(diagnostics)
        result.hypothetical = result.hypothetical or any(
            r.payload.get("hypothetical") for r in receipts
        )

        conversation.append(prompts.qa_results_message(receipts_for_model(receipts)))
        parsed = await ask_json(
            transport, journal, conversation, purpose="qa", settings=settings
        )
        reply = _reply_of(parsed)
        if reply:
            result.reply = reply
        if parsed.get("kind") == "clarification":
            result.kind = "clarification"
            result.clarification = result.reply
            return

        # The model may ask for more figures. Only read operations are honoured
        # here: this loop cannot write, whatever comes back (ADR-0029).
        follow_up = guard(parsed.get("intents"))
        result.diagnostics.extend(follow_up.diagnostics)
        if follow_up.mutations:
            # A change emitted during a question is held for the change phase,
            # exactly as one emitted during interpretation would be.
            guarded.mutations.extend(follow_up.mutations)
        if not follow_up.reads:
            return
        pending = follow_up.reads
        conversation.append({"role": "assistant", "content": _as_text(parsed)})


async def _change_phase(
    request: Request,
    *,
    book: BookRow,
    conn: AsyncConnection,
    clock: Clock,
    settings: Settings,
    transport: Transport,
    journal: TurnJournal,
    snapshot_json: str,
    text: str,
    context: str | None,
    scenario: str | None,
    operations: list[dict[str, Any]],
    result: TurnResult,
) -> None:
    """Dry-run, repair once, verify once, and store one card."""
    attempt = await _dry(
        request, book=book, clock=clock, operations=operations, context=context,
        scenario=scenario,
    )

    # One repair round from diagnostics (SPEC §2.3 step 4, proto TESTLOG item 3).
    if (
        not attempt.ok
        and attempt.clarification is None
        and settings.llm_diagnostic_repair_rounds > 0
    ):
        errors = [d.model_dump() for d in attempt.diagnostics if d.severity == "error"]
        if errors:
            repaired = await ask_json(
                transport,
                journal,
                [
                    *prompts.interpret_messages(snapshot_json, text),
                    {"role": "assistant", "content": _as_operations(operations)},
                    prompts.diagnostic_repair_message(errors, snapshot_json),
                ],
                purpose="repair",
                settings=settings,
            )
            fixed = guard(repaired.get("intents"))
            result.diagnostics.extend(fixed.diagnostics)
            if fixed.mutations:
                operations = fixed.mutations
                if _reply_of(repaired):
                    result.reply = _reply_of(repaired)
                attempt = await _dry(
                    request, book=book, clock=clock, operations=operations,
                    context=context, scenario=scenario,
                )

    # One bounded verification call, for the enumerated triggers only.
    if attempt.ok and verify.triggered(operations):
        operations = await _verify_phase(
            request,
            book=book,
            clock=clock,
            settings=settings,
            transport=transport,
            journal=journal,
            text=text,
            context=context,
            scenario=scenario,
            operations=operations,
            result=result,
        )

    # Store exactly one card. The service reaches the book only through
    # `proposals.create()`, the same call `POST /book/edits` makes.
    async with read_context(request, book, clock, scenario) as ctx:
        proposal_id, stored = await proposal_store.create(
            conn,
            kit=ctx.kit,
            book_id=book.id,
            origin="turn",
            scenario=ctx.scenario,
            operations=operations,
            as_of=ctx.as_of,
            clock=clock,
            settings=settings,
            context=context,
            turn_id=journal.turn_id,
        )
        result.revision, result.clean = ctx.revision, ctx.clean

    result.diagnostics.extend(_as_diagnostics(stored))
    if proposal_id is None:
        # SPEC §5-F5: an entry on the record-actual flow with no usable date is
        # a clarification, never a guess. Nothing is stored to confirm.
        result.kind = "clarification"
        result.clarification = stored.clarification
        result.reply = stored.clarification or result.reply
        return

    result.kind = "proposal"
    result.proposal_id = proposal_id
    log_chain(
        "turn.proposed",
        request_id=result.request_id,
        turn_id=journal.turn_id,
        proposal_id=proposal_id,
        operations=len(operations),
        ok=stored.ok,
    )


async def _verify_phase(
    request: Request,
    *,
    book: BookRow,
    clock: Clock,
    settings: Settings,
    transport: Transport,
    journal: TurnJournal,
    text: str,
    context: str | None,
    scenario: str | None,
    operations: list[dict[str, Any]],
    result: TurnResult,
) -> list[dict[str, Any]]:
    """One bounded call: instruction + operations + receipts in, verdict out."""
    async with read_context(request, book, clock, scenario) as ctx:
        receipts = verify.receipts(
            ctx.kit, operations, scenario=ctx.scenario, as_of=ctx.as_of, context=context
        )
    verdict = await ask_json(
        transport,
        journal,
        prompts.verify_messages(text, operations, receipts, prompts.CHANGE_GRAMMAR),
        purpose="verify",
        settings=settings,
    )
    if verdict.get("confirmed") is True:
        return operations
    corrective = guard(verdict.get("intents"))
    result.diagnostics.extend(corrective.diagnostics)
    if not corrective.mutations:
        return operations
    if _reply_of(verdict):
        result.reply = _reply_of(verdict)
    log_chain(
        "turn.verify_corrected",
        request_id=result.request_id,
        turn_id=journal.turn_id,
        before=len(operations),
        after=len(corrective.mutations),
    )
    return corrective.mutations


# --- helpers -------------------------------------------------------------- #


async def _dry(
    request: Request,
    *,
    book: BookRow,
    clock: Clock,
    operations: list[dict[str, Any]],
    context: str | None,
    scenario: str | None,
) -> DryRun:
    """Dry-run under the lock, so the repair and verify calls have evidence."""
    async with read_context(request, book, clock, scenario) as ctx:
        return dry_run(
            ctx.kit, operations, scenario=ctx.scenario, as_of=ctx.as_of, context=context
        )


async def ask_json(
    transport: Transport,
    journal: TurnJournal,
    messages: list[dict[str, str]],
    *,
    purpose: str,
    settings: Settings,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """One model call, retried on unparseable output, every attempt recorded.

    The retry is warmer on purpose: at temperature 0 the model re-emits the
    identical broken bytes, which the proto measured twice (T08, T09). Each
    attempt is its own ``llm_calls`` row — a retry is a model call, and SPEC §11
    counts it as one.
    """
    conversation = list(messages)
    temp = temperature
    attempts = max(1, settings.llm_json_retries + 1)
    last_error = "the model returned nothing usable"

    for attempt in range(attempts):
        completion = await transport.complete(
            conversation, temperature=temp, max_tokens=settings.llm_max_tokens
        )
        await journal.record(purpose if attempt == 0 else "repair", completion)
        if completion.ok and completion.parsed is not None:
            return completion.parsed
        last_error = completion.error or last_error
        temp = 0.7
        if completion.text:
            conversation = [
                *conversation,
                {"role": "assistant", "content": completion.text},
                prompts.json_repair_message(last_error),
            ]
    raise ModelUnavailable(last_error)


def _reply_of(parsed: dict[str, Any]) -> str:
    reply = parsed.get("reply")
    return reply.strip() if isinstance(reply, str) else ""


def _as_text(parsed: dict[str, Any]) -> str:
    import json

    return json.dumps(parsed, default=str)


def _as_operations(operations: list[dict[str, Any]]) -> str:
    import json

    return json.dumps({"reply": "", "intents": operations}, default=str)


def _as_diagnostics(stored: DryRun) -> list[Diagnostic]:
    """The stored dry-run's diagnostics, as engine objects, verbatim."""
    return [
        Diagnostic(
            severity=d.severity,
            code=d.code,
            message=d.message,
            suggested_fix=d.suggested_fix,
            item_id=d.item_id,
            field=d.field,
        )
        for d in stored.diagnostics
    ]


async def _stored_ops(conn: AsyncConnection, proposal_id: uuid.UUID) -> list[dict[str, Any]]:
    row = await proposal_store.load(conn, proposal_id)
    return list(row.ops) if row is not None else []


def _outcome(result: TurnResult) -> str:
    if result.kind == "proposal":
        return "proposed"
    if result.kind == "clarification":
        return "clarified"
    if any(d.severity == "error" for d in result.diagnostics):
        return "answered_with_diagnostics"
    return "answered"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def unparseable_diagnostic(error: str) -> Diagnostic:
    return app_diagnostic(CK_E902, f"The model's answer could not be read: {error}"[:400])


__all__ = ["TurnResult", "ask_json", "run_turn"]
