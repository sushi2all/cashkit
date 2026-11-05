"""Construction: books, items, segments, recurrences, escalation, probability."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from ..oracle import (
    ZERO, add_months, balances, escalated, eur, iso, long_date, money, month_name, month_starts,
    monthly_occurrences, pct, total,
)
from ..schema import Check
from ._common import (
    IMPORTS, BookSpec, D, c_balance, c_closing, c_exists, c_field, c_no_errors, c_param, c_series,
    c_total, create_code, dt, family, money_draw, pick, setup_book, task,
)

CAT = "construction"


def _year(rng) -> int:
    return pick(rng, 2026, 2027)


def _spec(rng, *, years: int = 1, params=(), opening=None, grain="MONTH") -> BookSpec:
    y = _year(rng)
    return BookSpec(
        id=pick(rng, "plan", "forecast", "budget", "cashplan") + f"_{y}",
        start=date(y, 1, 1),
        end=date(y + years, 1, 1),
        opening=opening if opening is not None else money_draw(rng, 5_000, 80_000, 500),
        grain=grain,
        params=tuple(params),
    )


def _book_sentence(spec: BookSpec) -> str:
    return (
        f"The book `{spec.id}` already exists at `ROOT` (open in `kit`): monthly grain, horizon "
        f"{long_date(spec.start)} to {long_date(spec.end)} (end exclusive), opening balance {eur(spec.opening)}."
    )


# --------------------------------------------------------------------------- #


@family("create_book", CAT, 1, "create_book with horizon, grain, opening balance and params")
def create_book_task(rng, variant):
    y = _year(rng)
    years = pick(rng, 1, 2, 3)
    book_id = pick(rng, "acme_srl", "studio_rossi", "bakery", "northwind") + f"_{y}"
    opening = money_draw(rng, 1_000, 250_000, 250)
    inflation = pick(rng, Decimal("0.02"), Decimal("0.025"), Decimal("0.03"), Decimal("0.035"))
    vat = pick(rng, Decimal("0.22"), Decimal("0.21"), Decimal("0.20"))
    phrasing = pick(
        rng,
        f"Create a new cash-flow book with id `{book_id}` at `ROOT`. It should cover {years} year(s) starting "
        f"1 January {y}, use monthly periods, and start with {eur(opening)} in the bank. Add two parameters: "
        f"`vat_standard` = {pct(vat)} and `inflation` = {pct(inflation)}.",
        f"Set up a brand-new book (`{book_id}`, stored at `ROOT`) for the {years}-year period beginning "
        f"January {y}. Monthly granularity. Opening cash: {eur(opening)}. Parameters: inflation {pct(inflation)} "
        f"(key `inflation`) and the standard VAT rate {pct(vat)} (key `vat_standard`).",
    )
    spec = BookSpec(book_id, date(y, 1, 1), date(y + years, 1, 1), opening, "MONTH",
                    (("inflation", inflation), ("vat_standard", vat)))
    reference = IMPORTS + "\n" + create_code(spec)
    checks = [
        Check("book_field", {"field": "id", "value": book_id}, "book id"),
        Check("book_field", {"field": "grain", "value": "month"}, "monthly grain"),
        Check("book_field", {"field": "horizon_start", "value": iso(spec.start)}, "horizon start"),
        Check("book_field", {"field": "horizon_end", "value": iso(spec.end)}, "horizon end (exclusive)"),
        Check("book_field", {"field": "opening_balance", "value": str(opening)}, "opening balance"),
        c_param("vat_standard", vat),
        c_param("inflation", inflation),
        c_no_errors(),
        c_closing(opening, label="an empty book closes at its opening balance"),
    ]
    return task(rng, variant, family="create_book", category=CAT, prompt=phrasing, reference=reference,
                checks=checks, difficulty=1)


@family("monthly_expense", CAT, 1, "a constant monthly outflow, signed and tagged")
def monthly_expense(rng, variant):
    spec = _spec(rng)
    amount = money_draw(rng, 600, 4_000, 50)
    start = add_months(spec.start, rng.randrange(0, 4))
    cat = pick(rng, "rent", "premises", "office")
    prompt = pick(
        rng,
        f"{_book_sentence(spec)}\n\nAdd the office rent: {eur(amount)} per month, paid at the start of each "
        f"month from {month_name(start)} onwards. Use item id `rent` and tag it `cat: {cat}`.",
        f"{_book_sentence(spec)}\n\nWe pay {eur(amount)} a month for the office starting {month_name(start)}, "
        f"open-ended. Model it as item `rent` with tag cat={cat}.",
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="rent", name="Office rent", kind="flow", direction="out", tags={{"cat": "{cat}"}},\n'
        f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(-amount)}))],\n"
        f"))\n"
    )
    flows = {d: -amount for d in monthly_occurrences(start, None, spec.end)}
    checks = [
        c_no_errors(),
        c_field("rent", "kind", "flow"),
        c_field("rent", "direction", "out"),
        c_field("rent", "tags.cat", cat),
        c_series("rent", spec.periods, flows),
        c_closing(spec.opening + total(flows)),
    ]
    return task(rng, variant, family="monthly_expense", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=1)


@family("fixed_term_income", CAT, 1, "an income with an inclusive end month (end date is exclusive)")
def fixed_term_income(rng, variant):
    spec = _spec(rng)
    amount = money_draw(rng, 1_000, 6_000, 100)
    first = add_months(spec.start, rng.randrange(0, 4))
    last = add_months(first, rng.randrange(2, 7))
    prompt = pick(
        rng,
        f"{_book_sentence(spec)}\n\nA client pays us a retainer of {eur(amount)} per month from {month_name(first)} "
        f"to {month_name(last)} inclusive, at the start of each month. Add it as item `retainer`.",
        f"{_book_sentence(spec)}\n\nAdd item `retainer`: {eur(amount)} monthly income, first payment "
        f"{long_date(first)}, last payment {long_date(last)}.",
    )
    end = add_months(last, 1)
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="retainer", name="Client retainer", kind="flow", direction="in",\n'
        f"    segments=[Segment(start={dt(first)}, end={dt(end)},  # end is exclusive\n"
        f"                      recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(amount)}))],\n"
        f"))\n"
    )
    flows = {d: amount for d in monthly_occurrences(first, end, spec.end)}
    checks = [
        c_no_errors(),
        c_field("retainer", "direction", "in"),
        c_series("retainer", spec.periods, flows, label="retainer paid exactly from first to last month"),
        c_total("retainer", total(flows)),
    ]
    return task(rng, variant, family="fixed_term_income", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=1, tags=("trap:exclusive-end",))


@family("quarterly_cost", CAT, 1, "a quarterly recurrence")
def quarterly_cost(rng, variant):
    spec = _spec(rng, years=pick(rng, 1, 2))
    amount = money_draw(rng, 300, 3_000, 50)
    start = add_months(spec.start, rng.randrange(0, 3))
    prompt = pick(
        rng,
        f"{_book_sentence(spec)}\n\nInsurance costs {eur(amount)} every quarter, first payment {long_date(start)}, "
        f"then every three months. Add it as item `insurance`.",
        f"{_book_sentence(spec)}\n\nAdd a quarterly insurance premium of {eur(amount)} (item `insurance`) "
        f"starting {month_name(start)}.",
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="insurance", name="Insurance", kind="flow", direction="out",\n'
        f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=3, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(-amount)}))],\n"
        f"))\n"
    )
    flows = {d: -amount for d in monthly_occurrences(start, None, spec.end, every=3)}
    checks = [c_no_errors(), c_series("insurance", spec.periods, flows), c_closing(spec.opening + total(flows))]
    return task(rng, variant, family="quarterly_cost", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=1)


@family("escalating_rent", CAT, 2, "annual escalation from a named param")
def escalating_rent(rng, variant):
    rate = pick(rng, Decimal("0.02"), Decimal("0.025"), Decimal("0.03"), Decimal("0.04"))
    spec = _spec(rng, years=3)
    amount = money_draw(rng, 800, 3_000, 50) + pick(rng, Decimal("0"), Decimal("37.5"), Decimal("12.25"))
    prompt = pick(
        rng,
        f"{_book_sentence(spec)}\n\nAdd rent of {eur(amount)} per month from {month_name(spec.start)}, "
        f"increasing by {pct(rate)} every year on the anniversary of the lease start. Store the escalation rate "
        f"as a parameter called `rent_escalation` so it can be changed later, and make the rent item (`rent`) use it.",
        f"{_book_sentence(spec)}\n\nThe lease (item `rent`) costs {eur(amount)}/month starting "
        f"{long_date(spec.start)} and indexes up {pct(rate)} per year. The rate must be a book parameter "
        f"`rent_escalation` that the item references, not a literal.",
    )
    reference = IMPORTS + "\n" + (
        f'kit.set_param("rent_escalation", {D(rate)})\n'
        f"kit.set_item(Item(\n"
        f'    id="rent", name="Rent", kind="flow", direction="out",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(-amount)}),\n"
        f'                      escalation=Escalation(rate="rent_escalation"))],\n'
        f"))\n"
    )
    flows = {d: escalated(-amount, rate, d, spec.start) for d in monthly_occurrences(spec.start, None, spec.end)}
    checks = [
        c_no_errors(),
        c_param("rent_escalation", rate),
        c_field("rent", "segments.0.escalation.rate", "rent_escalation", label="escalation reads the param"),
        c_series("rent", spec.periods, flows),
    ]
    return task(rng, variant, family="escalating_rent", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2, tags=("params-as-levers",))


@family("phased_contract", CAT, 2, "one item with two consecutive segments")
def phased_contract(rng, variant):
    spec = _spec(rng)
    ramp = money_draw(rng, 1_000, 3_000, 100)
    run_rate = ramp + money_draw(rng, 1_000, 5_000, 100)
    start = add_months(spec.start, rng.randrange(0, 3))
    ramp_months = rng.randrange(2, 5)
    switch = add_months(start, ramp_months)
    prompt = (
        f"{_book_sentence(spec)}\n\nWe signed Acme: they pay {eur(ramp)} per month for the first {ramp_months} "
        f"months (onboarding phase) starting {month_name(start)}, then {eur(run_rate)} per month from "
        f"{month_name(switch)} onwards. Model it as a single item `acme_contract` with one segment per phase, "
        f"tagged `customer: acme`."
    )
    reference = IMPORTS + "\n" + (
        f"monthly = Recurrence(every=1, unit=Grain.MONTH)\n"
        f"kit.set_item(Item(\n"
        f'    id="acme_contract", name="Acme contract", kind="flow", direction="in", tags={{"customer": "acme"}},\n'
        f"    segments=[\n"
        f"        Segment(start={dt(start)}, end={dt(switch)}, recurrence=monthly, amount=Amount(constant={D(ramp)})),\n"
        f"        Segment(start={dt(switch)}, recurrence=monthly, amount=Amount(constant={D(run_rate)})),\n"
        f"    ],\n"
        f"))\n"
    )
    flows = {d: ramp for d in monthly_occurrences(start, switch, spec.end)}
    flows.update({d: run_rate for d in monthly_occurrences(switch, None, spec.end)})
    checks = [
        c_no_errors(),
        c_field("acme_contract", "segments", 2, op="len", label="one segment per phase"),
        c_field("acme_contract", "tags.customer", "acme"),
        c_series("acme_contract", spec.periods, flows),
    ]
    return task(rng, variant, family="phased_contract", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2)


@family("seasonal_schedule", CAT, 2, "an explicit amount schedule on specific months")
def seasonal_schedule(rng, variant):
    spec = _spec(rng)
    months = sorted(rng.sample(range(12), 4))
    amounts = {add_months(spec.start, m): money_draw(rng, 500, 9_000, 250) for m in months}
    listing = ", ".join(f"{month_name(d, year=False)}: {eur(v)}" for d, v in amounts.items())
    prompt = (
        f"{_book_sentence(spec)}\n\nTrade-fair income arrives only in some months of {spec.start.year}, at the "
        f"start of the month — {listing}. Nothing in other months. Add it as one item `fairs` using an explicit "
        f"amount schedule."
    )
    schedule = ", ".join(f"({dt(d)}, {D(v)})" for d, v in amounts.items())
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="fairs", name="Trade fairs", kind="flow", direction="in",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(schedule=[{schedule}]))],\n"
        f"))\n"
    )
    checks = [c_no_errors(), c_series("fairs", spec.periods, amounts), c_closing(spec.opening + total(amounts))]
    return task(rng, variant, family="seasonal_schedule", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2)


@family("payday_business_day", CAT, 3, "day-of-month anchor with previous-business-day adjustment, daily grain")
def payday_business_day(rng, variant):
    y = _year(rng)
    start, end = date(y, 1, 1), date(y, 7, 1)
    spec = BookSpec(f"payroll_{y}", start, end, money_draw(rng, 20_000, 90_000, 1_000), "DAY")
    amount = money_draw(rng, 2_000, 9_000, 100)
    day = pick(rng, 25, 27, 28, 30, 31)
    prompt = (
        f"The book `{spec.id}` exists at `ROOT` (open in `kit`): **daily** grain, horizon {long_date(start)} to "
        f"{long_date(end)} (exclusive), opening balance {eur(spec.opening)}, weekends are Saturday and Sunday, "
        f"no public holidays.\n\nAdd payroll (item `payroll`, tag `cat: payroll`): {eur(amount)} paid on day {day} "
        f"of every month (or the month's last day when it is shorter), from January. When that day falls on a "
        f"weekend, pay on the previous business day."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="payroll", name="Payroll", kind="flow", direction="out", tags={{"cat": "payroll"}},\n'
        f"    segments=[Segment(start={dt(start)},\n"
        f'        recurrence=Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day={day},\n'
        f'                              business_day_adjust="prev"),\n'
        f"        amount=Amount(constant={D(-amount)}))],\n"
        f"))\n"
    )
    paydays = []
    for m in range(1, 7):
        last = calendar.monthrange(y, m)[1]
        d = date(y, m, min(day, last))
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        paydays.append(d)
    values = {iso(d): str(money(-amount)) for d in paydays}
    # One day either side of each payday must be zero — a wrong adjustment lands there.
    for d in paydays:
        for near in (d - timedelta(days=1), d + timedelta(days=1)):
            if near < end and iso(near) not in values:
                values[iso(near)] = "0.0000"
    checks = [
        c_no_errors(),
        Check("series", {"item": "payroll", "scenario": "base", "measure": "cash", "values": values},
              "payroll lands on the adjusted payday and nowhere near it"),
        c_total("payroll", -amount * 6),
    ]
    setup = IMPORTS + "\n" + create_code(spec).replace(
        "grain=Grain.DAY,", "grain=Grain.DAY,\n    calendar=CalendarSpec(weekend={5, 6}),")
    return task(rng, variant, family="payday_business_day", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup, difficulty=3, tags=("calendar",))


@family("pipeline_probability", CAT, 2, "probability-weighted pipeline deal with a flag")
def pipeline_probability(rng, variant):
    spec = _spec(rng)
    amount = money_draw(rng, 1_000, 8_000, 100) + pick(rng, Decimal("0"), Decimal("33.33"))
    p = pick(rng, Decimal("0.25"), Decimal("0.4"), Decimal("0.6"), Decimal("0.75"), Decimal("0.9"))
    start = add_months(spec.start, rng.randrange(2, 6))
    prompt = (
        f"{_book_sentence(spec)}\n\nWe might win a deal with Beta: {eur(amount)} per month from {month_name(start)}. "
        f"Sales puts the chance of closing at {pct(p)}. Add it as item `beta_deal`, weighted by that probability, "
        f"with the flag `pipeline` and tag `customer: beta`."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="beta_deal", name="Beta deal", kind="flow", direction="in",\n'
        f'    tags={{"customer": "beta"}}, flags={{"pipeline"}},\n'
        f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(amount)}), probability={D(p)})],\n"
        f"))\n"
    )
    flows = {d: money(amount * p) for d in monthly_occurrences(start, None, spec.end)}
    checks = [
        c_no_errors(),
        c_field("beta_deal", "flags", "pipeline", op="contains"),
        c_field("beta_deal", "segments.0.probability", str(p), label="probability stored on the segment"),
        c_series("beta_deal", spec.periods, flows, label="cash is probability-weighted"),
    ]
    return task(rng, variant, family="pipeline_probability", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2)
