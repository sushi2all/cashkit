"""Tax: VAT lines and regimes, VAT-exclusive authoring, manual (non-native) tax items."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..oracle import ZERO, add_months, bucket, eur, money, month_name, monthly_occurrences, pct, total
from ..schema import Check
from ._common import (
    IMPORTS, BookSpec, D, c_field, c_no_errors, c_series, dt, family, money_draw, pick, setup_book, task,
)
from .construction import _book_sentence, _year

CAT = "tax"


def _vat_spec(rng, rate: Decimal) -> BookSpec:
    y = _year(rng)
    return BookSpec(f"vat_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 10_000, 60_000, 1_000),
                    "MONTH", (("vat_standard", rate),))


@family("vat_quarterly", CAT, 3, "output and input VAT on items, netted by a quarterly accrual-point regime")
def vat_quarterly(rng, variant):
    rate = Decimal("0.22")
    spec = _vat_spec(rng, rate)
    sales = money_draw(rng, 5_000, 20_000, 500)
    costs = money_draw(rng, 1_000, 4_000, 250)
    offset = pick(rng, 16, 16, 20)
    prompt = (
        f"{_book_sentence(spec)} The book has the parameter `vat_standard` = {pct(rate)}.\n\n"
        f"1. Add item `sales`: {eur(sales)} per month (net of VAT), from {month_name(spec.start)}, standard VAT at "
        f"the `vat_standard` rate.\n"
        f"2. Add item `supplies`: {eur(costs)} per month (net of VAT) of purchases from {month_name(spec.start)}, "
        f"standard VAT, fully recoverable.\n"
        f"3. Add a VAT regime `iva`: settled quarterly on the accrual tax point (invoice date), paid {offset} days "
        f"after each quarter ends."
    )
    reference = IMPORTS + "\n" + (
        f"monthly = Recurrence(every=1, unit=Grain.MONTH)\n"
        f"kit.set_item(Item(\n"
        f'    id="sales", name="Sales", kind="flow", direction="in",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=monthly, amount=Amount(constant={D(sales)}))],\n"
        f'    vat=VatSpec(rate="vat_standard"),\n'
        f"))\n"
        f"kit.set_item(Item(\n"
        f'    id="supplies", name="Supplies", kind="flow", direction="out",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=monthly, amount=Amount(constant={D(-costs)}))],\n"
        f'    vat=VatSpec(rate="vat_standard", recoverable=Decimal("1")),\n'
        f"))\n"
        f'kit.set_book(tax_regimes=[TaxRegime(id="iva", accumulates="", measure="accrual",\n'
        f'                                    periodicity="quarterly", payment_offset="{offset}d")])\n'
    )
    months = monthly_occurrences(spec.start, None, spec.end)
    sales_cash = {d: money(sales * (1 + rate)) for d in months}
    costs_cash = {d: -money(costs * (1 + rate)) for d in months}
    quarter_vat = 3 * (money(sales * rate) - money(costs * rate))
    payments = bucket(
        [(add_months(spec.start, q * 3), -quarter_vat) for q in (1, 2, 3, 4)],
        spec.periods, spec.end,
    )
    checks = [
        c_no_errors(),
        c_series("sales", spec.periods, {d: sales for d in months}, measure="accrual",
                 label="sales accrue net (amounts are VAT-exclusive)"),
        c_series("sales", spec.periods, sales_cash, label="sales collect gross of VAT"),
        c_series("supplies", spec.periods, costs_cash, label="supplies pay gross of VAT"),
        Check("tax_regime", {"match": {"id": "iva", "periodicity": "quarterly", "measure": "accrual",
                                       "payment_offset": f"{offset}d"}}, "quarterly accrual-point regime"),
        Check("synthetic_series", {"kind": "tax", "values": {d.isoformat(): str(money(v)) for d, v in
                                   {p: payments.get(p, ZERO) for p in spec.periods}.items()}},
              "net VAT paid after each quarter"),
    ]
    return task(rng, variant, family="vat_quarterly", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=3)


@family("vat_inclusive_price", CAT, 3, "a price quoted gross of VAT must be authored net")
def vat_inclusive_price(rng, variant):
    rate = Decimal("0.22")
    spec = _vat_spec(rng, rate)
    net = money_draw(rng, 200, 3_000, 50)
    gross = money(net * (1 + rate))
    prompt = pick(
        rng,
        f"{_book_sentence(spec)} The book has `vat_standard` = {pct(rate)} and a monthly VAT regime already.\n\n"
        f"Our software subscription costs {eur(gross)} per month **including** {pct(rate)} VAT, charged at the "
        f"start of each month from {month_name(spec.start)}. The VAT is fully recoverable. Add it as item `software`.",
        f"{_book_sentence(spec)} `vat_standard` is {pct(rate)}; a monthly VAT regime exists.\n\n"
        f"Add item `software`: the card statement shows {eur(gross)} every month (VAT included, {pct(rate)}, "
        f"deductible), from {month_name(spec.start)}.",
    )
    reference = IMPORTS + "\n" + (
        f"# Authored amounts are VAT-exclusive: {gross} / 1.22 = {net}\n"
        f"kit.set_item(Item(\n"
        f'    id="software", name="Software subscription", kind="flow", direction="out",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(-net)}))],\n"
        f'    vat=VatSpec(rate="vat_standard"),\n'
        f"))\n"
    )
    months = monthly_occurrences(spec.start, None, spec.end)
    setup = setup_book(spec, 'kit.set_book(tax_regimes=[TaxRegime(id="iva", accumulates="", periodicity="monthly", payment_offset="16d")])')
    checks = [
        c_no_errors(),
        c_series("software", spec.periods, {d: -net for d in months}, measure="accrual",
                 label="authored net of VAT"),
        c_series("software", spec.periods, {d: -gross for d in months}, label="pays the gross price"),
        # A literal 0.22 is accepted too — the structure, not the spelling, is the point here.
        c_field("software", "vat.rate", ["vat_standard", "0.22"], op="in", label="VAT at the standard rate"),
    ]
    return task(rng, variant, family="vat_inclusive_price", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup, difficulty=3, tags=("trap:vat-inclusive",))


@family("manual_tax_item", CAT, 2, "a non-native tax (IRES advances) as a manual cat:tax item")
def manual_tax_item(rng, variant):
    y = _year(rng)
    spec = BookSpec(f"srl_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 20_000, 120_000, 1_000))
    ires = money_draw(rng, 8_000, 60_000, 500)
    first, second = money(ires * Decimal("0.4")), money(ires * Decimal("0.6"))
    prompt = (
        f"{_book_sentence(spec)}\n\nThe engine does not model IRES natively. Our accountant estimates IRES "
        f"advances (acconti) of {eur(ires)} for {y}: 40% paid on 30 June and 60% on 30 November. Add them as a "
        f"single manual item `ires_acconti` tagged `cat: tax` with the flag `manual_tax`."
    )
    reference = IMPORTS + "\n" + (
        f"once = Recurrence(every=1, unit=Grain.MONTH)\n"
        f"kit.set_item(Item(\n"
        f'    id="ires_acconti", name="IRES acconti", kind="flow", direction="out",\n'
        f'    tags={{"cat": "tax"}}, flags={{"manual_tax"}},\n'
        f"    segments=[\n"
        f"        Segment(start={dt(date(y, 6, 30))}, end={dt(date(y, 7, 1))}, recurrence=once,\n"
        f"                amount=Amount(constant={D(-first)})),\n"
        f"        Segment(start={dt(date(y, 11, 30))}, end={dt(date(y, 12, 1))}, recurrence=once,\n"
        f"                amount=Amount(constant={D(-second)})),\n"
        f"    ],\n"
        f"))\n"
    )
    flows = {date(y, 6, 1): -first, date(y, 11, 1): -second}
    checks = [
        c_no_errors(),
        c_field("ires_acconti", "tags.cat", "tax"),
        c_field("ires_acconti", "flags", "manual_tax", op="contains"),
        c_series("ires_acconti", spec.periods, flows, label="40% in June, 60% in November"),
    ]
    return task(rng, variant, family="manual_tax_item", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2, tags=("italian-tax",))
