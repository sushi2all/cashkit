"""Phase 5 gate, second half: the fact union and the cutover boundary.

The gate: a book with cutover mid-horizon shows actuals before and forecast
after, with **no double-count and no gap at the boundary**, verified by a
total-sum invariant.

The invariant used here is the strongest form available: build the same economic
world at every possible cutover position, with the ledger carrying exactly the
reconciled months, and assert the whole-horizon totals do not move. A
double-count makes a total grow as cutover advances; a gap makes it shrink. Both
engines are checked, because a boundary bug that agreed between them would still
be a boundary bug.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pytest

import cashkit.engine as engine
import cashkit.reference as reference
from cashkit.engine.facts import SYNTHETIC_EVENT_PREFIX, resolve_facts
from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    DueTerm,
    Event,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
)

START = date(2026, 1, 1)
END = date(2026, 7, 1)
MONTHS = [date(2026, month, 1) for month in range(1, 7)]
CALENDAR = CalendarSpec(fiscal_year_start_month=1, country="IT", holidays=[])

RENT = Decimal("-4200.00")
FEES = Decimal("18000.00")


def _monthly(item_id: str, amount: Decimal, settlement: Settlement | None) -> Item:
    return Item(
        id=item_id,
        name=item_id,
        kind="flow",
        tags={"cat": "revenue" if amount > 0 else "cost"},
        flags={"cashflow"},
        segments=[
            Segment(
                start=START,
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=amount),
            )
        ],
        settlement=settlement,
    )


NET_60 = Settlement(due=[DueTerm(share=Decimal(1), offset="60d")])


def _book(cutover: date, *, extra: list[Item] | None = None) -> Book:
    items = [
        _monthly("fees", FEES, NET_60),
        _monthly("rent", RENT, None),
        *(extra or []),
    ]
    return Book(
        id="boundary",
        base_grain=Grain.DAY,
        calendar=CALENDAR,
        horizon=PeriodRange(start=START, end=END),
        opening_balance=Decimal("50000"),
        cutover=cutover,
        params={},
        items={item.id: item for item in items},
    )


def _reconciled_events(cutover: date) -> list[Event]:
    """One actual per item per month strictly before ``cutover``.

    They carry exactly what generation would have produced, so the ledger is a
    faithful record of the reconciled past — which is what makes the total-sum
    invariant a statement about the boundary rule and not about the numbers.
    """
    events: list[Event] = []
    for month in MONTHS:
        if month >= cutover:
            continue
        for item_id, amount in (("fees", FEES), ("rent", RENT)):
            events.append(
                Event(
                    id=f"{item_id}-{month:%Y%m}",
                    date=month,
                    amount=amount,
                    status="actual",
                    item=item_id,
                    source="bank",
                    ext_id=f"{item_id}-{month:%Y%m}",
                )
            )
    return events


CUTOVERS = [START, *MONTHS[1:], END]


# --------------------------------------------------------------------------- #
# The gate: the total-sum invariant across the boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("runner", [engine.run, reference.run], ids=["vector", "oracle"])
@pytest.mark.parametrize("cutover", CUTOVERS, ids=lambda d: d.isoformat())
def test_total_sum_is_invariant_to_where_the_cutover_sits(runner, cutover) -> None:
    """Reconciling a month must not change the world, only its provenance."""
    baseline = runner(_book(START), events=())
    actual = runner(_book(cutover), events=_reconciled_events(cutover))
    for item_id in ("fees", "rent"):
        for measure in ("accrual", "cash"):
            assert actual.total(item_id, measure) == baseline.total(item_id, measure), (
                f"{item_id}.{measure} moved when cutover advanced to {cutover}"
            )


@pytest.mark.parametrize("cutover", CUTOVERS, ids=lambda d: d.isoformat())
def test_no_gap_and_no_double_count_period_by_period(cutover) -> None:
    """Cell-level, not just in total: every month accrues exactly once."""
    baseline = engine.run(_book(START))
    actual = engine.run(_book(cutover), events=_reconciled_events(cutover))
    for item_id in ("fees", "rent"):
        assert np.array_equal(
            baseline.column(item_id, "accrual"), actual.column(item_id, "accrual")
        ), f"{item_id} accrual differs cell-by-cell at cutover {cutover}"
        assert np.array_equal(
            baseline.column(item_id, "cash"), actual.column(item_id, "cash")
        ), f"{item_id} cash differs cell-by-cell at cutover {cutover}"


@pytest.mark.parametrize("cutover", CUTOVERS[1:-1], ids=lambda d: d.isoformat())
def test_before_cutover_the_ledger_is_the_only_source(cutover) -> None:
    """Generation is suppressed for *all* items before cutover (ADR-0004), so a
    reconciled month with an empty ledger is empty — not quietly forecast."""
    result = engine.run(_book(cutover), events=())
    starts = result.periods.starts
    for index, start in enumerate(starts):
        if start < cutover:
            assert int(result.column("fees", "accrual")[index]) == 0
            assert int(result.column("rent", "accrual")[index]) == 0


def test_a_missing_actual_shows_as_a_hole_rather_than_a_forecast() -> None:
    """The counterpart: an unreconciled month is visibly missing, which is what
    makes reconcile() a real check rather than a formality."""
    cutover = date(2026, 4, 1)
    events = [e for e in _reconciled_events(cutover) if not e.id.startswith("fees-202602")]
    result = engine.run(_book(cutover), events=events)
    baseline = engine.run(_book(START))
    assert result.total("fees", "accrual") == baseline.total("fees", "accrual") - FEES


# --------------------------------------------------------------------------- #
# The union precedes derived evaluation (non-negotiable #4)
# --------------------------------------------------------------------------- #

AGGREGATOR = Item(
    id="revenue_total",
    name="Revenue total",
    kind="derived",
    tags={"cat": "derived"},
    formula='agg(tag="cat:revenue", measure="accrual")',
)


@pytest.mark.parametrize("runner", [engine.run, reference.run], ids=["vector", "oracle"])
def test_agg_sees_ledger_actuals(runner) -> None:
    """If agg() cannot see actuals, every derived item is wrong."""
    cutover = date(2026, 4, 1)
    book = _book(cutover, extra=[AGGREGATOR])
    with_ledger = runner(book, events=_reconciled_events(cutover))
    assert with_ledger.total("revenue_total", "accrual") == with_ledger.total(
        "fees", "accrual"
    )
    assert with_ledger.total("revenue_total", "accrual") == FEES * 6


def test_an_unattached_event_becomes_a_synthetic_item_agg_can_see() -> None:
    """An event with no item still has dimensions, so it still has a column."""
    book = _book(START, extra=[AGGREGATOR])
    fee = Event(
        id="bankfee-1",
        date=date(2026, 2, 10),
        amount=Decimal("2500"),
        status="actual",
        tags={"cat": "revenue", "customer": "walkin"},
    )
    result = engine.run(book, events=[fee])
    synthetic = [i for i in result.accrual if i.startswith(SYNTHETIC_EVENT_PREFIX)]
    assert len(synthetic) == 1
    assert result.total(synthetic[0], "accrual") == Decimal("2500")
    assert result.total("revenue_total", "accrual") == FEES * 6 + Decimal("2500")


def test_unattached_events_sharing_dimensions_share_one_column() -> None:
    """The synthetic id is a function of the dimensions, not of the event id, so
    a thousand bank-fee rows are one row in the frame, and the id is stable."""
    events = [
        Event(
            id=f"fee-{n}",
            date=date(2026, 2, 1 + n),
            amount=Decimal("10"),
            status="actual",
            tags={"cat": "bank"},
        )
        for n in range(5)
    ]
    first = resolve_facts(_book(START), events)
    assert len(first.synthetic_items) == 1
    assert len({fact.target for fact in first.facts}) == 1
    # Stable across runs and across which events are present.
    second = resolve_facts(_book(START), events[:2])
    assert set(second.synthetic_items) == set(first.synthetic_items)

    other = resolve_facts(
        _book(START), [events[0].model_copy(update={"tags": {"cat": "other"}})]
    )
    assert set(other.synthetic_items) != set(first.synthetic_items)


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #


def test_actual_on_or_after_cutover_raises_ck_w003_and_still_counts() -> None:
    """Included, never deduplicated by a guess (ADR-0004)."""
    cutover = date(2026, 4, 1)
    late = Event(
        id="late-1",
        date=date(2026, 5, 12),
        amount=Decimal("900"),
        status="actual",
        item="fees",
        source="bank",
        ext_id="late-1",
    )
    result = engine.run(_book(cutover), events=[*_reconciled_events(cutover), late])
    codes = [d.code for d in result.diagnostics]
    assert codes.count("CK-W003") == 1
    assert "advance cutover" in next(
        d.suggested_fix for d in result.diagnostics if d.code == "CK-W003"
    )
    baseline = engine.run(_book(START))
    assert result.total("fees", "accrual") == baseline.total("fees", "accrual") + 900


def test_actual_before_cutover_is_silent() -> None:
    cutover = date(2026, 4, 1)
    result = engine.run(_book(cutover), events=_reconciled_events(cutover))
    assert [d.code for d in result.diagnostics] == []


def test_event_referencing_an_unknown_item_is_ck_e001() -> None:
    stray = Event(
        id="s1", date=date(2026, 2, 1), amount=Decimal("5"), status="forecast", item="ghost"
    )
    result = engine.run(_book(START), events=[stray])
    assert [d.code for d in result.diagnostics] == ["CK-E001"]
    assert result.total("fees", "accrual") == FEES * 6


def test_event_on_a_derived_item_is_refused_not_silently_overwritten() -> None:
    stray = Event(
        id="s1",
        date=date(2026, 2, 1),
        amount=Decimal("5"),
        status="forecast",
        item="revenue_total",
    )
    result = engine.run(_book(START, extra=[AGGREGATOR]), events=[stray])
    assert [d.code for d in result.diagnostics] == ["CK-E018"]


def test_cross_currency_event_is_ck_e020() -> None:
    stray = Event(
        id="s1",
        date=date(2026, 2, 1),
        amount=Decimal("5"),
        status="forecast",
        item="fees",
        currency="USD",
    )
    result = engine.run(_book(START), events=[stray])
    assert [d.code for d in result.diagnostics] == ["CK-E020"]
    assert result.total("fees", "accrual") == FEES * 6, "the mixed row is not summed"


def test_event_outside_the_horizon_is_outside_the_model() -> None:
    outside = Event(
        id="s1", date=date(2025, 12, 1), amount=Decimal("5"), status="actual", item="fees"
    )
    result = engine.run(_book(START), events=[outside])
    assert result.total("fees", "accrual") == FEES * 6
    assert result.total("fees", "cash") == engine.run(_book(START)).total("fees", "cash")


# --------------------------------------------------------------------------- #
# Settlement of ledger facts
# --------------------------------------------------------------------------- #


def test_an_event_inherits_its_items_settlement() -> None:
    """60-day terms on the item move the event's cash, not just the item's."""
    event = Event(
        id="one",
        date=date(2026, 1, 15),
        amount=Decimal("1000"),
        status="committed",
        item="fees",
    )
    result = engine.run(_book(END), events=[event])  # cutover at END: no generation
    cash = result.column("fees", "cash")
    landed = np.flatnonzero(cash)
    assert landed.size == 1
    assert result.periods.starts[int(landed[0])] == date(2026, 3, 16)
    assert result.value("fees", "cash", int(landed[0])) == Decimal("1000")


def test_an_event_can_override_the_items_settlement() -> None:
    event = Event(
        id="one",
        date=date(2026, 1, 15),
        amount=Decimal("1000"),
        status="committed",
        item="fees",
        settlement=Settlement(due=[DueTerm(share=Decimal(1), offset="0d")]),
    )
    result = engine.run(_book(END), events=[event])
    landed = np.flatnonzero(result.column("fees", "cash"))
    assert result.periods.starts[int(landed[0])] == date(2026, 1, 15)


@pytest.mark.parametrize("runner", [engine.run, reference.run], ids=["vector", "oracle"])
def test_withholding_applies_to_ledger_facts_too(runner) -> None:
    withheld = Settlement(
        due=[DueTerm(share=Decimal(1), offset="0d", withholding=Decimal("0.2"))]
    )
    book = _book(END, extra=[_monthly("consult", Decimal("1000"), withheld)])
    event = Event(
        id="one",
        date=date(2026, 2, 2),
        amount=Decimal("1000"),
        status="committed",
        item="consult",
    )
    result = runner(book, events=[event])
    assert result.total("consult", "accrual") == Decimal("1000")
    assert result.total("consult", "cash") == Decimal("800")
