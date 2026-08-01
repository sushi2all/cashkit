"""Vectorized expansion agrees with the scalar definitions it replaces.

Three places in Phase 3 replace a readable scalar rule with an array form for
speed. Each is only safe if the two agree everywhere, not merely on the fixture:

* occurrence generation — an integer-stride fast path for day/week cadences on a
  period boundary, against :func:`~cashkit.engine.calendars.occurrence_dates`;
* escalation steps — a binary search over anniversary boundaries, against the
  reference engine's naive walk of the same boundaries;
* settlement — :func:`~cashkit.engine.expand.split_legs` and
  :func:`~cashkit.engine.expand.leg_targets`, shared by the whole-horizon path
  and the sequential fold, so the fold cannot drift from the column path.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from cashkit.engine.calendars import (
    BusinessCalendar,
    PeriodIndex,
    occurrence_dates,
)
from cashkit.engine.expand import (
    FIXED,
    IMMEDIATE,
    INVALID,
    NEVER,
    SHARES,
    DateOps,
    FoldSettlement,
    classify_settlement,
    escalation_boundary,
    escalation_steps_array,
    leg_targets,
    occurrence_ordinals,
    settle_occurrences,
    split_legs,
)
from cashkit.engine.numeric import RoundingPolicy
from cashkit.model import (
    Amount,
    CalendarSpec,
    DueTerm,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
)
from cashkit.reference.engine import _escalation_steps

HORIZON_START = date(2026, 1, 1)
HORIZON_END = date(2027, 1, 1)

dates_in_range = st.dates(min_value=date(2020, 1, 1), max_value=date(2028, 12, 31))


# --------------------------------------------------------------------------- #
# Occurrence generation
# --------------------------------------------------------------------------- #


@given(
    start=dates_in_range,
    length=st.one_of(st.none(), st.integers(1, 900)),
    unit=st.sampled_from(list(Grain)),
    every=st.integers(1, 5),
    anchor=st.sampled_from(["period_start", "period_end", "eom", "day_of_month"]),
    day=st.integers(1, 31),
    adjust=st.sampled_from(["none", "prev", "next"]),
)
@settings(max_examples=400, deadline=None)
def test_ordinal_fast_path_matches_the_scalar_generator(
    start: date,
    length: int | None,
    unit: Grain,
    every: int,
    anchor: str,
    day: int,
    adjust: str,
) -> None:
    """Whatever route a recurrence takes, it produces the same anchor dates."""
    end = None if length is None else date.fromordinal(start.toordinal() + length)
    recurrence = Recurrence(
        every=every,
        unit=unit,
        anchor=anchor,
        day=day if anchor == "day_of_month" else None,
        business_day_adjust=adjust,
    )
    segment = Segment(
        start=start,
        end=end,
        recurrence=recurrence,
        amount=Amount(constant=Decimal("1000")),
    )
    fast = occurrence_ordinals(segment, HORIZON_START, HORIZON_END)
    scalar = [
        day_.toordinal()
        for day_ in occurrence_dates(recurrence, start, end, HORIZON_START, HORIZON_END)
    ]
    assert fast.tolist() == scalar


def test_ordinals_are_ascending_and_inside_the_horizon() -> None:
    segment = Segment(
        start=date(2025, 6, 15),
        end=None,
        recurrence=Recurrence(every=3, unit=Grain.DAY),
        amount=Amount(constant=Decimal("1")),
    )
    ordinals = occurrence_ordinals(segment, HORIZON_START, HORIZON_END)
    assert ordinals.size
    assert (np.diff(ordinals) > 0).all()
    assert ordinals[0] >= HORIZON_START.toordinal()
    assert ordinals[-1] < HORIZON_END.toordinal()


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #


@given(
    segment_start=dates_in_range,
    anchor=st.sampled_from(["segment_start", "calendar_year"]),
    every_years=st.integers(1, 4),
    offsets=st.lists(st.integers(0, 1500), min_size=1, max_size=12),
)
@settings(max_examples=300, deadline=None)
def test_binary_search_steps_match_the_reference_walk(
    segment_start: date, anchor: str, every_years: int, offsets: list[int]
) -> None:
    """ADR-0002's factor table is only shared if both engines index it the same."""
    occurrences = sorted(
        {segment_start.toordinal() + offset for offset in offsets}
    )
    horizon_end = date.fromordinal(max(occurrences) + 1)
    array = escalation_steps_array(
        anchor,
        every_years,
        segment_start,
        np.array(occurrences, dtype=np.int64),
        horizon_end,
    )
    walked = [
        _escalation_steps(anchor, every_years, segment_start, date.fromordinal(ordinal))
        for ordinal in occurrences
    ]
    assert array.tolist() == walked


def test_escalation_boundary_clamps_a_leap_day_anniversary() -> None:
    """29 February's anniversary is 28 February, the same clamp Duration uses."""
    assert escalation_boundary("segment_start", 1, date(2024, 2, 29), 1) == date(
        2025, 2, 28
    )
    assert escalation_boundary("segment_start", 1, date(2024, 2, 29), 4) == date(
        2028, 2, 29
    )
    assert escalation_boundary("calendar_year", 2, date(2026, 5, 3), 3) == date(
        2032, 1, 1
    )


# --------------------------------------------------------------------------- #
# Settlement classification and splitting
# --------------------------------------------------------------------------- #


def _item(settlement: Settlement | None) -> Item:
    return Item(
        id="probe",
        name="probe",
        kind="flow",
        segments=[
            Segment(
                start=HORIZON_START,
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal("1000")),
            )
        ],
        settlement=settlement,
    )


def test_classification_refuses_to_guess() -> None:
    assert classify_settlement(_item(None))[0] == IMMEDIATE
    assert classify_settlement(_item(Settlement(due=[])))[0] == NEVER
    assert classify_settlement(_item(Settlement.net(30)))[0] == SHARES
    fixed = Settlement(
        due=[
            DueTerm(amount=Decimal("100"), offset="0d"),
            DueTerm(remainder=True, offset="30d"),
        ]
    )
    assert classify_settlement(_item(fixed))[0] == FIXED
    short = Settlement.split([(Decimal("0.3"), "0d"), (Decimal("0.6"), "30d")])
    kind, diagnostics = classify_settlement(_item(short))
    assert kind == INVALID and diagnostics[0].code == "CK-E004"
    mixed = Settlement(
        due=[
            DueTerm(share=Decimal("0.5"), offset="0d"),
            DueTerm(amount=Decimal("1"), offset="0d"),
        ]
    )
    kind, diagnostics = classify_settlement(_item(mixed))
    assert kind == INVALID and diagnostics[0].code == "CK-E005"


@given(
    amounts=st.lists(
        st.integers(-10**9, 10**9), min_size=1, max_size=40
    ),
    first=st.sampled_from(
        [Decimal("0.1"), Decimal("0.3333"), Decimal("0.5"), Decimal("0.9999")]
    ),
    policy=st.sampled_from(list(RoundingPolicy)),
)
@settings(max_examples=200, deadline=None)
def test_share_legs_sum_to_the_accrual_exactly(
    amounts: list[int], first: Decimal, policy: RoundingPolicy
) -> None:
    """ADR-0003: the last share term absorbs the rounding residual."""
    settlement = Settlement(
        due=[
            DueTerm(share=first, offset="0d"),
            DueTerm(share=Decimal("0.05"), offset="30d"),
            DueTerm(share=Decimal(1) - first - Decimal("0.05"), offset="60d"),
        ]
    )
    item = _item(settlement)
    assume(classify_settlement(item)[0] == SHARES)
    values = np.array(amounts, dtype=np.int64)
    legs, _ = split_legs(item, SHARES, values, policy)
    assert np.array_equal(sum(legs), values)


@given(
    amounts=st.lists(st.integers(-10**7, 10**7), min_size=1, max_size=30),
)
@settings(max_examples=200, deadline=None)
def test_fixed_legs_never_flip_sign_and_the_remainder_absorbs(
    amounts: list[int],
) -> None:
    """PRD §4.4: a negative accrual routes entirely through the remainder."""
    settlement = Settlement(
        due=[
            DueTerm(amount=Decimal("500"), offset="0d"),
            DueTerm(remainder=True, offset="30d"),
        ]
    )
    item = _item(settlement)
    values = np.array(amounts, dtype=np.int64)
    legs, diagnostics = split_legs(item, FIXED, values, RoundingPolicy.HALF_UP)
    fixed, remainder = legs
    negative = values < 0
    assert (fixed[negative] == 0).all()
    assert np.array_equal(remainder[negative], values[negative])
    assert (fixed[~negative] == 5_000_000).all()
    assert (remainder >= np.minimum(values, 0)).all()
    codes = {diagnostic.code for diagnostic in diagnostics}
    if negative.any():
        assert "CK-W002" in codes
    if ((values >= 0) & (values < 5_000_000)).any():
        assert "CK-W001" in codes


# --------------------------------------------------------------------------- #
# The fold plan reproduces the whole-horizon settlement path
# --------------------------------------------------------------------------- #

_SETTLEMENTS = [
    None,
    Settlement(due=[]),
    Settlement.net(45),
    Settlement.split([(Decimal("0.35"), "0d"), (Decimal("0.65"), "2m")]),
    Settlement(
        due=[
            DueTerm(share=Decimal("0.5"), offset="1w", basis="month_end", adjust="prev"),
            DueTerm(
                share=Decimal("0.5"),
                offset="1y",
                basis="period_end",
                adjust="next",
                withholding=Decimal("0.2"),
            ),
        ]
    ),
    Settlement(
        due=[
            DueTerm(amount=Decimal("2500"), offset="0d", basis="month_end"),
            DueTerm(remainder=True, offset="30d", adjust="next", withholding=Decimal("0.04")),
        ]
    ),
    Settlement(
        due=[
            DueTerm(amount=Decimal("999999"), offset="0d"),
            DueTerm(remainder=True, offset="0d"),
        ]
    ),
]


def test_fold_settlement_plan_matches_the_whole_horizon_path() -> None:
    """The fold settles one period at a time; the column path settles the whole
    horizon at once. Same legs, same landing periods, same diagnostics."""
    periods = PeriodIndex.build(
        PeriodRange(start=HORIZON_START, end=HORIZON_END), Grain.DAY, 1
    )
    calendar = BusinessCalendar.from_spec(
        CalendarSpec(holidays=[date(2026, 4, 6), date(2026, 12, 25)], weekend={5, 6})
    )
    dates = DateOps(periods=periods, calendar=calendar)
    length = len(periods)
    rng = np.random.default_rng(7)
    accruals = rng.integers(-3_000_000, 8_000_000, length, dtype=np.int64)
    accruals[::11] = 0
    ordinals = np.fromiter(
        (day.toordinal() for day in periods.starts), dtype=np.int64, count=length
    )
    indices = np.arange(length, dtype=np.int64)

    for settlement in _SETTLEMENTS:
        item = _item(settlement)
        kind, _ = classify_settlement(item)
        whole = np.zeros(length, dtype=np.int64)
        column_diagnostics = settle_occurrences(
            item, kind, ordinals, accruals, indices, whole, dates, RoundingPolicy.HALF_UP
        )
        folded = np.zeros(length, dtype=np.int64)
        plan = FoldSettlement.build(item, kind, periods, dates)
        fold_codes: set[str] = set()
        for period in range(length):
            value = int(accruals[period])
            if kind in (NEVER, INVALID):
                continue
            if not plan.splits:
                folded[period] += value
                continue
            for diagnostic in plan.apply(
                period, value, folded, RoundingPolicy.HALF_UP
            ):
                fold_codes.add(diagnostic.code)
        assert np.array_equal(whole, folded), f"{settlement} diverged"
        assert fold_codes == {d.code for d in column_diagnostics}


def test_leg_targets_are_stable_and_flag_the_horizon_edges() -> None:
    periods = PeriodIndex.build(
        PeriodRange(start=HORIZON_START, end=date(2026, 3, 1)), Grain.DAY, 1
    )
    dates = DateOps(
        periods=periods,
        calendar=BusinessCalendar.from_spec(CalendarSpec(weekend={5, 6})),
    )
    length = len(periods)
    ordinals = np.fromiter(
        (day.toordinal() for day in periods.starts), dtype=np.int64, count=length
    )
    indices = np.arange(length, dtype=np.int64)
    late = leg_targets(DueTerm(share=Decimal(1), offset="1y"), ordinals, indices, dates)
    assert (late == -1).all(), "a leg a year out cannot land inside a two-month horizon"
    same = leg_targets(DueTerm(share=Decimal(1), offset="0d"), ordinals, indices, dates)
    assert same.tolist() == indices.tolist()
