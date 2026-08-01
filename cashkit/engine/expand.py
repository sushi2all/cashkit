"""Vectorized segment expansion and settlement (PRD §5.1, §5.3, ADR-0003).

Segment expansion is date-index masking: occurrence dates become an ordinal
array, escalation becomes a binary search over anniversary boundaries plus one
integer multiply per distinct factor, probability another multiply, and the
accrual is a scatter-add into the period column. Settlement is the same shape —
one leg array per :class:`DueTerm`, shifted by an offset table and scattered.

The canonical rounding order (ADR-0003) is applied here for both generative and
derived accruals: **escalation, probability, settlement share split, withholding**.
The VAT step that closes the canonical chain is Phase 6's (DECISIONS D-P2-14).

Structural settlement classification lives here rather than in either evaluator
because it is a property of the term list, not of arithmetic: both engines must
reach the same verdict and emit the same diagnostic, so they share one function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

import numpy as np

from cashkit.model import Diagnostic, DueTerm, Item, Segment
from cashkit.model.diagnostics import make_diagnostic

from .calendars import (
    BusinessCalendar,
    PeriodIndex,
    add_duration,
    add_months,
    month_length,
    occurrence_dates,
    parse_duration,
)
from .numeric import (
    INT64_MAX,
    MINOR_SCALE,
    RoundingPolicy,
    escalation_factor,
    mul_ratio_array,
    ratio_of,
    to_minor,
)
from cashkit.model import Grain

__all__ = [
    "DateOps",
    "FIXED",
    "IMMEDIATE",
    "INVALID",
    "NEVER",
    "SHARES",
    "Expansion",
    "FoldSettlement",
    "add_minor",
    "classify_settlement",
    "derived_accrual_ordinals",
    "escalation_boundary",
    "escalation_steps_array",
    "expand_item",
    "leg_targets",
    "occurrence_ordinals",
    "settle_occurrences",
    "split_legs",
]

# --------------------------------------------------------------------------- #
# Settlement classification — shared structure, duplicated arithmetic
# --------------------------------------------------------------------------- #

#: ``settlement=None``: cash moves when the amount accrues (DECISIONS D-P2-04).
IMMEDIATE = "immediate"
#: An explicit empty ``due`` list: accrues, never settles.
NEVER = "none"
#: All-share terms summing to exactly 1.
SHARES = "shares"
#: Fixed amounts plus exactly one remainder.
FIXED = "fixed"
#: Structurally invalid — accrues, produces no cash (DECISIONS D-P2-09).
INVALID = "invalid"


def classify_settlement(item: Item) -> tuple[str, tuple[Diagnostic, ...]]:
    """Decide how an item settles, refusing to guess on a malformed term list.

    Returns ``(kind, diagnostics)``. A structurally invalid settlement yields
    ``CK-E004`` (shares not summing to 1) or ``CK-E005`` (share/amount mixing, or
    the wrong number of ``remainder`` terms) and kind :data:`INVALID`: the item
    accrues but produces no cash. Inventing a split the author did not write is
    exactly the silent numerical error this engine forbids.
    """
    settlement = item.settlement
    if settlement is None:
        return IMMEDIATE, ()
    if not settlement.due:
        return NEVER, ()
    shares = [term for term in settlement.due if term.share is not None]
    amounts = [term for term in settlement.due if term.amount is not None]
    remainders = [term for term in settlement.due if term.remainder]
    if shares and (amounts or remainders):
        return INVALID, (
            make_diagnostic(
                "CK-E005",
                item_id=item.id,
                field="settlement.due",
                reason="terms mix 'share' with 'amount'/'remainder'",
            ),
        )
    if shares:
        total = sum((term.share for term in shares), start=Decimal(0))
        if total != Decimal(1):
            return INVALID, (
                make_diagnostic(
                    "CK-E004", item_id=item.id, field="settlement.due", total=total
                ),
            )
        return SHARES, ()
    if len(remainders) != 1:
        return INVALID, (
            make_diagnostic(
                "CK-E005",
                item_id=item.id,
                field="settlement.due",
                reason=(
                    f"fixed-amount settlement needs exactly one remainder term, "
                    f"found {len(remainders)}"
                ),
            ),
        )
    return FIXED, ()


# --------------------------------------------------------------------------- #
# Escalation anniversaries
# --------------------------------------------------------------------------- #


def escalation_boundary(
    anchor: str, every_years: int, segment_start: date, step: int
) -> date:
    """The date on which escalation step ``step`` takes effect.

    ``segment_start`` anchoring uses the segment's own anniversary, with the day
    clamped to month end exactly as ``Duration`` arithmetic clamps it — so the
    anniversary of 29 February is 28 February in a common year, consistent with
    the rest of the system. ``calendar_year`` anchoring steps on 1 January.

    Returns a ``date``; produces no diagnostics. This is the single definition
    both engines resolve — the reference walks the boundaries, the vectorized
    engine binary-searches them.
    """
    if anchor == "calendar_year":
        return date(segment_start.year + step * every_years, 1, 1)
    return add_months(segment_start, 12 * step * every_years)


def escalation_steps_array(
    anchor: str,
    every_years: int,
    segment_start: date,
    occurrence_ords: np.ndarray,
    horizon_end: date,
) -> np.ndarray:
    """Completed escalation steps per occurrence, as an int64 array.

    Builds the anniversary boundaries up to the horizon and resolves every
    occurrence against them with one binary search. Produces no diagnostics.
    """
    boundaries: list[int] = []
    step = 1
    while True:
        boundary = escalation_boundary(anchor, every_years, segment_start, step)
        if boundary > horizon_end or len(boundaries) >= 400:
            break
        boundaries.append(boundary.toordinal())
        step += 1
    if not boundaries:
        return np.zeros(occurrence_ords.shape, dtype=np.int64)
    table = np.array(boundaries, dtype=np.int64)
    return np.searchsorted(table, occurrence_ords, side="right").astype(np.int64)


# --------------------------------------------------------------------------- #
# Occurrence generation
# --------------------------------------------------------------------------- #

_DAYS_PER_UNIT = {Grain.DAY: 1, Grain.WEEK: 7}


def occurrence_ordinals(
    segment: Segment, horizon_start: date, horizon_end: date
) -> np.ndarray:
    """A segment's unadjusted anchor dates as an int64 ordinal array.

    Regular day- and week-cadence recurrences on a period boundary are produced
    with pure integer arithmetic; every other shape defers to
    :func:`~cashkit.engine.calendars.occurrence_dates`, whose bucket count is
    small (months, quarters, years). Both paths return the same dates —
    ``tests/test_expand.py`` pins that equivalence.

    Returns ordinals in ascending order; produces no diagnostics.
    """
    recurrence = segment.recurrence
    stride_days = _DAYS_PER_UNIT.get(recurrence.unit)
    if stride_days is None or recurrence.anchor not in ("period_start", "period_end"):
        return np.fromiter(
            (
                day.toordinal()
                for day in occurrence_dates(
                    recurrence, segment.start, segment.end, horizon_start, horizon_end
                )
            ),
            dtype=np.int64,
        )

    stride = stride_days * recurrence.every
    offset = 0 if recurrence.anchor == "period_start" else stride - 1
    start_ord = segment.start.toordinal()
    stop = horizon_end if segment.end is None else min(segment.end, horizon_end)
    stop_ord = stop.toordinal()
    if stop_ord <= start_ord:
        return np.zeros(0, dtype=np.int64)
    # Buckets are generated while bucket_start < stop, then anchors filtered —
    # the same two-step rule the scalar generator uses.
    bucket_count = -(-(stop_ord - start_ord) // stride)
    first = max(0, (horizon_start.toordinal() - start_ord - offset) // stride)
    if first >= bucket_count:
        return np.zeros(0, dtype=np.int64)
    indices = np.arange(first, bucket_count, dtype=np.int64)
    anchors = start_ord + stride * indices + offset
    keep = (anchors >= start_ord) & (anchors < stop_ord) & (
        anchors >= horizon_start.toordinal()
    )
    return anchors[keep]


# --------------------------------------------------------------------------- #
# Date arithmetic over the horizon, precomputed once per run
# --------------------------------------------------------------------------- #


@dataclass
class DateOps:
    """Horizon-indexed lookup tables for calendar shifts.

    Month-end and calendar-month offsets cannot be expressed as integer strides,
    so they are tabulated once per run over a padded horizon and then applied by
    array indexing. The padding covers a month-end pushing past the horizon.
    """

    periods: PeriodIndex
    calendar: BusinessCalendar
    pad: int = 400
    _month_end: np.ndarray | None = field(default=None, repr=False)
    _month_shift: dict[int, np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def base(self) -> int:
        """First ordinal covered by the tables. No diagnostics."""
        return self.periods.starts[0].toordinal() - self.pad

    @property
    def span(self) -> int:
        """Number of ordinals covered by the tables. No diagnostics."""
        return (
            self.periods.ends[-1].toordinal() - self.periods.starts[0].toordinal()
        ) + 2 * self.pad

    def month_end(self, ordinals: np.ndarray) -> np.ndarray:
        """Map each ordinal to the last day of its month. No diagnostics."""
        if self._month_end is None:
            table = np.empty(self.span, dtype=np.int64)
            for index in range(self.span):
                day = date.fromordinal(self.base + index)
                table[index] = date(
                    day.year, day.month, month_length(day.year, day.month)
                ).toordinal()
            self._month_end = table
        return self._month_end[ordinals - self.base]

    def shift_months(self, ordinals: np.ndarray, months: int) -> np.ndarray:
        """Add ``months`` calendar months, clamping the day to month end.

        Tabulated per distinct month count — a book has a handful. No diagnostics.
        """
        table = self._month_shift.get(months)
        if table is None:
            table = np.empty(self.span, dtype=np.int64)
            for index in range(self.span):
                table[index] = add_months(
                    date.fromordinal(self.base + index), months
                ).toordinal()
            self._month_shift[months] = table
        return table[ordinals - self.base]

    def offset(self, ordinals: np.ndarray, duration: str) -> np.ndarray:
        """Apply a ``Duration`` to every ordinal. No diagnostics."""
        count, unit = parse_duration(duration)
        if unit == "d":
            return ordinals + count
        if unit == "w":
            return ordinals + 7 * count
        return self.shift_months(ordinals, count if unit == "m" else 12 * count)


# --------------------------------------------------------------------------- #
# Expansion
# --------------------------------------------------------------------------- #


@dataclass
class Expansion:
    """Accrual and cash columns for one item, plus the diagnostics raised."""

    accrual: np.ndarray
    cash: np.ndarray
    diagnostics: list[Diagnostic] = field(default_factory=list)


def _scatter(target: np.ndarray, indices: np.ndarray, values: np.ndarray) -> None:
    """Scatter-add with an overflow pre-check — numpy would wrap silently."""
    if values.size == 0:
        return
    peak = int(np.abs(target).max()) if target.size else 0
    if peak + int(np.abs(values).sum()) > INT64_MAX:
        from .numeric import MoneyOverflowError

        raise MoneyOverflowError("scatter-add of cash legs would overflow int64")
    np.add.at(target, indices, values)


@lru_cache(maxsize=4096)
def _factor_ratio(rate: Decimal, steps: int) -> tuple[int, int]:
    return ratio_of(escalation_factor(rate, steps))


def _apply_escalation(
    amounts: np.ndarray,
    anchors: np.ndarray,
    segment: Segment,
    rate: Decimal,
    horizon_end: date,
    policy: RoundingPolicy,
) -> np.ndarray:
    assert segment.escalation is not None
    steps = escalation_steps_array(
        segment.escalation.anchor,
        segment.escalation.every_years,
        segment.start,
        anchors,
        horizon_end,
    )
    result = amounts.copy()
    # One integer multiply per distinct factor — the distinct count is
    # rates x years, so this loop runs a handful of times (ADR-0002).
    for step in np.unique(steps):
        if step == 0:
            continue
        mask = steps == step
        numerator, denominator = _factor_ratio(rate, int(step))
        result[mask] = mul_ratio_array(amounts[mask], numerator, denominator, policy)
    return result


def split_legs(
    item: Item, kind: str, amounts: np.ndarray, policy: RoundingPolicy
) -> tuple[list[np.ndarray], list[Diagnostic]]:
    """Split accruals into one leg array per :class:`DueTerm`, then withhold.

    Positions 3 and 4 of the canonical rounding order (ADR-0003). In a share
    split the last term absorbs the residual so the legs sum to the accrual
    exactly. This is the *arithmetic* half of settlement — placement in time is
    :func:`leg_targets` — and it is the only implementation, shared by the
    whole-horizon path and the sequential fold, so the two cannot drift.

    Returns ``(legs, diagnostics)``: ``CK-W001`` when a remainder clamps to zero
    and ``CK-W002`` when a negative accrual routes entirely through the remainder.
    """
    diagnostics: list[Diagnostic] = []
    assert item.settlement is not None
    terms = item.settlement.due
    legs: list[np.ndarray] = []

    if kind == SHARES:
        running = np.zeros(amounts.shape, dtype=np.int64)
        for position, term in enumerate(terms):
            assert term.share is not None
            if position == len(terms) - 1:
                legs.append(amounts - running)
            else:
                numerator, denominator = ratio_of(term.share)
                leg = mul_ratio_array(amounts, numerator, denominator, policy)
                running = running + leg
                legs.append(leg)
    else:
        fixed_total = sum(
            (term.amount for term in terms if term.amount is not None), start=Decimal(0)
        )
        fixed_minor = to_minor(fixed_total)
        negative = amounts < 0
        has_fixed = any(term.amount is not None for term in terms)
        leftover = amounts - fixed_minor
        clamped = leftover < 0
        if has_fixed and negative.any():
            # Fixed legs never flip sign: a credit note routes entirely through
            # the remainder (PRD §4.4).
            diagnostics.append(
                make_diagnostic("CK-W002", item_id=item.id, field="settlement.due")
            )
        offending = clamped & ~negative
        if offending.any():
            first = int(np.argmax(offending))
            diagnostics.append(
                make_diagnostic(
                    "CK-W001",
                    item_id=item.id,
                    field="settlement.due",
                    fixed_total=fixed_total,
                    accrued=_as_decimal(amounts[first]),
                )
            )
        remainder_leg = np.where(
            negative, amounts, np.where(clamped, 0, leftover)
        ).astype(np.int64)
        for term in terms:
            if term.remainder:
                legs.append(remainder_leg)
            else:
                assert term.amount is not None
                legs.append(
                    np.where(negative, 0, to_minor(term.amount)).astype(np.int64)
                )

    withheld: list[np.ndarray] = []
    for term, leg in zip(terms, legs):
        if term.withholding != Decimal(0):
            numerator, denominator = ratio_of(term.withholding)
            leg = leg - mul_ratio_array(leg, numerator, denominator, policy)
        withheld.append(leg)
    return withheld, diagnostics


def leg_targets(
    term: DueTerm,
    accrual_ords: np.ndarray,
    accrual_indices: np.ndarray,
    dates: DateOps,
) -> np.ndarray:
    """The period index each leg of ``term`` lands in, or ``-1`` outside the horizon.

    Placement is pure calendar arithmetic and depends only on the accrual dates,
    so for a derived item — whose accrual dates are the period starts — it is
    computed once per run and reused by every period of the fold. Returns an
    int64 array; produces no diagnostics.
    """
    due = dates.offset(
        _basis_ordinals(term, accrual_ords, accrual_indices, dates), term.offset
    )
    due = dates.calendar.adjust_array(due, term.adjust)
    return dates.periods.index_of_ordinals(due)


def settle_occurrences(
    item: Item,
    kind: str,
    accrual_ords: np.ndarray,
    amounts: np.ndarray,
    accrual_indices: np.ndarray,
    cash: np.ndarray,
    dates: DateOps,
    policy: RoundingPolicy,
) -> list[Diagnostic]:
    """Turn accruals into cash legs and scatter them into ``cash``.

    Composes :func:`split_legs` with :func:`leg_targets`. Returns the split's
    diagnostics (``CK-W001``, ``CK-W002``).
    """
    if kind in (NEVER, INVALID) or amounts.size == 0:
        return []
    if kind == IMMEDIATE:
        _scatter(cash, accrual_indices, amounts)
        return []

    assert item.settlement is not None
    legs, diagnostics = split_legs(item, kind, amounts, policy)
    for term, leg in zip(item.settlement.due, legs):
        target = leg_targets(term, accrual_ords, accrual_indices, dates)
        inside = target >= 0
        _scatter(cash, target[inside], leg[inside])
    return diagnostics


def _basis_ordinals(
    term: DueTerm,
    accrual_ords: np.ndarray,
    accrual_indices: np.ndarray,
    dates: DateOps,
) -> np.ndarray:
    if term.basis == "accrual":
        return accrual_ords
    if term.basis == "month_end":
        return dates.month_end(accrual_ords)
    # `period_end` is the base-grain period's inclusive last day (D-P2-16); at
    # day grain that coincides with the accrual date.
    ends = np.fromiter(
        (day.toordinal() for day in dates.periods.ends),
        dtype=np.int64,
        count=len(dates.periods.ends),
    )
    return ends[accrual_indices] - 1


def _as_decimal(minor: object) -> Decimal:
    return Decimal(int(minor)).scaleb(-4)


def expand_item(
    item: Item,
    kind: str,
    periods: PeriodIndex,
    dates: DateOps,
    cutover: date,
    params: dict[str, Decimal],
    policy: RoundingPolicy,
) -> Expansion:
    """Expand a generative item's segments into accrual and cash columns.

    Applies the canonical rounding order's first two steps — escalation then
    probability — before the settlement split. Occurrences dated before
    ``cutover`` are suppressed entirely, cash legs included: before cutover the
    ledger is the complete record (ADR-0004, DECISIONS D-P2-13).

    Returns an :class:`Expansion`; diagnostics are settlement warnings only,
    since every structural problem was caught at compile time.
    """
    length = len(periods)
    accrual = np.zeros(length, dtype=np.int64)
    cash = np.zeros(length, dtype=np.int64)
    diagnostics: list[Diagnostic] = []
    horizon_start = periods.starts[0]
    horizon_end = periods.ends[-1]
    cutover_ord = cutover.toordinal()

    for segment in item.segments:
        if segment.amount.schedule is not None:
            # The schedule's dates *are* the occurrences (DECISIONS D-P2-02).
            pairs = [
                (day.toordinal(), to_minor(value))
                for day, value in segment.amount.schedule
                if horizon_start <= day < horizon_end
            ]
            anchors = np.fromiter((p[0] for p in pairs), dtype=np.int64, count=len(pairs))
            base = np.fromiter((p[1] for p in pairs), dtype=np.int64, count=len(pairs))
        else:
            assert segment.amount.constant is not None
            anchors = occurrence_ordinals(segment, horizon_start, horizon_end)
            base = np.full(anchors.shape, to_minor(segment.amount.constant), dtype=np.int64)

        if anchors.size == 0:
            continue

        amounts = base
        if segment.escalation is not None:
            rate = segment.escalation.rate
            if isinstance(rate, str):
                rate = params[rate]
            amounts = _apply_escalation(
                amounts, anchors, segment, rate, horizon_end, policy
            )
        if segment.probability != Decimal(1):
            numerator, denominator = ratio_of(segment.probability)
            amounts = mul_ratio_array(amounts, numerator, denominator, policy)

        accrual_ords = dates.calendar.adjust_array(
            anchors, segment.recurrence.business_day_adjust
        )
        indices = periods.index_of_ordinals(accrual_ords)
        keep = (indices >= 0) & (accrual_ords >= cutover_ord)
        if not keep.any():
            continue
        accrual_ords = accrual_ords[keep]
        indices = indices[keep]
        amounts = amounts[keep]

        _scatter(accrual, indices, amounts)
        diagnostics.extend(
            settle_occurrences(
                item, kind, accrual_ords, amounts, indices, cash, dates, policy
            )
        )

    return Expansion(accrual=accrual, cash=cash, diagnostics=diagnostics)


def derived_accrual_ordinals(periods: PeriodIndex) -> np.ndarray:
    """Accrual dates for a derived item: its period's start date (D-P2-05).

    Returns an int64 ordinal array, one entry per period. No diagnostics.
    """
    return np.fromiter(
        (day.toordinal() for day in periods.starts), dtype=np.int64, count=len(periods)
    )


@dataclass(frozen=True)
class FoldSettlement:
    """One fold member's settlement, with its calendar work already done.

    A derived item accrues in every period, so where each leg lands is a fixed
    function of the period index. Resolving it once per run keeps the sequential
    fold free of calendar arithmetic, array allocation and scatter reductions —
    it was the single largest cost in the fold's profile.
    """

    item: Item
    kind: str
    #: One int64 array per :class:`DueTerm`: the period each leg lands in, or -1.
    targets: tuple[np.ndarray, ...]

    @classmethod
    def build(
        cls, item: Item, kind: str, periods: PeriodIndex, dates: DateOps
    ) -> "FoldSettlement":
        """Resolve every term's landing period over the whole horizon. No diagnostics."""
        if kind in (NEVER, INVALID, IMMEDIATE) or item.settlement is None:
            return cls(item=item, kind=kind, targets=())
        accrual_ords = derived_accrual_ordinals(periods)
        indices = np.arange(len(periods), dtype=np.int64)
        return cls(
            item=item,
            kind=kind,
            targets=tuple(
                leg_targets(term, accrual_ords, indices, dates)
                for term in item.settlement.due
            ),
        )

    @property
    def splits(self) -> bool:
        """True when settling needs the term split; ``False`` for immediate,
        never-settling and structurally invalid settlements. No diagnostics."""
        return bool(self.targets)

    def apply(
        self, period: int, amount: int, cash: np.ndarray, policy: RoundingPolicy
    ) -> list[Diagnostic]:
        """Settle one period's accrual into ``cash``, in place.

        Only called when :attr:`splits` is true — immediate settlement is one
        add and the fold does it inline. ``amount`` is int64 minor units.
        Returns the split's diagnostics (``CK-W001``, ``CK-W002``); raises
        :class:`~cashkit.engine.numeric.MoneyOverflowError` rather than letting
        a cash cell wrap.
        """
        legs, diagnostics = split_legs(
            self.item, self.kind, np.array([amount], dtype=np.int64), policy
        )
        for targets, leg in zip(self.targets, legs):
            target = int(targets[period])
            if target >= 0:
                add_minor(cash, target, int(leg[0]))
        return diagnostics


def add_minor(column: np.ndarray, index: int, value: int) -> None:
    """Add one minor-unit value into one cell, refusing to wrap.

    numpy's int64 addition wraps silently, so the check happens before the add.
    Produces no diagnostics; raises
    :class:`~cashkit.engine.numeric.MoneyOverflowError` on overflow.
    """
    total = int(column[index]) + value
    if not -INT64_MAX - 1 <= total <= INT64_MAX:
        from .numeric import MoneyOverflowError

        raise MoneyOverflowError(
            f"cash cell would reach {total} minor units, beyond int64"
        )
    column[index] = total


assert MINOR_SCALE == 10_000  # the 4 dp assumption baked into _as_decimal
