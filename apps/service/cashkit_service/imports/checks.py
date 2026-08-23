"""Reconciliation: the sheet's own totals against the engine's own figures.

This module is the honest half of the import loop. The model reads a
spreadsheet and says *which* cell is a total and *what* that total means; it
never says what the total is, and it never says what the engine computed.
Both sides of every comparison are produced by something that cannot be
persuaded:

* the **sheet** side is read out of the workbook by :mod:`.sheets`;
* the **engine** side is computed by the engine from the dry-run of the
  operations the model authored.

A reconciliation the model could satisfy by asserting would not be one.

**The 1-cent parity label (SPEC §7.5).** Engine intermediates are int64 at 4dp
with banker's rounding; Excel uses float ``ROUND``. They disagree on exact
ties: ``612.07 × 0.0795 → 48.6596`` at 4dp, ``/12 → 4.0550``, an exact tie,
which banker's rounding takes to ``4.06`` where Excel gives ``4.05``. That is
designed behaviour on both sides. A divergence of at most one cent is
therefore **labelled** ``parity`` — and it stays a mismatch. Nothing here
absorbs a divergence, rounds one away, or reports a matched row that did not
match; that would be the exact failure this engine exists to prevent.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from cashkit.sdk import CashKit, balance_series
from pydantic import BaseModel, Field

from ..money import Money, from_minor_units, money
from .sheets import Sheets, read_number

#: SPEC §7.5. One cent, flat: the divergence class the engine's rounding order
#: can produce against Excel's on a single figure. It is deliberately not
#: scaled by how many figures were accumulated — a looser tolerance would start
#: absorbing real errors, and a real error reported as a mismatch is the
#: outcome this report exists to produce.
PARITY_TOLERANCE = Decimal("0.01")

Measure = Literal["closing", "total_in", "total_out", "net", "item_total", "items_total"]

#: Measures whose sheet-side figure may legitimately carry the opposite sign,
#: because many human budgets write outflows as positive numbers (proto T07).
SIGN_FLEXIBLE: frozenset[str] = frozenset({"total_out", "item_total", "items_total"})


class CheckSpec(BaseModel):
    """One reconciliation check, as the plan describes it.

    Every field is either a cell reference or a *meaning*. There is no slot for
    a figure: the value comes from the workbook, the engine value from the
    engine.
    """

    ref: str = Field(description="The sheet cell holding the sheet's own total, e.g. 'Budget!B14'.")
    label: str = ""
    measure: Measure = "closing"
    period: _dt.date | None = None
    item: str | None = None
    items: list[str] = Field(default_factory=list)
    since: _dt.date | None = None
    until: _dt.date | None = None


class CheckResult(BaseModel):
    """One row of the reconciliation report (SPEC §6-S14).

    ``sheet_value`` and ``delta`` are plain decimal strings on purpose. Only
    ``engine_value`` is a money figure, because only it is one: the other two
    are a spreadsheet cell and a comparison between the two systems.
    """

    ref: str
    label: str
    measure: str
    period: _dt.date | None = None
    #: ``absolute`` when the engine figure is the book's own; ``added`` when it
    #: is what this import *added* to a scenario that already had a plan in it.
    #: A fork carries the book's existing lines, so an absolute comparison
    #: against a sheet's own total would be comparing two different things.
    basis: Literal["absolute", "added"] = "absolute"
    status: Literal["matched", "mismatched", "skipped"]
    #: SPEC §7.5. True when a mismatch is at most one cent — the engine-vs-Excel
    #: rounding class. It labels the row; it never turns it into a match.
    parity: bool = False
    sheet_value: str | None = None
    engine_value: Money | None = None
    delta: str | None = None
    note: str = ""


class ReconciliationReport(BaseModel):
    """The whole report, per sheet row plus the counts (SPEC §6-S14)."""

    target_scenario: str
    target_reason: Literal["empty_book", "non_empty_book"]
    created_fork: bool
    source_filename: str
    checks: list[CheckResult] = Field(default_factory=list)
    matched: int = 0
    mismatched: int = 0
    skipped: int = 0
    parity_notes: int = 0
    parity_tolerance: str = str(PARITY_TOLERANCE)
    llm_calls: int = 0
    call_cap: int = 0
    capped: bool = False
    partial: bool = False
    #: What the loop could not do, in the user's words. Present on a partial.
    incomplete_reason: str = ""

    def tally(self) -> "ReconciliationReport":
        self.matched = sum(1 for c in self.checks if c.status == "matched")
        self.mismatched = sum(1 for c in self.checks if c.status == "mismatched")
        self.skipped = sum(1 for c in self.checks if c.status == "skipped")
        self.parity_notes = sum(1 for c in self.checks if c.parity)
        return self

    @property
    def all_matched(self) -> bool:
        return self.mismatched == 0 and self.matched > 0


# --- the engine side ------------------------------------------------------ #


@dataclass
class Figures:
    """Every engine figure a check can be compared against, for one scenario.

    Built once per dry-run, from the engine's own columns. ``closing`` is
    :func:`cashkit.sdk.balance_series`, which is the authoritative balance and
    already includes one-off events, attached and unattached alike.

    :meth:`added` builds the other basis, and the reason it exists is SPEC §7.3.
    An import into a non-empty book lands in a fork, and that fork carries the
    book's own plan as well as the sheet's. Comparing the fork's January total
    against the sheet's January total would then be comparing the sheet plus
    the user's existing lines against the sheet alone — a mismatch that is not
    the import's fault and that the loop would waste its calls chasing. The
    honest comparison is what the import *added*, so that is what is compared,
    and every row says which basis it used.
    """

    starts: list[_dt.date]
    closing: list[Decimal]
    inflow: list[Decimal]
    outflow: list[Decimal]
    net: list[Decimal]
    per_item: dict[str, list[Decimal]]
    basis: Literal["absolute", "added"] = "absolute"

    @classmethod
    def added(cls, target: "Figures", baseline: "Figures") -> "Figures":
        """What this import added to a scenario that already had a plan in it.

        ``closing`` is deliberately empty. A running balance is the sum of a
        starting balance and everything since, and the sheet's starting balance
        is not this book's — the book keeps its own, because the opening
        balance is book-level and moving it would change base (SPEC §7.3). So a
        balance row cannot be reconciled on a fork at all, and the report says
        that rather than inventing a comparison that would hold.
        """
        def minus(a: list[Decimal], b: list[Decimal]) -> list[Decimal]:
            return [a[i] - (b[i] if i < len(b) else Decimal(0)) for i in range(len(a))]

        zeros = [Decimal(0)] * len(target.starts)
        return cls(
            starts=list(target.starts),
            closing=[],
            inflow=minus(target.inflow, baseline.inflow),
            outflow=minus(target.outflow, baseline.outflow),
            net=minus(target.net, baseline.net),
            per_item={
                item: minus(values, baseline.per_item.get(item, zeros))
                for item, values in target.per_item.items()
            },
            basis="added",
        )

    @classmethod
    def of(cls, kit: CashKit, scenario: str) -> "Figures":
        run = kit.run(scenario)
        starts = list(run.result.periods.starts)
        series, _description = balance_series(run.result, run.book)
        closing = [from_minor_units(int(v)) for v in series]

        per_item: dict[str, list[Decimal]] = {}
        flows: list[list[Decimal]] = []
        for column, values in run.result.cash.items():
            item = run.book.items.get(column)
            if item is not None and item.kind == "stock":
                # balance_series sums every non-stock column; a stock item is a
                # level, not a flow, and adding it would double-count.
                continue
            decimals = [from_minor_units(int(v)) for v in values]
            flows.append(decimals)
            if item is not None:
                per_item[column] = decimals

        zeros = [Decimal(0)] * len(starts)
        inflow = list(zeros)
        outflow = list(zeros)
        for values in flows:
            for index, value in enumerate(values):
                if value > 0:
                    inflow[index] += value
                elif value < 0:
                    outflow[index] += value
        net = [inflow[i] + outflow[i] for i in range(len(starts))]
        return cls(
            starts=starts, closing=closing, inflow=inflow, outflow=outflow,
            net=net, per_item=per_item,
        )

    def index_of(self, period: _dt.date | None) -> int | None:
        if period is None:
            return None
        for index, start in enumerate(self.starts):
            if start.year == period.year and start.month == period.month:
                return index
        return None

    def window(self, since: _dt.date | None, until: _dt.date | None) -> range:
        lo = 0
        hi = len(self.starts)
        if since is not None:
            lo = next((i for i, s in enumerate(self.starts) if s >= since), hi)
        if until is not None:
            hi = next((i for i, s in enumerate(self.starts) if s > until), hi)
        return range(lo, max(lo, hi))

    def value_for(self, check: CheckSpec) -> tuple[Decimal | None, str]:
        """The engine's figure for one check, and why there is none if there is not."""
        measure = check.measure
        if measure == "closing" and self.basis == "added":
            return None, (
                "a balance row cannot be checked here: this import lands in a scenario "
                "beside the plan the book already has, and the book keeps its own "
                "opening balance, so the sheet's running balance is not the same "
                "quantity. The monthly totals below are checked."
            )
        if measure in ("closing", "total_in", "total_out", "net"):
            index = self.index_of(check.period)
            if index is None:
                return None, (
                    f"{check.period.isoformat() if check.period else 'that month'} is "
                    "outside the book's horizon, so the engine computes nothing for it."
                )
            column = {
                "closing": self.closing,
                "total_in": self.inflow,
                "total_out": self.outflow,
                "net": self.net,
            }[measure]
            return column[index], ""

        ids = [check.item] if check.item else list(check.items)
        ids = [i for i in ids if i]
        if not ids:
            return None, "The check named no item."
        missing = [i for i in ids if i not in self.per_item]
        if missing:
            return None, f"No item {', '.join(sorted(missing))} in the book."
        if check.period is not None:
            index = self.index_of(check.period)
            if index is None:
                return None, "That month is outside the book's horizon."
            return sum((self.per_item[i][index] for i in ids), Decimal(0)), ""
        span = self.window(check.since, check.until)
        if not span:
            return None, "That window is outside the book's horizon."
        return (
            sum((self.per_item[i][index] for i in ids for index in span), Decimal(0)),
            "",
        )


# --- the comparison ------------------------------------------------------- #


def evaluate(
    figures: Figures, sheets: Sheets, checks: list[CheckSpec]
) -> list[CheckResult]:
    """Compare every check's sheet cell against the engine's own figure."""
    return [_one(figures, sheets, check) for check in checks]


def _one(figures: Figures, sheets: Sheets, check: CheckSpec) -> CheckResult:
    sheet_value = read_number(sheets, check.ref)
    base = CheckResult(
        ref=check.ref,
        label=check.label or check.ref,
        measure=check.measure,
        period=check.period,
        basis=figures.basis,
        status="skipped",
        sheet_value=None if sheet_value is None else str(sheet_value),
    )
    if sheet_value is None:
        base.note = f"{check.ref} holds no number, so there is nothing to check against."
        return base

    engine_value, why = figures.value_for(check)
    if engine_value is None:
        base.note = why
        return base

    base.engine_value = money(engine_value)
    delta = engine_value - sheet_value
    used = sheet_value
    note = ""

    if delta != 0 and check.measure in SIGN_FLEXIBLE:
        flipped = engine_value - (-sheet_value)
        if abs(flipped) < abs(delta):
            # Many human budgets write outflows as positive numbers (proto T07).
            # The flip is host-side, deterministic and disclosed on the row; it
            # is never applied when it makes the delta worse.
            delta = flipped
            used = -sheet_value
            note = "the sheet writes outflows as positive numbers; compared against the negated cell"

    base.delta = str(delta)
    base.note = note
    if delta == 0:
        base.status = "matched"
        return base

    base.status = "mismatched"
    if abs(delta) <= PARITY_TOLERANCE:
        base.parity = True
        base.note = _join(
            note,
            "1-cent parity: the engine works in int64 at 4dp with banker's rounding "
            "and Excel uses float ROUND, so an exact tie lands one cent apart. "
            "Reported, not absorbed (SPEC §7.5).",
        )
    else:
        base.note = _join(
            note,
            f"the sheet says {used} and the engine computes {engine_value}.",
        )
    return base


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def evidence_for(kit: CashKit, scenario: str, results: list[CheckResult]) -> list[dict[str, Any]]:
    """``trace()`` receipts for the mismatched months (SPEC §7.2).

    The loop investigates a mismatch with the engine's own explanation rather
    than by re-reading the spreadsheet: what the engine says it computed, item
    by item, is what tells the model which line it got wrong.
    """
    run = kit.run(scenario)
    evidence: list[dict[str, Any]] = []
    seen: set[_dt.date] = set()
    for result in results:
        if result.status != "mismatched" or result.period is None or result.period in seen:
            continue
        seen.add(result.period)
        rows: list[dict[str, Any]] = []
        for item_id in run.book.items:
            try:
                trace = run.trace(item=item_id, period=result.period, measure="cash")
            except Exception:  # noqa: BLE001 — an item with no column has nothing to say
                continue
            if trace.value == 0:
                continue
            rows.append(
                {
                    "item": item_id,
                    "value": str(trace.value),
                    "steps": [f"{s.expression} = {s.value}" for s in trace.steps][:4],
                }
            )
        evidence.append(
            {
                "period": result.period.isoformat(),
                "check": result.label,
                "sheet_value": result.sheet_value,
                "engine_value": None if result.engine_value is None else result.engine_value.exact,
                "delta": result.delta,
                "engine_rows": rows,
            }
        )
        if len(evidence) >= 4:
            break
    return evidence


__all__ = [
    "PARITY_TOLERANCE",
    "CheckResult",
    "CheckSpec",
    "Figures",
    "Measure",
    "ReconciliationReport",
    "evaluate",
    "evidence_for",
]
