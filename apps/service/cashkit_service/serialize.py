"""SDK objects to API payloads.

Two rules govern everything here.

**Engine numbers pass through verbatim.** Every money figure goes through
:func:`cashkit_service.money.money` and nothing else. Nothing is re-derived,
re-added or rounded on the way out; where the engine says ``None`` the payload
says ``null``, because absent is not zero (SPEC §5-F4).

**Diagnostics pass through verbatim.** Code, severity, message and
suggested_fix are copied, never rewritten, summarized, suppressed, or turned
into advice (ADR-0015, SPEC §3). :class:`DiagnosticOut` has no field the SDK
did not fill.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from cashkit.model import Diagnostic
from cashkit.sdk import CashKit, RunRef, balance_series
from pydantic import BaseModel

from .money import Money, from_minor_units, money, money_or_none


class DiagnosticOut(BaseModel):
    """One engine diagnostic, verbatim."""

    code: str
    severity: str
    message: str
    suggested_fix: str
    item_id: str | None = None
    field: str | None = None


def diagnostic_out(d: Diagnostic) -> DiagnosticOut:
    return DiagnosticOut(
        code=d.code,
        severity=d.severity,
        message=d.message,
        suggested_fix=d.suggested_fix,
        item_id=d.item_id,
        field=d.field,
    )


def diagnostics_out(items: Iterable[Diagnostic]) -> list[DiagnosticOut]:
    return [diagnostic_out(d) for d in items]


# --- summary ------------------------------------------------------------- #


class SummaryOut(BaseModel):
    """``RunSummary``, field for field."""

    grain: str
    balance_source: str
    periods: int
    opening_balance: Money
    closing_balance: Money
    min_cash: Money
    min_cash_period: date | None
    runway_periods: int | None
    runway_end: date | None
    breakeven_period: date | None
    total_inflow: Money
    total_outflow: Money
    net_cash: Money
    total_accrual: Money
    diagnostics: list[DiagnosticOut]


def summary_out(run: RunRef) -> SummaryOut:
    s = run.summary()
    return SummaryOut(
        grain=s.grain,
        balance_source=s.balance_source,
        periods=s.periods,
        opening_balance=money(s.opening_balance),
        closing_balance=money(s.closing_balance),
        min_cash=money(s.min_cash),
        min_cash_period=s.min_cash_period,
        runway_periods=s.runway_periods,
        runway_end=s.runway_end,
        breakeven_period=s.breakeven_period,
        total_inflow=money(s.total_inflow),
        total_outflow=money(s.total_outflow),
        net_cash=money(s.net_cash),
        total_accrual=money(s.total_accrual),
        diagnostics=diagnostics_out(s.diagnostics),
    )


# --- periods and series -------------------------------------------------- #


def period_starts(run: RunRef) -> list[date]:
    return list(run.result.periods.starts)


def closing_series(run: RunRef) -> list[Decimal]:
    """The balance series, as exact Decimals, straight off the int64 columns."""
    series, _description = balance_series(run.result, run.book)
    return [from_minor_units(int(v)) for v in series]


class ItemSeries(BaseModel):
    """One item's columns over the horizon."""

    id: str
    name: str
    kind: str
    direction: str | None
    tags: dict[str, str]
    formula: str | None
    cash: list[Money]
    accrual: list[Money]


def item_series(run: RunRef) -> list[ItemSeries]:
    rows: list[ItemSeries] = []
    for item in run.book.items.values():
        try:
            cash = run.result.cash[item.id]
            accrual = run.result.accrual[item.id]
        except KeyError:
            # A synthetic or unevaluated item has no column; it is omitted
            # rather than reported as zeros, which would be a fabricated number.
            continue
        rows.append(
            ItemSeries(
                id=item.id,
                name=item.name,
                kind=item.kind,
                direction=item.direction,
                tags=dict(item.tags),
                formula=item.formula,
                cash=[money(from_minor_units(int(v))) for v in cash],
                accrual=[money(from_minor_units(int(v))) for v in accrual],
            )
        )
    return rows


# --- warnings (D-MLP-05(b), SPEC §5-F2) ---------------------------------- #


class NegativeMonth(BaseModel):
    period: date
    depth: Money


class Warnings(BaseModel):
    """Standing, structural warnings. No thresholds, nothing configurable.

    Computed at every update, never on a schedule (D-MLP-05(b)): the state
    payload always reflects the book as it is right now.
    """

    negative_months: list[NegativeMonth]
    min_cash: Money
    min_cash_period: date | None


def warnings_for(run: RunRef) -> Warnings:
    starts = period_starts(run)
    closing = closing_series(run)
    s = run.summary()
    return Warnings(
        negative_months=[
            NegativeMonth(period=period, depth=money(value))
            for period, value in zip(starts, closing, strict=True)
            if value < 0
        ],
        min_cash=money(s.min_cash),
        min_cash_period=s.min_cash_period,
    )


# --- trace and why_zero -------------------------------------------------- #


class BindingOut(BaseModel):
    symbol: str
    kind: str
    value: Money
    source: str
    target: str
    detail: str


class StepOut(BaseModel):
    expression: str
    operation: str
    inputs: list[str]
    value: Money
    rounding: str


class TraceOut(BaseModel):
    item_id: str
    item_name: str
    kind: str
    measure: str
    period_index: int
    period_start: date
    period_end: date
    value: Money
    formula: str
    bindings: list[BindingOut]
    steps: list[StepOut]
    children: list["TraceOut"]
    depth: int
    truncated: bool
    reconciles: bool
    notes: list[str]
    diagnostics: list[DiagnosticOut]


def trace_out(trace: Any) -> TraceOut:
    return TraceOut(
        item_id=trace.item_id,
        item_name=trace.item_name,
        kind=trace.kind,
        measure=trace.measure,
        period_index=trace.period_index,
        period_start=trace.period_start,
        period_end=trace.period_end,
        value=money(trace.value),
        formula=trace.formula,
        bindings=[
            BindingOut(
                symbol=b.symbol, kind=b.kind, value=money(b.value),
                source=b.source, target=b.target, detail=b.detail,
            )
            for b in trace.bindings
        ],
        steps=[
            StepOut(
                expression=s.expression, operation=s.operation, inputs=list(s.inputs),
                value=money(s.value), rounding=s.rounding,
            )
            for s in trace.steps
        ],
        children=[trace_out(c) for c in trace.children],
        depth=trace.depth,
        truncated=trace.truncated,
        reconciles=trace.reconciles,
        notes=list(trace.notes),
        diagnostics=diagnostics_out(trace.diagnostics),
    )


class ExplanationOut(BaseModel):
    item_id: str
    measure: str
    period_index: int
    period_start: date
    value: Money
    cause: str
    message: str
    detail: str
    also: list[str]
    suggested_fix: str
    diagnostics: list[DiagnosticOut]


def explanation_out(explanation: Any) -> ExplanationOut:
    return ExplanationOut(
        item_id=explanation.item_id,
        measure=explanation.measure,
        period_index=explanation.period_index,
        period_start=explanation.period_start,
        value=money(explanation.value),
        cause=explanation.cause,
        message=explanation.message,
        detail=explanation.detail,
        also=list(explanation.also),
        suggested_fix=explanation.suggested_fix,
        diagnostics=diagnostics_out(explanation.diagnostics),
    )


# --- ledger, reconcile, compare, history --------------------------------- #


class EventOut(BaseModel):
    """One ledger row, as ``query_events`` produced it."""

    id: str
    date: date
    amount: Money
    status: str
    item: str | None
    currency: str
    source: str | None
    ext_id: str | None
    corrects: str | None
    note: str | None
    tags: dict[str, str]


def events_out(table: Any) -> list[EventOut]:
    return [
        EventOut(
            id=row["id"],
            date=row["date"],
            amount=money(row["amount"]),
            status=row["status"],
            item=row["item"],
            currency=row["currency"],
            source=row["source"],
            ext_id=row["ext_id"],
            corrects=row["corrects"],
            note=row["note"],
            tags=dict(row["tags"] or {}),
        )
        for row in table.to_dicts()
    ]


class ReconciliationLineOut(BaseModel):
    item_id: str
    forecast: Money
    actual: Money
    drift: Money


class ReconciliationOut(BaseModel):
    measure: str
    since: date
    until: date
    suggested_cutover: date
    lines: list[ReconciliationLineOut]
    forecast_total: Money
    actual_total: Money
    drift_total: Money
    actual_events: int
    reconciled: bool
    diagnostics: list[DiagnosticOut]


def reconciliation_out(report: Any) -> ReconciliationOut:
    return ReconciliationOut(
        measure=report.measure,
        since=report.since,
        until=report.until,
        suggested_cutover=report.suggested_cutover,
        lines=[
            ReconciliationLineOut(
                item_id=line.item_id,
                forecast=money(line.forecast),
                actual=money(line.actual),
                drift=money(line.drift),
            )
            for line in report.lines
        ],
        forecast_total=money(report.forecast_total),
        actual_total=money(report.actual_total),
        drift_total=money(report.drift_total),
        actual_events=report.actual_events,
        reconciled=report.reconciled,
        diagnostics=diagnostics_out(report.diagnostics),
    )


class ComparePeriod(BaseModel):
    """One period of a scenario comparison.

    ``values`` maps scenario id to figure, and a scenario absent from a period
    is ``null`` — never ``0``. The engine keeps that distinction and so does
    this payload (SPEC §5-F4).
    """

    period_start: date
    values: dict[str, Money | None]
    delta: Money | None = None


class RevisionOut(BaseModel):
    id: str
    parent: str | None
    message: str
    author: str
    timestamp: str
    depth: int
    engine_version: str | None = None


def revisions_out(revisions: Sequence[Any]) -> list[RevisionOut]:
    return [
        RevisionOut(
            id=r.id,
            parent=r.parent,
            message=r.message,
            author=r.author,
            timestamp=r.timestamp,
            depth=r.depth,
            engine_version=dict(r.metadata).get("engine-version"),
        )
        for r in revisions
    ]


class ScenarioOut(BaseModel):
    id: str
    parent: str | None
    note: str
    is_base: bool
    is_active: bool


def scenarios_out(kit: CashKit, active: str) -> list[ScenarioOut]:
    from .envelope import BASE_SCENARIO

    return [
        ScenarioOut(
            id=scenario.id,
            parent=scenario.parent,
            note=scenario.note,
            is_base=scenario.id == BASE_SCENARIO,
            is_active=scenario.id == active,
        )
        for scenario in (kit.scenarios.scenarios[key] for key in sorted(kit.scenarios.scenarios))
    ]


__all__ = [
    "BindingOut", "ComparePeriod", "DiagnosticOut", "EventOut", "ExplanationOut",
    "ItemSeries", "NegativeMonth", "ReconciliationLineOut", "ReconciliationOut",
    "RevisionOut", "ScenarioOut", "StepOut", "SummaryOut", "TraceOut", "Warnings",
    "closing_series", "diagnostic_out", "diagnostics_out", "events_out",
    "explanation_out", "item_series", "period_starts", "reconciliation_out",
    "revisions_out", "scenarios_out", "summary_out", "trace_out", "warnings_for",
]
