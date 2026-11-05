"""The eval's own correctness gate, half two: plausible-wrong answers FAIL.

A grader that passes the reference proves nothing on its own — a grader that
checks nothing passes it too. Here every family gets at least one mutation of
its reference solution that encodes a realistic mistake (the exclusive-end
off-by-one, authoring a gross price, writing a what-if into base, a float
answer, a non-idempotent re-run …), and the mutated script must fail a
**critical** check. Plus the null script: doing nothing must fail everywhere.

Mutations are textual edits of variant 0's reference: each ``old`` must occur
in it (asserted), so a refactor of a family that silently disarms its control
fails here rather than passing vacuously.
"""

from __future__ import annotations

import re

import pytest

from sdkeval import checks, sandbox
from sdkeval.tasks import FAMILIES, instance


def _grade(task, code):
    obs = sandbox.execute(code, setup=task.setup, run_twice=task.run_twice)
    results = checks.grade(checks.checks_for(task.checks, read_only=task.read_only), obs, code)
    passed = obs.get("status") == "ok" and all(r.passed for r in results if r.critical)
    return passed, obs, results


# (family, description, [(old, new), ...]) — regex=True entries use re.sub once.
MUTATIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("create_book", "daily instead of monthly grain", [("grain=Grain.MONTH", "grain=Grain.DAY")]),
    ("monthly_expense", "rent authored as income", [('direction="out"', 'direction="in"'), ('Decimal("-', 'Decimal("')]),
    ("fixed_term_income", "end set to the last month (end is exclusive)", [("re:end=date\\((\\d+), (\\d+), 1\\),  # end is exclusive", "END_LAST_MONTH")]),
    ("quarterly_cost", "monthly instead of quarterly", [("every=3", "every=1")]),
    ("escalating_rent", "rate as a literal, no param", [('Escalation(rate="rent_escalation")', 'Escalation(rate=Decimal("0.03"))'), ('kit.set_param("rent_escalation"', 'print("skip"')]),
    ("phased_contract", "two items instead of one with two segments", [("        Segment(start", "        Segment(start", )]),
    ("seasonal_schedule", "schedule shifted by a month", [("re:\\(date\\((\\d+), (\\d+), 1\\), Decimal", "SHIFT_SCHEDULE")]),
    ("payday_business_day", "pays a day early", [("re:day=(\\d+),", "DAY_MINUS_ONE")]),
    ("pipeline_probability", "probability not applied", [("probability=Decimal(", "probability=Decimal('1') if True else Decimal(")]),
    ("net_terms", "paid immediately", [("Settlement.net(", "Settlement.immediate() or Settlement.net(")]),
    ("split_payment", "shares swapped", [("SWAP_SHARES", "")]),
    ("deposit_remainder", "remainder paid on the invoice date", [('DueTerm(remainder=True, offset="', 'DueTerm(remainder=True, offset="0d") if True else DueTerm(remainder=True, offset="')]),
    ("withholding", "remittance item missing", [("kit.set_item(Item(\n    id=\"f24_ritenute\"", "print(Item(\n    id=\"f24_ritenute\"")]),
    ("vat_quarterly", "monthly regime", [('periodicity="quarterly"', 'periodicity="monthly"')]),
    ("vat_inclusive_price", "gross price authored (VAT counted twice)", [("re:amount=Amount\\(constant=Decimal\\(\"(-[0-9.]+)\"\\)\\)", "GROSS_AMOUNT")]),
    ("manual_tax_item", "missing the manual_tax flag", [('flags={"manual_tax"}', "flags=set()")]),
    ("derived_commission", "literal rate in the formula", [("-p.commission_rate *", "-0.05 *")]),
    ("derived_agg", "lists current items instead of agg()", [('agg(tag="cat:revenue")', '(it("web") + it("seo") + it("ads"))')]),
    ("threshold_bonus", "branches swapped", [('-p.bonus_rate * it("sales"), 0)', '0, -p.bonus_rate * it("sales"))')]),
    ("lagged_royalty", "same month instead of previous", [('prev("sales", n=1, init=0)', 'it("sales")')]),
    ("cumulative_tracker", "derived flow instead of stock (moves cash)", [('kind="stock", agg_rule="last"', 'kind="derived", direction="in"')]),
    ("forecast_events", "refund recorded as forecast", [('status="committed"', 'status="forecast"')]),
    ("import_actuals", "non-idempotent re-import", [("ext_id=tx, note=memo)", "ext_id=f\"{tx}-{len(kit.events_for('base')[0])}\", note=memo)"), ("Event(id=tx.lower(),", "Event(id=f\"{tx.lower()}_{len(kit.events_for('base')[0])}\",")]),
    ("void_forecast", "offset with a negative event instead of voiding", [('kit.void_event("big_order", note="customer withdrew")', "OFFSET_EVENT")]),
    ("correct_actual", "voids the actual (refused) instead of correcting", [('report = kit.correct_event(old.id, fixed, note="keying error")', 'report = kit.void_event(old.id, note="keying error")')]),
    ("downside_param", "what-if written into base", [(', scenario="downside")', ")")]),
    ("scale_customer", "scaled by the cut, not the remainder", [("SCALE_BY_CUT", "")]),
    ("delay_project", "delayed one month too many", [("re:by=\"(\\d)m\"", "DELAY_PLUS_ONE")]),
    ("hire_vs_contractor", "contractor removed from base", [('kit.remove_item("contractor", scenario="hire")', 'kit.remove_item("contractor")')]),
    ("capital_injection", "opening set to the raise alone", [("SET_RAISE_ONLY", "")]),
    ("nested_scenarios", "late scenario forked from base", [('kit.fork("downside_late", parent="downside")', 'kit.fork("downside_late", parent="base")')]),
    ("runway_month", "answers the period index, not the date", [("summary().runway_end", "summary().runway_periods")]),
    ("min_cash", "float answer", [("answer = kit.run().summary().min_cash", "answer = float(kit.run().summary().min_cash)")]),
    ("scenario_gap", "sign reversed", [('answer = kit.run("base")', 'answer = -kit.run("base")'), ('- kit.run("downside")', '+ kit.run("downside")')]),
    ("cash_vs_accrual", "reads accrual instead of cash", [("measures=['cash']", "measures=['accrual']")]),
    ("total_outflow", "negative instead of positive", [("answer = -kit.run()", "answer = kit.run()")]),
    ("fix_unknown_reference", "deletes the failing item", [("kit.set_item(Item(id=\"commission\"", "kit.remove_item(\"commission\") or print(Item(id=\"commission\""), ("assert not [d", "#")]),
    ("fix_missing_param", "wrong rate", [("re:kit.set_param\\(\"card_fee_rate\", Decimal\\(\"([0-9.]+)\"\\)\\)", "WRONG_RATE")]),
    ("fix_inverted_sign", "direction flipped but amount still positive", [("Amount(constant=-s.amount.constant)", "Amount(constant=s.amount.constant)")]),
    ("fix_cycle", "two-month lag", [('prev("profit", n=1', 'prev("profit", n=2')]),
    ("commit_after_build", "never commits", [("re:report = kit.commit\\(.*\\)\\nprint\\(report.revision\\)", "NO_COMMIT")]),
    ("rerunnable_build", "plain create_book (second run gets None)", [("kit, diagnostics = CashKit.open(ROOT)\nif kit is None:\n", "if True:\n")]),
    ("full_build", "scenario never forked", [('kit.fork("slow")\n', ""), ('scenario="slow")', ")")]),
    ("intra_month_trough", "reports a month-end balance", [("summary().min_cash", "summary().closing_balance")]),
    ("breakeven_month", "answers the trough month", [("summary().breakeven_period", "summary().min_cash_period")]),
    ("scenario_terms_change", "new terms written into base", [('scenario="acme_slow")\n', ")\n")]),
    ("retag_by_selector", "wrong selector", [('selector="cat:payroll"', 'selector="cat:premises"')]),
    ("escalation_calendar_year", "escalates on the anniversary", [(', anchor="calendar_year"', "")]),
    ("iva_per_cassa", "accrual tax point", [('measure="cash"', 'measure="accrual"')]),
    ("event_inherits_item", "event not attached (no VAT, no terms)", [(',\n                    item="consulting"))', "))")]),
    ("startup_brief", "two calendar months written as 60 days", [('(Decimal("0.7"), "2m")', '(Decimal("0.7"), "60d")')]),
]


def _apply(task, family: str, edits: list[tuple[str, str]]) -> str:
    code = task.reference
    for old, new in edits:
        # Mutations whose replacement depends on the variant's numbers.
        if new == "END_LAST_MONTH":
            m = re.search(old[3:], code)
            assert m, (family, old)
            y, mo = int(m.group(1)), int(m.group(2))
            y, mo = (y - 1, 12) if mo == 1 else (y, mo - 1)
            code = code[: m.start()] + f"end=date({y}, {mo}, 1)," + code[m.end():]
            continue
        if new == "SHIFT_SCHEDULE":
            def shift(m):
                y, mo = int(m.group(1)), int(m.group(2))
                y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
                return f"(date({y}, {mo}, 1), Decimal"
            code, n = re.subn(old[3:], shift, code)
            assert n, (family, old)
            continue
        if new == "GROSS_AMOUNT":
            m = re.search(old[3:], code)
            assert m, (family, old)
            from decimal import Decimal
            gross = (Decimal(m.group(1)) * Decimal("1.22")).quantize(Decimal("0.0001"))
            code = code[: m.start()] + f'amount=Amount(constant=Decimal("{gross}"))' + code[m.end():]
            continue
        if new == "DELAY_PLUS_ONE":
            m = re.search(old[3:], code)
            assert m, (family, old)
            code = code[: m.start()] + f'by="{int(m.group(1)) + 1}m"' + code[m.end():]
            continue
        if new == "DAY_MINUS_ONE":
            m = re.search(old[3:], code)
            assert m, (family, old)
            code = code[: m.start()] + f"day={int(m.group(1)) - 1}," + code[m.end():]
            continue
        if new == "WRONG_RATE":
            m = re.search(old[3:], code)
            assert m, (family, old)
            code = code[: m.start()] + 'kit.set_param("card_fee_rate", Decimal("0.03"))' + code[m.end():]
            continue
        if new == "NO_COMMIT":
            code, n = re.subn(old[3:], "pass", code)
            assert n, (family, old)
            continue
        if new == "OFFSET_EVENT":
            assert old in code, (family, old)
            amount = re.search(r'id="big_order", date=(date\([^)]*\)), amount=Decimal\("([0-9.]+)"\)', task.setup)
            code = code.replace(old, "from cashkit.model import Event\n"
                                f'kit.add_event(Event(id="big_order_reversal", date={amount.group(1)}, '
                                f'amount=Decimal("-{amount.group(2)}"), status="forecast"))')
            continue
        if old == "SWAP_SHARES":
            m = re.search(r'Settlement\.split\(\[\(Decimal\("([0-9.]+)"\), "0d"\), \(Decimal\("([0-9.]+)"\)', code)
            assert m, family
            a, b = m.group(1), m.group(2)
            if a == b:  # 50/50 — swap timing instead
                code = re.sub(r'"(\d)m"\)\]\)', lambda mm: f'"{int(mm.group(1)) + 1}m")])', code, count=1)
            else:
                code = code.replace(f'(Decimal("{a}"), "0d"), (Decimal("{b}")', f'(Decimal("{b}"), "0d"), (Decimal("{a}")')
            continue
        if old == "SCALE_BY_CUT":
            m = re.search(r'factor=Decimal\("([0-9.]+)"\)', code)
            assert m, family
            from decimal import Decimal
            cut = 1 - Decimal(m.group(1))
            code = code.replace(m.group(0), f'factor=Decimal("{cut}")')
            continue
        if old == "SET_RAISE_ONLY":
            m = re.search(r'kit.set_param\("opening_balance", Decimal\("([0-9.]+)"\)', code)
            assert m, family
            opening = re.search(r'opening_balance=Decimal\("([0-9.]+)"\)', task.setup).group(1)
            from decimal import Decimal
            raise_only = Decimal(m.group(1)) - Decimal(opening)
            code = code.replace(m.group(0), f'kit.set_param("opening_balance", Decimal("{raise_only}")')
            continue
        if family == "phased_contract":
            # Split the two segments into two items.
            code = code.replace(
                "    segments=[\n        Segment(start",
                "    segments=[\n        Segment(start", 1)
            first, second = code.split("        Segment(start", 2)[1:]
            head = code.split("    segments=[\n")[0]
            seg1 = "Segment(start" + first.rstrip().rstrip(",")
            seg2 = "Segment(start" + second.split("\n    ],")[0].rstrip().rstrip(",")
            code = (head + f"    segments=[{seg1}],\n))\n" + head.replace('id="acme_contract"', 'id="acme_run_rate"')
                    + f"    segments=[{seg2}],\n))\n")
            continue
        assert old in code, f"{family}: mutation anchor {old!r} not in reference"
        code = code.replace(old, new, 1)
    assert code != task.reference, family
    return code


#: Controls where the mistake surfaces as a crash by design (the second run of a
#: non-idempotent script gets ``None`` back from ``create_book``).
CRASH_EXPECTED = {"rerunnable_build"}


def test_every_family_has_a_mutation():
    covered = {family for family, _, _ in MUTATIONS}
    assert covered == set(FAMILIES), sorted(set(FAMILIES) ^ covered)


@pytest.mark.parametrize("family,description,edits", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_plausible_mistake_fails(family, description, edits):
    task = instance(family, 0)
    code = _apply(task, family, edits)
    passed, obs, results = _grade(task, code)
    assert not passed, f"{family}: the mutation '{description}' was graded as a pass"
    if family not in CRASH_EXPECTED:
        # A crash would fail the sample without proving any *check* works.
        assert obs["status"] == "ok", f"{family}: the mutated script crashed instead: {obs.get('exception')}"
        assert any(r.critical and not r.passed for r in results), family


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_doing_nothing_fails(family):
    task = instance(family, 0)
    passed, obs, results = _grade(task, "pass\n")
    assert not passed, f"{family}: an empty script passes"
