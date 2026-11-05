"""Ledger: forecast/committed events, idempotent imports of actuals, void, correction."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..oracle import ZERO, add_months, eur, long_date, money, month_of, month_starts
from ..schema import Check
from ._common import IMPORTS, BookSpec, D, c_balance, c_closing, c_no_errors, dt, family, money_draw, pick, task
from ._common import create_code
from .construction import _book_sentence, _year

CAT = "ledger"


def _spec(rng, cutover_month: int = 1) -> BookSpec:
    y = _year(rng)
    return BookSpec(f"ledger_{y}", date(y, 1, 1), date(y + 1, 1, 1), money_draw(rng, 10_000, 50_000, 1_000))


@family("forecast_events", CAT, 2, "one-off forecast and committed events (not recurring segments)")
def forecast_events(rng, variant):
    spec = _spec(rng)
    y = spec.start.year
    laptop_day = date(y, rng.randrange(2, 7), rng.randrange(1, 28))
    laptop = money_draw(rng, 900, 3_500, 50)
    refund_day = date(y, rng.randrange(7, 12), rng.randrange(1, 28))
    refund = money_draw(rng, 1_000, 8_000, 100)
    prompt = (
        f"{_book_sentence(spec)}\n\nRecord two one-off cash movements in the ledger (they are not recurring):\n"
        f"- event `laptop_purchase`: we expect to buy laptops for {eur(laptop)} on {long_date(laptop_day)} "
        f"(forecast, tag `cat: capex`);\n"
        f"- event `vat_refund`: the tax office has confirmed a VAT refund of {eur(refund)} on "
        f"{long_date(refund_day)} (committed, tag `cat: tax`)."
    )
    reference = IMPORTS + "\n" + (
        f'kit.add_event(Event(id="laptop_purchase", date={dt(laptop_day)}, amount={D(-laptop)},\n'
        f'                    status="forecast", tags={{"cat": "capex"}}))\n'
        f'kit.add_event(Event(id="vat_refund", date={dt(refund_day)}, amount={D(refund)},\n'
        f'                    status="committed", tags={{"cat": "tax"}}))\n'
    )
    checks = [
        c_no_errors(),
        Check("events", {"match": {"id": "laptop_purchase", "date": laptop_day.isoformat(), "amount": str(-laptop),
                                   "status": "forecast"}}, "laptop purchase is a forecast event"),
        Check("events", {"match": {"id": "vat_refund", "date": refund_day.isoformat(), "amount": str(refund),
                                   "status": "committed"}}, "VAT refund is a committed event"),
        c_balance(month_of(laptop_day), spec.opening - laptop, label="balance after the purchase month"),
        c_closing(spec.opening - laptop + refund),
    ]
    return task(rng, variant, family="forecast_events", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=IMPORTS + "\n" + create_code(spec), difficulty=2)


def _bank_rows(rng, y: int, n: int) -> list[tuple[str, date, Decimal, str]]:
    rows, day = [], date(y, 1, 2)
    for i in range(n):
        day = day + timedelta(days=rng.randrange(3, 12))
        amount = money_draw(rng, 50, 3_000, 5) * pick(rng, 1, -1, -1) + pick(rng, Decimal("0"), Decimal("0.5"), Decimal("0.99"))
        rows.append((f"TX{1000 + i}", day, amount, pick(rng, "card", "SEPA", "wire", "POS")))
    return rows


@family("import_actuals", CAT, 3, "idempotent import of bank actuals keyed on (source, ext_id); script runs twice")
def import_actuals(rng, variant):
    spec = _spec(rng)
    y = spec.start.year
    n = rng.randrange(5, 9)
    rows = _bank_rows(rng, y, n)
    cutover = add_months(month_of(rows[-1][1]), 1)
    csv = "\n".join(f"{tx},{d.isoformat()},{a},{memo}" for tx, d, a, memo in rows)
    prompt = (
        f"The book `{spec.id}` exists at `ROOT` (open in `kit`), monthly grain, horizon {long_date(spec.start)} to "
        f"{long_date(spec.end)}, opening balance {eur(spec.opening)}, cutover {long_date(cutover)}.\n\n"
        f"Import these bank-statement rows as **actual** events. The source is `bank:main`; the first column is "
        f"the bank's transaction id. This script will be run again tomorrow on the same file, so re-running it "
        f"must not create duplicates.\n\n```csv\ntx_id,date,amount,memo\n{csv}\n```"
    )
    rows_src = ",\n".join(f'    ("{tx}", {dt(d)}, {D(a)}, "{memo}")' for tx, d, a, memo in rows)
    reference = IMPORTS + "\n" + (
        f"rows = [\n{rows_src},\n]\n"
        f"events = [\n"
        f"    Event(id=tx.lower(), date=day, amount=amount, status=\"actual\", source=\"bank:main\",\n"
        f"          ext_id=tx, note=memo)\n"
        f"    for tx, day, amount, memo in rows\n"
        f"]\n"
        f'report = kit.import_events(events, source="bank:main")\n'
        f"print(report)\n"
    )
    total_amount = sum((a for _, _, a, _ in rows), ZERO)
    setup = IMPORTS + "\n" + create_code(spec).replace("    grain=Grain.MONTH,", f"    grain=Grain.MONTH,\n    cutover={dt(cutover)},")
    checks = [
        c_no_errors(),
        Check("events", {"match": {"status": "actual", "source": "bank:main"}, "count": n},
              f"exactly {n} actual events after two runs (idempotent)"),
        Check("events_total", {"status": "actual", "value": str(total_amount)}, "actuals sum to the statement"),
        *[Check("events", {"match": {"ext_id": tx, "amount": str(a), "date": d.isoformat()}},
                f"row {tx} imported with its id as ext_id") for tx, d, a, _ in rows[:2]],
        c_closing(spec.opening + total_amount),
    ]
    return task(rng, variant, family="import_actuals", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup, run_twice=True, difficulty=3, tags=("idempotency",))


@family("void_forecast", CAT, 2, "tombstone a forecast event rather than deleting or negating it")
def void_forecast(rng, variant):
    spec = _spec(rng)
    y = spec.start.year
    keep_day, drop_day = date(y, rng.randrange(2, 6), 15), date(y, rng.randrange(6, 12), 10)
    keep, drop = money_draw(rng, 1_000, 5_000, 100), money_draw(rng, 5_000, 20_000, 500)
    body = (
        f'kit.add_event(Event(id="grant_tranche", date={dt(keep_day)}, amount={D(keep)}, status="committed"))\n'
        f'kit.add_event(Event(id="big_order", date={dt(drop_day)}, amount={D(drop)}, status="forecast"))\n'
    )
    prompt = (
        f"{_book_sentence(spec)} The ledger holds a committed event `grant_tranche` ({eur(keep)} on "
        f"{long_date(keep_day)}) and a forecast event `big_order` ({eur(drop)} on {long_date(drop_day)}).\n\n"
        f"The big order fell through. Cancel `big_order` with the note \"customer withdrew\" — the ledger is "
        f"append-only, so it must be tombstoned, not offset with a negative event."
    )
    reference = IMPORTS + "\n" + 'kit.void_event("big_order", note="customer withdrew")\n'
    checks = [
        c_no_errors(),
        Check("events", {"match": {"id": "big_order"}, "count": 0}, "big_order no longer counts"),
        Check("events", {"match": {"id": "grant_tranche"}, "count": 1}, "grant_tranche untouched"),
        c_closing(spec.opening + keep),
        Check("called", {"name": "void_event"}, "used void_event (append-only ledger)", critical=False),
    ]
    return task(rng, variant, family="void_forecast", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=IMPORTS + "\n" + create_code(spec) + "\n" + body, difficulty=2)


@family("correct_actual", CAT, 3, "correct an actual through correct_event (actuals are never voided)")
def correct_actual(rng, variant):
    spec = _spec(rng)
    y = spec.start.year
    rows = _bank_rows(rng, y, 4)
    cutover = date(y, 4, 1)
    bad_tx, bad_day, bad_amount, memo = rows[2]
    right = bad_amount + pick(rng, Decimal("-90"), Decimal("90"), Decimal("-9"))
    body = (
        "kit.import_events([\n" + "".join(
            f'    Event(id="{tx.lower()}", date={dt(d)}, amount={D(a)}, status="actual", source="bank:main", ext_id="{tx}"),\n'
            for tx, d, a, _ in rows) + '], source="bank:main")\n'
    )
    prompt = (
        f"The book `{spec.id}` exists at `ROOT` (open in `kit`), monthly grain, horizon {long_date(spec.start)} to "
        f"{long_date(spec.end)}, opening balance {eur(spec.opening)}, cutover {long_date(cutover)}. Its ledger holds "
        f"these actual bank events (source `bank:main`, event id = lower-cased tx id):\n\n"
        + "\n".join(f"- `{tx.lower()}` {d.isoformat()} {a}" for tx, d, a, _ in rows)
        + f"\n\nEvent `{bad_tx.lower()}` was keyed wrongly: the real amount is {right}, same date. Fix it with the "
        f"note \"keying error\" so the history shows the correction."
    )
    reference = IMPORTS + "\n" + (
        f'old = next(e for e in kit.events_for("base")[0] if e.id == "{bad_tx.lower()}")\n'
        f'fixed = old.model_copy(update={{"id": "{bad_tx.lower()}_fix", "amount": {D(right)}, "corrects": old.id}})\n'
        f'report = kit.correct_event(old.id, fixed, note="keying error")\n'
        f"print(report)\n"
    )
    total_amount = sum((a for _, _, a, _ in rows), ZERO) - bad_amount + right
    setup = IMPORTS + "\n" + create_code(spec).replace("    grain=Grain.MONTH,", f"    grain=Grain.MONTH,\n    cutover={dt(cutover)},") + "\n" + body
    checks = [
        c_no_errors(),
        Check("events", {"match": {"status": "actual"}, "count": 4}, "still four live actuals"),
        Check("events_total", {"status": "actual", "value": str(total_amount)}, "actuals reflect the corrected amount"),
        Check("events", {"match": {"amount": str(bad_amount), "date": bad_day.isoformat()}, "count": 0},
              "the wrong amount no longer counts"),
        Check("called", {"name": "correct_event"}, "used correct_event (ADR-0012)", critical=False),
    ]
    return task(rng, variant, family="correct_actual", category=CAT, prompt=prompt, reference=reference,
                checks=checks, setup=setup, difficulty=3, tags=("actuals-immutable",))
