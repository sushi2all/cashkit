"""Engine wrapper read endpoints (SPEC §3).

Every payload here carries the SPEC §3 envelope, and every money figure in it
is the engine's own number through the one canonical serializer. The parity
test in ``tests/test_sdk_parity.py`` compares these payloads against a direct
SDK call on the same book, revision and as_of.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..deps import BookDep, ClockDep
from ..envelope import Envelope
from ..money import Money, money
from ..reads import read_context
from ..serialize import (
    ItemSeries,
    SummaryOut,
    Warnings,
    closing_series,
    item_series,
    period_starts,
    summary_out,
    warnings_for,
)

router = APIRouter(tags=["book"])


class BookParams(BaseModel):
    id: str
    grain: str
    currency: str
    horizon_start: date
    horizon_end: date
    cutover: date
    opening_balance: Money
    params: dict[str, str]


class BookState(Envelope):
    """``GET /book/state`` — SPEC §3.

    Items, params, summary, months, per-item series, dirty flag, revision id,
    as_of, and server-computed ``warnings``.
    """

    dirty: bool
    active_scenario: str
    scenarios: list[str]
    book: BookParams
    months: list[date]
    closing: list[Money]
    items: list[ItemSeries]
    summary: SummaryOut
    warnings: Warnings
    diagnostics: list


@router.get("/book/state")
async def get_state(
    request: Request, book: BookDep, clock: ClockDep, scenario: str | None = None
) -> BookState:
    async with read_context(request, book, clock, scenario) as ctx:
        run = ctx.run()
        resolved = run.book
        from ..serialize import diagnostics_out

        return BookState(
            **ctx.envelope().model_dump(),
            dirty=not ctx.clean,
            active_scenario=book.active_scenario,
            scenarios=sorted(ctx.kit.scenarios.scenarios),
            book=BookParams(
                id=resolved.id,
                grain=resolved.base_grain.value,
                currency="EUR",
                horizon_start=resolved.horizon.start,
                horizon_end=resolved.horizon.end,
                cutover=resolved.cutover,
                opening_balance=money(resolved.opening_balance),
                params={k: str(v) for k, v in resolved.params.items()},
            ),
            months=period_starts(run),
            closing=[money(v) for v in closing_series(run)],
            items=item_series(run),
            summary=summary_out(run),
            warnings=warnings_for(run),
            diagnostics=diagnostics_out(run.diagnostics),
        )
