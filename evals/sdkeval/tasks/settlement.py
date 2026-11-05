"""Settlement: payment terms, share splits, deposits with remainder, withholding."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..oracle import ZERO, add_duration, add_months, bucket, eur, money, month_name, monthly_occurrences, pct, total
from ..schema import Check
from ._common import IMPORTS, D, c_closing, c_field, c_no_errors, c_series, dt, family, money_draw, pick, setup_book, task
from .construction import _book_sentence, _spec

CAT = "settlement"


@family("net_terms", CAT, 2, "monthly invoices collected N days after invoice; accrual vs cash")
def net_terms(rng, variant):
    spec = _spec(rng)
    amount = money_draw(rng, 2_000, 12_000, 250)
    days = pick(rng, 45, 60, 90)
    start = add_months(spec.start, rng.randrange(0, 3))
    prompt = pick(
        rng,
        f"{_book_sentence(spec)}\n\nWe invoice Gamma {eur(amount)} at the start of every month from "
        f"{month_name(start)}; Gamma pays {days} days after the invoice date. Add item `gamma` "
        f"(tag `customer: gamma`) so that accrual shows the invoices and cash shows the payments.",
        f"{_book_sentence(spec)}\n\nAdd revenue item `gamma`, tag customer=gamma: {eur(amount)} invoiced monthly "
        f"from {month_name(start)}, payment terms net {days} days.",
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="gamma", name="Gamma", kind="flow", direction="in", tags={{"customer": "gamma"}},\n'
        f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(amount)}))],\n"
        f"    settlement=Settlement.net({days}),\n"
        f"))\n"
    )
    invoices = monthly_occurrences(start, None, spec.end)
    accrual = {d: amount for d in invoices}
    cash = bucket([(add_duration(d, f"{days}d"), amount) for d in invoices], spec.periods, spec.end)
    checks = [
        c_no_errors(),
        c_series("gamma", spec.periods, accrual, measure="accrual", label="invoices accrue monthly"),
        c_series("gamma", spec.periods, cash, label=f"cash arrives {days} days later"),
        c_closing(spec.opening + total(cash)),
    ]
    return task(rng, variant, family="net_terms", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2)


@family("split_payment", CAT, 2, "share split: part on invoice, the rest months later")
def split_payment(rng, variant):
    spec = _spec(rng)
    amount = money_draw(rng, 1_000, 9_000, 10)
    upfront = pick(rng, Decimal("0.3"), Decimal("0.4"), Decimal("0.5"))
    later = pick(rng, 2, 3)
    start = add_months(spec.start, rng.randrange(0, 3))
    prompt = (
        f"{_book_sentence(spec)}\n\nWe bill Delta {eur(amount)} at the start of each month from "
        f"{month_name(start)}. Delta pays {pct(upfront)} immediately on the invoice date and the remaining "
        f"{pct(1 - upfront)} exactly {later} calendar months after the invoice. Add item `delta`."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="delta", name="Delta", kind="flow", direction="in",\n'
        f"    segments=[Segment(start={dt(start)}, recurrence=Recurrence(every=1, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(amount)}))],\n"
        f'    settlement=Settlement.split([({D(upfront)}, "0d"), ({D(1 - upfront)}, "{later}m")]),\n'
        f"))\n"
    )
    first_leg = money(amount * upfront)
    legs = []
    for d in monthly_occurrences(start, None, spec.end):
        legs.append((d, first_leg))
        legs.append((add_months(d, later), amount - first_leg))
    cash = bucket(legs, spec.periods, spec.end)
    checks = [
        c_no_errors(),
        c_field("delta", "settlement.due", 2, op="len", label="two settlement legs"),
        c_series("delta", spec.periods, cash),
    ]
    return task(rng, variant, family="split_payment", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=2)


@family("deposit_remainder", CAT, 3, "fixed deposit leg plus a remainder leg")
def deposit_remainder(rng, variant):
    spec = _spec(rng)
    fee = money_draw(rng, 6_000, 20_000, 500)
    deposit = money_draw(rng, 1_000, 3_000, 250)
    days = pick(rng, 45, 60)
    prompt = (
        f"{_book_sentence(spec)}\n\nA maintenance contract with Epsilon is billed {eur(fee)} every quarter, invoices "
        f"on 1 January, 1 April, 1 July and 1 October {spec.start.year}. On each invoice Epsilon pays a fixed "
        f"deposit of {eur(deposit)} on the invoice date and whatever remains {days} days after the invoice. "
        f"Add it as item `epsilon` using a fixed-amount term plus a remainder term."
    )
    reference = IMPORTS + "\n" + (
        f"kit.set_item(Item(\n"
        f'    id="epsilon", name="Epsilon maintenance", kind="flow", direction="in",\n'
        f"    segments=[Segment(start={dt(spec.start)}, end={dt(spec.end)},\n"
        f"                      recurrence=Recurrence(every=3, unit=Grain.MONTH),\n"
        f"                      amount=Amount(constant={D(fee)}))],\n"
        f"    settlement=Settlement(due=[\n"
        f'        DueTerm(amount={D(deposit)}, offset="0d"),\n'
        f'        DueTerm(remainder=True, offset="{days}d"),\n'
        f"    ]),\n"
        f"))\n"
    )
    legs = []
    for d in monthly_occurrences(spec.start, spec.end, spec.end, every=3):
        legs += [(d, deposit), (add_duration(d, f"{days}d"), fee - deposit)]
    cash = bucket(legs, spec.periods, spec.end)
    checks = [
        c_no_errors(),
        c_field("epsilon", "settlement.due.1.remainder", True, label="second leg is the remainder"),
        c_series("epsilon", spec.periods, cash),
    ]
    return task(rng, variant, family="deposit_remainder", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=3)


@family("withholding", CAT, 3, "ritenuta d'acconto at settlement plus the manual F24 remittance item")
def withholding(rng, variant):
    spec = _spec(rng)
    fee = money_draw(rng, 1_000, 5_000, 100)
    rate = pick(rng, Decimal("0.2"), Decimal("0.2"), Decimal("0.23"))
    prompt = (
        f"{_book_sentence(spec)}\n\nWe pay a freelance consultant {eur(fee)} per month (gross), invoiced and paid at "
        f"the start of each month from {month_name(spec.start)}. As withholding agent we retain {pct(rate)} "
        f"ritenuta d'acconto, so the consultant receives less cash. Add item `consultant` with that withholding, "
        f"and also add the remittance of the withheld tax to the state: item `f24_ritenute`, tagged `cat: tax`, "
        f"paid on the 16th of the month after each consultant payment."
    )
    withheld = money(fee * rate)
    reference = IMPORTS + "\n" + (
        f"monthly = Recurrence(every=1, unit=Grain.MONTH)\n"
        f"kit.set_item(Item(\n"
        f'    id="consultant", name="Consultant", kind="flow", direction="out",\n'
        f"    segments=[Segment(start={dt(spec.start)}, recurrence=monthly, amount=Amount(constant={D(-fee)}))],\n"
        f'    settlement=Settlement(due=[DueTerm(share=Decimal("1"), offset="0d", withholding={D(rate)})]),\n'
        f"))\n"
        f"kit.set_item(Item(\n"
        f'    id="f24_ritenute", name="F24 ritenute", kind="flow", direction="out", tags={{"cat": "tax"}},\n'
        f"    segments=[Segment(start={dt(add_months(spec.start, 1).replace(day=16))}, recurrence=monthly,\n"
        f"                      amount=Amount(constant={D(-withheld)}))],\n"
        f"))\n"
    )
    pays = monthly_occurrences(spec.start, None, spec.end)
    cash = {d: -(fee - withheld) for d in pays}
    accrual = {d: -fee for d in pays}
    f24 = bucket([(add_months(d, 1).replace(day=16), -withheld) for d in pays], spec.periods, spec.end)
    checks = [
        c_no_errors(),
        c_series("consultant", spec.periods, accrual, measure="accrual", label="consultant cost accrues gross"),
        c_series("consultant", spec.periods, cash, label="consultant cash is net of withholding"),
        c_series("f24_ritenute", spec.periods, f24, label="withheld tax remitted the following month"),
        c_field("f24_ritenute", "tags.cat", "tax"),
        Check("no_code", {"code": "CK-W004"}, "remittance item present, so CK-W004 is silent"),
    ]
    return task(rng, variant, family="withholding", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup_book(spec), difficulty=3, tags=("italian-tax",))
