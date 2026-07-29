"""Run output: int64 minor-unit columns plus the diagnostics produced (PRD §5.5).

Both evaluators return this type, which is what makes the dual-engine gate a
byte-for-byte comparison rather than a tolerance check.

Canonical storage is tidy/long — one row per ``(period, item, measure)`` — and
:meth:`RunResult.rows` emits exactly that. Tags are deliberately *not*
denormalized into the rows; they live on the item and are joined on demand
(PRD §5.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import numpy as np

from cashkit.model import Diagnostic, ItemId

from .calendars import PeriodIndex
from .numeric import from_minor

__all__ = ["MEASURE_NAMES", "RunResult", "Row"]

#: Measures every item carries. A stock's ``accrual`` column holds its level and
#: its ``cash`` column is zero — a stock is a balance, not a movement.
MEASURE_NAMES = ("accrual", "cash")


@dataclass(frozen=True)
class Row:
    """One tidy/long frame row."""

    period_start: date
    period_end: date
    item_id: ItemId
    measure: str
    value: Decimal
    currency: str
    status: str


@dataclass(frozen=True)
class RunResult:
    """The evaluated book: two int64 columns per item, in minor units at 4 dp.

    ``diagnostics`` carries everything the run wants to tell the caller —
    rejected formulas, clamped remainders, masked divisions. An error-severity
    diagnostic means some column is zero because the engine refused to guess.
    """

    book_id: str
    periods: PeriodIndex
    accrual: dict[ItemId, np.ndarray]
    cash: dict[ItemId, np.ndarray]
    diagnostics: tuple[Diagnostic, ...]
    currencies: dict[ItemId, str]

    def column(self, item_id: ItemId, measure: str) -> np.ndarray:
        """Return one item's column for ``measure``.

        Returns an int64 array of minor units. Raises ``KeyError`` for an
        unknown item and ``ValueError`` for an unknown measure (programmer
        error). Produces no diagnostics.
        """
        if measure == "accrual":
            return self.accrual[item_id]
        if measure == "cash":
            return self.cash[item_id]
        raise ValueError(f"unknown measure {measure!r}; expected one of {MEASURE_NAMES}")

    def value(self, item_id: ItemId, measure: str, period: int) -> Decimal:
        """Return one cell as a boundary ``Decimal`` at 4 dp. No diagnostics."""
        return from_minor(int(self.column(item_id, measure)[period]))

    def total(self, item_id: ItemId, measure: str) -> Decimal:
        """Return an item's whole-horizon total as a ``Decimal``. No diagnostics."""
        return from_minor(int(self.column(item_id, measure).sum()))

    def rows(self) -> list[Row]:
        """Emit the tidy/long frame: one row per ``(period, item, measure)``.

        Returns rows ordered by period, then item id, then measure. ``status`` is
        ``"forecast"`` for everything generated from Items; Phase 5 introduces
        ledger-derived rows with other statuses. Produces no diagnostics.
        """
        out: list[Row] = []
        for index, (start, end) in enumerate(zip(self.periods.starts, self.periods.ends)):
            for item_id in sorted(self.accrual):
                for measure in MEASURE_NAMES:
                    out.append(
                        Row(
                            period_start=start,
                            period_end=end,
                            item_id=item_id,
                            measure=measure,
                            value=from_minor(int(self.column(item_id, measure)[index])),
                            currency=self.currencies[item_id],
                            status="forecast",
                        )
                    )
        return out

    def diagnostic_keys(self) -> tuple[tuple[str, str | None, str | None], ...]:
        """Return a canonical, order-independent view of the diagnostics.

        ``(code, item_id, field)`` triples, sorted. Used by the dual-engine test:
        the two engines must agree on *what* they complained about, not on the
        order in which they noticed. Produces no diagnostics.
        """
        return tuple(
            sorted((d.code, d.item_id, d.field) for d in self.diagnostics)
        )
