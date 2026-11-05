"""Scenarios: fork, params in overlays, macros, add/remove, opening balance, nesting.

Every family here also checks that **base did not move** — writing a what-if
into base instead of into the scenario is the most likely scenario mistake,
and it produces plausible numbers in both places.
"""

from __future__ import annotations

from decimal import Decimal

from ..oracle import add_months, eur, month_name, monthly_occurrences, pct
from ..schema import Check
from ._common import D, c_closing, c_exists, c_no_errors, c_param, c_series, dt, family, money_draw, pick, task
from .company import company, setup

CAT = "scenario"


def _base_intact(c) -> list[Check]:
    return [c_closing(c.closing(), "base", label="base is unchanged")]


@family("downside_param", CAT, 2, "fork a scenario and override a param in it")
def downside_param(rng, variant):
    c = company(rng)
    low = Decimal(int(c.units * pick(rng, Decimal("0.5"), Decimal("0.6"), Decimal("0.75"))))
    prompt = (
        f"{c.description()}\n\nCreate a scenario `downside` forked from base in which we only sell {low} units a "
        f"month instead of {c.units}. Base must stay as it is."
    )
    reference = (
        f'kit.fork("downside", parent="base", note="lower volume")\n'
        f'kit.set_param("units", Decimal("{low}"), scenario="downside")\n'
    )
    flows = c.flows(units=low)
    checks = [
        c_no_errors("downside"),
        Check("scenario", {"id": "downside", "parent": "base"}, "downside forks from base"),
        c_param("units", low, "downside"),
        c_param("units", c.units, "base", label="base keeps its units"),
        c_series("product", c.periods, flows["product"], scenario="downside"),
        c_closing(c.closing(flows), "downside"),
        *_base_intact(c),
    ]
    return task(rng, variant, family="downside_param", category=CAT, prompt=prompt,
                reference="from decimal import Decimal\n" + reference, checks=checks, setup=setup(c), difficulty=2)


@family("scale_customer", CAT, 2, "ScaleItems over a tag selector inside a scenario")
def scale_customer(rng, variant):
    c = company(rng)
    cut = pick(rng, Decimal("0.2"), Decimal("0.3"), Decimal("0.5"))
    factor = 1 - cut
    prompt = (
        f"{c.description()}\n\nModel scenario `acme_cut` (child of base): Acme cuts its spend by {pct(cut)} on "
        f"everything tagged `customer: acme`. Use a selector, not item ids, so new Acme items would be caught too."
    )
    reference = (
        "from decimal import Decimal\nfrom cashkit.sdk import ScaleItems\n"
        'kit.fork("acme_cut")\n'
        f'kit.apply_macro(ScaleItems(selector="customer:acme", factor=Decimal("{factor}")), scenario="acme_cut")\n'
    )
    flows = c.flows(acme_factor=factor)
    checks = [
        c_no_errors("acme_cut"),
        Check("scenario", {"id": "acme_cut", "parent": "base"}, "acme_cut forks from base"),
        c_series("acme", c.periods, flows["acme"], scenario="acme_cut"),
        c_series("beta", c.periods, flows["beta"], scenario="acme_cut", label="other customers untouched"),
        c_closing(c.closing(flows), "acme_cut"),
        *_base_intact(c),
        Check("called", {"name": "apply_macro"}, "used a selector macro", critical=False),
    ]
    return task(rng, variant, family="scale_customer", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup(c), difficulty=2)


@family("delay_project", CAT, 2, "ShiftItems: delay everything in a project by N months")
def delay_project(rng, variant):
    c = company(rng)
    months = pick(rng, 1, 2, 3)
    prompt = (
        f"{c.description()}\n\nWhat if project Alpha slips by {months} month(s)? Create scenario `alpha_late` from "
        f"base where every item tagged `project: alpha` starts and ends {months} month(s) later."
    )
    reference = (
        "from cashkit.sdk import ShiftItems\n"
        'kit.fork("alpha_late")\n'
        f'kit.apply_macro(ShiftItems(selector="project:alpha", by="{months}m"), scenario="alpha_late")\n'
    )
    flows = c.flows(alpha_shift=months)
    checks = [
        c_no_errors("alpha_late"),
        Check("scenario", {"id": "alpha_late", "parent": "base"}, "alpha_late forks from base"),
        c_series("alpha_project", c.periods, flows["alpha_project"], scenario="alpha_late"),
        c_closing(c.closing(flows), "alpha_late"),
        *_base_intact(c),
    ]
    return task(rng, variant, family="delay_project", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup(c), difficulty=2)


@family("hire_vs_contractor", CAT, 3, "add one item and remove another inside a scenario")
def hire_vs_contractor(rng, variant):
    c = company(rng)
    salary = money_draw(rng, 3_000, 6_000, 100)
    start = add_months(c.spec.start, rng.randrange(2, 6))
    prompt = (
        f"{c.description()}\n\nScenario `hire` (from base): we replace the contractor with an employee. In this "
        f"scenario the `contractor` item no longer exists at all, and a new item `new_hire` (tag `cat: payroll`) "
        f"costs {eur(salary)} per month from {month_name(start)}. Base keeps the contractor."
    )
    reference = (
        "from datetime import date\nfrom decimal import Decimal\n"
        "from cashkit.model import Amount, Grain, Item, Recurrence, Segment\n"
        'kit.fork("hire")\n'
        'kit.remove_item("contractor", scenario="hire")\n'
        f'kit.set_item(Item(id="new_hire", name="New hire", kind="flow", direction="out", tags={{"cat": "payroll"}},\n'
        f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(-salary)}))]), scenario=\"hire\")\n"
    )
    hire = {d: -salary for d in monthly_occurrences(start, None, c.spec.end)}
    flows = c.flows(drop=("contractor",), extra={"new_hire": hire})
    checks = [
        c_no_errors("hire"),
        c_exists("contractor", False, "hire"),
        c_exists("contractor", True, "base", label="base keeps the contractor"),
        c_exists("new_hire", False, "base", label="new_hire exists only in the scenario"),
        c_series("new_hire", c.periods, hire, scenario="hire"),
        c_closing(c.closing(flows), "hire"),
        *_base_intact(c),
    ]
    return task(rng, variant, family="hire_vs_contractor", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup(c), difficulty=3)


@family("capital_injection", CAT, 2, "opening_balance as the reserved param in a scenario")
def capital_injection(rng, variant):
    c = company(rng)
    raise_ = money_draw(rng, 50_000, 300_000, 5_000)
    prompt = (
        f"{c.description()}\n\nScenario `seed_round` (from base): we close a {eur(raise_)} seed round just before "
        f"the horizon starts, so the opening cash in this scenario is {eur(c.spec.opening + raise_)}. Nothing else "
        f"changes and base keeps its opening balance."
    )
    reference = (
        "from decimal import Decimal\n"
        'kit.fork("seed_round")\n'
        f'kit.set_param("opening_balance", Decimal("{c.spec.opening + raise_}"), scenario="seed_round")\n'
    )
    checks = [
        c_no_errors("seed_round"),
        Check("opening", {"scenario": "seed_round", "value": str(c.spec.opening + raise_)}, "scenario opening balance"),
        Check("opening", {"scenario": "base", "value": str(c.spec.opening)}, "base opening unchanged"),
        c_closing(c.closing(opening=c.spec.opening + raise_), "seed_round"),
        *_base_intact(c),
    ]
    return task(rng, variant, family="capital_injection", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup(c), difficulty=2)


@family("nested_scenarios", CAT, 3, "a scenario forked from a scenario; changes compose down the chain")
def nested_scenarios(rng, variant):
    c = company(rng)
    low = Decimal(int(c.units * Decimal("0.6")))
    months = pick(rng, 2, 3)
    prompt = (
        f"{c.description()}\n\nBuild two scenarios:\n"
        f"1. `downside` from base: units drop to {low}.\n"
        f"2. `downside_late` **from `downside`**: on top of the downside, project Alpha (`project: alpha`) "
        f"slips {months} months.\n"
        f"`downside` itself must not include the slip."
    )
    reference = (
        "from decimal import Decimal\nfrom cashkit.sdk import ShiftItems\n"
        'kit.fork("downside")\n'
        f'kit.set_param("units", Decimal("{low}"), scenario="downside")\n'
        'kit.fork("downside_late", parent="downside")\n'
        f'kit.apply_macro(ShiftItems(selector="project:alpha", by="{months}m"), scenario="downside_late")\n'
    )
    down = c.flows(units=low)
    late = c.flows(units=low, alpha_shift=months)
    checks = [
        c_no_errors("downside_late"),
        Check("scenario", {"id": "downside", "parent": "base"}, "downside forks from base"),
        Check("scenario", {"id": "downside_late", "parent": "downside"}, "downside_late forks from downside"),
        c_closing(c.closing(down), "downside"),
        c_closing(c.closing(late), "downside_late"),
        c_series("alpha_project", c.periods, late["alpha_project"], scenario="downside_late"),
        c_series("product", c.periods, late["product"], scenario="downside_late", label="inherits the lower units"),
        *_base_intact(c),
    ]
    return task(rng, variant, family="nested_scenarios", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup(c), difficulty=3)
