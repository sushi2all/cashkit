"""Harder families: features in combination, and traps that need the semantics.

Difficulty 3–4. Each one is a place where a model that has learned the API's
*shape* but not its *meaning* produces a book that validates clean and is
wrong — the failure mode the PRD ranks worst.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..oracle import (
    ZERO, add_duration, add_months, balances, bucket, eur, long_date, money, month_name, month_of,
    monthly_occurrences, pct, total,
)
from ..schema import Check
from ._common import (
    IMPORTS, BookSpec, D, c_answer, c_closing, c_exists, c_field, c_no_errors, c_series, create_code, dt,
    family, money_draw, monthly_item_code, pick, setup_book, task,
)
from .company import company, setup as company_setup
from .construction import _book_sentence, _year

VAT = Decimal("0.22")


# --------------------------------------------------------------------------- #
# Query: a trough inside a month
# --------------------------------------------------------------------------- #


@family("intra_month_trough", "query", 3, "min cash on a daily book where the trough is inside a month")
def intra_month_trough(rng, variant):
    y = _year(rng)
    start, end = date(y, 1, 1), date(y, 4, 1)
    opening = money_draw(rng, 3_000, 8_000, 500)
    rent = money_draw(rng, 1_500, 3_000, 100)
    salary_day, revenue_day = rng.randrange(3, 9), rng.randrange(20, 27)
    salaries = money_draw(rng, 4_000, 7_000, 250)
    revenue = rent + salaries + money_draw(rng, 200, 1_500, 50)
    spec = BookSpec(f"daily_{y}", start, end, opening, "DAY")

    def item(item_id, amount, day):
        anchor = 'anchor="period_start"' if day == 1 else f'anchor="day_of_month", day={day}'
        return (
            f'kit.set_item(Item(id="{item_id}", name="{item_id.title()}", kind="flow", '
            f'direction="{"in" if amount > 0 else "out"}",\n'
            f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH, {anchor}),\n"
            f"                      amount=Amount(constant={D(amount)}))]))\n"
        )

    body = item("rent", -rent, 1) + item("salaries", -salaries, salary_day) + item("revenue", revenue, revenue_day)
    flows: dict[date, Decimal] = {}
    for m in (1, 2, 3):
        for day, amount in ((1, -rent), (salary_day, -salaries), (revenue_day, revenue)):
            d = date(y, m, day)
            flows[d] = flows.get(d, ZERO) + amount
    days = [start + timedelta(days=i) for i in range((end - start).days)]
    low = min(balances(opening, days, flows).values())
    prompt = (
        f"The book `{spec.id}` exists at `ROOT` (open in `kit`): **daily** grain, {long_date(start)} to "
        f"{long_date(end)} (exclusive), opening balance {eur(opening)}. It was built with:\n\n```python\n{body}```\n\n"
        f"What is the lowest balance the bank account reaches at any point in the horizon (base scenario)? "
        f"Do not change the book. Put it in `answer` as a `Decimal`."
    )
    reference = "answer = kit.run().summary().min_cash\nprint(answer)\n"
    return task(rng, variant, family="intra_month_trough", category="query", prompt=prompt, reference=reference,
                checks=[c_answer(low, "decimal", "the intra-month low, not a month-end balance")],
                setup=setup_book(spec, body), read_only=True, difficulty=3, tags=("trap:grain",))


@family("breakeven_month", "query", 2, "first month from which net cash flow stays non-negative")
def breakeven_month(rng, variant):
    c = company(rng)
    flows = c.flows()
    net = c.net(flows)
    # Add a contract that turns the company cash-positive part-way through the year.
    start = c.periods[rng.randrange(4, 9)]
    deficit = max((-net[p] for p in c.periods), default=ZERO)
    boost = max(deficit, ZERO) + money_draw(rng, 500, 3_000, 250)
    extra = monthly_item_code("gamma", "Gamma contract", boost, start, tags={"customer": "gamma", "cat": "revenue"})
    growth = {d: boost for d in monthly_occurrences(start, None, c.spec.end)}
    net = c.net({**flows, "gamma": growth})
    breakeven = None
    for i in range(len(c.periods) - 1, -1, -1):
        if net[c.periods[i]] < 0:
            breakeven = c.periods[i + 1] if i + 1 < len(c.periods) else None
            break
    else:
        breakeven = c.periods[0]
    prompt = (
        f"{c.description()}\n- `gamma` {eur(boost)}/month inflow from {month_name(start)} (tags customer=gamma).\n\n"
        f"From which month onwards is the monthly net cash flow non-negative for the rest of the horizon (base)? "
        f"Do not change the book. Put the first day of that month in `answer` as a `datetime.date`."
    )
    reference = "answer = kit.run().summary().breakeven_period\nprint(answer)\n"
    return task(rng, variant, family="breakeven_month", category="query", prompt=prompt, reference=reference,
                checks=[c_answer(breakeven.isoformat(), "date", "breakeven month")],
                setup=company_setup(c, "from cashkit.model import Settlement\n" + extra.replace("kit.set_item", "kit.set_item", 1)),
                read_only=True, difficulty=2)


# --------------------------------------------------------------------------- #
# Scenario: change one field of an inherited item
# --------------------------------------------------------------------------- #


@family("scenario_terms_change", "scenario", 3, "re-author one inherited item's settlement inside a scenario")
def scenario_terms_change(rng, variant):
    c = company(rng)
    base_days, slow_days = pick(rng, 30, 45), pick(rng, 90, 120)
    prompt = (
        f"{c.description()}\n\nCurrently Acme pays on receipt. Create scenario `acme_slow` (from base) in which "
        f"**only** Acme's payment terms change: Acme pays {slow_days} days after each invoice. Everything else about "
        f"`acme` (amount, tags, dates) stays as it is, and base keeps immediate payment."
    )
    reference = (
        "from cashkit.model import Settlement\n"
        'kit.fork("acme_slow")\n'
        'acme = kit.resolve("acme_slow").book.items["acme"]\n'
        f'kit.set_item(acme.model_copy(update={{"settlement": Settlement.net({slow_days})}}), scenario="acme_slow")\n'
    )
    invoices = monthly_occurrences(c.spec.start, None, c.spec.end)
    slow = bucket([(add_duration(d, f"{slow_days}d"), c.acme) for d in invoices], c.periods, c.spec.end)
    flows = c.flows(drop=("acme",), extra={"acme": slow})
    checks = [
        c_no_errors("acme_slow"),
        Check("scenario", {"id": "acme_slow", "parent": "base"}, "acme_slow forks from base"),
        c_series("acme", c.periods, slow, scenario="acme_slow", label="acme cash arrives late in the scenario"),
        c_series("acme", c.periods, {d: c.acme for d in invoices}, scenario="acme_slow", measure="accrual",
                 label="acme still invoices the same amounts"),
        c_field("acme", "tags.customer", "acme", scenario="acme_slow"),
        c_series("acme", c.periods, {d: c.acme for d in invoices}, scenario="base", label="base acme still pays on receipt"),
        c_closing(c.closing(flows), "acme_slow"),
        c_closing(c.closing(), "base", label="base is unchanged"),
    ]
    return task(rng, variant, family="scenario_terms_change", category="scenario", prompt=prompt, reference=reference,
                checks=checks, setup=company_setup(c), difficulty=3)


# --------------------------------------------------------------------------- #
# Construction: selector retag, calendar-year escalation
# --------------------------------------------------------------------------- #


@family("retag_by_selector", "construction", 2, "RetagItems over a selector in base")
def retag_by_selector(rng, variant):
    c = company(rng)
    dept = pick(rng, "ops", "people", "delivery")
    prompt = (
        f"{c.description()}\n\nFor reporting, every item tagged `cat: payroll` must also carry the tag "
        f"`dept: {dept}`. Apply it in base with a selector (so it is one operation over whatever matches), "
        f"keeping each item's existing tags."
    )
    reference = (
        "from cashkit.sdk import RetagItems\n"
        f'report = kit.apply_macro(RetagItems(selector="cat:payroll", tags={{"dept": "{dept}"}}))\n'
        "print(report.changed)\n"
    )
    checks = [
        c_no_errors(),
        c_field("payroll", "tags.dept", dept),
        c_field("contractor", "tags.dept", dept),
        c_field("payroll", "tags.cat", "payroll", label="existing tags kept"),
        c_field("rent", "tags.dept", None, op="absent", label="non-payroll items untouched"),
        c_field("acme", "tags.dept", None, op="absent", label="non-payroll items untouched"),
        c_closing(c.closing(), "base", label="numbers unchanged"),
    ]
    return task(rng, variant, family="retag_by_selector", category="construction", prompt=prompt, reference=reference,
                checks=checks, setup=company_setup(c), difficulty=2)


@family("escalation_calendar_year", "construction", 3, "escalation stepping each January, not on the anniversary")
def escalation_calendar_year(rng, variant):
    y = _year(rng)
    spec = BookSpec(f"lease_{y}", date(y, 1, 1), date(y + 3, 1, 1), money_draw(rng, 10_000, 50_000, 1_000))
    start = date(y, rng.randrange(3, 10), 1)
    rate = pick(rng, Decimal("0.02"), Decimal("0.03"), Decimal("0.05"))
    amount = money_draw(rng, 900, 2_500, 50)
    prompt = (
        f"{_book_sentence(spec)}\n\nA new lease (item `lease`) starts {month_name(start)}: {eur(amount)} per month. "
        f"It is indexed by {pct(rate)} **every 1 January** (calendar-year indexation, not on the lease "
        f"anniversary), so the first increase applies from January {y + 1}. Use a literal rate."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="lease", name="Lease", kind="flow", direction="out",\n'
        f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(-amount)}),\n"
        f'                      escalation=Escalation(rate={D(rate)}, anchor="calendar_year"))],\n'
        f"))\n"
    )
    flows = {d: money(-amount * (1 + rate) ** (d.year - start.year)) for d in monthly_occurrences(start, None, spec.end)}
    checks = [c_no_errors(), c_series("lease", spec.periods, flows, label="steps every January")]
    return task(rng, variant, family="escalation_calendar_year", category="construction", prompt=prompt,
                reference=reference, checks=checks, setup=setup_book(spec), difficulty=3)


# --------------------------------------------------------------------------- #
# Tax: VAT on the cash tax point
# --------------------------------------------------------------------------- #


@family("iva_per_cassa", "tax", 4, "VAT owed when collected (measure='cash') with long payment terms")
def iva_per_cassa(rng, variant):
    y = _year(rng)
    spec = BookSpec(f"cassa_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 10_000, 60_000, 1_000),
                    "MONTH", (("vat_standard", VAT),))
    sales = money_draw(rng, 4_000, 15_000, 500)
    days = pick(rng, 60, 90)
    prompt = (
        f"{_book_sentence(spec)} The book has `vat_standard` = {pct(VAT)}.\n\n"
        f"We opted into **IVA per cassa**: VAT becomes due when the customer pays, not when we invoice.\n"
        f"- Item `sales`: {eur(sales)} net per month invoiced from {month_name(spec.start)}, standard VAT, "
        f"customers pay {days} days after the invoice.\n"
        f"- VAT regime `iva`: quarterly, on the cash tax point, paid 16 days after each quarter ends."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="sales", name="Sales", kind="flow", direction="in",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(sales)}))],\n"
        f'    vat=VatSpec(rate="vat_standard"), settlement=Settlement.net({days}),\n'
        f"))\n"
        f'kit.set_book(tax_regimes=[TaxRegime(id="iva", accumulates="", measure="cash",\n'
        f'                                    periodicity="quarterly", payment_offset="16d")])\n'
    )
    invoices = monthly_occurrences(spec.start, None, spec.end)
    collected = [(add_duration(d, f"{days}d"), sales) for d in invoices]
    cash = bucket([(day, money(v * (1 + VAT))) for day, v in collected], spec.periods, spec.end)
    vat_by_quarter: dict[int, Decimal] = {}
    for day, v in collected:
        if day >= spec.end:
            continue
        q = (day.month - 1) // 3
        vat_by_quarter[q] = vat_by_quarter.get(q, ZERO) + money(v * VAT)
    payments = bucket([(date(y, 3 * q + 4, 16) if q < 3 else date(y + 1, 1, 16), -v) for q, v in vat_by_quarter.items()],
                      spec.periods, spec.end)
    checks = [
        c_no_errors(),
        c_series("sales", spec.periods, cash, label="sales collect gross, late"),
        Check("tax_regime", {"match": {"id": "iva", "measure": "cash", "periodicity": "quarterly"}}, "cash tax point"),
        Check("synthetic_series", {"kind": "tax", "values": {p.isoformat(): str(money(payments.get(p, ZERO)))
                                                              for p in spec.periods}},
              "VAT paid on what was collected each quarter"),
    ]
    return task(rng, variant, family="iva_per_cassa", category="tax", prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=4, tags=("italian-tax",))


# --------------------------------------------------------------------------- #
# Ledger: an event that inherits an item's terms
# --------------------------------------------------------------------------- #


@family("event_inherits_item", "ledger", 3, "a one-off event attached to an item inherits its VAT and terms")
def event_inherits_item(rng, variant):
    y = _year(rng)
    spec = BookSpec(f"consult_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 10_000, 40_000, 1_000),
                    "MONTH", (("vat_standard", VAT),))
    monthly = money_draw(rng, 2_000, 6_000, 500)
    days = pick(rng, 30, 45)
    body = (
        f'kit.set_item(Item(id="consulting", name="Consulting", kind="flow", direction="in", tags={{"cat": "revenue"}},\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(monthly)}))],\n"
        f'    vat=VatSpec(rate="vat_standard"), settlement=Settlement.net({days})))\n'
        f'kit.set_book(tax_regimes=[TaxRegime(id="iva", accumulates="", periodicity="quarterly", payment_offset="16d")])\n'
    )
    day = date(y, rng.randrange(3, 9), rng.randrange(2, 20))
    extra = money_draw(rng, 3_000, 12_000, 500)
    prompt = (
        f"{_book_sentence(spec)} It has `vat_standard` = {pct(VAT)}, a quarterly VAT regime, and:\n\n```python\n{body}```\n\n"
        f"Record a one-off extra consulting invoice of {eur(extra)} (net) dated {long_date(day)} as forecast "
        f"event `extra_workshop`. It is billed exactly like the `consulting` item — same VAT, same payment "
        f"terms, same tags — so attach it to that item rather than restating them."
    )
    reference = IMPORTS + "\n" + (
        f'kit.add_event(Event(id="extra_workshop", date={dt(day)}, amount={D(extra)}, status="forecast",\n'
        f'                    item="consulting"))\n'
    )
    # An attached event lands in the item's own columns (cashkit/engine/facts.py),
    # settled on the item's terms and grossed up by the item's VAT.
    legs = [(add_duration(d, f"{days}d"), money(monthly * (1 + VAT)))
            for d in monthly_occurrences(spec.start, None, spec.end)]
    legs.append((add_duration(day, f"{days}d"), money(extra * (1 + VAT))))
    consulting_cash = bucket(legs, spec.periods, spec.end)
    checks = [
        c_no_errors(),
        Check("events", {"match": {"id": "extra_workshop", "amount": str(extra), "item": "consulting"}},
              "event attached to the consulting item"),
        c_series("consulting", spec.periods, consulting_cash,
                 label="the workshop is collected gross of VAT on consulting's terms"),
        Check("synthetic_total", {"kind": "events", "value": "0"}, "nothing left in an unattached event column"),
    ]
    return task(rng, variant, family="event_inherits_item", category="ledger", prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body), difficulty=3)


# --------------------------------------------------------------------------- #
# Workflow: one brief, every feature
# --------------------------------------------------------------------------- #


@family("startup_brief", "workflow", 4, "a whole book from a founder's brief: VAT, terms, split, event, scenario")
def startup_brief(rng, variant):
    y = _year(rng)
    spec = BookSpec(f"startup_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 30_000, 120_000, 5_000))
    a = money_draw(rng, 4_000, 10_000, 500)       # monthly, net 60
    b = money_draw(rng, 9_000, 24_000, 1_000)     # quarterly, 30% now / 70% in 2 months
    payroll = money_draw(rng, 9_000, 18_000, 500)
    laptop_day = date(y, rng.randrange(2, 6), 10)
    laptop = money_draw(rng, 2_000, 6_000, 500)
    cut = pick(rng, Decimal("0.5"), Decimal("0.6"))
    prompt = (
        f"Set up a forecast for our startup at `ROOT` (no book exists; `kit` is `None`). Book id `{spec.id}`, "
        f"monthly, calendar year {y}, opening cash {eur(spec.opening)}, VAT standard rate {pct(VAT)} as param "
        f"`vat_standard`.\n\n"
        f"- Customer Alpha (`alpha`, tag customer=alpha): {eur(a)} + VAT invoiced at the start of every month, "
        f"pays 60 days after invoice.\n"
        f"- Customer Beta (`beta`, tag customer=beta): {eur(b)} + VAT invoiced every quarter (1 Jan, 1 Apr, 1 Jul, "
        f"1 Oct); pays 30% on the invoice date and 70% two calendar months later.\n"
        f"- Payroll (`payroll`): {eur(payroll)} per month, no VAT.\n"
        f"- Laptops: a one-off forecast purchase of {eur(laptop)} on {long_date(laptop_day)}, no VAT "
        f"(event `laptops`).\n"
        f"- VAT regime `iva`: quarterly, accrual tax point, paid 16 days after quarter end.\n"
        f"- Scenario `beta_shrinks` (from base): Beta's invoices are {pct(cut)} of base.\n\n"
        f"Leave the kit in `kit`."
    )
    reference = IMPORTS + "\n" + create_code(spec).replace("params={}", f'params={{"vat_standard": {D(VAT)}}}') + (
        "monthly = Recurrence(every=1, unit=Grain.MONTH)\n"
        f'kit.set_item(Item(id="alpha", name="Alpha", kind="flow", direction="in", tags={{"customer": "alpha"}},\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=monthly, amount=Amount(constant={D(a)}))],\n"
        f'    vat=VatSpec(rate="vat_standard"), settlement=Settlement.net(60)))\n'
        f'kit.set_item(Item(id="beta", name="Beta", kind="flow", direction="in", tags={{"customer": "beta"}},\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=3, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(b)}))],\n"
        f'    vat=VatSpec(rate="vat_standard"),\n'
        f'    settlement=Settlement.split([(Decimal("0.3"), "0d"), (Decimal("0.7"), "2m")])))\n'
        + monthly_item_code("payroll", "Payroll", -payroll, spec.start)
        + f'kit.add_event(Event(id="laptops", date={dt(laptop_day)}, amount={D(-laptop)}, status="forecast"))\n'
        + 'kit.set_book(tax_regimes=[TaxRegime(id="iva", accumulates="", measure="accrual", periodicity="quarterly", payment_offset="16d")])\n'
        + 'kit.fork("beta_shrinks")\n'
        + f'kit.apply_macro(ScaleItems(selector="customer:beta", factor={D(cut)}), scenario="beta_shrinks")\n'
    )
    months = monthly_occurrences(spec.start, None, spec.end)
    quarters = monthly_occurrences(spec.start, None, spec.end, every=3)

    def model(beta_amount: Decimal):
        alpha_cash = bucket([(add_duration(d, "60d"), money(a * (1 + VAT))) for d in months], spec.periods, spec.end)
        legs = []
        for d in quarters:
            first = money(beta_amount * Decimal("0.3"))
            second = beta_amount - first
            legs += [(d, first + money(first * VAT)), (add_months(d, 2), second + money(second * VAT))]
        beta_cash = bucket(legs, spec.periods, spec.end)
        vat_q = {}
        for q in range(4):
            out_vat = 3 * money(a * VAT) + money(money(beta_amount * Decimal("0.3")) * VAT) + money(
                (beta_amount - money(beta_amount * Decimal("0.3"))) * VAT)
            vat_q[q] = out_vat
        tax = bucket([(date(y, 3 * q + 4, 16) if q < 3 else date(y + 1, 1, 16), -v) for q, v in vat_q.items()],
                     spec.periods, spec.end)
        pay = {d: -payroll for d in months}
        net = {}
        for m in (alpha_cash, beta_cash, tax, pay, {month_of(laptop_day): -laptop}):
            for k, v in m.items():
                net[k] = net.get(k, ZERO) + v
        return alpha_cash, beta_cash, tax, spec.opening + total(net)

    alpha_cash, beta_cash, tax, base_close = model(b)
    _, shrunk_cash, _, shrink_close = model(money(b * cut))
    checks = [
        c_no_errors(),
        Check("book_field", {"field": "id", "value": spec.id}, "book id"),
        c_series("alpha", spec.periods, alpha_cash, label="Alpha collects gross, 60 days late"),
        c_series("beta", spec.periods, beta_cash, label="Beta 30/70 split, gross"),
        Check("synthetic_series", {"kind": "tax", "values": {p.isoformat(): str(money(tax.get(p, ZERO)))
                                                              for p in spec.periods}}, "quarterly VAT payments"),
        Check("events", {"match": {"id": "laptops", "amount": str(-laptop), "status": "forecast"}}, "laptop event"),
        c_closing(base_close, "base"),
        c_series("beta", spec.periods, shrunk_cash, scenario="beta_shrinks", label="Beta shrinks in the scenario"),
        c_closing(shrink_close, "beta_shrinks"),
    ]
    return task(rng, variant, family="startup_brief", category="workflow", prompt=prompt, reference=reference,
                checks=checks, setup=None, difficulty=4)
