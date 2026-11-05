"""Repair: start from a book that reports error diagnostics; leave it validating clean.

These measure the loop the SDK is designed around — "errors are data": the
model must read a diagnostic, find the cause, and fix *that*, rather than
delete the item that complains.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..oracle import ZERO, eur, money, month_name, monthly_occurrences, pct
from ..schema import Check
from ._common import D, c_exists, c_field, c_no_errors, c_param, c_series, dt, family, money_draw, pick, setup_book, task
from .construction import _book_sentence
from .formula import _sales_book, _setup_listing

CAT = "repair"


@family("fix_unknown_reference", CAT, 2, "CK-E001: a formula names an item that does not exist (typo)")
def fix_unknown_reference(rng, variant):
    rate = pick(rng, Decimal("0.05"), Decimal("0.1"))
    spec, amounts, body = _sales_book(rng, params=(("commission_rate", rate),))
    typo = pick(rng, "salse", "sale", "sales_total")
    broken = (
        f'kit.set_item(Item(id="commission", name="Commission", kind="derived", direction="out",\n'
        f"    formula='-p.commission_rate * it(\"{typo}\")'))\n"
    )
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body + broken)}\n\n`kit.validate()` reports an error on "
        f"`commission`. Find out why and fix the book so it validates with no errors. Commission is meant to be "
        f"`commission_rate` × `sales`."
    )
    reference = (
        "from cashkit.model import Item\n"
        "for d in kit.validate():\n    print(d.code, d.item_id, d.message)\n"
        'kit.set_item(Item(id="commission", name="Commission", kind="derived", direction="out",\n'
        "    formula='-p.commission_rate * it(\"sales\")'))\n"
        "assert not [d for d in kit.validate() if d.severity == 'error']\n"
    )
    checks = [
        c_no_errors(),
        c_exists("commission", label="commission was fixed, not deleted"),
        c_series("commission", spec.periods, {d: -money(v * rate) for d, v in amounts.items()}),
    ]
    return task(rng, variant, family="fix_unknown_reference", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body + broken), difficulty=2)


@family("fix_missing_param", CAT, 2, "CK-E008: a formula reads a param the book does not define")
def fix_missing_param(rng, variant):
    spec, amounts, body = _sales_book(rng)
    rate = pick(rng, Decimal("0.015"), Decimal("0.025"), Decimal("0.029"))
    broken = (
        f'kit.set_item(Item(id="card_fees", name="Card fees", kind="derived", direction="out",\n'
        f"    formula='-p.card_fee_rate * it(\"sales\")'))\n"
    )
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body + broken)}\n\nValidation fails. The `card_fees` formula is "
        f"correct as written; the processor charges {pct(rate)} of sales. Fix the book."
    )
    reference = f'from decimal import Decimal\nkit.set_param("card_fee_rate", Decimal("{rate}"))\n'
    checks = [
        c_no_errors(),
        c_param("card_fee_rate", rate),
        c_field("card_fees", "formula", "p.card_fee_rate", op="contains", label="formula kept as written"),
        c_series("card_fees", spec.periods, {d: -money(v * rate) for d, v in amounts.items()}),
    ]
    return task(rng, variant, family="fix_missing_param", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body + broken), difficulty=2)


@family("fix_inverted_sign", CAT, 2, "an expense authored as an inflow must become an outflow")
def fix_inverted_sign(rng, variant):
    from .construction import _spec

    spec = _spec(rng)
    amount = money_draw(rng, 800, 3_000, 50)
    body = (
        f'kit.set_item(Item(id="lease", name="Van lease", kind="flow", direction="in",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(amount)}))]))\n"
    )
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body)}\n\nThe van lease is a cost we pay, {eur(amount)} a month, "
        f"but the forecast shows it as income. Fix `lease` (keep the id)."
    )
    reference = (
        "from decimal import Decimal\nfrom cashkit.model import Amount, Segment\n"
        'lease = kit.book.items["lease"]\n'
        "segments = [s.model_copy(update={\"amount\": Amount(constant=-s.amount.constant)}) for s in lease.segments]\n"
        'kit.set_item(lease.model_copy(update={"direction": "out", "segments": segments}))\n'
    )
    flows = {d: -amount for d in monthly_occurrences(spec.start, None, spec.end)}
    checks = [
        c_no_errors(),
        c_field("lease", "direction", "out"),
        c_series("lease", spec.periods, flows),
    ]
    return task(rng, variant, family="fix_inverted_sign", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body), difficulty=2)


@family("fix_cycle", CAT, 3, "CK-E002: break a dependency cycle with prev()")
def fix_cycle(rng, variant):
    spec, amounts, body = _sales_book(rng)
    rate = pick(rng, Decimal("0.1"), Decimal("0.15"), Decimal("0.2"))
    broken = (
        'kit.set_item(Item(id="profit", name="Profit", kind="derived", formula=\'it("sales") + it("bonus")\'))\n'
        f'kit.set_item(Item(id="bonus", name="Bonus", kind="derived", direction="out",\n'
        f"    formula='-{rate} * it(\"profit\")'))\n"
    )
    prompt = (
        f"{_book_sentence(spec)} {_setup_listing(body + broken)}\n\nThe book reports a circular dependency "
        f"between `profit` and `bonus`. The intent: the bonus paid each month is {pct(rate)} of **the previous "
        f"month's** profit (zero in the first month); profit is sales plus the (negative) bonus. Fix it."
    )
    reference = (
        "from cashkit.model import Item\n"
        f'kit.set_item(Item(id="bonus", name="Bonus", kind="derived", direction="out",\n'
        f"    formula='-{rate} * prev(\"profit\", n=1, init=0)'))\n"
    )
    bonus, previous = {}, ZERO
    for d, sales in amounts.items():
        b = -money(rate * previous)
        bonus[d] = b
        previous = sales + b
    checks = [c_no_errors(), c_exists("bonus"), c_series("bonus", spec.periods, bonus)]
    return task(rng, variant, family="fix_cycle", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec, body + broken), difficulty=3)
