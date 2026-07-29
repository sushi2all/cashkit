"""Calendar arithmetic: durations, business days, periods, recurrences.

``add_months`` / ``add_duration`` are cross-checked against
``dateutil.relativedelta`` rather than against a second hand-rolled clamp, so the
Duration semantics of PRD §4.0 are verified by an independent implementation.

The weekday convention is load-bearing: DECISIONS conflict C-P1-01 records that
``CalendarSpec.weekend`` uses Python ``date.weekday()`` numbering, so the model's
default ``{5, 6}`` really is Saturday/Sunday.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from dateutil.relativedelta import relativedelta
from hypothesis import given, settings
from hypothesis import strategies as st

from cashkit.engine.calendars import (
    BusinessCalendar,
    PeriodIndex,
    add_duration,
    add_months,
    month_length,
    occurrence_dates,
)
from cashkit.model import CalendarSpec, Grain, PeriodRange, Recurrence


# --------------------------------------------------------------------------- #
# Duration arithmetic
# --------------------------------------------------------------------------- #


@given(
    anchor=st.dates(min_value=date(2000, 1, 1), max_value=date(2060, 12, 31)),
    months=st.integers(min_value=-120, max_value=120),
)
@settings(max_examples=300, deadline=None)
def test_add_months_matches_dateutil(anchor: date, months: int) -> None:
    assert add_months(anchor, months) == anchor + relativedelta(months=months)


def test_add_months_clamps_the_day_to_month_end() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)


@given(
    anchor=st.dates(min_value=date(2000, 1, 1), max_value=date(2060, 12, 31)),
    count=st.integers(min_value=0, max_value=99),
    unit=st.sampled_from("dwmy"),
)
@settings(max_examples=300, deadline=None)
def test_add_duration_matches_dateutil(anchor: date, count: int, unit: str) -> None:
    expected = {
        "d": lambda: anchor + relativedelta(days=count),
        "w": lambda: anchor + relativedelta(weeks=count),
        "m": lambda: anchor + relativedelta(months=count),
        "y": lambda: anchor + relativedelta(years=count),
    }[unit]()
    assert add_duration(anchor, f"{count}{unit}") == expected


def test_add_duration_rejects_a_malformed_string() -> None:
    with pytest.raises(ValueError):
        add_duration(date(2026, 1, 1), "30 days")


def test_leap_day_plus_one_year_clamps() -> None:
    assert add_duration(date(2024, 2, 29), "1y") == date(2025, 2, 28)


# --------------------------------------------------------------------------- #
# Business days
# --------------------------------------------------------------------------- #


def test_weekend_indices_are_python_weekday_numbering() -> None:
    """C-P1-01: the model default {5, 6} must mean Saturday and Sunday."""
    calendar = BusinessCalendar.from_spec(CalendarSpec())
    assert not calendar.is_business_day(date(2026, 1, 3))  # Saturday
    assert not calendar.is_business_day(date(2026, 1, 4))  # Sunday
    assert calendar.is_business_day(date(2026, 1, 2))  # Friday
    assert calendar.is_business_day(date(2026, 1, 5))  # Monday


def test_ordinal_weekday_formula_matches_date_weekday() -> None:
    """The vectorized path derives the weekday from the ordinal; prove it agrees."""
    calendar = BusinessCalendar.from_spec(CalendarSpec())
    days = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(400)]
    ordinals = np.array([day.toordinal() for day in days], dtype=np.int64)
    assert calendar.business_mask(ordinals).tolist() == [
        calendar.is_business_day(day) for day in days
    ]


def test_holidays_and_weekends_roll_in_both_directions() -> None:
    spec = CalendarSpec(holidays=[date(2026, 1, 1), date(2026, 1, 2)])
    calendar = BusinessCalendar.from_spec(spec)
    # Jan 1 (Thu) and Jan 2 (Fri) are holidays; Jan 3-4 is the weekend.
    assert calendar.adjust(date(2026, 1, 1), "next") == date(2026, 1, 5)
    assert calendar.adjust(date(2026, 1, 1), "prev") == date(2025, 12, 31)
    assert calendar.adjust(date(2026, 1, 1), "none") == date(2026, 1, 1)


def test_adjust_array_matches_the_scalar_path() -> None:
    spec = CalendarSpec(holidays=[date(2026, 4, 6), date(2026, 5, 1)])
    calendar = BusinessCalendar.from_spec(spec)
    days = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(200)]
    ordinals = np.array([day.toordinal() for day in days], dtype=np.int64)
    for mode in ("none", "prev", "next"):
        adjusted = calendar.adjust_array(ordinals, mode)
        assert [date.fromordinal(int(value)) for value in adjusted] == [
            calendar.adjust(day, mode) for day in days
        ]


# --------------------------------------------------------------------------- #
# Period index
# --------------------------------------------------------------------------- #


def test_day_grain_gives_one_period_per_day() -> None:
    periods = PeriodIndex.build(
        PeriodRange(start=date(2026, 1, 1), end=date(2031, 1, 1)), Grain.DAY
    )
    assert len(periods) == 1826  # the PRD's benchmark horizon
    assert periods.starts[0] == date(2026, 1, 1)
    assert periods.ends[-1] == date(2031, 1, 1)


@pytest.mark.parametrize(
    "grain,expected",
    [
        (Grain.DAY, 181),
        (Grain.WEEK, 26),
        (Grain.MONTH, 6),
        (Grain.QUARTER, 2),
        (Grain.YEAR, 1),
    ],
)
def test_period_counts_per_grain(grain: Grain, expected: int) -> None:
    periods = PeriodIndex.build(
        PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1)), grain
    )
    assert len(periods) == expected
    assert periods.starts[0] == date(2026, 1, 1)
    assert periods.ends[-1] == date(2026, 7, 1)


@pytest.mark.parametrize("grain", list(Grain))
def test_index_of_partitions_the_horizon(grain: Grain) -> None:
    horizon = PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1))
    periods = PeriodIndex.build(horizon, grain)
    for offset in range((horizon.end - horizon.start).days):
        day = horizon.start + timedelta(days=offset)
        index = periods.index_of(day)
        assert index is not None
        assert periods.starts[index] <= day < periods.ends[index]
    assert periods.index_of(horizon.start - timedelta(days=1)) is None
    assert periods.index_of(horizon.end) is None


@pytest.mark.parametrize("grain", list(Grain))
def test_index_of_ordinals_matches_the_scalar_path(grain: Grain) -> None:
    horizon = PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1))
    periods = PeriodIndex.build(horizon, grain)
    days = [date(2025, 12, 25) + timedelta(days=offset) for offset in range(200)]
    ordinals = np.array([day.toordinal() for day in days], dtype=np.int64)
    vectorized = periods.index_of_ordinals(ordinals).tolist()
    scalar = [
        -1 if periods.index_of(day) is None else periods.index_of(day) for day in days
    ]
    assert vectorized == scalar


def test_quarter_end_follows_the_fiscal_year_start() -> None:
    calendar_year = PeriodIndex.build(
        PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1)), Grain.DAY, 1
    )
    assert calendar_year.is_quarter_end(date(2026, 3, 31))
    assert calendar_year.is_quarter_end(date(2026, 6, 30))
    assert not calendar_year.is_quarter_end(date(2026, 3, 30))
    assert not calendar_year.is_quarter_end(date(2026, 5, 31))

    # A July fiscal year shares the calendar grid (7 == 1 mod 3). February does
    # not: its quarters close at the end of April, July, October and January.
    february_fiscal = PeriodIndex.build(
        PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1)), Grain.DAY, 2
    )
    assert february_fiscal.is_quarter_end(date(2026, 4, 30))
    assert february_fiscal.is_quarter_end(date(2026, 7, 31))
    assert february_fiscal.is_quarter_end(date(2026, 1, 31))
    assert not february_fiscal.is_quarter_end(date(2026, 3, 31))
    assert not february_fiscal.is_quarter_end(date(2026, 6, 30))


# --------------------------------------------------------------------------- #
# Recurrence expansion
# --------------------------------------------------------------------------- #


def test_recurrence_phase_follows_the_segment_not_the_horizon() -> None:
    """A segment open since 2025 still falls due on its own day of the month."""
    recurrence = Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=15)
    dates = occurrence_dates(
        recurrence, date(2025, 1, 15), None, date(2026, 1, 1), date(2026, 4, 1)
    )
    assert dates == [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)]


def test_day_of_month_clamps_past_month_end() -> None:
    recurrence = Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=31)
    dates = occurrence_dates(
        recurrence, date(2026, 1, 1), None, date(2026, 1, 1), date(2026, 5, 1)
    )
    assert dates == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


@pytest.mark.parametrize(
    "anchor,expected",
    [
        ("period_start", [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]),
        ("period_end", [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]),
        ("eom", [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]),
    ],
)
def test_monthly_anchors(anchor: str, expected: list[date]) -> None:
    recurrence = Recurrence(every=1, unit=Grain.MONTH, anchor=anchor)
    assert (
        occurrence_dates(
            recurrence, date(2026, 1, 1), date(2026, 4, 1), date(2026, 1, 1), date(2027, 1, 1)
        )
        == expected
    )


def test_adjacent_segments_partition_their_occurrences() -> None:
    """The window test uses the unadjusted anchor, so no occurrence is claimed
    twice and none is dropped at a segment boundary."""
    recurrence = Recurrence(every=1, unit=Grain.MONTH, anchor="eom")
    first = occurrence_dates(
        recurrence, date(2026, 1, 1), date(2026, 4, 1), date(2026, 1, 1), date(2026, 7, 1)
    )
    second = occurrence_dates(
        recurrence, date(2026, 4, 1), None, date(2026, 1, 1), date(2026, 7, 1)
    )
    assert not set(first) & set(second)
    assert first + second == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_every_n_units_steps_correctly() -> None:
    quarterly = Recurrence(every=1, unit=Grain.QUARTER)
    assert occurrence_dates(
        quarterly, date(2026, 1, 1), None, date(2026, 1, 1), date(2027, 1, 1)
    ) == [date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)]

    fortnightly = Recurrence(every=2, unit=Grain.WEEK)
    assert occurrence_dates(
        fortnightly, date(2026, 1, 1), None, date(2026, 1, 1), date(2026, 2, 1)
    ) == [date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 29)]


def test_month_length_handles_leap_years() -> None:
    assert month_length(2024, 2) == 29
    assert month_length(2026, 2) == 28
    assert month_length(2026, 12) == 31
