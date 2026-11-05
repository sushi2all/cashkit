"""Formula: derived items over it(), p.<param>, agg(), where(), prev() and cum()."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..oracle import ZERO, add_months, eur, money, month_name, monthly_occurrences, pct, total
from ..schema import Check
from ._common import (
    IMPORTS, BookSpec, D, c_field, c_no_errors, c_param, c_series, dt, family, money_draw, pick, setup_book, task,
)
from .construction import _book_sentence, _year

CAT = "formula"


def _sales_book(rng, *, params=()) -> tuple[BookSpec, dict[date, Decimal], str]:
    """A year book with a `sales` item whose monthly amounts vary (an explicit schedule)."""
    y = _year(rng)
    spec = BookSpec(f"shop_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 5_000, 40_000, 1_000),
                    "MONTH", tuple(params))
    amounts = {d: money_draw(rng, 4_000, 16_000, 100) for d in spec.periods}
    schedule = ", ".join(f"({dt(d)}, {D(v)})" for d, v in amounts.items())
    body = (
        f"kit.set_item(Item(\n"
        f'    id="sales", name="Sales", kind="flow", direction="in", tags={{"cat": "revenue"}},\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(schedule=[{schedule}]))],\n"
        f"))\n"
    )
    return spec, amounts, body


def _setup_listing(setup_body: str) -> str:
    return "It was built with this code (you do not need to re-run it):\n\n```python\n" + setup_body.strip() + "\n```"


@family("derived_commission", CAT, 2, "a derived item = param × another item")
def derived_commission(rng, variant):
    rate = pick(rng, Decimal("0.03"), Decimal("0.05"), Decimal("0.075"), Decimal("0.1"))
    spec, amounts, body = _sales_book(rng)
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body)}\n\nSales staff earn a commission of {pct(rate)} of `sales` "
        f"in the same month. Store the rate as parameter `commission_rate` and add a derived item `commission` "
        f"(an outflow) computed from it, so changing the parameter changes the commission."
    )
    reference = IMPORTS + "\n" + (
        f'kit.set_param("commission_rate", {D(rate)})\n'
        f"kit.set_item(Item(\n"
        f'    id="commission", name="Sales commission", kind="derived", direction="out",\n'
        f"    formula='-p.commission_rate * it(\"sales\")',\n"
        f"))\n"
    )
    flows = {d: -money(v * rate) for d, v in amounts.items()}
    checks = [
        c_no_errors(),
        c_param("commission_rate", rate),
        c_field("commission", "kind", "derived"),
        c_field("commission", "formula", "p.commission_rate", op="contains", label="formula reads the param"),
        c_series("commission", spec.periods, flows),
    ]
    return task(rng, variant, family="derived_commission", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body), difficulty=2)


@family("derived_agg", CAT, 2, "a derived item over a tag selector with agg()")
def derived_agg(rng, variant):
    y = _year(rng)
    spec = BookSpec(f"agency_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 5_000, 40_000, 1_000))
    rate = pick(rng, Decimal("0.05"), Decimal("0.08"), Decimal("0.1"))
    revenue = {name: money_draw(rng, 2_000, 9_000, 100) for name in ("web", "seo", "ads")}
    cost = money_draw(rng, 1_000, 5_000, 100)
    body = "".join(
        f"kit.set_item(Item(id=\"{n}\", name=\"{n.upper()} revenue\", kind=\"flow\", direction=\"in\", "
        f"tags={{\"cat\": \"revenue\"}},\n    segments=[Segment(start={dt(spec.start)}, "
        f"recurrence=Recurrence(every=1, unit=Grain.MONTH), amount=Amount(constant={D(v)}))]))\n"
        for n, v in revenue.items()
    ) + (
        f"kit.set_item(Item(id=\"hosting\", name=\"Hosting\", kind=\"flow\", direction=\"out\", "
        f"tags={{\"cat\": \"cost\"}},\n    segments=[Segment(start={dt(spec.start)}, "
        f"recurrence=Recurrence(every=1, unit=Grain.MONTH), amount=Amount(constant={D(-cost)}))]))\n"
    )
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body)}\n\nAdd a derived outflow `marketing` equal to {pct(rate)} "
        f"of **all revenue** each month — every item tagged `cat: revenue`, including revenue items added later, "
        f"so do not list the current items by id."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="marketing", name="Marketing", kind="derived", direction="out",\n'
        f"    formula='-{rate} * agg(tag=\"cat:revenue\")',\n"
        f"))\n"
    )
    monthly = -money(sum(revenue.values()) * rate)
    checks = [
        c_no_errors(),
        c_field("marketing", "formula", "agg(", op="contains", label="uses agg() over the tag, not item ids"),
        c_series("marketing", spec.periods, {d: monthly for d in spec.periods}),
    ]
    return task(rng, variant, family="derived_agg", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body), difficulty=2)


@family("threshold_bonus", CAT, 3, "elementwise where() with two params")
def threshold_bonus(rng, variant):
    spec, amounts, body = _sales_book(rng)
    threshold = money_draw(rng, 8_000, 12_000, 500)
    rate = pick(rng, Decimal("0.02"), Decimal("0.04"), Decimal("0.05"))
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body)}\n\nIn any month where `sales` is strictly above "
        f"{eur(threshold)}, the team gets a bonus of {pct(rate)} of that month's sales; otherwise nothing. Add "
        f"parameters `bonus_threshold` and `bonus_rate` and a derived outflow item `bonus` that uses them."
    )
    reference = IMPORTS + "\n" + (
        f'kit.set_param("bonus_threshold", {D(threshold)})\n'
        f'kit.set_param("bonus_rate", {D(rate)})\n'
        f"kit.set_item(Item(\n"
        f'    id="bonus", name="Sales bonus", kind="derived", direction="out",\n'
        f"    formula='where(it(\"sales\") > p.bonus_threshold, -p.bonus_rate * it(\"sales\"), 0)',\n"
        f"))\n"
    )
    flows = {d: (-money(v * rate) if v > threshold else ZERO) for d, v in amounts.items()}
    checks = [
        c_no_errors(),
        c_param("bonus_threshold", threshold),
        c_param("bonus_rate", rate),
        c_series("bonus", spec.periods, flows),
    ]
    return task(rng, variant, family="threshold_bonus", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body), difficulty=3, tags=("where-not-if",))


@family("lagged_royalty", CAT, 3, "prev() lag: a cost computed from last month's value")
def lagged_royalty(rng, variant):
    spec, amounts, body = _sales_book(rng)
    rate = pick(rng, Decimal("0.04"), Decimal("0.06"), Decimal("0.08"))
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body)}\n\nWe owe the licensor a royalty of {pct(rate)} of "
        f"**the previous month's** `sales`, paid the following month (nothing is owed in the first month of the "
        f"horizon). Add parameter `royalty_rate` and a derived outflow item `royalty`."
    )
    reference = IMPORTS + "\n" + (
        f'kit.set_param("royalty_rate", {D(rate)})\n'
        f"kit.set_item(Item(\n"
        f'    id="royalty", name="Royalty", kind="derived", direction="out",\n'
        f"    formula='-p.royalty_rate * prev(\"sales\", n=1, init=0)',\n"
        f"))\n"
    )
    ordered = list(amounts.items())
    flows = {ordered[i][0]: -money(ordered[i - 1][1] * rate) for i in range(1, len(ordered))}
    checks = [c_no_errors(), c_param("royalty_rate", rate), c_series("royalty", spec.periods, flows)]
    return task(rng, variant, family="lagged_royalty", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body), difficulty=3)


@family("cumulative_tracker", CAT, 2, "cum() running total as a derived stock")
def cumulative_tracker(rng, variant):
    spec, amounts, body = _sales_book(rng)
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body)}\n\nAdd a derived **stock** item `ytd_sales` holding "
        f"year-to-date sales (the running total of `sales` since the start of the horizon). It is a tracker, "
        f"not a cash flow, so it must not move the bank balance."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="ytd_sales", name="YTD sales", kind="stock", agg_rule="last",\n'
        f"    formula='cum(\"sales\")',\n"
        f"))\n"
    )
    running, ytd = ZERO, {}
    for d, v in amounts.items():
        running += v
        ytd[d] = running
    checks = [
        c_no_errors(),
        c_field("ytd_sales", "kind", "stock"),
        c_series("ytd_sales", spec.periods, ytd, measure="accrual", label="running total of sales"),
        Check("closing", {"scenario": "base", "value": str(money(spec.opening + total(amounts)))},
              "the tracker does not move the balance"),
    ]
    return task(rng, variant, family="cumulative_tracker", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body), difficulty=2)
