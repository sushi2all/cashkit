"""Dry-run: what would this change do, computed before anything is applied.

ADR-0029's guarantee is that the card the user confirms is the card that
applies. That only holds if the figures on the card came from actually running
the change — so the dry-run applies the operations to a throwaway copy of the
whole book, runs it, and reports the difference against the same book untouched.

The deltas block is SPEC §5-F2 plus D-MLP-05(b): closing balance, min cash and
runway before → after, affected items, and the **crossing flags** — every month
the change turns negative, and the min-cash movement. Warnings are structural
and always on; there are no configurable thresholds in the MLP, and nothing
waits for a background job.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from cashkit.model import Diagnostic
from cashkit.sdk import CashKit, balance_series
from pydantic import BaseModel

from ..books import scratch_copy
from ..money import Money, from_minor_units, money, money_or_none
from ..serialize import DiagnosticOut, diagnostics_out
from .applier import OpResult, apply_op


class MoneyMove(BaseModel):
    """One figure, before and after."""

    before: Money | None
    after: Money | None
    change: Money | None


class Crossing(BaseModel):
    """A month this change turns negative (D-MLP-05(b))."""

    period: _dt.date
    before: Money
    after: Money


class PeriodMove(BaseModel):
    period: _dt.date | None
    before: _dt.date | None = None
    after: _dt.date | None = None


class Deltas(BaseModel):
    """The proposal card's deltas block."""

    closing_balance: MoneyMove
    min_cash: MoneyMove
    min_cash_period: PeriodMove
    runway_end: PeriodMove
    runway_periods_before: int | None
    runway_periods_after: int | None
    affected_items: list[str]
    affected_events: list[str]
    #: Every month the change turns negative, and the months already negative
    #: that get deeper. Computed here, at update time, never on a schedule.
    crossings: list[Crossing]
    negative_months_before: int
    negative_months_after: int


class DryRun(BaseModel):
    """A dry-run's whole result."""

    ok: bool
    scenario: str
    deltas: Deltas
    diagnostics: list[DiagnosticOut]
    #: When an op on the record-actual flow has no usable date, the pipeline
    #: answers with a clarification rather than guessing (SPEC §5-F5).
    clarification: str | None = None
    operations: list[dict[str, Any]]


@dataclass
class _Snapshot:
    starts: list[_dt.date]
    closing: list[Decimal]
    closing_balance: Decimal
    min_cash: Decimal
    min_cash_period: _dt.date | None
    runway_end: _dt.date | None
    runway_periods: int | None
    items: set[str]

    @classmethod
    def of(cls, kit: CashKit, scenario: str) -> "_Snapshot":
        run = kit.run(scenario)
        series, _ = balance_series(run.result, run.book)
        summary = run.summary()
        return cls(
            starts=list(run.result.periods.starts),
            closing=[from_minor_units(int(v)) for v in series],
            closing_balance=summary.closing_balance,
            min_cash=summary.min_cash,
            min_cash_period=summary.min_cash_period,
            runway_end=summary.runway_end,
            runway_periods=summary.runway_periods,
            items=set(run.book.items),
        )


def _move(before: Decimal | None, after: Decimal | None) -> MoneyMove:
    change = None if before is None or after is None else after - before
    return MoneyMove(
        before=money_or_none(before), after=money_or_none(after), change=money_or_none(change)
    )


def _crossings(before: _Snapshot, after: _Snapshot) -> list[Crossing]:
    """Months the change turns negative, or drives further negative."""
    by_period_before = dict(zip(before.starts, before.closing, strict=True))
    crossings: list[Crossing] = []
    for period, value in zip(after.starts, after.closing, strict=True):
        was = by_period_before.get(period)
        if was is None:
            if value < 0:
                crossings.append(Crossing(period=period, before=money(Decimal(0)), after=money(value)))
            continue
        if value < 0 and (was >= 0 or value < was):
            crossings.append(Crossing(period=period, before=money(was), after=money(value)))
    return crossings


def dry_run(
    kit: CashKit,
    operations: list[dict[str, Any]],
    *,
    scenario: str,
    as_of: _dt.date,
    context: str | None = None,
) -> DryRun:
    """Apply ``operations`` to a copy of the book and report the difference.

    The real book is never touched. Diagnostics from the copy are the same
    diagnostics the accept would produce, so the card shows what will happen
    rather than a guess about it.
    """
    before = _Snapshot.of(kit, scenario)
    diagnostics: list[Diagnostic] = []
    clarification: str | None = None
    results: list[OpResult] = []

    with scratch_copy(kit, Path(kit.root)) as scratch:
        for index, operation in enumerate(operations):
            result = apply_op(
                scratch, operation, scenario=scenario, as_of=as_of, context=context, seq=index
            )
            results.append(result)
            diagnostics.extend(result.diagnostics)
            if not result.ok:
                from .applier import CK_E903

                if any(d.code == CK_E903 and operation.get("op") in ("add_event", "record_actual")
                       and operation.get("date") is None for d in result.diagnostics):
                    clarification = result.diagnostics[0].message
                break  # a failed operation makes every later one hypothetical
        scratch.save()
        after = _Snapshot.of(scratch, scenario)

    ok = all(r.ok for r in results) and len(results) == len(operations)
    touched_items = sorted({i for r in results for i in r.touched_items} | (after.items - before.items))
    touched_events = sorted({e for r in results for e in r.touched_events})

    deltas = Deltas(
        closing_balance=_move(before.closing_balance, after.closing_balance),
        min_cash=_move(before.min_cash, after.min_cash),
        min_cash_period=PeriodMove(
            period=after.min_cash_period, before=before.min_cash_period, after=after.min_cash_period
        ),
        runway_end=PeriodMove(period=after.runway_end, before=before.runway_end, after=after.runway_end),
        runway_periods_before=before.runway_periods,
        runway_periods_after=after.runway_periods,
        affected_items=touched_items,
        affected_events=touched_events,
        crossings=_crossings(before, after),
        negative_months_before=sum(1 for v in before.closing if v < 0),
        negative_months_after=sum(1 for v in after.closing if v < 0),
    )
    return DryRun(
        ok=ok,
        scenario=scenario,
        deltas=deltas,
        diagnostics=diagnostics_out(diagnostics),
        clarification=clarification,
        operations=[r.op for r in results] or list(operations),
    )


__all__ = ["Crossing", "Deltas", "DryRun", "MoneyMove", "PeriodMove", "dry_run"]
