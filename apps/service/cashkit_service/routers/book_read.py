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
from ..errors import bad_request, not_found
from ..money import Money, money
from ..reads import read_context
from ..serialize import (
    EventOut,
    ExplanationOut,
    ItemSeries,
    ReconciliationOut,
    RevisionOut,
    SummaryOut,
    TraceOut,
    Warnings,
    closing_series,
    events_out,
    explanation_out,
    item_series,
    period_starts,
    reconciliation_out,
    revisions_out,
    summary_out,
    trace_out,
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


# --- forecast (F3) -------------------------------------------------------- #


class ForecastRow(BaseModel):
    """One row of the designed monthly view: MONTH / IN / OUT / END."""

    period: date
    inflow: Money
    outflow: Money
    net: Money
    closing: Money


class Forecast(Envelope):
    grain: str
    window: list[date]
    rows: list[ForecastRow]
    summary: SummaryOut
    warnings: Warnings
    diagnostics: list


@router.get("/book/forecast")
async def get_forecast(
    request: Request,
    book: BookDep,
    clock: ClockDep,
    scenario: str | None = None,
    grain: str | None = None,
    window: int | None = None,
    start: date | None = None,
) -> Forecast:
    """``GET /book/forecast`` — the grid payload for F3.

    IN and OUT are the split of the same cash columns the closing series is
    built from, summed per period. Nothing is re-derived from a rounded figure:
    the addition happens on the engine's int64 minor units and is serialized
    once, at the end.
    """
    from ..money import from_minor_units
    from ..serialize import diagnostics_out

    async with read_context(request, book, clock, scenario) as ctx:
        run = ctx.run()
        starts = period_starts(run)
        closing = closing_series(run)

        inflow = [0] * len(starts)
        outflow = [0] * len(starts)
        for item in run.book.items.values():
            if item.kind == "stock":
                continue  # a stock is a level, not a flow; adding it double-counts
            column = run.result.cash.get(item.id)
            if column is None:
                continue
            for index, raw in enumerate(column):
                value = int(raw)
                if value >= 0:
                    inflow[index] += value
                else:
                    outflow[index] += value

        lo = 0 if start is None else next(
            (i for i, period in enumerate(starts) if period >= start), len(starts)
        )
        hi = len(starts) if window is None else min(lo + max(int(window), 0), len(starts))

        rows = [
            ForecastRow(
                period=starts[i],
                inflow=money(from_minor_units(inflow[i])),
                outflow=money(from_minor_units(outflow[i])),
                net=money(from_minor_units(inflow[i] + outflow[i])),
                closing=money(closing[i]),
            )
            for i in range(lo, hi)
        ]
        return Forecast(
            **ctx.envelope().model_dump(),
            grain=grain or run.book.base_grain.value,
            window=starts[lo:hi],
            rows=rows,
            summary=summary_out(run),
            warnings=warnings_for(run),
            diagnostics=diagnostics_out(run.diagnostics),
        )


# --- trace and why_zero (F3, R7/R8) --------------------------------------- #


class TraceResponse(Envelope):
    period: date
    measure: str
    trace: TraceOut


@router.get("/book/trace")
async def get_trace(
    request: Request,
    book: BookDep,
    clock: ClockDep,
    item: str,
    period: date,
    measure: str = "accrual",
    depth: int = 3,
    scenario: str | None = None,
) -> TraceResponse:
    """``GET /book/trace`` — ``trace()`` for the tap-to-explain screen (R7)."""
    async with read_context(request, book, clock, scenario) as ctx:
        run = ctx.run()
        try:
            trace = run.trace(item, period, measure=measure, depth=depth)
        except KeyError as exc:
            raise not_found("NO_ITEM", f"No item {item!r} in this scenario.") from exc
        except ValueError as exc:
            raise bad_request("BAD_TRACE_ARGS", str(exc)) from exc
        return TraceResponse(
            **ctx.envelope().model_dump(), period=period, measure=measure, trace=trace_out(trace)
        )


class WhyZeroResponse(Envelope):
    period: date
    measure: str
    explanation: ExplanationOut


@router.get("/book/why_zero")
async def get_why_zero(
    request: Request,
    book: BookDep,
    clock: ClockDep,
    item: str,
    period: date,
    measure: str = "cash",
    scenario: str | None = None,
) -> WhyZeroResponse:
    """``GET /book/why_zero`` — R8.

    The cause and the suggested fix travel verbatim; the service never
    paraphrases an engine explanation into advice (ADR-0015).
    """
    async with read_context(request, book, clock, scenario) as ctx:
        run = ctx.run()
        try:
            explanation = run.why_zero(item, period, measure=measure)
        except KeyError as exc:
            raise not_found("NO_ITEM", f"No item {item!r} in this scenario.") from exc
        except ValueError as exc:
            raise bad_request("BAD_TRACE_ARGS", str(exc)) from exc
        return WhyZeroResponse(
            **ctx.envelope().model_dump(),
            period=period,
            measure=measure,
            explanation=explanation_out(explanation),
        )


# --- ledger and reconcile (F5) -------------------------------------------- #


class EventsResponse(Envelope):
    events: list[EventOut]


@router.get("/book/events")
async def get_events(
    request: Request,
    book: BookDep,
    clock: ClockDep,
    where: str | None = None,
    since: date | None = None,
    until: date | None = None,
    include_voided: bool = False,
    scenario: str | None = None,
) -> EventsResponse:
    """``GET /book/events`` — the ledger view (F5).

    ``include_voided`` is what makes a correction's scar visible: the original
    row is tombstoned, not deleted, and the Actuals screen shows it struck with
    the correction linked (ADR-0012, SPEC §6-S7).
    """
    async with read_context(request, book, clock, scenario) as ctx:
        table = ctx.kit.query_events(
            where=where, since=since, until=until, include_voided=include_voided
        )
        return EventsResponse(**ctx.envelope().model_dump(), events=events_out(table))


class ReconcileResponse(Envelope):
    reconciliation: ReconciliationOut


@router.get("/book/reconcile")
async def get_reconcile(
    request: Request,
    book: BookDep,
    clock: ClockDep,
    until: date | None = None,
    since: date | None = None,
    scenario: str | None = None,
) -> ReconcileResponse:
    """``GET /book/reconcile`` — per-item forecast/actual/drift (F5, S8).

    ``until`` defaults to ``as_of``: the host fills the date, the engine never
    reads a clock to find it (ADR-0019 rule 2).
    """
    async with read_context(request, book, clock, scenario) as ctx:
        report = ctx.kit.reconcile(
            until or ctx.as_of, since=since, scenario_id=ctx.scenario
        )
        return ReconcileResponse(
            **ctx.envelope().model_dump(), reconciliation=reconciliation_out(report)
        )


# --- history and validate (R12, R10) -------------------------------------- #


class HistoryResponse(Envelope):
    revisions: list[RevisionOut]


@router.get("/book/history")
async def get_history(
    request: Request, book: BookDep, clock: ClockDep, limit: int = 50, scenario: str | None = None
) -> HistoryResponse:
    """``GET /book/history`` — R12, the read-only revision list (SPEC §6-S15)."""
    async with read_context(request, book, clock, scenario) as ctx:
        return HistoryResponse(
            **ctx.envelope().model_dump(),
            revisions=revisions_out(ctx.kit.history(limit=limit)),
        )


class ValidateResponse(Envelope):
    diagnostics: list


@router.get("/book/validate")
async def get_validate(
    request: Request, book: BookDep, clock: ClockDep, scenario: str | None = None
) -> ValidateResponse:
    """``GET /book/validate`` — R10.

    ``validate()`` checks model consistency, not domain completeness
    (ADR-0021); the consumer MLP defers the domain-coverage duty entirely
    (D-MLP-02). The diagnostics render verbatim, with no advice framing.
    """
    from ..serialize import diagnostics_out

    async with read_context(request, book, clock, scenario) as ctx:
        return ValidateResponse(
            **ctx.envelope().model_dump(),
            diagnostics=diagnostics_out(ctx.kit.validate(ctx.scenario)),
        )
