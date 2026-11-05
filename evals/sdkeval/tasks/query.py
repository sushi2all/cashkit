"""Queries: read-only questions answered from a run, returned in ``answer``.

Every family here is ``read_only``: any write call is a hygiene violation
(ADR-0029 — question turns never write). Answers must be exact ``Decimal`` or
``date`` values; a float answer fails, because a float is how a forecast gets
a cent wrong without anyone noticing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..oracle import ZERO, add_duration, bucket, eur, money, month_name, monthly_occurrences, pct, total
from ..schema import Check
from ._common import c_answer, family, money_draw, pick, task
from .company import company, setup

CAT = "query"

ANSWER_NOTE = (
    "\n\nDo not change the book. Put the result in a variable named `answer` "
    "({kind})."
)


@family("runway_month", CAT, 2, "first month the balance goes negative (summary().runway_end)")
def runway_month(rng, variant):
    c = company(rng, burn=True)
    bal = c.balances()
    negative = [d for d, v in bal.items() if v < 0]
    if not negative:  # the draw must actually run out; deepen it deterministically
        c.payroll += Decimal(10_000)
        from .company import _body
        c.body = _body(c)
        bal = c.balances()
        negative = [d for d, v in bal.items() if v < 0]
    first = negative[0]
    prompt = pick(
        rng,
        f"{c.description()}\n\nWhen do we run out of cash in the base scenario?",
        f"{c.description()}\n\nIn which month does the base-scenario balance first go below zero?",
    ) + ANSWER_NOTE.format(kind="a `datetime.date`: the first day of that month")
    reference = "run = kit.run()\nanswer = run.summary().runway_end\nprint(answer)\n"
    return task(rng, variant, family="runway_month", category=CAT, prompt=prompt, reference=reference,
                checks=[c_answer(first.isoformat(), "date", "runway month is right")], setup=setup(c),
                read_only=True, difficulty=2)


@family("min_cash", CAT, 2, "lowest balance over the horizon")
def min_cash(rng, variant):
    c = company(rng, dip=True)
    low = min(c.balances().values())
    prompt = (
        f"{c.description()}\n\nWhat is the lowest cash balance we reach in the base scenario over the horizon?"
        + ANSWER_NOTE.format(kind="a `Decimal`, signed as the balance is")
    )
    reference = "answer = kit.run().summary().min_cash\nprint(answer)\n"
    return task(rng, variant, family="min_cash", category=CAT, prompt=prompt, reference=reference,
                checks=[c_answer(low, "decimal", "minimum balance is right")], setup=setup(c),
                read_only=True, difficulty=2)


@family("scenario_gap", CAT, 2, "compare closing balances of two existing scenarios")
def scenario_gap(rng, variant):
    c = company(rng)
    low = Decimal(int(c.units * pick(rng, Decimal("0.5"), Decimal("0.7"))))
    extra = (
        'kit.fork("downside")\n'
        f'kit.set_param("units", Decimal("{low}"), scenario="downside")\n'
    )
    gap = c.closing() - c.closing(c.flows(units=low))
    prompt = (
        f"{c.description()}\n\nThere is also a scenario `downside` (from base) where `units` = {low}.\n\n"
        f"By how much does the downside end the year with less cash than base?"
        + ANSWER_NOTE.format(kind="a positive `Decimal`")
    )
    reference = (
        'answer = kit.run("base").summary().closing_balance - kit.run("downside").summary().closing_balance\n'
        "print(answer)\n"
    )
    return task(rng, variant, family="scenario_gap", category=CAT, prompt=prompt, reference=reference,
                checks=[c_answer(gap, "decimal", "closing-balance gap is right")], setup=setup(c, extra),
                read_only=True, difficulty=2)


@family("cash_vs_accrual", CAT, 2, "cash actually received in a month for an item with payment terms")
def cash_vs_accrual(rng, variant):
    c = company(rng)
    days = pick(rng, 60, 90)
    invoices = monthly_occurrences(c.spec.start, None, c.spec.end)
    # Every month's invoice differs, so in no month does cash equal accrual by
    # coincidence — reading the wrong measure must give a wrong answer.
    base_amount = money_draw(rng, 4_000, 8_000, 250)
    step = money_draw(rng, 100, 600, 50)
    amounts = {d: base_amount + i * step for i, d in enumerate(invoices)}
    schedule = ", ".join(f'(date({d.year}, {d.month}, 1), Decimal("{v}"))' for d, v in amounts.items())
    extra = (
        "from cashkit.model import Settlement\n"
        f'kit.set_item(Item(id="gamma", name="Gamma", kind="flow", direction="in", tags={{"customer": "gamma"}},\n'
        f"    segments=[Segment(start=date({c.spec.start.year}, 1, 1), recurrence=monthly,\n"
        f"                      amount=Amount(schedule=[{schedule}]))],\n"
        f"    settlement=Settlement.net({days})))\n"
    )
    cash = bucket([(add_duration(d, f"{days}d"), amounts[d]) for d in invoices], c.periods, c.spec.end)
    month = c.periods[rng.randrange(3, 9)]  # after the first payment lands: cash is a past invoice
    expected = cash.get(month, ZERO)
    listing = ", ".join(f"{month_name(d, year=False)[:3]} {eur(v)}" for d, v in amounts.items())
    prompt = (
        f"{c.description()}\n- `gamma` invoiced at the start of every month ({listing}), tags customer=gamma, "
        f"paid net {days} days.\n\nHow much cash do we actually **receive** from `gamma` in {month_name(month)} "
        f"(base scenario)?" + ANSWER_NOTE.format(kind="a `Decimal`")
    )
    reference = (
        "from datetime import date\nfrom decimal import Decimal\n"
        "table = kit.run().frame(measures=['cash'], where='customer:gamma', include_synthetic=False)\n"
        "assert table.ok, table.diagnostics\n"
        f"month = date({month.year}, {month.month}, 1)\n"
        "answer = sum((r['value'] for r in table.to_dicts() if r['period_start'] == month), Decimal(0))\n"
        "print(answer)\n"
    )
    return task(rng, variant, family="cash_vs_accrual", category=CAT, prompt=prompt, reference=reference,
                checks=[c_answer(expected, "decimal", "cash (not accrual) for the month")],
                setup=setup(c, extra), read_only=True, difficulty=2, tags=("trap:cash-vs-accrual",))


@family("total_outflow", CAT, 1, "total cash paid out over the horizon in a scenario")
def total_outflow(rng, variant):
    c = company(rng)
    flows = c.flows()
    out = -sum((v for item in flows.values() for v in item.values() if v < 0), ZERO)
    prompt = (
        f"{c.description()}\n\nWhat is the total cash we pay out over the whole horizon in base?"
        + ANSWER_NOTE.format(kind="a positive `Decimal`")
    )
    reference = "answer = -kit.run().summary().total_outflow\nprint(answer)\n"
    return task(rng, variant, family="total_outflow", category=CAT, prompt=prompt, reference=reference,
                checks=[c_answer(out, "decimal", "total outflow is right")], setup=setup(c),
                read_only=True, difficulty=1)
