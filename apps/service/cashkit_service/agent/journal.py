"""Turn and model-call persistence, and the SPEC §11 correlation chain.

SPEC §11 wants one chain — ``request_id → turn_id → llm_calls.seq →
proposal_id`` — in every log line and payload envelope, so one user report is
traceable end to end. This module owns the two tables that carry it and the
structured log line that repeats it.

**Why a second connection.** The turn journal is written on its own database
connection, not the request's. A turn that ends in a 502 because the provider
was down is exactly the turn an operator most wants to see, and a row written
on the request connection would roll back with the request. The journal is an
observability record; it must outlive the failure it records.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from ..clock import Clock
from ..db import Database, llm_calls, turns
from .transport import Completion

logger = logging.getLogger("cashkit.turn")


def log_chain(
    event: str,
    *,
    request_id: str,
    turn_id: uuid.UUID | str | None = None,
    seq: int | None = None,
    proposal_id: uuid.UUID | str | None = None,
    **fields: Any,
) -> None:
    """Emit one structured line carrying the whole SPEC §11 chain.

    Every link is present on every line, ``None`` included, so a log search on
    a request id finds the turn, its model calls and the proposal they produced
    without needing to know which line type carries which field.
    """
    logger.info(
        json.dumps(
            {
                "event": event,
                "request_id": request_id,
                "turn_id": str(turn_id) if turn_id else None,
                "llm_call_seq": seq,
                "proposal_id": str(proposal_id) if proposal_id else None,
                **fields,
            },
            default=str,
        )
    )


@dataclass
class TurnJournal:
    """The open record of one turn.

    Created before the first model call so every ``llm_calls`` row has a turn
    to point at, and closed once with the aggregates SPEC §4 asks for.
    """

    database: Database
    clock: Clock
    turn_id: uuid.UUID
    request_id: str
    model: str
    seq: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: Decimal = field(default_factory=lambda: Decimal("0"))
    latency_ms: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    async def open(
        cls,
        database: Database,
        *,
        clock: Clock,
        user_id: uuid.UUID,
        book_id: uuid.UUID,
        request_id: str,
        text: str,
        context: str | None,
        model: str,
    ) -> "TurnJournal":
        turn_id = uuid.uuid4()
        async with database.connect() as conn:
            await conn.execute(
                turns.insert().values(
                    id=turn_id,
                    user_id=user_id,
                    book_id=book_id,
                    request_id=request_id,
                    input_text=text,
                    context=context,
                    model=model,
                    outcome="running",
                    created_at=clock.now(),
                )
            )
        log_chain("turn.open", request_id=request_id, turn_id=turn_id, context=context)
        return cls(
            database=database,
            clock=clock,
            turn_id=turn_id,
            request_id=request_id,
            model=model,
        )

    async def record(self, purpose: str, completion: Completion) -> int:
        """Write one ``llm_calls`` row. One model call, one row (SPEC §11)."""
        seq = self.seq
        self.seq += 1
        # Record what actually answered: OpenRouter reports the served model,
        # which is not always the string that was asked for.
        if completion.model:
            self.model = completion.model
        self.prompt_tokens += completion.prompt_tokens or 0
        self.completion_tokens += completion.completion_tokens or 0
        self.cost += completion.cost or Decimal("0")
        self.latency_ms += completion.latency_ms
        async with self.database.connect() as conn:
            await conn.execute(
                llm_calls.insert().values(
                    id=uuid.uuid4(),
                    turn_id=self.turn_id,
                    seq=seq,
                    purpose=purpose,
                    # §4/§9: the raw payloads carry user financial data and
                    # purge after 30 days; the numeric columns survive.
                    request=_jsonable(completion.request),
                    response=_jsonable(completion.response),
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                    cost=completion.cost,
                    latency_ms=completion.latency_ms,
                    error=completion.error,
                    created_at=self.clock.now(),
                )
            )
        self.calls.append(
            {"seq": seq, "purpose": purpose, "repaired": completion.repaired,
             "error": completion.error}
        )
        log_chain(
            "llm.call",
            request_id=self.request_id,
            turn_id=self.turn_id,
            seq=seq,
            purpose=purpose,
            model=completion.model,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            cost=str(completion.cost) if completion.cost is not None else None,
            latency_ms=completion.latency_ms,
            repaired=completion.repaired,
            error=completion.error,
        )
        return seq

    async def close(
        self,
        *,
        kind: str,
        outcome: str,
        intents: list[dict[str, Any]] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
        proposal_id: uuid.UUID | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Write the SPEC §4 per-turn aggregates and close the record."""
        async with self.database.connect() as conn:
            await conn.execute(
                turns.update()
                .where(turns.c.id == self.turn_id)
                .values(
                    kind=kind,
                    outcome=outcome,
                    intents=_jsonable(intents or []),
                    diagnostics=_jsonable(diagnostics or []),
                    model=self.model,
                    prompt_tokens=self.prompt_tokens,
                    completion_tokens=self.completion_tokens,
                    cost=self.cost,
                    latency_ms=latency_ms if latency_ms is not None else self.latency_ms,
                )
            )
        log_chain(
            "turn.close",
            request_id=self.request_id,
            turn_id=self.turn_id,
            proposal_id=proposal_id,
            kind=kind,
            outcome=outcome,
            calls=self.seq,
            cost=str(self.cost),
            latency_ms=latency_ms if latency_ms is not None else self.latency_ms,
        )


async def record_refusal(
    conn: AsyncConnection,
    *,
    clock: Clock,
    user_id: uuid.UUID,
    book_id: uuid.UUID,
    request_id: str,
    text: str,
    context: str | None,
    outcome: str,
) -> uuid.UUID:
    """Record a turn that never reached the model (SPEC §8 guardrails).

    It is written on the request connection because the request succeeds: a
    guardrail refusal is a normal turn outcome with a normal 200 response, not
    a failure to survive.
    """
    turn_id = uuid.uuid4()
    await conn.execute(
        turns.insert().values(
            id=turn_id,
            user_id=user_id,
            book_id=book_id,
            request_id=request_id,
            input_text=text,
            context=context,
            kind="refusal",
            outcome=outcome,
            intents=[],
            diagnostics=[],
            prompt_tokens=0,
            completion_tokens=0,
            cost=Decimal("0"),
            latency_ms=0,
            created_at=clock.now(),
        )
    )
    log_chain("turn.refused", request_id=request_id, turn_id=turn_id, outcome=outcome)
    return turn_id


def _jsonable(value: Any) -> Any:
    """Make a payload safe for ``jsonb``: no Decimal, no date, no set."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (Decimal, _dt.date, _dt.datetime, uuid.UUID)):
        return str(value)
    return value


__all__ = ["TurnJournal", "log_chain", "record_refusal"]
