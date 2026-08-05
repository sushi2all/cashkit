"""Deterministic calendar arithmetic: periods, durations, business days.

Nothing here reads the wall clock. The holiday set is the resolved list stored
on :class:`~cashkit.model.CalendarSpec` (ADR-0010) — the ``holidays`` package is
a seed used at book creation, never at evaluation time.

Weekday numbering follows Python ``date.weekday()`` (Mon=0 ... Sun=6), so the
model's default ``weekend={5, 6}`` is Saturday/Sunday. The PRD's "ISO weekday"
label contradicts its own stated default; see DECISIONS.md conflict C-P1-01.
"""

from __future__ import annotations

import calendar as _calendar
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

import numpy as np

from cashkit.model import CalendarSpec, Grain, PeriodRange, Recurrence

__all__ = [
    "BusinessCalendar",
    "GRAIN_COLUMN",
    "PeriodIndex",
    "add_duration",
    "add_months",
    "bucket_of",
    "month_length",
    "occurrence_dates",
    "parse_duration",
]

_DURATION_RE = re.compile(r"^(0|[1-9][0-9]*)([dwmy])$")

#: Months per step for each recurrence unit that steps in months.
_MONTHS_PER_UNIT = {Grain.MONTH: 1, Grain.QUARTER: 3, Grain.YEAR: 12}
#: Days per step for each recurrence unit that steps in days.
_DAYS_PER_UNIT = {Grain.DAY: 1, Grain.WEEK: 7}

#: A business-day adjustment never walks further than this. A gap this long
#: means the holiday list is wrong; refusing beats looping.
_MAX_BUSINESS_DAY_WALK = 400


@lru_cache(maxsize=8192)
def month_length(year: int, month: int) -> int:
    """Return the number of days in ``month`` of ``year``. No diagnostics.

    Memoized: the engine asks this once per period while building its lookup
    tables, and the distinct ``(year, month)`` count is tiny.
    """
    return _calendar.monthrange(year, month)[1]


def add_months(anchor: date, months: int) -> date:
    """Add calendar ``months`` to ``anchor``, clamping the day to month end.

    ``2026-01-31 + 1m`` is ``2026-02-28`` — the Duration semantics of PRD §4.0.
    Returns a ``date``; produces no diagnostics.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(anchor.day, month_length(year, month)))


def parse_duration(duration: str) -> tuple[int, str]:
    """Split a ``Duration`` string into ``(count, unit)``.

    Returns e.g. ``(30, "d")``. Raises ``ValueError`` on a malformed string —
    the model layer already constrains the pattern, so that is programmer error.
    Produces no diagnostics.
    """
    match = _DURATION_RE.match(duration)
    if match is None:
        raise ValueError(f"malformed Duration {duration!r}; expected <n>d|w|m|y")
    return int(match.group(1)), match.group(2)


def add_duration(anchor: date, duration: str) -> date:
    """Offset ``anchor`` by a ``Duration`` with calendar semantics.

    Days and weeks are exact; months and years clamp the day to month end.
    Returns a ``date``; produces no diagnostics.
    """
    count, unit = parse_duration(duration)
    if unit == "d":
        return anchor + timedelta(days=count)
    if unit == "w":
        return anchor + timedelta(days=7 * count)
    if unit == "m":
        return add_months(anchor, count)
    return add_months(anchor, 12 * count)


# --------------------------------------------------------------------------- #
# Business days
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BusinessCalendar:
    """Weekend and holiday rules, addressed by proleptic Gregorian ordinal.

    Ordinals are used throughout the vectorized engine so the whole calendar is
    integer arithmetic; ``date.fromordinal(1)`` is a Monday, hence
    ``weekday == (ordinal - 1) % 7``.
    """

    weekend: frozenset[int]
    holiday_ordinals: frozenset[int]

    @classmethod
    def from_spec(cls, spec: CalendarSpec) -> "BusinessCalendar":
        """Build from a :class:`CalendarSpec`. Returns a BusinessCalendar; no
        diagnostics."""
        return cls(
            weekend=frozenset(spec.weekend),
            holiday_ordinals=frozenset(day.toordinal() for day in spec.holidays),
        )

    def is_business_day(self, day: date) -> bool:
        """True when ``day`` is neither a weekend day nor a holiday. No diagnostics."""
        return day.weekday() not in self.weekend and day.toordinal() not in self.holiday_ordinals

    def adjust(self, day: date, mode: str) -> date:
        """Roll ``day`` off a non-business day per ``mode`` (``none``/``prev``/``next``).

        Returns the adjusted ``date``. Raises ``ValueError`` if no business day
        is found within :data:`_MAX_BUSINESS_DAY_WALK` days (a corrupt holiday
        list). Produces no diagnostics.
        """
        if mode == "none":
            return day
        step = -1 if mode == "prev" else 1
        current = day
        for _ in range(_MAX_BUSINESS_DAY_WALK):
            if self.is_business_day(current):
                return current
            current = current + timedelta(days=step)
        raise ValueError(
            f"no business day within {_MAX_BUSINESS_DAY_WALK} days of {day} "
            f"walking {mode}; check the resolved holiday list"
        )

    def business_mask(self, ordinals: np.ndarray) -> np.ndarray:
        """Vectorized :meth:`is_business_day` over an ordinal array. No diagnostics."""
        weekday = (ordinals - 1) % 7
        mask = np.ones(ordinals.shape, dtype=bool)
        for index in sorted(self.weekend):
            mask &= weekday != index
        if self.holiday_ordinals:
            holidays = np.fromiter(sorted(self.holiday_ordinals), dtype=np.int64)
            mask &= ~np.isin(ordinals, holidays)
        return mask

    def adjust_array(self, ordinals: np.ndarray, mode: str) -> np.ndarray:
        """Vectorized :meth:`adjust` over an ordinal array. Returns an int64
        array. Raises ``ValueError`` on an unresolvable walk; no diagnostics."""
        if mode == "none" or ordinals.size == 0:
            return ordinals
        step = -1 if mode == "prev" else 1
        current = ordinals.astype(np.int64, copy=True)
        for _ in range(_MAX_BUSINESS_DAY_WALK):
            bad = ~self.business_mask(current)
            if not bad.any():
                return current
            current[bad] += step
        raise ValueError(
            f"business-day adjustment did not converge within "
            f"{_MAX_BUSINESS_DAY_WALK} days; check the resolved holiday list"
        )


# --------------------------------------------------------------------------- #
# Period index
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PeriodIndex:
    """The horizon cut into base-grain periods.

    Period ``t`` spans ``[starts[t], ends[t])``. The base grain is DAY (D1);
    coarser base grains bucket forward from the horizon start, so a book whose
    horizon does not begin on a natural boundary still gets a clean partition.
    """

    grain: Grain
    starts: tuple[date, ...]
    ends: tuple[date, ...]
    fiscal_year_start_month: int

    @classmethod
    def build(
        cls, horizon: PeriodRange, grain: Grain, fiscal_year_start_month: int = 1
    ) -> "PeriodIndex":
        """Cut ``horizon`` into periods of ``grain``. Returns a PeriodIndex;
        produces no diagnostics."""
        starts: list[date] = []
        cursor = horizon.start
        step_months = _MONTHS_PER_UNIT.get(grain)
        step_days = _DAYS_PER_UNIT.get(grain)
        index = 0
        while cursor < horizon.end:
            starts.append(cursor)
            index += 1
            if step_days is not None:
                cursor = horizon.start + timedelta(days=step_days * index)
            else:
                assert step_months is not None
                cursor = add_months(horizon.start, step_months * index)
        ends = [*starts[1:], horizon.end]
        return cls(
            grain=grain,
            starts=tuple(starts),
            ends=tuple(ends),
            fiscal_year_start_month=fiscal_year_start_month,
        )

    def __len__(self) -> int:
        return len(self.starts)

    @property
    def start_ordinals(self) -> np.ndarray:
        """Period start dates as an int64 ordinal array. No diagnostics."""
        return np.fromiter((day.toordinal() for day in self.starts), dtype=np.int64,
                           count=len(self.starts))

    def index_of(self, day: date) -> int | None:
        """Return the period containing ``day``, or ``None`` if outside the horizon.

        Produces no diagnostics.
        """
        if not self.starts or day < self.starts[0] or day >= self.ends[-1]:
            return None
        if self.grain is Grain.DAY:
            return (day - self.starts[0]).days
        return bisect_right(self.starts, day) - 1

    def index_of_ordinals(self, ordinals: np.ndarray) -> np.ndarray:
        """Vectorized :meth:`index_of`; out-of-horizon dates map to ``-1``.

        Returns an int64 array. Produces no diagnostics.
        """
        if ordinals.size == 0:
            return ordinals.astype(np.int64)
        first = self.starts[0].toordinal()
        last = self.ends[-1].toordinal()
        inside = (ordinals >= first) & (ordinals < last)
        if self.grain is Grain.DAY:
            positions = ordinals - first
        else:
            positions = np.searchsorted(self.start_ordinals, ordinals, side="right") - 1
        return np.where(inside, positions, -1).astype(np.int64)

    def is_quarter_end(self, day: date) -> bool:
        """True when ``day`` closes a fiscal quarter.

        Quarters run from ``fiscal_year_start_month``, so an entity whose fiscal
        year starts in July closes quarters in September, December, March, June.
        Produces no diagnostics.
        """
        if day.day != month_length(day.year, day.month):
            return False
        return (day.month - self.fiscal_year_start_month) % 3 == 2


# --------------------------------------------------------------------------- #
# Aggregation buckets (Phase 8)
# --------------------------------------------------------------------------- #

#: Short name of each grain's bucket, used by the frame store's period
#: dimension. Kept here, next to ``PeriodIndex.is_quarter_end``, because both
#: state the same fiscal convention and a second statement of it elsewhere is
#: exactly how the two would drift apart.
GRAIN_COLUMN = {
    Grain.DAY: "day",
    Grain.WEEK: "week",
    Grain.MONTH: "month",
    Grain.QUARTER: "quarter",
    Grain.YEAR: "year",
}


def bucket_of(day: date, grain: Grain, fiscal_year_start_month: int = 1) -> tuple[date, date]:
    """Return the half-open ``[start, end)`` bucket of ``grain`` containing ``day``.

    Buckets are **calendar-aligned**, not horizon-aligned: a month is a calendar
    month, and quarters and years follow ``fiscal_year_start_month`` exactly as
    :meth:`PeriodIndex.is_quarter_end` and the VAT return periods do. Weeks
    start on Monday, matching the ``date.weekday()`` numbering used throughout
    (DECISIONS C-P1-01). Buckets are not clipped to the horizon: the bucket
    identifies the calendar period, and a partial one at either edge is
    information, not an error.

    This is aggregation of a frame to a coarser grain, which is a different job
    from :meth:`PeriodIndex.build`'s partitioning of a horizon into base-grain
    periods — that one steps from the horizon start, because the base grain
    defines the model's own periods rather than describing a calendar.

    Returns ``(start, end)`` with ``end`` exclusive. Raises ``ValueError`` for an
    unknown grain (programmer error); produces no diagnostics.
    """
    if grain is Grain.DAY:
        return day, day + timedelta(days=1)
    if grain is Grain.WEEK:
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    first_of_month = date(day.year, day.month, 1)
    if grain is Grain.MONTH:
        return first_of_month, add_months(first_of_month, 1)
    offset = (day.month - fiscal_year_start_month) % 12
    if grain is Grain.QUARTER:
        start = add_months(first_of_month, -(offset % 3))
        return start, add_months(start, 3)
    if grain is Grain.YEAR:
        start = add_months(first_of_month, -offset)
        return start, add_months(start, 12)
    raise ValueError(f"unknown grain {grain!r}")


# --------------------------------------------------------------------------- #
# Recurrence expansion
# --------------------------------------------------------------------------- #


def _anchor_date(recurrence: Recurrence, bucket_start: date, next_bucket_start: date) -> date:
    anchor = recurrence.anchor
    if anchor == "period_start":
        return bucket_start
    if anchor == "period_end":
        return next_bucket_start - timedelta(days=1)
    if anchor == "eom":
        return date(
            bucket_start.year,
            bucket_start.month,
            month_length(bucket_start.year, bucket_start.month),
        )
    assert recurrence.day is not None  # guaranteed by the model
    return date(
        bucket_start.year,
        bucket_start.month,
        min(recurrence.day, month_length(bucket_start.year, bucket_start.month)),
    )


def occurrence_dates(
    recurrence: Recurrence,
    segment_start: date,
    segment_end: date | None,
    horizon_start: date,
    horizon_end: date,
) -> list[date]:
    """Generate a segment's unadjusted anchor dates inside the horizon.

    Buckets step from ``segment_start`` — always, even when the segment begins
    before the horizon, because the segment's own start defines the recurrence
    *phase*. A rent segment running since 2025 still falls due on the 1st, not on
    whatever day the horizon happens to open.

    Anchors are kept when they fall inside both the segment window
    ``[segment_start, segment_end)`` and the horizon. The segment window is
    evaluated on the *unadjusted* anchor, so adjacent segments partition their
    occurrences cleanly however business-day rolls move the effective dates.

    Accruals outside the horizon are outside the model and are not generated
    (DECISIONS D-P2-03). Returns dates in ascending order; produces no
    diagnostics.
    """
    stop = horizon_end if segment_end is None else min(segment_end, horizon_end)
    if stop <= segment_start or stop <= horizon_start:
        return []
    step_months = _MONTHS_PER_UNIT.get(recurrence.unit)
    step_days = _DAYS_PER_UNIT.get(recurrence.unit)

    # Skip whole buckets that cannot reach the horizon, arithmetically: a segment
    # opened years before the horizon must not cost years of iteration.
    if step_days is not None:
        stride = step_days * recurrence.every
        first = max(0, (horizon_start - segment_start).days // stride - 1)
    else:
        assert step_months is not None
        stride = step_months * recurrence.every
        elapsed = (horizon_start.year - segment_start.year) * 12 + (
            horizon_start.month - segment_start.month
        )
        first = max(0, elapsed // stride - 1)

    dates: list[date] = []
    index = first
    while True:
        if step_days is not None:
            bucket_start = segment_start + timedelta(days=stride * index)
            next_start = segment_start + timedelta(days=stride * (index + 1))
        else:
            bucket_start = add_months(segment_start, stride * index)
            next_start = add_months(segment_start, stride * (index + 1))
        if bucket_start >= stop:
            break
        anchor = _anchor_date(recurrence, bucket_start, next_start)
        if segment_start <= anchor < stop and horizon_start <= anchor:
            dates.append(anchor)
        index += 1
    return dates
