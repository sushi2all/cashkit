"""Phase 6 gate: VAT and tax regimes.

Three claims, all hand-computed and all checked on both engines:

1. A fixture entity with mixed VAT rates, one exempt item, one reverse-charge
   item, 60-day customer terms and quarterly accrual-basis VAT reproduces the
   F24 schedule committed in ``tests/fixtures/f24_schedule.csv``.
2. A fixture with input above output for two consecutive quarters shows a
   **credit stock**, not a negative payment — the failure mode that would
   overstate cash in exactly the investment year the forecast exists for.
3. Flipping ``measure`` to ``"cash"`` shifts the liability, by the amount the
   60-day terms move.

Around them: what each ``VatSpec.treatment`` does, that a line's VAT legs sum to
the VAT the line states, and that VAT sits where ADR-0003 says it does — after
withholding, computed on the taxable amount rather than on what is left of it.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from vat_book import build_credit_book, build_f24_book

import cashkit.engine as engine
import cashkit.reference as reference
from cashkit.engine.numeric import from_minor
from cashkit.engine.tax import credit_id, liability_id, regime_periods, tax_diagnostics
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
    TaxRegime,
    VatSpec,
)

ENGINES = pytest.mark.parametrize(
    "run", [engine.run, reference.run], ids=["vector", "oracle"]
)

LIABILITY = liability_id("vat")
CREDIT = credit_id("vat")

SCHEDULE = [
    row
    for row in csv.DictReader(
        line
        for line in (Path("tests/fixtures/f24_schedule.csv").read_text().splitlines())
        if not line.startswith("#")
    )
]


def _period_index(result, day: date) -> int:
    return result.periods.starts.index(day)


# --------------------------------------------------------------------------- #
# Gate 1 — the F24 schedule
# --------------------------------------------------------------------------- #


@ENGINES
def test_f24_schedule_matches_the_hand_computed_fixture(run) -> None:
    book = build_f24_book()
    result = run(book)
    assert [d.code for d in result.diagnostics] == []

    accrual = result.column(LIABILITY, "accrual")
    cash = result.column(LIABILITY, "cash")
    assert len(SCHEDULE) == 4

    for row in SCHEDULE:
        period_end = date.fromisoformat(row["period_end"])
        payment_date = date.fromisoformat(row["payment_date"])
        expected = -Decimal(row["payment"])
        recognised = _period_index(result, period_end)
        paid = _period_index(result, payment_date)
        assert result.value(LIABILITY, "accrual", recognised) == expected, row["quarter"]
        assert result.value(LIABILITY, "cash", paid) == expected, row["quarter"]

    # Nothing anywhere else: four returns, four recognitions, four payments.
    assert np.count_nonzero(accrual) == 4
    assert np.count_nonzero(cash) == 4
    assert result.total(LIABILITY, "cash") == -Decimal(SCHEDULE[0]["payment"]) * 4


def test_output_and_input_vat_match_the_fixture_quarter_by_quarter() -> None:
    """The payment is right; this checks it is right for the right reasons."""
    book = build_f24_book()
    kit = engine.Engine(book)
    result = kit.run()
    plan = kit.compiled.tax.plans["vat"]
    closing = [period for period in plan.periods if period.closes]
    assert len(closing) == 4

    for row, period in zip(SCHEDULE, closing):
        assert period.end == date.fromisoformat(row["period_end"])
        output = sum(
            int(result.vat_columns(item).output_accrual[period.lo : period.hi].sum())
            for item in plan.base
        )
        inputs = sum(
            int(result.vat_columns(item).input_accrual[period.lo : period.hi].sum())
            for item in plan.base
        )
        assert from_minor(output) == Decimal(row["output_vat"]), row["quarter"]
        assert from_minor(inputs) == Decimal(row["input_vat"]), row["quarter"]
        assert from_minor(output + inputs) == Decimal(row["net"]), row["quarter"]


@ENGINES
def test_cash_legs_are_grossed_up_by_their_vat(run) -> None:
    """A 1,000 invoice at 22% collects 1,220 (ADR-0005)."""
    result = run(build_f24_book())
    # 11 of the 12 consulting invoices settle inside the horizon (December's
    # 60-day leg falls past it), each collecting 10,000 + 2,200.
    assert result.total("consulting", "cash") == Decimal("12200") * 11
    assert result.total("training", "cash") == Decimal("4400") * 12
    # Exempt: no VAT anywhere, so cash is the net amount.
    assert result.total("grants", "cash") == result.total("grants", "accrual")
    # Reverse charge: the buyer self-accounts, so no VAT rides the cash leg.
    assert result.total("contractor", "cash") == result.total("contractor", "accrual")
    # Partial deductibility: the whole VAT is paid, only 40% is reclaimed, and
    # the non-recoverable 60% stays a real cost in cash.
    assert result.total("car_lease", "cash") == Decimal("-976") * 12


@ENGINES
def test_a_return_that_does_not_close_inside_the_horizon_recognises_nothing(run) -> None:
    """The first quarter of 2027 is open at the horizon end; nobody owes it yet."""
    result = run(build_f24_book())
    last = len(result.periods) - 1
    assert result.value(LIABILITY, "accrual", last) == Decimal(0)
    assert result.periods.starts[last] >= date(2027, 1, 1)


# --------------------------------------------------------------------------- #
# Gate 2 — credit carry-forward is a stock, not a negative payment
# --------------------------------------------------------------------------- #


@ENGINES
def test_input_above_output_accumulates_a_credit_stock(run) -> None:
    result = run(build_credit_book())
    assert [d.code for d in result.diagnostics] == []

    levels = {
        date(2026, 3, 31): Decimal("5940"),
        date(2026, 6, 30): Decimal("11880"),
        date(2026, 9, 30): Decimal("0"),
        date(2026, 12, 31): Decimal("0"),
    }
    for day, expected in levels.items():
        assert result.value(CREDIT, "accrual", _period_index(result, day)) == expected

    # The credit is a level that persists between returns, not a movement.
    between = _period_index(result, date(2026, 5, 15))
    assert result.value(CREDIT, "accrual", between) == Decimal("5940")


@ENGINES
def test_a_credit_is_never_a_cash_inflow(run) -> None:
    """The anti-pattern the PRD names explicitly."""
    result = run(build_credit_book())
    cash = result.column(LIABILITY, "cash")
    assert (cash <= 0).all(), "a VAT credit must never appear as money coming in"
    assert result.column(CREDIT, "cash").any() == False  # noqa: E712 - a stock has no cash


@ENGINES
def test_the_credit_offsets_the_next_liability_rather_than_being_refunded(run) -> None:
    result = run(build_credit_book())
    payments = {
        result.periods.starts[int(index)]: result.value(LIABILITY, "cash", int(index))
        for index in np.flatnonzero(result.column(LIABILITY, "cash"))
    }
    # Q3 owes 19,800 and carries 11,880 of credit into it, so 7,920 moves.
    assert payments == {
        date(2026, 10, 16): Decimal("-7920"),
        date(2027, 1, 16): Decimal("-19800"),
    }


# --------------------------------------------------------------------------- #
# Gate 3 — the cash tax point
# --------------------------------------------------------------------------- #


@ENGINES
def test_flipping_measure_to_cash_shifts_the_liability(run) -> None:
    """``IVA per cassa``: VAT is due when the money arrives, not when invoiced.

    Only January's consulting invoice is collected inside the first quarter, so
    the first F24 drops from 5,998.19 to 1,554.19 — the working-capital hole the
    accrual tax point creates, and the reason opting in is a real decision.
    """
    accrual_basis = run(build_f24_book(measure="accrual"))
    cash_basis = run(build_f24_book(measure="cash"))

    first = _period_index(accrual_basis, date(2026, 4, 16))
    assert accrual_basis.value(LIABILITY, "cash", first) == Decimal("-5998.1880")
    assert cash_basis.value(LIABILITY, "cash", first) == Decimal("-1554.1880")

    # Later quarters have reached a steady state, so only the transition moves.
    for day in (date(2026, 7, 16), date(2026, 10, 16), date(2027, 1, 16)):
        index = _period_index(accrual_basis, day)
        assert cash_basis.value(LIABILITY, "cash", index) == Decimal("-5998.1880")

    assert cash_basis.total(LIABILITY, "cash") > accrual_basis.total(LIABILITY, "cash")


def test_the_cash_tax_point_follows_the_settlement_lag_exactly() -> None:
    """One invoice, one leg: the return period is the leg's, not the invoice's."""
    book = _single_invoice_book(measure="cash")
    result = engine.run(book)
    # Invoiced 15 February, collected 16 April, so the VAT belongs to Q2 and is
    # paid on 16 July rather than 16 April.
    paid = np.flatnonzero(result.column(LIABILITY, "cash"))
    assert [result.periods.starts[int(i)] for i in paid] == [date(2026, 7, 16)]

    on_accrual = engine.run(_single_invoice_book(measure="accrual"))
    paid = np.flatnonzero(on_accrual.column(LIABILITY, "cash"))
    assert [on_accrual.periods.starts[int(i)] for i in paid] == [date(2026, 4, 16)]


def _single_invoice_book(*, measure: str) -> Book:
    item = Item(
        id="sale",
        name="Sale",
        kind="flow",
        direction="in",
        tags={"cat": "revenue"},
        segments=[
            Segment(
                start=date(2026, 2, 1),
                end=date(2026, 3, 1),
                recurrence=Recurrence(
                    every=1, unit=Grain.MONTH, anchor="day_of_month", day=15
                ),
                amount=Amount(constant=Decimal("1000")),
            )
        ],
        settlement=Settlement(due=[DueTerm(share=Decimal(1), offset="60d")]),
        vat=VatSpec(rate="vat_standard"),
    )
    return Book(
        id="one-invoice",
        calendar=CalendarSpec(fiscal_year_start_month=1, holidays=[]),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        opening_balance=Decimal(0),
        cutover=date(2026, 1, 1),
        params={"vat_standard": Decimal("0.22")},
        items={item.id: item},
        tax_regimes=[
            TaxRegime(
                id="vat",
                accumulates="",
                measure=measure,
                periodicity="quarterly",
                payment_offset="16d",
            )
        ],
    )


# --------------------------------------------------------------------------- #
# Treatments
# --------------------------------------------------------------------------- #


def _treatment_book(treatment: str, *, amount: str, recoverable: str = "1") -> Book:
    item = Item(
        id="line",
        name="Line",
        kind="flow",
        direction="in" if Decimal(amount) > 0 else "out",
        tags={"cat": "revenue" if Decimal(amount) > 0 else "cost"},
        segments=[
            Segment(
                start=date(2026, 1, 1),
                end=date(2026, 2, 1),
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal(amount)),
            )
        ],
        vat=VatSpec(
            rate="vat_standard",
            treatment=treatment,
            recoverable=Decimal(recoverable),
        ),
    )
    return Book(
        id="treatment",
        calendar=CalendarSpec(fiscal_year_start_month=1, holidays=[]),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1)),
        opening_balance=Decimal(0),
        cutover=date(2026, 1, 1),
        params={"vat_standard": Decimal("0.22")},
        items={item.id: item},
        tax_regimes=[
            TaxRegime(
                id="vat",
                accumulates="",
                measure="accrual",
                periodicity="quarterly",
                payment_offset="16d",
            )
        ],
    )


@ENGINES
@pytest.mark.parametrize(
    ("treatment", "amount", "cash", "paid", "credit"),
    [
        ("standard", "1000", "1220", "220", "0"),
        # A pure purchase reclaims: a credit stock, never a negative payment.
        ("standard", "-1000", "-1220", "0", "220"),
        ("exempt", "1000", "1000", "0", "0"),
        ("out_of_scope", "1000", "1000", "0", "0"),
        ("export", "1000", "1000", "0", "0"),
        # Split payment: the buyer remits the VAT to the state, so the receivable
        # is net and the supplier never owes it (PRD §7.2).
        ("split_payment", "1000", "1000", "0", "0"),
        # Reverse charge on a purchase: self-accounted both ways, netting to zero
        # when fully deductible; the sale side carries no VAT at all.
        ("reverse_charge", "-1000", "-1000", "0", "0"),
        ("reverse_charge", "1000", "1000", "0", "0"),
    ],
)
def test_each_treatment_does_what_it_says(
    run, treatment, amount, cash, paid, credit
) -> None:
    result = run(_treatment_book(treatment, amount=amount))
    assert result.total("line", "cash") == Decimal(cash)
    assert result.total(LIABILITY, "accrual") == -Decimal(paid)
    assert result.value(CREDIT, "accrual", len(result.periods) - 1) == Decimal(credit)


@ENGINES
def test_reverse_charge_leaves_the_non_recoverable_part_payable(run) -> None:
    """40% deductible: 220 output self-assessed, 88 reclaimed, 132 owed."""
    result = run(_treatment_book("reverse_charge", amount="-1000", recoverable="0.4"))
    assert result.total("line", "cash") == Decimal("-1000")
    assert result.total(LIABILITY, "accrual") == Decimal("-132")


@ENGINES
def test_direction_decides_the_side_even_when_a_credit_note_flips_the_sign(run) -> None:
    """A credit note against a sale reduces output VAT; it is not input VAT."""
    book = _treatment_book("standard", amount="1000")
    item = book.items["line"]
    credited = item.model_copy(
        update={
            "segments": [
                *item.segments,
                Segment(
                    start=date(2026, 3, 1),
                    end=date(2026, 4, 1),
                    recurrence=Recurrence(every=1, unit=Grain.MONTH),
                    amount=Amount(constant=Decimal("-400")),
                ),
            ]
        }
    )
    result = run(book.model_copy(update={"items": {"line": credited}}))
    columns = result.vat_columns("line")
    assert from_minor(int(columns.output_accrual.sum())) == Decimal("132")
    assert from_minor(int(columns.input_accrual.sum())) == Decimal("0")


# --------------------------------------------------------------------------- #
# Allocation, withholding and the canonical order
# --------------------------------------------------------------------------- #


def _split_book(withholding: str = "0") -> Book:
    item = Item(
        id="invoice",
        name="Invoice",
        kind="flow",
        direction="in",
        tags={"cat": "revenue"},
        segments=[
            Segment(
                start=date(2026, 1, 1),
                end=date(2026, 2, 1),
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal("1000")),
            )
        ],
        settlement=Settlement(
            due=[
                DueTerm(
                    share=Decimal("0.3333"),
                    offset="0d",
                    withholding=Decimal(withholding),
                ),
                DueTerm(
                    share=Decimal("0.6667"),
                    offset="30d",
                    withholding=Decimal(withholding),
                ),
            ]
        ),
        vat=VatSpec(rate="vat_standard"),
    )
    return Book(
        id="split",
        calendar=CalendarSpec(fiscal_year_start_month=1, holidays=[]),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1)),
        opening_balance=Decimal(0),
        cutover=date(2026, 1, 1),
        params={"vat_standard": Decimal("0.22")},
        items={item.id: item},
        tax_regimes=[
            TaxRegime(
                id="vat",
                accumulates="",
                measure="cash",
                periodicity="monthly",
                payment_offset="16d",
            )
        ],
    )


@ENGINES
def test_a_lines_vat_legs_sum_to_the_vat_the_line_states(run) -> None:
    """Residual absorption, the same rule ADR-0003 fixes for the share split."""
    result = run(_split_book())
    columns = result.vat_columns("invoice")
    assert from_minor(int(columns.output_cash.sum())) == Decimal("220")
    assert from_minor(int(columns.output_accrual.sum())) == Decimal("220")
    # Both legs collect VAT, in proportion, and together they collect exactly 220.
    assert result.total("invoice", "cash") == Decimal("1220")


@ENGINES
def test_vat_is_computed_on_the_taxable_amount_not_on_what_withholding_leaves(
    run,
) -> None:
    """A 1,000 invoice at 22% VAT with a 20% ritenuta collects 1,020.

    VAT sits last in the canonical order but it is not applied *to* the
    withheld amount: a ritenuta d'acconto has never reduced the VAT on an
    invoice, and computing 22% of 800 would understate the liability by 44.
    """
    result = run(_split_book(withholding="0.2"))
    assert result.total("invoice", "cash") == Decimal("1020")
    columns = result.vat_columns("invoice")
    assert from_minor(int(columns.output_accrual.sum())) == Decimal("220")
    assert result.total(LIABILITY, "accrual") == Decimal("-220")


# --------------------------------------------------------------------------- #
# Regime configuration and diagnostics
# --------------------------------------------------------------------------- #


def test_regime_periods_follow_the_fiscal_year() -> None:
    from cashkit.engine.calendars import PeriodIndex

    book = build_f24_book()
    fiscal = book.model_copy(
        update={
            "calendar": CalendarSpec(fiscal_year_start_month=7, holidays=[]),
        }
    )
    periods = PeriodIndex.build(fiscal.horizon, fiscal.base_grain, 7)
    quarters = regime_periods(fiscal.tax_regimes[0], periods)
    assert [q.end for q in quarters][:3] == [
        date(2026, 3, 31),
        date(2026, 6, 30),
        date(2026, 9, 30),
    ]
    assert quarters[0].start == date(2026, 1, 1)


@ENGINES
def test_an_unknown_vat_rate_param_is_ck_e008_and_the_item_is_broken(run) -> None:
    book = _treatment_book("standard", amount="1000")
    item = book.items["line"].model_copy(update={"vat": VatSpec(rate="vat_unknown")})
    result = run(book.model_copy(update={"items": {"line": item}}))
    assert [d.code for d in result.diagnostics] == ["CK-E008"]
    assert result.total("line", "accrual") == Decimal(0), "a broken item computes nothing"


@ENGINES
def test_a_regime_whose_selector_matches_nothing_is_ck_e019(run) -> None:
    book = _treatment_book("standard", amount="1000")
    regime = book.tax_regimes[0].model_copy(update={"accumulates": "cat:nonexistent"})
    result = run(book.model_copy(update={"tax_regimes": [regime]}))
    assert [d.code for d in result.diagnostics] == ["CK-E019"]
    assert LIABILITY not in result.accrual, "a refused regime materializes nothing"


@ENGINES
def test_refund_annual_without_a_month_is_refused(run) -> None:
    book = _treatment_book("standard", amount="1000")
    regime = book.tax_regimes[0].model_copy(update={"credit_handling": "refund_annual"})
    result = run(book.model_copy(update={"tax_regimes": [regime]}))
    assert [d.code for d in result.diagnostics] == ["CK-E019"]


@ENGINES
def test_refund_annual_converts_the_credit_into_an_inflow(run) -> None:
    book = build_credit_book()
    regime = book.tax_regimes[0].model_copy(
        update={"credit_handling": "refund_annual", "annual_adjustment_month": 6}
    )
    result = run(book.model_copy(update={"tax_regimes": [regime]}))
    # Two quarters of credit are claimed at the June adjustment instead of being
    # carried, so 11,880 comes back on 16 July.
    assert result.value(
        LIABILITY, "cash", _period_index(result, date(2026, 7, 16))
    ) == Decimal("11880")
    assert result.value(
        CREDIT, "accrual", _period_index(result, date(2026, 6, 30))
    ) == Decimal(0)


@ENGINES
def test_a_regime_caught_in_a_feedback_loop_is_refused_not_silently_zeroed(run) -> None:
    """A regime's schedule folds over *return* periods, not base periods, so it
    cannot sit inside a per-period feedback loop.

    An item whose VAT feeds a regime and whose formula reads that regime back
    closes a same-period cycle, and the run refuses it by name (``CK-E002``)
    rather than producing a zero tax column with no explanation. ``compile_book``
    keeps a second guard (``CK-E019``) for a cycle that reached the fold through
    a lagged edge instead; the formula language cannot spell one today, because
    `prev()` names authored ids and only `agg()` can reach a synthetic one.
    """
    book = build_f24_book()
    looped = Item(
        id="commission",
        name="Commission",
        kind="derived",
        direction="in",
        tags={"cat": "revenue"},
        formula='prev("commission") + agg(tag="cat:tax") * 0.1',
        vat=VatSpec(rate="vat_standard"),
    )
    items = {**book.items, "commission": looped}
    result = run(book.model_copy(update={"items": items}))
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert [d.code for d in errors] == ["CK-E002"]
    assert "commission" in errors[0].message and "_tax:vat" in errors[0].message
    assert not result.column(LIABILITY, "accrual").any()
    assert not result.column("commission", "accrual").any()


# --------------------------------------------------------------------------- #
# Events carry VAT too
# --------------------------------------------------------------------------- #


@ENGINES
def test_a_ledger_event_inherits_and_can_override_its_items_vat(run) -> None:
    book = _treatment_book("standard", amount="1000")
    inherited = Event(
        id="e1",
        date=date(2026, 2, 10),
        amount=Decimal("500"),
        status="actual",
        item="line",
    )
    overridden = Event(
        id="e2",
        date=date(2026, 2, 11),
        amount=Decimal("500"),
        status="actual",
        item="line",
        vat=VatSpec(rate="vat_standard", treatment="exempt"),
    )
    result = run(book, events=[inherited, overridden])
    # 1,000 generated + 500 + 500 = 2,000 accrued; VAT on 1,500 of it.
    assert result.total("line", "accrual") == Decimal("2000")
    assert result.total("line", "cash") == Decimal("2000") + Decimal("330")


@ENGINES
def test_an_unattached_event_can_carry_its_own_vat(run) -> None:
    book = _treatment_book("standard", amount="1000")
    fee = Event(
        id="e1",
        date=date(2026, 2, 10),  # same quarter as the generated sale
        amount=Decimal("-200"),
        status="actual",
        tags={"cat": "cost"},
        vat=VatSpec(rate="vat_standard"),
    )
    result = run(book, events=[fee])
    synthetic = next(item for item in result.accrual if item.startswith("_event:"))
    assert result.total(synthetic, "cash") == Decimal("-244")
    # 220 output on the sale, 44 input on the fee.
    assert result.total(LIABILITY, "accrual") == Decimal("-176")


# --------------------------------------------------------------------------- #
# The tax warnings validate() owes the user (§9.5)
# --------------------------------------------------------------------------- #


def test_withholding_without_a_remittance_item_warns() -> None:
    book = _split_book(withholding="0.2")
    codes = [d.code for d in tax_diagnostics(book)]
    assert "CK-W004" in codes


def test_a_regime_with_no_manual_tax_items_is_flagged() -> None:
    codes = [d.code for d in tax_diagnostics(build_f24_book())]
    assert "CK-I001" in codes


def test_a_book_with_manual_tax_items_is_not_flagged() -> None:
    book = build_f24_book()
    manual = Item(
        id="ires",
        name="IRES advance",
        kind="flow",
        direction="out",
        tags={"cat": "tax"},
        flags={"manual_tax"},
        segments=[
            Segment(
                start=date(2026, 6, 30),
                end=date(2026, 7, 1),
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal("-12000")),
            )
        ],
    )
    with_manual = book.model_copy(update={"items": {**book.items, "ires": manual}})
    assert tax_diagnostics(with_manual) == ()
