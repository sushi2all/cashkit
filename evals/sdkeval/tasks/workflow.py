"""Workflow: committing at a boundary, re-runnable scripts, multi-step builds."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..oracle import add_months, eur, long_date, month_name, monthly_occurrences, total
from ..schema import Check
from ._common import (
    IMPORTS, BookSpec, D, c_closing, c_no_errors, c_series, create_code, dt, family, money_draw,
    monthly_item_code, pick, setup_book, task,
)
from .construction import _book_sentence, _spec

CAT = "workflow"


@family("commit_after_build", CAT, 2, "build, validate, then commit with a message")
def commit_after_build(rng, variant):
    spec = _spec(rng)
    rent = money_draw(rng, 800, 2_500, 50)
    fees = money_draw(rng, 3_000, 9_000, 100)
    message = pick(rng, "Q1 plan as presented", "baseline budget", "board pack v1")
    prompt = (
        f"{_book_sentence(spec)}\n\nAdd `rent` ({eur(rent)}/month outflow) and `fees` ({eur(fees)}/month income), "
        f"both from {month_name(spec.start)}. Check that the book validates with no errors, then record this "
        f"state as a revision with the message \"{message}\"."
    )
    reference = IMPORTS + "\n" + (
        monthly_item_code("rent", "Rent", -rent, spec.start)
        + monthly_item_code("fees", "Fees", fees, spec.start)
        + "errors = [d for d in kit.validate() if d.severity == 'error']\n"
        + "assert not errors, errors\n"
        + f'report = kit.commit("{message}")\n'
        + "print(report.revision)\n"
    )
    months = monthly_occurrences(spec.start, None, spec.end)
    checks = [
        c_no_errors(),
        c_series("rent", spec.periods, {d: -rent for d in months}),
        c_series("fees", spec.periods, {d: fees for d in months}),
        Check("committed", {"message": message}, "a revision with the requested message"),
        Check("clean", {}, "nothing left uncommitted"),
        Check("called", {"name": "validate"}, "validated before committing (PRD §9.3 rule 3)", critical=False),
    ]
    return task(rng, variant, family="commit_after_build", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2)


@family("rerunnable_build", CAT, 3, "a script that creates-or-opens the book; run twice, same result")
def rerunnable_build(rng, variant):
    spec = _spec(rng)
    rent = money_draw(rng, 800, 2_500, 50)
    salary = money_draw(rng, 2_000, 6_000, 100)
    prompt = (
        f"Write a setup script for a new book `{spec.id}` stored at `ROOT` (no book exists there yet; `kit` is "
        f"`None`): monthly grain, horizon {long_date(spec.start)} to {long_date(spec.end)} (exclusive), opening "
        f"balance {eur(spec.opening)}. It holds `rent` ({eur(rent)}/month outflow) and `salary` "
        f"({eur(salary)}/month outflow), both from {month_name(spec.start)}.\n\n"
        f"The script will be re-run unchanged (e.g. after an interruption): the second run must succeed and must "
        f"leave the book exactly as the first run did. Leave the kit in `kit`."
    )
    reference = IMPORTS + "\n" + (
        "kit, diagnostics = CashKit.open(ROOT)\n"
        "if kit is None:\n"
        + "\n".join("    " + line for line in create_code(spec).splitlines())
        + "\n"
        + monthly_item_code("rent", "Rent", -rent, spec.start)
        + monthly_item_code("salary", "Salary", -salary, spec.start)
    )
    months = monthly_occurrences(spec.start, None, spec.end)
    checks = [
        c_no_errors(),
        Check("book_field", {"field": "id", "value": spec.id}, "book id"),
        c_series("rent", spec.periods, {d: -rent for d in months}, label="rent counted once"),
        c_series("salary", spec.periods, {d: -salary for d in months}, label="salary counted once"),
        c_closing(spec.opening - 12 * (rent + salary)),
    ]
    return task(rng, variant, family="rerunnable_build", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=None, run_twice=True, difficulty=3, tags=("idempotency",))


@family("full_build", CAT, 3, "a multi-step build from one brief: book, params, items, terms, scenario")
def full_build(rng, variant):
    y = pick(rng, 2026, 2027)
    spec = BookSpec(f"studio_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 10_000, 40_000, 1_000))
    day_rate = money_draw(rng, 300, 700, 25)
    days = Decimal(rng.randrange(10, 18))
    rent = money_draw(rng, 700, 1_800, 50)
    terms = pick(rng, 30, 60)
    low_days = days - Decimal(rng.randrange(3, 7))
    prompt = (
        f"Create a book `{spec.id}` at `ROOT` (nothing exists yet; `kit` is `None`): monthly, calendar year {y}, "
        f"opening balance {eur(spec.opening)}. Then:\n"
        f"- params `day_rate` = {day_rate} and `days_billed` = {days};\n"
        f"- derived inflow `billings` = day_rate × days_billed each month (tag `cat: revenue`);\n"
        f"- `rent`: {eur(rent)} per month outflow from January;\n"
        f"- scenario `slow` (from base) where `days_billed` = {low_days}.\n"
        f"Leave the kit in `kit`. (Billings are collected the same month.)"
    )
    reference = IMPORTS + "\n" + create_code(spec).replace("params={}", f'params={{"day_rate": {D(day_rate)}, "days_billed": {D(days)}}}') + (
        'kit.set_item(Item(id="billings", name="Billings", kind="derived", direction="in", tags={"cat": "revenue"},\n'
        '    formula="p.day_rate * p.days_billed"))\n'
        + monthly_item_code("rent", "Rent", -rent, spec.start)
        + 'kit.fork("slow")\n'
        + f'kit.set_param("days_billed", {D(low_days)}, scenario="slow")\n'
    )
    months = spec.periods
    base_closing = spec.opening + 12 * (day_rate * days - rent)
    slow_closing = spec.opening + 12 * (day_rate * low_days - rent)
    checks = [
        c_no_errors(),
        c_no_errors("slow"),
        Check("book_field", {"field": "id", "value": spec.id}, "book id"),
        c_series("billings", months, {d: day_rate * days for d in months}),
        c_series("rent", months, {d: -rent for d in months}),
        Check("scenario", {"id": "slow", "parent": "base"}, "slow forks from base"),
        c_closing(base_closing, "base"),
        c_closing(slow_closing, "slow"),
    ]
    return task(rng, variant, family="full_build", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=None, difficulty=3)
