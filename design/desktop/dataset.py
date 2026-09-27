#!/usr/bin/env python3
"""CashKit desktop demo book — "Delta Ingegneria 2026" (revision 2, after critique).

Every figure in DATASET.md is printed by this script. Money is an int in
minor units at 4 dp (1 EUR = 10_000 units), like the engine core. Factors
(escalation, probability, shares, VAT, withholding) are Decimal and every
product is rounded HALF_UP to 4 dp (the engine's default RoundingPolicy) in
the canonical order:

    amount -> escalation -> probability -> settlement split -> withholding -> VAT

Display is 2 dp HALF_EVEN (D-MLP-06). No float anywhere. Run:

    python3 design/desktop/dataset.py > design/desktop/DATASET.md

The script refuses to print if any money cell reads "0.00" or any table does
not add up.
"""
from __future__ import annotations

import calendar
import io
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP

SCALE = 10_000
D = Decimal
MINUS = "−"


# ----------------------------------------------------------------- money --
def m(s: str) -> int:
    """Decimal string -> minor units (4 dp)."""
    return int((D(s) * SCALE).to_integral_value(ROUND_HALF_UP))


def mul(units: int, factor: Decimal) -> int:
    """Engine step: 4 dp HALF_UP (engine/numeric.py default policy)."""
    return int((D(units) * factor).to_integral_value(ROUND_HALF_UP))


def eur(units: int, signed: bool = True) -> str:
    """Display: 2 dp HALF_EVEN. A computed zero prints as an em dash, never 0.00."""
    if units == 0:
        return "—"
    q = (D(units) / SCALE).quantize(D("0.01"), ROUND_HALF_EVEN)
    body = f"{abs(q):,.2f}"
    if q < 0:
        return f"{MINUS} € {body}"
    if signed and q > 0:
        return f"+ € {body}"
    return f"€ {body}"


def eurp(units: int) -> str:
    """Balances and stocks: no sign unless negative."""
    return eur(units, signed=False)


def pct(num: int, den: int) -> str:
    """1 dp percentage with a typographic minus; den 0 -> dash."""
    if den == 0 or num == 0:
        return "—"
    v = (D(num) / D(abs(den)) * 100).quantize(D("0.1"), ROUND_HALF_EVEN)
    s = f"{abs(v)} %"
    return f"{MINUS}{s}" if v < 0 else s


def dmy(d: date) -> str:
    return d.strftime("%d %b %Y").lstrip("0")


def dm(d: date) -> str:
    return d.strftime("%d %b").lstrip("0")


def wd(d: date) -> str:
    return d.strftime("%a")


# -------------------------------------------------------------- calendar --
HOLIDAYS = {
    date(2024, 11, 1), date(2024, 12, 25), date(2024, 12, 26),
    date(2025, 1, 1), date(2025, 1, 6), date(2025, 4, 21), date(2025, 4, 25), date(2025, 5, 1),
    date(2025, 6, 2), date(2025, 8, 15), date(2025, 11, 1), date(2025, 12, 8), date(2025, 12, 25),
    date(2025, 12, 26),
    date(2026, 1, 1), date(2026, 1, 6), date(2026, 4, 6), date(2026, 4, 25),
    date(2026, 5, 1), date(2026, 6, 2), date(2026, 8, 15), date(2026, 11, 1),
    date(2026, 12, 8), date(2026, 12, 25), date(2026, 12, 26),
    date(2027, 1, 1), date(2027, 1, 6), date(2027, 3, 29), date(2027, 4, 25),
    date(2027, 5, 1), date(2027, 6, 2),
}


def is_bday(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def adjust(d: date, rule: str) -> date:
    if rule == "none":
        return d
    step = 1 if rule == "next" else -1
    while not is_bday(d):
        d += timedelta(days=step)
    return d


def eom(y: int, mo: int) -> date:
    return date(y, mo, calendar.monthrange(y, mo)[1])


def add_months(d: date, n: int) -> date:
    y, mo = divmod(d.month - 1 + n, 12)
    y += d.year
    mo += 1
    return date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def week_label(s: date, e: date) -> str:
    return f"W{s.isocalendar()[1]} · {dm(s)}–{dm(e)}"


# ------------------------------------------------------------------ book --
BOOK_ID = "delta-ingegneria"
BOOK_NAME = "Delta Ingegneria 2026"
CURRENCY = "EUR"
H_START, H_END = date(2026, 1, 1), date(2027, 7, 1)      # [start, end): runs through 30 Jun 2027
AS_OF = date(2026, 9, 28)                                # Monday, host clock
CUTOVER = date(2026, 9, 20)                              # Sunday, last weekly roll (r51)
STATEMENT_TO = date(2026, 9, 25)                         # Friday, last bank statement line
REVISION = "r51 · 7c2e19b"
ENGINE = "1.4.0"                                         # assumed semver, see DATASET.md note
OPENING = m("196350.00")
PRE_AR = date(2024, 9, 1)            # AR retainers carry 24 months of history for calibration
PRE = date(2025, 10, 1)              # other ERP lines start one quarter before the horizon

PARAMS_BASE = {                      # undotted keys: [a-z][a-z0-9_]* (CK-E007)
    "vat_standard": D("0.22"),
    "istat_index": D("0.02"),
    "min_cash": m("60000.00"),
    "credit_line": m("150000.00"),
}
VAT_RATE_MEAL = D("0.04")            # literal VatSpec.rate on buoni_pasto


@dataclass
class DueTerm:
    share: Decimal = D(1)
    offset: int = 0                  # literal days (Duration "68d"); no param reference exists
    adjust: str = "none"             # none | prev | next
    withholding: Decimal = D(0)


@dataclass
class Segment:
    start: date
    end: date | None
    every: int
    unit: str                        # "month" | "year"
    anchor: str = "eom"              # "eom" | "day"
    day: int | None = None
    bday_adjust: str = "none"
    constant: int | None = None
    schedule: dict | None = None     # {date: units}
    escalation: str | None = None    # param key, anchor segment_start, every year
    probability: Decimal = D(1)


@dataclass
class Item:
    id: str
    name: str
    direction: str                   # in | out
    cat: str
    segments: list
    due: list = field(default_factory=lambda: [DueTerm()])
    vat_treatment: str = "standard"  # standard | split_payment | exempt | out_of_scope
    vat_rate: Decimal | None = None  # literal rate; None = p.vat_standard
    recoverable: Decimal = D(1)
    tags: dict = field(default_factory=dict)
    source: str = "manual"           # erp:ar | erp:ap | payroll:zucchetti | bank:intesa | manual
    docs: int = 1                    # ledger rows per occurrence (documents / statement lines)
    contractual: str = ""            # contractual terms as text, for the aging screen
    added_at: date = date(2025, 1, 1)  # cutover of the revision that added the item (for at())


def items_base() -> list[Item]:
    return [
        # -------- inflows (all VAT-exclusive; every DueTerm adjust=next: a bank never credits on a weekend)
        Item("ret_acme", "Retainer — Acme Automation", "in", "revenue",
             [Segment(PRE_AR, date(2026, 1, 1), 1, "month", constant=m("60000")),
              Segment(date(2026, 1, 1), None, 1, "month", constant=m("60000"), escalation="istat_index")],
             [DueTerm(offset=68, adjust="next")], tags={"customer": "acme"}, source="erp:ar", contractual="net 60"),
        Item("ret_borghi", "Retainer — Borghi Impianti", "in", "revenue",
             [Segment(PRE_AR, None, 1, "month", constant=m("26000"))],
             [DueTerm(offset=52, adjust="next")], tags={"customer": "borghi"}, source="erp:ar", contractual="net 30"),
        Item("support_misc", "Support contracts — small customers", "in", "revenue",
             [Segment(PRE_AR, None, 1, "month", constant=m("34500"))],
             [DueTerm(offset=40, adjust="next")], tags={"customer": "various"}, source="erp:ar", contractual="net 30"),
        Item("ms_comune", "Milestones — Comune di Monza (PA)", "in", "revenue",
             [Segment(date(2026, 1, 1), None, 1, "month", schedule={
                 date(2026, 2, 28): m("75000"), date(2026, 7, 31): m("110000"),
                 date(2026, 11, 30): m("110000"), date(2027, 3, 31): m("95000")})],
             [DueTerm(offset=120, adjust="next")], vat_treatment="split_payment",
             tags={"customer": "comune_monza"}, source="erp:ar", contractual="net 30 (PA)"),
        Item("ms_veltro", "Milestones — Veltro Robotics", "in", "revenue",
             [Segment(date(2026, 1, 1), None, 1, "month", schedule={
                 date(2026, 5, 31): m("75000"), date(2026, 9, 30): m("85000"),
                 date(2027, 1, 31): m("70000")})],
             [DueTerm(share=D("0.3"), offset=0, adjust="next"), DueTerm(share=D("0.7"), offset=60, adjust="next")],
             tags={"customer": "veltro"}, source="erp:ar", contractual="30 % at invoice, 70 % net 60"),
        Item("pipe_nord", "Pipeline — Nord Energia framework", "in", "revenue",
             [Segment(date(2026, 11, 1), None, 1, "month", constant=m("30000"), probability=D("0.6"))],
             [DueTerm(offset=40, adjust="next")], tags={"customer": "nord_energia"}, contractual="net 30 (draft)"),
        # -------- payroll and F24 (CCNL metalmeccanico: 13th month, no 14th)
        Item("payroll", "Payroll — net salaries (25 FTE)", "out", "payroll",
             [Segment(date(2026, 1, 1), None, 1, "month", anchor="day", day=27, bday_adjust="prev",
                      constant=m("-52000"))],
             vat_treatment="out_of_scope", source="payroll:zucchetti"),
        Item("payroll_13", "Payroll — 13th month (CCNL metalmeccanico)", "out", "payroll",
             [Segment(date(2026, 12, 15), None, 1, "year", anchor="day", day=15, bday_adjust="prev",
                      constant=m("-52000"))],
             vat_treatment="out_of_scope", source="payroll:zucchetti"),
        Item("f24_contrib", "F24 — INPS contributions and IRPEF withholding", "out", "contributions",
             [Segment(date(2026, 1, 1), None, 1, "month", anchor="day", day=16, bday_adjust="next",
                      constant=m("-42000"))],
             vat_treatment="out_of_scope", source="payroll:zucchetti"),
        Item("f24_contrib_13", "F24 — contributions and IRPEF on the 13th month", "out", "contributions",
             [Segment(date(2026, 1, 16), None, 1, "year", anchor="day", day=16, bday_adjust="next",
                      constant=m("-21000"))],
             vat_treatment="out_of_scope", source="payroll:zucchetti"),
        Item("inail", "INAIL — autoliquidazione (F24)", "out", "contributions",
             [Segment(date(2026, 2, 16), None, 1, "year", anchor="day", day=16, bday_adjust="next",
                      constant=m("-4300"))],
             vat_treatment="out_of_scope", source="payroll:zucchetti"),
        # -------- opex (erp:ap unless stated)
        Item("rent", "Office rent — Via Zanica 12", "out", "opex",
             [Segment(PRE, date(2026, 1, 1), 1, "month", anchor="day", day=1, constant=m("-4800")),
              Segment(date(2026, 1, 1), None, 1, "month", anchor="day", day=1, constant=m("-4800"),
                      escalation="istat_index")],
             [DueTerm(offset=13, adjust="next")], source="erp:ap", contractual="net 13"),
        Item("software", "Software subscriptions (3 vendors)", "out", "opex",
             [Segment(PRE, None, 1, "month", anchor="day", day=5, constant=m("-2150"))],
             [DueTerm(offset=0, adjust="next")], source="erp:ap", docs=3, contractual="card at invoice"),
        Item("lease_vehicles", "Vehicle leases (4 cars, 40 % recoverable)", "out", "opex",
             [Segment(PRE, None, 1, "month", anchor="day", day=10, constant=m("-1900"))],
             [DueTerm(offset=0, adjust="next")], recoverable=D("0.4"), source="erp:ap", docs=4, contractual="SDD at invoice"),
        Item("utilities", "Utilities — electricity, gas, water", "out", "opex",
             [Segment(PRE, None, 1, "month", anchor="day", day=20, constant=m("-1650"))],
             [DueTerm(offset=15, adjust="next")], source="erp:ap", docs=3, contractual="net 15"),
        Item("telco", "Telco — mobile and fibre (50 % recoverable)", "out", "opex",
             [Segment(PRE, None, 1, "month", anchor="day", day=24, constant=m("-1250"))],
             [DueTerm(offset=0, adjust="next")], recoverable=D("0.5"), source="erp:ap", docs=2, contractual="SDD at invoice"),
        Item("buoni_pasto", "Buoni pasto (VAT 4 %)", "out", "opex",
             [Segment(PRE, None, 1, "month", anchor="day", day=23, constant=m("-3150"))],
             [DueTerm(offset=0, adjust="next")], vat_rate=VAT_RATE_MEAL, source="erp:ap", contractual="prepaid order"),
        Item("prof_fees", "Professional fees — commercialista, consulente del lavoro", "out", "opex",
             [Segment(PRE, None, 1, "month", anchor="day", day=10, constant=m("-2900"))],
             [DueTerm(offset=30, adjust="next")], source="erp:ap", docs=2, contractual="net 30"),
        Item("fuel_tolls", "Fuel cards and tolls (40 % recoverable)", "out", "opex",
             [Segment(PRE, None, 1, "month", anchor="day", day=22, constant=m("-2400"))],
             [DueTerm(offset=0, adjust="next")], recoverable=D("0.4"), source="bank:intesa", docs=4,
             contractual="SDD at statement"),
        Item("expense_reimb", "Expense reimbursements — engineers on site", "out", "opex",
             [Segment(date(2026, 1, 1), None, 1, "month", anchor="day", day=27, bday_adjust="prev",
                      constant=m("-3600"))],
             vat_treatment="out_of_scope", source="bank:intesa", docs=8, contractual="with payroll"),
        Item("bank_charges", "Bank charges — Intesa account fees", "out", "opex",
             [Segment(date(2026, 9, 1), None, 1, "month", anchor="day", day=1, bday_adjust="next", constant=m("-48"))],
             vat_treatment="exempt", source="bank:intesa", added_at=date(2026, 9, 6)),
        Item("cciaa", "Diritto annuale CCIAA", "out", "opex",
             [Segment(date(2026, 6, 30), None, 1, "year", anchor="day", day=30, bday_adjust="prev", constant=m("-220"))],
             vat_treatment="out_of_scope", source="bank:intesa"),
        Item("insurance", "Insurance — RC professionale and property", "out", "insurance",
             [Segment(date(2026, 4, 1), None, 1, "year", anchor="day", day=1, bday_adjust="next", constant=m("-7200"))],
             vat_treatment="exempt", source="erp:ap", contractual="at invoice"),
        # -------- subcontracting
        Item("subcontract", "Subcontractors — engineering firms (2)", "out", "subcontract",
             [Segment(PRE, None, 1, "month", constant=m("-8000"))],
             [DueTerm(offset=30, adjust="next")], source="erp:ap", docs=2, contractual="net 30"),
        Item("freelance", "Freelancers — ritenuta 20 % (3)", "out", "subcontract",
             [Segment(PRE, None, 1, "month", constant=m("-6000"))],
             [DueTerm(offset=30, adjust="next", withholding=D("0.20"))], source="erp:ap", docs=3, contractual="net 30"),
        # -------- financing
        Item("loan_intesa", "Term loan — Intesa (instalment)", "out", "financing",
             [Segment(date(2026, 1, 1), None, 1, "month", anchor="day", day=15, bday_adjust="next",
                      constant=m("-6250"))],
             vat_treatment="out_of_scope", source="bank:intesa"),
        Item("fido_commission", "Fido di cassa — availability commission (0.25 %/quarter)", "out", "financing",
             [Segment(date(2026, 1, 1), None, 3, "month", anchor="day", day=1, bday_adjust="next", constant=m("-375"))],
             vat_treatment="exempt", source="bank:intesa"),
    ]


def known_items(items: list[Item], cutover: date) -> list[Item]:
    """The item list as the book held it at the revision whose cutover is `cutover` (for at())."""
    return [it for it in items if it.added_at <= cutover]


# -------------------------------------------------- observed payment behaviour --
# Observed settlement offsets (days after invoice date) per AR invoice, Sep 2024 → Aug 2026,
# one entry per monthly invoice. None = not settled at cutover (the leg stays committed at the
# item's calibrated terms). These are the facts the erp:ar actual rows carry as their
# settlement override, and the input of the calibration statistics.
OBSERVED = {
    "ret_acme":    [66, 70, 68, 63, 74, 68, 71, 65, 69, 72, 67, 70,
                    68, 66, 73, 69, 68, 64, 70, 71, 95, 68, None, None],
    "ret_borghi":  [49, 51, 50, 53, 48, 52, 51, 50, 54, 52, 49, 53,
                    52, 55, 54, 53, 56, 55, 57, 54, 58, 55, None, None],
    "support_misc": [38, 41, 40, 39, 42, 40, 37, 41, 40, 43, 39, 40,
                     41, 38, 40, 42, 39, 40, 41, 40, 39, 44, None, None],
}
CONTRACTUAL_DAYS = {"ret_acme": 60, "ret_borghi": 30, "support_misc": 30, "ms_comune": 30, "ms_veltro": 0}
# Schedule items: observed offsets per accrual date
OBSERVED_SCHEDULE = {("ms_comune", date(2026, 2, 28)): 118}

# Amount variances (the invoice carried a different net than the item generates)
AMOUNT_VARIANCES = {
    ("freelance", date(2026, 6, 30)): (m("-7200"), "extra designer, 8 days"),
    ("subcontract", date(2026, 7, 31)): (m("-9500"), "extra hours on Veltro line 2"),
    ("software", date(2026, 8, 5)): (m("-2400"), "new licence, 5 seats"),
    ("software", date(2026, 9, 5)): (m("-2400"), "new licence, 5 seats — second month: update the item"),
    ("utilities", date(2026, 8, 20)): (m("-2140"), "summer air conditioning"),
    ("f24_contrib", date(2026, 9, 16)): (m("-42380"), "new hire from 1 Aug"),
}

# Bank rows with no item (uncovered movements) — actual events, source bank:intesa, tag cat:uncovered.
# (date, amount, ext_id, description, source). erp:gl rows are booked in the ERP general ledger but map to no
# item; bank:intesa rows exist only on the statement.
UNCOVERED = [(date(2026, mo, 1), m("-48.00"), f"2026-{mo:02d}-01-0002", "Bank charges (item added 1 Sep, r48)", "bank:intesa")
             for mo in range(1, 9)] + [
    (date(2026, 3, 18), m("-21500.00"), "GL-2026-0412", "Used van for site work — one-off purchase, declared omission", "erp:gl"),
    (date(2026, 5, 12), m("-18000.00"), "GL-2026-0655", "Dividend to shareholders — assembly decision, declared omission", "erp:gl"),
    (date(2026, 6, 30), m("-22600.00"), "GL-2026-0810", "F24 acconto IRES/IRAP — NOT MODELLED (CK-I001)", "erp:gl"),
    (date(2026, 7, 22), m("1240.00"), "2026-07-22-0017", "Rimborso INAIL", "bank:intesa"),
]

# The forecast event (manual, r50): server refresh, authored NET with VatSpec standard.
FORECAST_EVENT = dict(id="ev-0840", date=date(2026, 10, 15), net=m("-22500.00"), cat="capex",
                      note="Server refresh, Dell quote", added_at=date(2026, 9, 13))

# Statement lines in the open window (cutover, statement_to] that match nothing in the book.
WINDOW_EXCEPTIONS = [
    (date(2026, 9, 25), m("4880.00"), "Bonifico Studio Rossi — saldo consulenza", "no committed row, no item → annotate or add item"),
]
# Observed offset for AR rows whose cash lands inside the open window (evidence, not yet in the book)
WINDOW_OBSERVED = {("ret_borghi", date(2026, 7, 31)): 52, ("support_misc", date(2026, 7, 31)): 55}


# ------------------------------------------------------------ generation --
def occurrences(seg: Segment, h_end: date):
    end = seg.end or h_end
    if seg.unit == "month":
        y, mo = seg.start.year, seg.start.month
        while True:
            d = eom(y, mo) if seg.anchor == "eom" else date(y, mo, min(seg.day, calendar.monthrange(y, mo)[1]))
            if d >= end:
                break
            if d >= seg.start:
                yield d, adjust(d, seg.bday_adjust)
            mo += seg.every
            while mo > 12:
                mo -= 12
                y += 1
    else:
        y = seg.start.year
        while True:
            d = date(y, seg.start.month, seg.day or seg.start.day)
            if d >= end:
                break
            yield d, adjust(d, seg.bday_adjust)
            y += seg.every


def years_since(start: date, d: date) -> int:
    n = d.year - start.year
    if (d.month, d.day) < (start.month, start.day):
        n -= 1
    return max(n, 0)


@dataclass
class Leg:
    raw_date: date                   # accrual + offset, before adjust
    cash_date: date                  # after adjust
    net: int
    wh: int
    vat: int
    cash: int
    observed: bool = False           # settlement override from the ledger (fact)


@dataclass
class Line:
    """One accrual occurrence and its cash legs — what trace() returns for a cell."""
    item: str
    cat: str
    accrual_date: date
    base: int
    esc: Decimal
    prob: Decimal
    accrual: int
    legs: list
    vat_out: int
    vat_in: int
    source: str
    docs: int
    status: str = "generated"        # generated | ledger | forecast
    note: str = ""
    settled: bool = True             # False = the cash leg has no bank line yet (committed even if its date passed)


def vat_rate_of(it: Item, params: dict) -> Decimal:
    return it.vat_rate if it.vat_rate is not None else params["vat_standard"]


def build_line(it: Item, d: date, base: int, esc: Decimal, prob: Decimal, accrual: int, params: dict,
               overrides: dict | None = None) -> Line:
    """overrides: {leg_index: offset_days} — the per-event settlement override (observed offset)."""
    rate = vat_rate_of(it, params)
    legs, vat_out, vat_in = [], 0, 0
    for i, t in enumerate(it.due):
        leg_net = mul(accrual, t.share)
        wh = mul(leg_net, t.withholding)
        vat = mul(leg_net, rate) if it.vat_treatment == "standard" else 0
        cash = leg_net - wh + vat
        off = overrides.get(i, t.offset) if overrides else t.offset
        raw = d + timedelta(days=off)
        legs.append(Leg(raw, adjust(raw, t.adjust), leg_net, wh, vat, cash, observed=bool(overrides and i in overrides)))
        if it.direction == "in":
            vat_out += vat
        else:
            vat_in += mul(-vat, it.recoverable)
    return Line(it.id, it.cat, d, base, esc, prob, accrual, legs, vat_out, vat_in, it.source, it.docs)


def gen_lines(items: list[Item], params: dict, since: date | None = None) -> list[Line]:
    """Every occurrence with accrual date > `since` (None = all), at the item's own terms."""
    out = []
    for it in items:
        for seg in it.segments:
            for d, d_adj in occurrences(seg, H_END):
                if since and d_adj <= since:
                    continue
                if seg.schedule is not None:
                    if d not in seg.schedule:
                        continue                          # why_zero cause 5
                    base = seg.schedule[d]
                else:
                    base = seg.constant
                esc = D(1)
                if seg.escalation:
                    esc = (D(1) + params[seg.escalation]) ** years_since(seg.start, d)
                a1 = mul(base, esc)
                a2 = mul(a1, seg.probability)
                out.append(build_line(it, d_adj, base, esc, seg.probability, a2, params))
    return out


def observed_offset(item: str, accrual: date) -> int | None:
    if item in OBSERVED:
        idx = (accrual.year - PRE_AR.year) * 12 + accrual.month - PRE_AR.month
        if 0 <= idx < len(OBSERVED[item]):
            return OBSERVED[item][idx]
        return None
    return OBSERVED_SCHEDULE.get((item, accrual))


def ledger_lines(items: list[Item], params: dict, cutover: date) -> list[Line]:
    """Occurrences with accrual date <= cutover, as the ledger held them at that cutover.

    Facts known at `cutover`: the invoice net (known at invoice date) and the observed cash date
    (known once the bank line lands, i.e. observed cash date <= cutover). Legs not yet settled
    follow the item's calibrated terms and are committed.
    """
    out = []
    for ln in gen_lines(items, params):
        if ln.accrual_date > cutover:
            continue
        it = next(i for i in items if i.id == ln.item)
        v = AMOUNT_VARIANCES.get((ln.item, ln.accrual_date))
        accrual = v[0] if v else ln.accrual
        obs = observed_offset(ln.item, ln.accrual_date)
        overrides = None
        if obs is not None and adjust(ln.accrual_date + timedelta(days=obs), "next") <= cutover:
            overrides = {0: obs}
        nl = build_line(it, ln.accrual_date, ln.base, ln.esc, ln.prob, accrual, params, overrides)
        nl.status = "ledger"
        if ln.item in OBSERVED:
            nl.settled = overrides is not None
        nl.note = v[1] if v else ""
        out.append(nl)
    return out


def event_line(ev: dict, params: dict) -> Line:
    """A literal forecast Event authored net, VatSpec standard (recoverable 1), settlement 0d."""
    vat = mul(ev["net"], params["vat_standard"])
    leg = Leg(ev["date"], ev["date"], ev["net"], 0, vat, ev["net"] + vat)
    return Line(ev["id"], ev["cat"], ev["date"], ev["net"], D(1), D(1), ev["net"], [leg], 0, -vat, "manual", 1,
                status="forecast", note=ev["note"])


# ------------------------------------------------------------ VAT regime --
VAT_OFFSET_DAYS = 16                 # TaxRegime.payment_offset "16d": month end + 16 d = the 16th, no adjust


def vat_schedule(lines: list[Line]):
    """Monthly liability with credit carry. [(pay_date, month_end, out, in, carried, net, paid)]."""
    by_m = defaultdict(lambda: [0, 0])
    for ln in lines:
        me = eom(ln.accrual_date.year, ln.accrual_date.month)
        by_m[me][0] += ln.vat_out
        by_m[me][1] += ln.vat_in
    rows, credit = [], 0
    for me in sorted(by_m):
        vo, vi = by_m[me]
        net = vo - vi - credit
        pay = me + timedelta(days=VAT_OFFSET_DAYS)
        if net > 0:
            rows.append((pay, me, vo, vi, credit, net, net))
            credit = 0
        else:
            rows.append((pay, me, vo, vi, credit, net, 0))
            credit = -net
    return rows


# ------------------------------------------------------------------- run --
@dataclass
class Flow:
    date: date
    amount: int
    item: str
    cat: str
    status: str                      # actual | committed | generated | forecast
    customer: str = ""
    accrual: date | None = None
    docs: int = 1


def run(items: list[Item], params: dict, cutover: date = CUTOVER, with_ledger: bool = True,
        with_uncovered: bool = True, with_forecast_event: bool = True, generate: bool = True,
        with_bank_only: bool = True):
    """(flows, vat_rows, lines) for the book as held at `cutover` (at()) or with cutover_override."""
    items = known_items(items, cutover)
    lines = ledger_lines(items, params, cutover) if with_ledger else []
    if generate:
        lines += gen_lines(items, params, since=cutover)
    if with_forecast_event and FORECAST_EVENT["added_at"] <= cutover:
        lines.append(event_line(FORECAST_EVENT, params))
    cust = {it.id: it.tags.get("customer", "") for it in items}
    flows = []
    for ln in lines:
        for leg in ln.legs:
            if ln.status == "forecast":
                status = "forecast"
            elif ln.status == "ledger":
                status = "actual" if (leg.cash_date <= cutover and ln.settled) else "committed"
            else:
                status = "generated"
            flows.append(Flow(leg.cash_date, leg.cash, ln.item, ln.cat, status, cust.get(ln.item, ""),
                              ln.accrual_date, ln.docs))
    vat_rows = vat_schedule(lines)
    for (pd, me, vo, vi, cr, net, paid) in vat_rows:
        if paid:
            flows.append(Flow(pd, -paid, "_tax:iva:liability", "tax", "actual" if pd <= cutover else "generated",
                              accrual=me))
    if with_ledger and with_uncovered:
        for (d, amt, ext, note, src) in UNCOVERED:
            if d <= cutover and (with_bank_only or src != "bank:intesa"):
                flows.append(Flow(d, amt, "", "uncovered", "actual"))
    flows = [f for f in flows if H_START <= f.date < H_END]
    flows.sort(key=lambda f: (f.date, f.item))
    return flows, vat_rows, lines


def daily_balances(flows: list[Flow]):
    bal, day, out = OPENING, H_START, {}
    by_day = defaultdict(int)
    for f in flows:
        by_day[f.date] += f.amount
    while day < H_END:
        bal += by_day.get(day, 0)
        out[day] = bal
        day += timedelta(days=1)
    return out


# ------------------------------------------------------------- scenarios --
SPLIT = date(2026, 9, 1)


def items_downside() -> list[Item]:
    """Fork 'downside' (items only; events are book-level): revenue −15 % from 1 Sep 2026 (segment
    split, segments atomic), AR terms +30 d (set_item on due[0].offset), pipe_nord probability 0."""
    its = items_base()
    for it in its:
        if it.cat != "revenue":
            continue
        if it.id == "pipe_nord":
            it.segments = [Segment(s.start, s.end, s.every, s.unit, s.anchor, s.day, s.bday_adjust,
                                   s.constant, s.schedule, s.escalation, D(0)) for s in it.segments]
            continue
        if it.id in ("ret_acme", "ret_borghi", "support_misc", "ms_comune"):
            it.due = [DueTerm(t.share, t.offset + 30, t.adjust, t.withholding) for t in it.due]
        new_segs = []
        for s in it.segments:
            if s.schedule is not None:
                sched = {d: (mul(a, D("0.85")) if d >= SPLIT else a) for d, a in s.schedule.items()}
                new_segs.append(Segment(s.start, s.end, s.every, s.unit, s.anchor, s.day, s.bday_adjust,
                                        None, sched, s.escalation, s.probability))
            elif s.end is not None and s.end <= SPLIT:
                new_segs.append(s)
            elif s.start >= SPLIT:
                new_segs.append(Segment(s.start, s.end, s.every, s.unit, s.anchor, s.day, s.bday_adjust,
                                        mul(s.constant, D("0.85")), None, s.escalation, s.probability))
            else:
                new_segs.append(Segment(s.start, SPLIT, s.every, s.unit, s.anchor, s.day, s.bday_adjust,
                                        s.constant, None, s.escalation, s.probability))
                new_segs.append(Segment(SPLIT, s.end, s.every, s.unit, s.anchor, s.day, s.bday_adjust,
                                        mul(s.constant, D("0.85")), None, s.escalation, s.probability))
        it.segments = new_segs
    return its


def items_sweep(item_id: str, offset: int) -> list[Item]:
    """One throwaway scenario: set_item(<item>, due[0].offset = <offset>d)."""
    its = items_base()
    for it in its:
        if it.id == item_id:
            it.due = [DueTerm(t.share, offset, t.adjust, t.withholding) for t in it.due]
    return its


# --------------------------------------------------------------- reports --
def week_rows(bal: dict, flows: list[Flow], start: date, n: int):
    """(wk_start, wk_end, inflow, outflow, closing, min_day, min_bal) per ISO week from `start`."""
    rows, wk_start = [], start
    for _ in range(n):
        wk_end = min(wk_start + timedelta(days=(6 - wk_start.weekday())), H_END - timedelta(days=1))
        inflow = sum(f.amount for f in flows if wk_start <= f.date <= wk_end and f.amount > 0)
        outflow = sum(f.amount for f in flows if wk_start <= f.date <= wk_end and f.amount < 0)
        days = [d for d in bal if wk_start <= d <= wk_end]
        md = min(days, key=lambda k: (bal[k], k))
        rows.append((wk_start, wk_end, inflow, outflow, bal[wk_end], md, bal[md]))
        wk_start = wk_end + timedelta(days=1)
    return rows


def month_rows(bal: dict, flows: list[Flow]):
    rows, d = [], H_START
    while d < H_END:
        e = eom(d.year, d.month)
        inflow = sum(f.amount for f in flows if d <= f.date <= e and f.amount > 0)
        outflow = sum(f.amount for f in flows if d <= f.date <= e and f.amount < 0)
        days = [k for k in bal if d <= k <= e]
        md = min(days, key=lambda k: (bal[k], k))
        rows.append((d, e, inflow, outflow, bal[e], md, bal[md]))
        d = add_months(date(d.year, d.month, 1), 1)
    return rows


def summary(bal: dict, flows: list[Flow], params: dict, cutover: date = CUTOVER):
    """RunSummary fields (engine) + the host statistics the desktop adds, kept apart."""
    close = bal[H_END - timedelta(days=1)]
    low_d = min(bal, key=lambda k: (bal[k], k))
    fwd = {k: v for k, v in bal.items() if k > cutover}
    fl_d = min(fwd, key=lambda k: (fwd[k], k))
    below = [d for d in sorted(bal) if d > cutover and bal[d] < params["min_cash"]]
    below_hist = [d for d in sorted(bal) if d <= cutover and bal[d] < params["min_cash"]]
    neg = [d for d in sorted(bal) if bal[d] < 0]
    tin = sum(f.amount for f in flows if f.amount > 0)
    tout = sum(f.amount for f in flows if f.amount < 0)
    return dict(close=close, low_d=low_d, low=bal[low_d], runway_end=(neg[0] if neg else None),
                tin=tin, tout=tout, net=tin + tout,
                fl_d=fl_d, fl=bal[fl_d], below=below, below_hist=below_hist)


def nearest_rank(values: list[int], p: Decimal) -> int:
    s = sorted(values)
    k = int((p * len(s)).to_integral_value(rounding="ROUND_CEILING"))
    return s[max(k, 1) - 1]


def median(values: list[int]) -> int:
    return nearest_rank(values, D("0.5"))


# ------------------------------------------------------------------ print --
OUT = io.StringIO()


def p(*a):
    print(*a, file=OUT)


def table(header, rows, align_first_left=True):
    p("| " + " | ".join(header) + " |")
    p("|" + "|".join("---:" if i else "---" for i in range(len(header))) + "|")
    for r in rows:
        p("| " + " | ".join(r) + " |")
    p()


def stamp(extra: str = "", scenario: str = "base") -> str:
    return f"as-of {AS_OF} · {REVISION} · {scenario} · engine {ENGINE}" + (f" · {extra}" if extra else "")


def main():
    base_items = items_base()
    flows_b, vat_b, lines_b = run(base_items, PARAMS_BASE)
    bal_b = daily_balances(flows_b)
    flows_d, vat_d, lines_d = run(items_downside(), PARAMS_BASE)
    bal_d = daily_balances(flows_d)
    P = PARAMS_BASE
    by_id = {it.id: it for it in base_items}

    p(f"# DATASET — {BOOK_NAME}\n")
    p("The one demo book every CashKit desktop screen uses. Every figure below is printed by "
      "`design/desktop/dataset.py` (integer minor units at 4 dp, Decimal factors, 4 dp steps HALF_UP as the "
      "engine's default policy, display 2 dp HALF_EVEN, no float). Regenerate with "
      "`python3 design/desktop/dataset.py > design/desktop/DATASET.md`. Do not edit figures by hand.\n")
    p("## The company\n")
    p("Delta Ingegneria S.r.l., Bergamo. 25 people, engineering and automation services, CCNL metalmeccanico "
      "(13th month in December, no 14th). One bank account (Intesa, IT60 X054 2811 1010 0000 0123 456) and one "
      "credit line (fido di cassa € 150,000.00, undrawn). Six customers: two retainers, small support contracts, "
      "one PA customer paying at 120 days under split payment, one robotics customer paying 30/70, and a "
      "probability-weighted framework agreement from November. Outflows: net payroll on the 27th, one F24 on the "
      "16th (IVA, INPS and IRPEF, INAIL in February), monthly VAT, a term loan, vehicle leases with 40 % VAT "
      "recoverability, utilities, telco, buoni pasto, expense reimbursements, professional fees, fuel cards, "
      "the fido commission, freelancers with 20 % ritenuta.\n")
    p(f"The controller (M. Conti) rolls the cutover every Monday. The last roll was Mon 21 Sep (r51, cutover "
      f"Sun {dmy(CUTOVER)}); the bank statement to Fri {dmy(STATEMENT_TO)} was imported Friday evening and waits "
      f"for the Monday close. The CFO (L. Ferri) recalibrated Acme and Borghi terms in May after Q1 payments came "
      f"in late. IRES/IRAP acconti and the ritenuta remittance are not modelled, which is what the diagnostics say; "
      f"the June acconto sits in the ledger as an uncovered bank movement.\n")
    p("## Book identity\n")
    p("| field | value |\n|---|---|")
    p(f"| book id | `{BOOK_ID}` |\n| name | {BOOK_NAME} |\n| currency | {CURRENCY} (single currency per book) |")
    p(f"| horizon | [{H_START} , {H_END}) — half-open; the last day in the frame is 30 Jun 2027 |")
    p("| base grain | day (views aggregate to week / month / quarter) |")
    p(f"| opening balance (1 Jan 2026) | {eurp(OPENING)} |\n| cutover | {dmy(CUTOVER)} ({wd(CUTOVER)}; last reconciled date, committed at r51) |")
    p(f"| bank statement imported to | {dmy(STATEMENT_TO)} ({wd(STATEMENT_TO)}) — open window ({dm(CUTOVER)}, {dm(STATEMENT_TO)}] |")
    p(f"| as-of (host clock) | {dmy(AS_OF)} ({wd(AS_OF)}) |\n| revision | {REVISION} |")
    p(f"| engine | {ENGINE} — **assumption**: `ENGINE_VERSION` is the string `\"1\"` today; the demo assumes a "
      f"future semver so the history can show a version move (1.3.2 → 1.4.0) and CK-W011 |")
    p("| rounding policy | 4 dp HALF_UP in the engine (default `RoundingPolicy`); 2 dp HALF_EVEN at display |")
    p("| calendar | IT holidays 2026–2027 resolved at book creation; weekend Sat/Sun |")
    p("| accounting day | end of month (lines that name no day) |")
    p(f"| VAT regime | `iva` · monthly · accrual tax point · `payment_offset: {VAT_OFFSET_DAYS}d` (month end + 16 d = "
      f"the 16th) · no surcharge · credit carried. The engine applies **no business-day adjust** to a tax payment "
      f"(SDK request SR-2): a 16th that is a Saturday stays a Saturday in the frame |")
    p("| pre-horizon lines | AR retainers start 1 Sep 2024 (24 months of settled invoices for calibration); other "
      "ERP lines start 1 Oct 2025 so the receivables and payables open at 1 Jan 2026 exist in the ledger. "
      "Their December 2025 VAT is paid 16 Jan 2026 |\n")

    p("## Authoring convention for ledger rows (applies to every table)\n")
    p("- `erp:ar` / `erp:ap` rows: `date` = invoice date (accrual, drives the VAT tax point) · `amount` = **net** "
      "· `VatSpec` from the invoice line (rate, treatment, recoverable) · `settlement` = the item's calibrated terms "
      "while open (status committed); once the bank line is matched, the row carries a per-event settlement "
      "override equal to the observed offset (e.g. `95d`), so the cash leg lands on the bank date (status actual).")
    p("- `payroll:zucchetti` rows: `date` = pay date, `amount` = net pay or F24 total, VAT `out_of_scope`, settlement `0d`.")
    p("- `bank:intesa` statement lines are **match evidence**, not events. A line matched to an erp or payroll row "
      "confirms its cash date. A line matched to a generated occurrence of a no-VAT item (loan, fido commission, "
      "reimbursements, fuel-card SDD, bank charges) becomes an actual event that references the item, `date` = value "
      "date, `amount` = the line. A line matching nothing becomes an actual event with no item, tag `cat:uncovered`, "
      "VAT `out_of_scope`.")
    p("- A row dated after the cutover that references an item with segments would be counted twice (the engine generates after "
      "cutover and includes every ledger row). The import gate `date sanity` holds such rows in staging until the cutover passes them; "
      "payroll and bank figures for dates after cutover are evidence for the next match, never rows.")
    p("- `manual` rows: forecast events typed by the controller (net, VatSpec explicit). `commercialista` rows: "
      "committed tax events from the accountant's schedule (none yet — see coverage).")
    p("- A settled committed row is recorded as `void_event(committed, note)` + the actual row (`ext_id` suffixed "
      "`-S`, pilot §3.6 convention); a partial payment as `-P1` actual + `-R` committed; a credit note as its own row.\n")

    p("## Formatting rules used in every table\n")
    p("- Money: IBM Plex Mono, always 2 dp, thousands `,`, decimal `.`: `€ 18,420.00`.")
    p(f"- Sign: flows carry a sign, `+ € 31,720.00` (inflow) and `{MINUS} € 5,856.00` (outflow, U+2212). Balances and stock figures carry no sign unless negative.")
    p("- Zero: a computed zero is `—`. A blank cell means no generator covers the period. `0.00` never appears in a money cell (asserted by the script).")
    p("- Dates: `27 Nov 2026` in tables; `27 Nov` inside a column whose header carries the year; ISO weeks Monday start, `W47 · 16 Nov–22 Nov`.")
    p(f"- Every engine figure carries a stamp `{stamp()}`; hypothetical figures add `WHAT-IF · downside`; "
      "figures the host computes carry `host statistic · <definition>` instead of an engine stamp.")
    p(f"- Percentages: 1 dp, `{MINUS}11.6 %` (U+2212). `not within horizon` where the engine returns None.\n")

    p("## Non-engine figures (host statistics) and their definitions\n")
    p("| figure | definition | where |")
    p("|---|---|---|")
    p("| LOWEST POINT after cutover | min of the day-grain `cash` series over (cutover, end) | Position, compare |")
    p("| first day below floor · days below floor | first / count of days after cutover with `cash` < `min_cash` | Position, alert |")
    p("| below floor in actuals | days ≤ cutover with `cash` < `min_cash` | Position |")
    p("| headroom | derived item `headroom = it(\"cash\") − p.min_cash` (engine row, stamped) | grid |")
    p("| available liquidity | balance + `credit_line` undrawn (host; drawn is a manual stock item, none yet) | Position |")
    p("| buffer days | balance ÷ (Σ outflows over the trailing 90 days to cutover ÷ 90), integer | Position |")
    p("| coverage % | Σ\\|actual movements with an item\\| ÷ Σ\\|actual movements\\| in the horizon to cutover | Coverage |")
    p("| bank-vs-book Δ | statement closing balance − engine `cash` at the statement date | Position, Close |")
    p("| moved / changed | per occurrence: same amount other period vs different amount (plan run vs actual run) | Variance |")
    p("| MAPE, trough errors, directional accuracy | pilot §8.2 over weekly closing balances (forecast-then vs actual) | Validation |")
    p("| calibration percentiles | nearest-rank percentiles of (observed offset − contractual days) per customer | Calibration |")
    p("| receipts / disbursements / VAT & tax / net rows | derived items `agg(tag=…)` in the book (engine rows), see grid source map | grid |\n")

    p(f"---\n\n# Computed tables\n")
    p(f"book `{BOOK_ID}` · currency {CURRENCY} · horizon [{H_START} , {H_END}) · opening {eurp(OPENING)} "
      f"· cutover {dmy(CUTOVER)} · statement to {dmy(STATEMENT_TO)} · as-of {dmy(AS_OF)} · revision {REVISION} · engine {ENGINE}\n")

    # ---- status()
    p("## status() — uncommitted changes\n")
    p("No uncommitted changes. HEAD = r51 · 7c2e19b. The header reads `r51 · 7c2e19b · saved`.\n")

    # ---- params
    p("## Params (base) — the whole lever surface\n")
    rows = []
    for k, v in P.items():
        rows.append((f"`{k}`", eurp(v) if k in ("min_cash", "credit_line") else str(v),
                     {"vat_standard": "every VatSpec with rate `p.vat_standard`",
                      "istat_index": "ret_acme, rent (escalation)",
                      "min_cash": "derived item `headroom`, alert rule",
                      "credit_line": "Position (available liquidity)"}[k],
                     {"vat_standard": "r1", "istat_index": "r1", "min_cash": "r12", "credit_line": "r12"}[k]))
    table(["param", "value", "referenced by", "last changed"], rows)
    p("Settlement offsets are literal Durations on each item (`\"68d\"`); a DueTerm cannot reference a param "
      "(SDK request SR-1). Calibration therefore writes `set_item` on every item of the customer, not a param. "
      "The downside fork differs from base by items only.\n")

    # ---- items
    p("## Items\n")
    rows = []
    for it in base_items:
        s = it.segments[-1]
        rec = f"every {s.every} {s.unit}" + (" · each month end" if s.anchor == "eom" else f" · day {s.day}")
        if s.bday_adjust != "none":
            rec += f" · bank day {s.bday_adjust}"
        if s.schedule:
            amt = "schedule: " + "; ".join(f"{dm(d)} {d.year} {eurp(abs(a))}" for d, a in s.schedule.items())
        else:
            amt = eurp(abs(s.constant)) + (f" · escalation p.{s.escalation}" if s.escalation else "")
        if s.probability != 1:
            amt += f" · probability {s.probability}"
        terms = " + ".join(
            f"{t.share if t.share != 1 else '1.0'} @ {t.offset}d" + (f" {t.adjust}" if t.adjust != "none" else "")
            + (f" · withholding {t.withholding}" if t.withholding else "") for t in it.due)
        vat = it.vat_treatment + (f" {it.vat_rate}" if it.vat_rate is not None else "") + \
            (f" · recoverable {it.recoverable}" if it.recoverable != 1 else "")
        rows.append((f"`{it.id}`", it.name, it.direction, f"cat:{it.cat}" + "".join(f" {k}:{v}" for k, v in it.tags.items()),
                     f"{dmy(it.segments[0].start)} → {dmy(s.end) if s.end else 'open'}"
                     + (f" ({len(it.segments)} segments, escalation from {dmy(s.start)})" if len(it.segments) > 1 else ""),
                     rec, amt, terms, vat, it.source))
    table(["id", "name", "dir", "tags", "segments", "recurrence", "amount (net, excl. VAT)", "settlement", "VAT", "source"], rows)
    p("Derived items defined in the book through the SDK (read-only in the UI, `defined in SDK` stamp), so every "
      "frame row is an engine row with a stamp:\n")
    p("| id | formula | grid row |\n|---|---|---|")
    p("| `sub_receipts` | `agg(tag=\"cat:revenue\")` | Receipts |")
    p("| `sub_disbursements` | `agg(tag=\"cat:payroll\") + agg(tag=\"cat:opex\") + agg(tag=\"cat:subcontract\") + agg(tag=\"cat:financing\") + agg(tag=\"cat:insurance\") + agg(tag=\"cat:capex\") + agg(tag=\"cat:uncovered\")` | Disbursements |")
    p("| `sub_vat_tax` | `agg(tag=\"cat:contributions\") + agg(tag=\"cat:tax\")` + the `_tax:iva:liability` frame row (whether `it(\"_tax:iva:liability\")` is referenceable from a formula is SDK request SR-3) | VAT & tax |")
    p("| `net_flow` | `sub_receipts + sub_disbursements + sub_vat_tax` | Net |")
    p("| `cash` | the engine's balance fold (`balance_source`) | Opening / Closing |")
    p("| `headroom` | `it(\"cash\") − p.min_cash` | Headroom |\n")

    # ---- VAT regime schedule (monthly)
    p(f"## VAT regime `iva` — monthly, accrual tax point, `payment_offset {VAT_OFFSET_DAYS}d`, no surcharge, credit carried\n")
    rows = []
    for (pd, me, vo, vi, cr, net, paid) in vat_b:
        if me < date(2025, 12, 1):
            continue
        when = dmy(pd) + (f" ({wd(pd)}; no adjust in the engine)" if not is_bday(pd) else "")
        if pd >= H_END:
            when += " · **beyond horizon end — in no frame**"
        rows.append((me.strftime("%b %Y"), eurp(vo), eurp(vi), eurp(cr), eurp(net) if net > 0 else eurp(net),
                     when, eurp(paid) if paid else "— (credit carried as a stock)"))
    table(["month", "output VAT", "input VAT (recoverable)", "credit carried in", "net", "payment date", "paid"], rows)
    p("The VAT credit, when it exists, is the stock `_tax:iva:credit` (aggregation `last`, no sum row). It is never an inflow.\n")

    # ---- tax calendar (F24 of the 16th)
    p("## Tax calendar — the F24 of the 16th, by section (Oct 2026 → Jun 2027)\n")
    rows = []
    for me_i in range(0, 9):
        me = eom(2026, 9) if me_i == 0 else eom(*divmod_month(2026, 9 + me_i))
        pay_iva = next((pd for (pd, m_, *_r) in vat_b if m_ == me), None)
        paid_iva = next((paid for (pd, m_, vo, vi, cr, net, paid) in vat_b if m_ == me), 0)
        f24_month = add_months(date(me.year, me.month, 1), 1)
        contrib = sum(f.amount for f in flows_b if f.cat == "contributions" and f.date.year == f24_month.year and f.date.month == f24_month.month)
        contrib_date = next((f.date for f in flows_b if f.cat == "contributions" and f.date.year == f24_month.year and f.date.month == f24_month.month), None)
        if pay_iva is None or pay_iva >= H_END:
            continue
        same = contrib_date == pay_iva
        rows.append((f24_month.strftime("%b %Y"),
                     f"{dmy(pay_iva)}" + ("" if is_bday(pay_iva) else f" ({wd(pay_iva)} — engine, no adjust)"),
                     eur(-paid_iva) if paid_iva else "— (credit)",
                     f"{dmy(contrib_date)}" if contrib_date else "—", eur(contrib),
                     "NOT MODELLED (CK-W004)",
                     eur(-paid_iva + contrib) if same else "two dates — see note"))
    table(["F24 month", "Erario IVA · date", "Erario IVA", "INPS + IRPEF (+ INAIL Feb) · date", "INPS + IRPEF", "Erario ritenute", "F24 total"], rows)
    p("Where the 16th is a weekend the controller pays one F24 on the Monday; the engine dates the IVA section on "
      "the Saturday and the contributions (item `f24_contrib`, `bank day next`) on the Monday. The two dates are "
      "shown, not merged. The ritenute section is € 1,200.00/month (20 % of the freelance net — host arithmetic "
      "shown in the coverage checklist, never in the diagnostic row) and is not modelled.\n")

    # ---- monthly series with accrual measure
    p("## Monthly series — cash and accrual measures, month-end closing balance, day-grain minimum\n")
    mb, md_ = month_rows(bal_b, flows_b), month_rows(bal_d, flows_d)
    accr_by_m = defaultdict(int)
    for ln in lines_b:
        if ln.accrual_date >= H_START:
            accr_by_m[(ln.accrual_date.year, ln.accrual_date.month)] += ln.accrual
    rows = []
    for (d, e, i, o, c, mind, minb), (_, _, i2, o2, c2, _, _) in zip(mb, md_):
        tag = "actual" if e <= CUTOVER else ("open" if d <= CUTOVER else "forecast")
        cross = "◆ " if (minb < P["min_cash"] <= c) else ""
        rows.append((d.strftime("%b %Y"), tag, eur(accr_by_m[(d.year, d.month)]), eurp(i), eur(o), eur(i + o), eurp(c),
                     f"{cross}{eurp(minb)} · {dm(mind)}",
                     eurp(c2) if e > CUTOVER else "= base", eur(c2 - c) if e > CUTOVER else "—"))
    table(["month", "status", "accrual (net)", "cash in", "cash out", "cash net", "closing · base", "day-grain min (◆ = below floor inside the month)",
           "closing · downside (WHAT-IF)", "Δ"], rows)
    p("◆ marks a month whose closing is at or above `min_cash` while a day inside it is below: the aggregated "
      "column must show the crossing (UI-05). Accrual is the net amount recognised on the invoice date; cash is what "
      "moved. They are separate measures on every run and never mixed in one column.\n")

    # ---- 13-week series
    p("## 13-week series — weeks from the day after cutover, closing balance on Sunday\n")
    w_start = CUTOVER + timedelta(days=1)
    wb, wdn = week_rows(bal_b, flows_b, w_start, 13), week_rows(bal_d, flows_d, w_start, 13)
    rows = []
    for n, ((s, e, i, o, c, mind, minb), (_, _, i2, o2, c2, _, _)) in enumerate(zip(wb, wdn), 1):
        label = week_label(s, e) + (" (partial)" if s.weekday() != 0 else "")
        cross = "◆ " if (minb < P["min_cash"] <= c) else ""
        rows.append((str(n), label, eurp(i), eur(o), eur(i + o), eurp(c), f"{cross}{eurp(minb)} · {dm(mind)}",
                     eurp(c2), eur(c2 - c)))
    table(["#", "week", "inflows", "outflows", "net", "closing · base", "day-grain min", "closing · downside (WHAT-IF)", "Δ"], rows)

    # ---- 13-week grid
    p("## 13-week grid — base, cash measure; receipts by customer, disbursements by category\n")
    customers = ["acme", "borghi", "various", "comune_monza", "veltro", "nord_energia"]
    cust_name = {"acme": "Acme Automation", "borghi": "Borghi Impianti", "various": "Small customers",
                 "comune_monza": "Comune di Monza (PA)", "veltro": "Veltro Robotics", "nord_energia": "Nord Energia (p 0.6)"}
    cats_out = ["payroll", "opex", "subcontract", "financing", "insurance", "capex", "uncovered"]
    cats_tax = ["contributions", "tax"]
    hdr = ["row · source"] + [week_label(s, e).replace(" · ", "<br>") for (s, e, *_) in wb]
    grid = []
    opening = bal_b[CUTOVER]
    op_row, cl_row = [], []
    for (s, e, i, o, c, *_r) in wb:
        op_row.append(eurp(opening))
        cl_row.append(eurp(c))
        opening = c
    grid.append(["**Opening balance** · `cash`"] + op_row)

    def cell(v):
        return eur(v) if v else "—"

    rec_tot = [0] * 13
    for cu in customers:
        vals = []
        for k, (s, e, *_r) in enumerate(wb):
            v = sum(f.amount for f in flows_b if s <= f.date <= e and f.customer == cu and f.amount > 0)
            rec_tot[k] += v
            vals.append(cell(v))
        grid.append([f"customer:{cu} · {cust_name[cu]} · `pivot(columns=tag:customer)`"] + vals)
    grid.append(["**Receipts** · `sub_receipts`"] + [cell(v) for v in rec_tot])
    dis_tot = [0] * 13
    for cat in cats_out:
        vals = []
        for k, (s, e, *_r) in enumerate(wb):
            v = sum(f.amount for f in flows_b if s <= f.date <= e and f.cat == cat)
            dis_tot[k] += v
            vals.append(cell(v))
        grid.append([f"cat:{cat}"] + vals)
    grid.append(["**Disbursements** · `sub_disbursements`"] + [cell(v) for v in dis_tot])
    tax_tot = [0] * 13
    for cat in cats_tax:
        vals = []
        for k, (s, e, *_r) in enumerate(wb):
            v = sum(f.amount for f in flows_b if s <= f.date <= e and f.cat == cat)
            tax_tot[k] += v
            vals.append(cell(v))
        grid.append([f"cat:{cat}" + (" · `_tax:iva:liability`" if cat == "tax" else " · f24_contrib, inail")] + vals)
    grid.append(["**VAT & tax** · `sub_vat_tax`"] + [cell(v) for v in tax_tot])
    grid.append(["**Net** · `net_flow`"] + [eur(i + o) for (s, e, i, o, *_r) in wb])
    grid.append(["**Closing balance** · `cash`"] + cl_row)
    grid.append(["**Headroom** · `headroom`"] + [eur(c - P["min_cash"]) for (s, e, i, o, c, *_r) in wb])
    grid.append(["day-grain min · ◆ crossing inside the week"] + [
        ("◆ " if (minb < P["min_cash"] <= c) else "") + f"{eurp(minb)}<br>{dm(mind)}" for (s, e, i, o, c, mind, minb) in wb])
    table(hdr, grid)
    p("Source map: customer rows come from `pivot(index=period, columns=tag:customer, values=cash)`; `cat:` rows "
      "from `frame(where=cat:<x>)`; the bold rows are the derived items listed under Items; Opening and Closing are "
      "the `cash` item. Nothing is summed in the client.\n")

    # ---- self-checks
    prev = bal_b[CUTOVER]
    for k, (s_, e_, i, o, c, *_r) in enumerate(wb):
        assert prev + i + o == c, ("week does not add up", s_)
        assert rec_tot[k] + dis_tot[k] + tax_tot[k] == i + o, ("rows do not sum to net", s_)
        prev = c
    prev = OPENING
    for (d, e, i, o, c, *_r) in mb:
        assert prev + i + o == c, ("month does not add up", d)
        prev = c
    assert sum(i + o for (_, _, i, o, *_r) in mb) == bal_b[H_END - timedelta(days=1)] - OPENING
    p("_Self-check: every week and month satisfies opening + inflows + outflows = closing; customer and category rows sum to the net row._\n")

    # ---- committed-only closing line
    p("## Committed-only closing line — 13 weeks (host run on a throwaway overlay with every generative segment stripped; the engine folds it)\n")
    flows_c, _, _ = run(base_items, P, generate=False, with_forecast_event=False)
    bal_c = daily_balances(flows_c)
    wc = week_rows(bal_c, flows_c, w_start, 13)
    rows = [(week_label(s, e), eurp(c), eurp(cc), eur(cc - c)) for (s, e, i, o, c, *_r), (_, _, _, _, cc, *_r2) in zip(wb, wc)]
    table(["week", "closing · base (all statuses)", "closing · committed only", "Δ"], rows)
    p("Stamp on the committed-only line: `PREVIEW · committed only · r51 · engine 1.4.0`. It is the floor of "
      "certainty: only actual and committed rows, no generated occurrence, no forecast event.\n")

    # ---- summary
    p("## summary() and the Position figures\n")
    sb = summary(bal_b, flows_b, P)
    sd = summary(bal_d, flows_d, P)
    rows = []
    for name, s_, bal in (("base", sb, bal_b), ("downside (WHAT-IF)", sd, bal_d)):
        rows.append((name, eurp(bal[CUTOVER]), eurp(bal[STATEMENT_TO]), eurp(s_["close"]),
                     f"{eurp(s_['low'])} on {dmy(s_['low_d'])}", eurp(s_["tin"]), eur(s_["tout"]),
                     dmy(s_["runway_end"]) if s_["runway_end"] else "not within horizon"))
    table(["scenario", "book balance at cutover 20 Sep", "book balance at 25 Sep (engine fold)", "horizon close 30 Jun 2027",
           "min_cash · min_cash_period (whole horizon)", "total_inflow", "total_outflow", "runway_end (first negative)"], rows)
    p("The columns above are `RunSummary` fields (`opening_balance`, `closing_balance`, `min_cash`, "
      "`min_cash_period`, `total_inflow`, `total_outflow`, `runway_end`). The desktop adds these host statistics, "
      "each labelled as such on screen:\n")
    rows = []
    for name, s_, bal in (("base", sb, bal_b), ("downside (WHAT-IF)", sd, bal_d)):
        below = s_["below"]
        back = next((d for d in sorted(bal) if below and d > below[0] and bal[d] >= P["min_cash"]), None)
        rows.append((name, f"{eurp(s_['fl'])} on {dmy(s_['fl_d'])}",
                     dmy(below[0]) if below else "not within horizon", str(len(below)),
                     (dmy(back) if back else "not within horizon") if below else "—",
                     (f"{dmy(min(s_['below_hist'], key=lambda k: bal[k]))} ({eurp(bal[min(s_['below_hist'], key=lambda k: bal[k])])}), "
                      f"{len(s_['below_hist'])} day(s)") if s_["below_hist"] else "none"))
    table(["scenario", "LOWEST POINT after cutover", "first day below min_cash", "days below floor", "back above the floor", "below floor in actuals"], rows)

    # bank tie-out
    stmt_lines = statement_lines(flows_b)
    not_received = [f for f in flows_b if f.status == "committed" and f.date <= CUTOVER]
    bank_at_cutover = bal_b[CUTOVER] - sum(f.amount for f in not_received)
    stmt_bal = bank_at_cutover + sum(a for (_, a, *_r) in stmt_lines)
    book_25 = bal_b[STATEMENT_TO]
    p("### Bank-vs-book tie-out at the statement date (Position, top of Close)\n")
    p(f"| figure | value | stamp |\n|---|---:|---|")
    p(f"| bank statement closing balance {dmy(STATEMENT_TO)} | {eurp(stmt_bal)} | fact · bank:intesa · {len(stmt_lines)} lines imported 25 Sep 18:30 |")
    p(f"| book balance {dmy(STATEMENT_TO)} | {eurp(book_25)} | {stamp('cash')} |")
    p(f"| Δ bank − book | {eur(stmt_bal - book_25)} | host statistic · explained by {len(WINDOW_EXCEPTIONS)} unmatched line(s), see Close |")
    p(f"| book balance at cutover {dmy(CUTOVER)} | {eurp(bal_b[CUTOVER])} | {stamp('cash')} · includes {len(not_received)} committed leg(s) dated on/before cutover and not received: {', '.join(f.item + ' ' + eur(f.amount) + ' expected ' + dm(f.date) for f in not_received)} |")
    p(f"| bank balance at cutover {dmy(CUTOVER)} | {eurp(bank_at_cutover)} | fact · bank:intesa |\n")

    # buffer and liquidity
    win_s = CUTOVER - timedelta(days=89)
    out90 = -sum(f.amount for f in flows_b if win_s <= f.date <= CUTOVER and f.amount < 0)
    avg = out90 // 90
    p("### Liquidity and buffer (host statistics)\n")
    p(f"- available liquidity at the statement date {dmy(STATEMENT_TO)}: bank balance {eurp(stmt_bal)} (fact) + credit line {eurp(P['credit_line'])} undrawn = {eurp(stmt_bal + P['credit_line'])}; headroom vs floor {eur(stmt_bal - P['min_cash'])}")
    p(f"- buffer days: trailing-90-day outflows ({dm(win_s)}–{dm(CUTOVER)}) {eurp(out90)} → {eurp(avg)}/day → the bank balance covers "
      f"{stmt_bal // avg} days of outflow; with the credit line {(stmt_bal + P['credit_line']) // avg} days\n")

    # ---- next 10 bank days
    p(f"## Next 10 bank days from as-of {dmy(AS_OF)} — running balance (Position)\n")
    days, d = [], AS_OF
    while len(days) < 10:
        if is_bday(d):
            days.append(d)
        d += timedelta(days=1)
    rows = []
    running = bal_b[AS_OF - timedelta(days=1)]
    for d in days:
        fl = [f for f in flows_b if f.date == d]
        if not fl:
            rows.append((f"{dmy(d)} {wd(d)}", "—", "—", "—", eurp(running)))
            continue
        for f in fl:
            running += f.amount
            glyph = {"committed": "C", "generated": "G", "forecast": "F", "actual": "A"}[f.status]
            rows.append((f"{dmy(d)} {wd(d)}", glyph, f.item or "(no item)", eur(f.amount), eurp(running)))
    table(["bank day", "status", "item", "flow", "running balance"], rows)
    p(f"Opening of the table = book balance on {dmy(AS_OF - timedelta(days=1))} ({eurp(bal_b[AS_OF - timedelta(days=1)])}). "
      "C = committed row, G = generated occurrence, F = forecast event. Committed and generated are shown as separate "
      "glyphs; the running balance is the engine's `cash` series at day grain.\n")

    # ---- imports and sources
    p("## Imports and sources (source inventory; status strip reads it)\n")
    ar_open = [f for f in flows_b if f.status == "committed" and f.amount > 0]
    ap_open = [f for f in flows_b if f.status == "committed" and f.amount < 0]
    rows = [
        ("erp:ar", "M. Conti", "CSV export (TeamSystem)", "each bank day 07:10", "28 Sep 2026 07:10", f"{sum(f.docs for f in ar_open)} rows · inserted 0 · skipped {sum(f.docs for f in ar_open)} · conflicted 0", "no"),
        ("erp:ap", "M. Conti", "CSV export (TeamSystem)", "each bank day 07:10", "28 Sep 2026 07:10", f"{sum(f.docs for f in ap_open)} rows · inserted 0 · skipped {sum(f.docs for f in ap_open)} · conflicted 0", "no"),
        ("bank:intesa", "M. Conti", "statement CSV (CBI)", "each bank day", "25 Sep 2026 18:30", f"{len(stmt_lines)} lines staged (evidence) · matched 0 of {len(stmt_lines)}", "no (no bank day since)"),
        ("payroll:zucchetti", "consulente del lavoro", "monthly file", "monthly, by the 24th", "24 Sep 2026 15:02", "2 figures staged as evidence (net payroll 25 Sep, F24 16 Oct); rows are written at match time", "no"),
        ("manual", "M. Conti", "typed", "—", "18 Sep 2026 (ev-0840)", "1 forecast event", "—"),
        ("commercialista", "Studio Bassi", "schedule by email", "quarterly", "—", "**not covered** — IRES/IRAP acconti not in the book", "n/a"),
        ("backlog (ERP sales)", "—", "—", "—", "—", "**not covered** — milestones are authored as items", "n/a"),
    ]
    table(["source", "owner", "method", "cadence", "last run", "rows (last run)", "stale"], rows)
    p("A source is stale when as-of − last run exceeds its cadence; the strip names it ('payroll:zucchetti 34 days old, cadence monthly'), never colours it.\n")

    # ---- import gate report
    p("## Import gate report — erp:ar, 28 Sep 2026 07:10 (D09)\n")
    ar_gross = sum(f.amount for f in ar_open)
    n_ar = sum(f.docs for f in ar_open)
    rows = [
        ("Completeness", f"{n_ar} rows · Σ net = ageing report Σ", "pass", "—"),
        ("AR tie-out", f"Σ open AR gross {eurp(ar_gross)} vs AR control account {eurp(ar_gross)}", "pass", "—"),
        ("VAT code coverage", f"{n_ar} of {n_ar} codes mapped (22, N6.9)", "pass", "—"),
        ("Terms coverage", f"{n_ar} of {n_ar} customers with mapped terms", "pass", "—"),
        ("Date sanity", f"no invoice date after {dmy(AS_OF)}, none before 1 Sep 2024; no row dated after cutover {dmy(CUTOVER)} references a generative item (the engine would generate the same flow: PRD D6)", "pass", "—"),
        ("Currency", f"{n_ar} of {n_ar} EUR", "pass", "—"),
        ("Duplicate ext_id", "0 within the batch", "pass", "—"),
        ("Sign convention", f"{n_ar} of {n_ar} positive", "pass", "—"),
    ]
    table(["gate", "check", "result", "Δ"], rows)
    p(f"ImportReport: inserted 0 · skipped {n_ar} (identical payload) · conflicted 0. Target: base ledger (events are book-level, shared by every scenario). "
      "A failed gate (result `stop`) leaves the ledger untouched and the screen says so. An unmapped VAT code renders as "
      "the app-layer diagnostic `CK-E903 · VAT code ‹code› has no mapping` (D-MLP-19 band: needs something the book does not have) with a fix button opening the mapping row (D20).\n")

    # ---- VAT code mapping
    p("## VAT code mapping (Book › Settings, D20)\n")
    table(["ERP code", "rate", "treatment", "recoverable", "used by"], [
        ("22", "p.vat_standard (0.22)", "standard", "1.00", "retainers, subcontract, freelance, rent, software, utilities, professional fees"),
        ("22A40", "p.vat_standard", "standard", "0.40", "lease_vehicles, fuel_tolls"),
        ("22T50", "p.vat_standard", "standard", "0.50", "telco"),
        ("04", "0.04 (literal)", "standard", "1.00", "buoni_pasto"),
        ("N6.9", "p.vat_standard", "split_payment", "—", "ms_comune (PA: VAT never collected)"),
        ("N4", "—", "exempt", "—", "insurance, bank charges, fido commission"),
        ("N2.2", "—", "out_of_scope", "—", "payroll, F24, loan, reimbursements"),
    ])

    # ---- reconcile window
    p(f"## Reconcile window ({dm(CUTOVER)}, {dm(STATEMENT_TO)}] — statement lines vs the book (D11)\n")
    expected = [f for f in flows_b if CUTOVER < f.date <= STATEMENT_TO] + not_received
    rows = []
    matched_lines = 0
    for f in sorted(expected, key=lambda f: f.date):
        lines_n = f.docs
        matched_lines += lines_n
        target = "committed row → void_event + actual row (`-S`)" if f.status == "committed" else "generated occurrence → add_event (actual, item ref)"
        obs = WINDOW_OBSERVED.get((f.item, f.accrual))
        line_date = adjust(f.accrual + timedelta(days=obs), "next") if obs else f.date
        moved = (line_date - f.date).days
        how = "proposed (amount =, date =)" if moved == 0 else f"proposed (amount =, date moved +{moved} d; annotated 'not received' at r49)"
        rows.append((dmy(f.date), f.item, {"committed": "C", "generated": "G", "forecast": "F"}[f.status], eur(f.amount),
                     f"{lines_n} line(s) · {dmy(line_date)} · Σ {eur(f.amount)}", how, target))
    for (d, a, desc, why) in WINDOW_EXCEPTIONS:
        rows.append((dmy(d), "—", "—", "—", f"1 line · {eur(a)} · {desc}", "**exception**", why))
    table(["date", "book row", "status", "book amount", "statement line(s)", "match", "write on confirm"], rows)
    n_groups = len(expected)
    p(f"Batch card: **Confirm {n_groups} matches ({matched_lines} statement lines → {n_groups} book rows)**, 1:1 and N:1. "
      f"Exceptions: {len(WINDOW_EXCEPTIONS)} — each needs a match, a correction (D08) or an annotation before `Set cutover to {dmy(STATEMENT_TO)}` enables (host check). "
      f"Then `set_cutover(2026-09-25, note)` through the card; Save is the separate act (commit as r52).\n")
    p(f"Bank statement balance {dmy(STATEMENT_TO)} {eurp(stmt_bal)} · book {eurp(book_25)} · Δ {eur(stmt_bal - book_25)} = the exception line.\n")

    # ---- reproduction of the past (pilot Week 1)
    p("## Reproduction of the past — last 26 weeks to cutover (Validation, D23)\n")
    flows_m, _, _ = run(base_items, P, with_bank_only=False)
    bal_m = daily_balances(flows_m)
    rep_start = CUTOVER + timedelta(days=1) - timedelta(weeks=26)
    wr_m = week_rows(bal_m, flows_m, rep_start, 26)
    wr_b = week_rows(bal_b, flows_b, rep_start, 26)
    rows, prev_delta, n_ok, n_expl = [], 0, 0, 0
    for (s, e, i, o, c_m, *_r), (_, _, ib, ob, c_b, *_r2) in zip(wr_m, wr_b):
        delta = c_m - c_b
        new = delta - prev_delta
        turnover = ib - ob
        unc = [(d, a, note) for (d, a, ext, note, src) in UNCOVERED if s <= d <= e and src == "bank:intesa"]
        within = abs(delta) * 100 <= 3 * abs(c_b)
        n_ok += within
        note = "; ".join(f"{note.split(' —')[0]} {eur(a)}" for (d, a, note) in unc)
        flag = new != 0 and abs(new) * 100 > 2 * turnover
        if flag:
            n_expl += 1
        rows.append((week_label(s, e), eurp(c_m), eurp(c_b), eur(delta), pct(delta, c_b),
                     eur(new) if new else "—", (pct(new, turnover) + (" ▲" if flag else "")) if new else "—", note or "—"))
        prev_delta = delta
    table(["week", "modelled closing (items + erp/payroll rows)", "bank closing (statement)", "Δ", "Δ % of balance",
           "new discrepancy this week", "% of weekly turnover (▲ > 2 %)", "explanation (uncovered rows)"], rows)
    p(f"Host statistics. Modelled = a host run on a throwaway overlay holding the items and every erp:*, payroll and manual row "
      f"(`from ERP data alone`, pilot §7 Week 1) and none of the bank-only rows; bank = the statement closing balance, which equals "
      f"the book fold because every statement line to {dmy(CUTOVER)} is in the ledger. Δ at the first week already holds the bank-only "
      f"rows before the window (bank charges Jan–Mar). {n_ok} of 26 weeks within 3 %; {n_expl} new discrepancy(ies) above 2 % of weekly "
      f"turnover, each explained by a listed bank-only row. The ERP GL one-offs (van, dividend, acconto) are in both columns: they are "
      f"uncovered for the coverage metric (no item) but not a reproduction gap.\n")

    # ---- backtest
    p("## Backtest — run(cutover_override) at −3 / −6 / −12 months, scored on weekly closing balances (Validation, D23)\n")
    rows = []
    for label, bt_cut in (("−3 months", date(2026, 6, 21)), ("−6 months", date(2026, 3, 22)), ("−12 months", date(2025, 9, 21))):
        if bt_cut < H_START:
            rows.append((label, dmy(bt_cut), "not run: cutover before horizon start 1 Jan 2026", "—", "—", "—", "—", "—", "—"))
            continue
        flows_t, _, _ = run(base_items, P, cutover=bt_cut)
        bal_t = daily_balances(flows_t)
        n_weeks = 13
        wt = week_rows(bal_t, flows_t, bt_cut + timedelta(days=1), n_weeks)
        wa = week_rows(bal_b, flows_b, bt_cut + timedelta(days=1), n_weeks)
        ape = [(abs(ct - ca) * 10000) // abs(ca) for (*_x, ct, _m1, _m2), (*_y, ca, _m3, _m4) in
               zip([(w[0], w[1], w[2], w[3], w[4], w[5], w[6]) for w in wt], [(w[0], w[1], w[2], w[3], w[4], w[5], w[6]) for w in wa])]
        mape14 = sum(ape[:4]) // 4
        mape513 = sum(ape[4:13]) // 9
        tr_t = min(wt, key=lambda w: (w[4], w[0]))
        tr_a = min(wa, key=lambda w: (w[4], w[0]))
        timing = abs((tr_t[1] - tr_a[1]).days)
        depth = (abs(tr_t[4] - tr_a[4]) * 10000) // abs(tr_a[4])
        prev_t, prev_a, hits = bal_t[bt_cut], bal_b[bt_cut], 0
        for (w_t, w_a) in zip(wt, wa):
            hits += (w_t[4] - prev_t > 0) == (w_a[4] - prev_a > 0)
            prev_t, prev_a = w_t[4], w_a[4]
        win_flows = [f for f in flows_b if f.status == "actual" and bt_cut < f.date <= wa[-1][1]]
        tot = sum(abs(f.amount) for f in win_flows)
        unc = sum(abs(f.amount) for f in win_flows if f.cat == "uncovered")
        cov = (D(tot - unc) / D(tot) * 100).quantize(D("0.1"), ROUND_HALF_EVEN)

        def bp(x):
            return f"{(D(x) / 100).quantize(D('0.1'), ROUND_HALF_EVEN)} %"
        rows.append((label, dmy(bt_cut), f"{bp(mape14)} (target < 5 %) {'pass' if mape14 < 500 else 'fail'}",
                     f"{bp(mape513)} (< 12 %) {'pass' if mape513 < 1200 else 'fail'}",
                     f"{timing} d (< 7) {'pass' if timing < 7 else 'fail'} · forecast {dm(tr_t[1])} vs actual {dm(tr_a[1])}",
                     f"{bp(depth)} (< 10 %) {'pass' if depth < 1000 else 'fail'} · {eurp(tr_t[4])} vs {eurp(tr_a[4])}",
                     f"{hits} of {n_weeks} = {bp(hits * 10000 // n_weeks)} (> 85 %) {'pass' if hits * 100 > 85 * n_weeks else 'fail'}",
                     f"{cov} % (> 95 %) {'pass' if cov > 95 else 'fail'}",
                     "not cached · excluded from snapshots"))
    table(["run", "cutover_override", "MAPE weeks 1–4", "MAPE weeks 5–13", "trough timing", "trough depth", "directional accuracy", "coverage in window", "label"], rows)
    p("Host statistics (pilot §8.2), each stamped `host statistic · backtest · r51 · engine 1.4.0`. `run(cutover_override)` uses the "
      "current items and terms; the model as it was at that date is `at(r)`, which reports CK-W011 when the engine version moved.\n")

    # ---- plan vs actual
    PLAN_CUT = date(2026, 6, 21)
    p(f"## Plan vs actual — 13 closed weeks W26–W38 ({dm(PLAN_CUT + timedelta(days=1))}–{dm(CUTOVER)} 2026), cash by category (Variance, D12)\n")
    p(f"Plan = revision current at the cutover that opened the window: r38 · e07a552 · cutover {dmy(PLAN_CUT)} · engine 1.3.2, reproduced through `at(r38)` "
      f"(the at() kit runs the engine recorded in that revision). Actual = r51 · engine 1.4.0. The table stamps both triples and renders "
      f"**CK-W011** (`Engine version moved since revision r38: snapshot recorded 1.3.2, this build is 1.4.0`) beside it.\n")
    plan_flows, _, plan_lines = run(base_items, P, cutover=PLAN_CUT)
    ws, we = PLAN_CUT + timedelta(days=1), CUTOVER
    cats = ["revenue", "payroll", "contributions", "opex", "subcontract", "financing", "insurance", "tax", "uncovered"]
    # moved / changed per occurrence
    plan_by = defaultdict(list)
    for f in plan_flows:
        plan_by[(f.item, f.accrual)].append(f)
    act_by = defaultdict(list)
    for f in flows_b:
        if f.status == "actual":
            act_by[(f.item, f.accrual)].append(f)
    moved, changed = defaultdict(int), defaultdict(int)
    for key in set(plan_by) | set(act_by):
        pf = sorted(plan_by.get(key, []), key=lambda f: f.date)
        af = sorted(act_by.get(key, []), key=lambda f: f.date)
        for a_, b_ in zip(pf, af):
            in_p, in_a = ws <= a_.date <= we, ws <= b_.date <= we
            if a_.amount == b_.amount and in_p != in_a:
                moved[a_.cat] += (b_.amount if in_a else 0) - (a_.amount if in_p else 0)
            elif a_.amount != b_.amount and (in_p or in_a):
                changed[a_.cat] += (b_.amount if in_a else 0) - (a_.amount if in_p else 0)
        for extra in af[len(pf):]:
            if ws <= extra.date <= we:
                changed[extra.cat] += extra.amount
        for extra in pf[len(af):]:
            if ws <= extra.date <= we:
                moved[extra.cat] -= extra.amount          # expected in the window, not received: still committed
    rows, tp, ta = [], 0, 0
    for cat in cats:
        pl = sum(f.amount for f in plan_flows if ws <= f.date <= we and f.cat == cat)
        ac = sum(f.amount for f in flows_b if ws <= f.date <= we and f.cat == cat and f.status == "actual")
        tp += pl
        ta += ac
        assert ac - pl == moved[cat] + changed[cat], ("moved+changed != delta", cat, ac - pl, moved[cat], changed[cat])
        rows.append((f"cat:{cat}", eur(pl), eur(ac), eur(ac - pl), pct(ac - pl, pl), eur(moved[cat]), eur(changed[cat])))
    rows.append(("**Net**", eur(tp), eur(ta), eur(ta - tp), "—", eur(sum(moved.values())), eur(sum(changed.values()))))
    table(["category", "plan (r38 · 1.3.2)", "actual (r51 · 1.4.0)", "Δ", "Δ %", "moved (same amount, other period)", "changed (other amount)"], rows)
    p("Receipts by customer inside the same window:\n")
    rows = []
    for cu in customers:
        pl = sum(f.amount for f in plan_flows if ws <= f.date <= we and f.customer == cu and f.amount > 0)
        ac = sum(f.amount for f in flows_b if ws <= f.date <= we and f.customer == cu and f.amount > 0 and f.status == "actual")
        if pl or ac:
            rows.append((f"customer:{cu}", eur(pl), eur(ac), eur(ac - pl), pct(ac - pl, pl)))
    table(["customer", "plan", "actual", "Δ", "Δ %"], rows)
    p("Variance explanations (event notes on the actual rows):\n")
    for (item, d), (net, why) in AMOUNT_VARIANCES.items():
        if ws <= d <= we or (item, d) in AMOUNT_VARIANCES and d.month in (6, 7, 8, 9):
            p(f"- `{item}` invoice {dmy(d)}: {why} → net {eur(net)} (changed)")
    p(f"- `ret_acme` invoice 31 May 2026: paid at 95 d on 3 Sep 2026 instead of 68 d (7 Aug): moved into the window, not changed")
    p(f"- `ret_borghi` invoice 30 Jun 2026: paid at 55 d (24 Aug) instead of 52 d (21 Aug): moved inside the window, no variance")
    p(f"- `support_misc` invoice 31 Jul 2026: expected 9 Sep (40 d), not received at cutover — the committed leg stays on 9 Sep in the book, the actual column shows nothing (empty track, never a zero bar); the statement line of 24 Sep is matched in Close")
    p(f"- `cat:uncovered`: rows with no item in the window (see Uncovered movements)\n")

    # ---- uncovered movements and coverage
    p("## Uncovered movements (actual rows with no item) and coverage (host statistics)\n")
    rows = [(dmy(d), eur(a), src, ext, note) for (d, a, ext, note, src) in UNCOVERED]
    table(["date", "amount", "source", "ext_id", "description"], rows)
    actual_flows = [f for f in flows_b if f.status == "actual"]
    tot = sum(abs(f.amount) for f in actual_flows)
    unc = sum(abs(f.amount) for f in actual_flows if f.cat == "uncovered")
    cov = (D(tot - unc) / D(tot) * 100).quantize(D("0.01"), ROUND_HALF_EVEN)
    n_rows = sum(f.docs for f in actual_flows if f.cat != "uncovered") + len(UNCOVERED)
    pre_rows = sum(1 for ln in lines_b if ln.status == "ledger" and ln.accrual_date < H_START)
    committed = [f for f in flows_b if f.status == "committed"]
    p(f"- actual ledger rows dated in the horizon to cutover: {n_rows} (+1 correction pair) · pre-horizon rows (calibration history): {pre_rows} · committed open rows: {sum(f.docs for f in committed)} · forecast rows: 1")
    p(f"- actual movements {eurp(tot)} · attributable to a modelled item {eurp(tot - unc)} · uncovered {eurp(unc)} ({len(UNCOVERED)} rows) → **coverage {cov} %** (target > 95 %)")
    p(f"- committed open receivables (gross): {eurp(sum(f.amount for f in committed if f.amount > 0))} · committed open payables (gross): {eur(sum(f.amount for f in committed if f.amount < 0))}")
    p(f"- the bank charges were uncovered Jan–Aug (8 × {eur(m('-48'))} = {eur(8 * m('-48'))}); item `bank_charges` added at r48 covers them from 1 Sep — the coverage loop closing\n")

    p("### Coverage checklist (app layer, PRD §9.5) — signed r46 · 10 Aug 2026 · M. Conti · checklist unchanged since\n")
    table(["mechanic", "state", "evidence in the book"], [
        ("VAT (output, input, recoverable %, split payment)", "present", "TaxRegime `iva` monthly; VatSpec per item"),
        ("INPS contributions and IRPEF withholding on payroll", "present", "`f24_contrib`, `f24_contrib_13` (cat:contributions)"),
        ("INAIL", "present", "`inail` 16 Feb (cat:contributions)"),
        ("13th month", "present", "`payroll_13` 15 Dec; no 14th under CCNL metalmeccanico"),
        ("Ritenuta d'acconto remittance (F24 Erario)", "**NOT MODELLED**", f"CK-W004 on `freelance`; € 1,200.00/month = 20 % × € 6,000.00 (host arithmetic)"),
        ("IRES / IRAP saldo and acconti (30 Jun, 30 Nov)", "**NOT MODELLED**", "CK-I001; the 30 Jun 2026 acconto is uncovered row 2026-06-30-0027"),
        ("Acconto IVA (27 Dec)", "**NOT MODELLED**", "declared omission; commercialista to supply the figure in December"),
        ("TFR (severance on exit)", "**NOT MODELLED**", "declared omission: no exits planned"),
        ("Tax credits", "n/a", "no credit carried; `_tax:iva:credit` is —"),
        ("Loan and lease instalments", "present", "`loan_intesa`, `lease_vehicles`"),
        ("Dividends, one-off capex", "**declared omissions**", "uncovered rows 2026-05-12-0014, 2026-03-18-0009"),
    ])
    p("Standing sentence on screen: *The forecast understates outflows by the items marked NOT MODELLED.* Sign-off validity: "
      "the chip reads `signed r46 · checklist unchanged` while the tax regimes, cat:tax items, manual_tax flags and declared omissions are the same as at r46; "
      "any change flips it to `checklist changed since r46 · re-sign`.\n")

    # ---- aging
    p(f"## Receivables and payables — open committed rows at as-of {dmy(AS_OF)} (D24)\n")
    rows = []
    ctrl = 0
    for f in sorted(committed, key=lambda f: f.date):
        if f.amount <= 0:
            continue
        it = by_id[f.item]
        ln = next(l for l in lines_b if l.item == f.item and l.accrual_date == f.accrual)
        leg = next(l for l in ln.legs if l.cash_date == f.date)
        cdays = CONTRACTUAL_DAYS[f.item]
        cdue = adjust(f.accrual + timedelta(days=cdays), "next")
        over = (AS_OF - cdue).days
        bucket = "current" if over <= 0 else "1–30" if over <= 30 else "31–60" if over <= 60 else "61–90" if over <= 90 else "90+"
        obs_off = WINDOW_OBSERVED.get((f.item, f.accrual))
        on_stmt = f" · **on statement {dm(adjust(f.accrual + timedelta(days=obs_off), 'next'))} — confirm in Close**" if obs_off else ""
        ctrl += f.amount
        rows.append((f"INV · {dmy(f.accrual)}", cust_name[f.customer], eur(leg.net), eur(leg.vat) if leg.vat else "— (split payment)" if it.vat_treatment == "split_payment" else "—",
                     eur(f.amount), f"{dmy(cdue)} ({it.contractual})", f"{dmy(f.date)} ({it.due[0].offset}d {it.due[0].adjust})" + on_stmt,
                     week_label(f.date - timedelta(days=f.date.weekday()), f.date + timedelta(days=6 - f.date.weekday())).split(" · ")[0],
                     f"{over} d" if over > 0 else "—", bucket))
    table(["document", "customer", "net", "VAT", "gross (cash)", "due (contractual terms)", "expected (calibrated)", "expected week", "overdue vs terms", "bucket"], rows)
    p(f"Tie-out: Σ open AR gross {eurp(ctrl)} = AR control total from the erp:ar gate {eurp(ctrl)} · Δ —\n")
    rows = []
    for f in sorted(committed, key=lambda f: f.date):
        if f.amount >= 0:
            continue
        it = by_id[f.item]
        ln = next(l for l in lines_b if l.item == f.item and l.accrual_date == f.accrual)
        leg = next(l for l in ln.legs if l.cash_date == f.date)
        rows.append((f"{it.name} · inv {dmy(f.accrual)} ({f.docs} doc)", eur(leg.net), eur(leg.vat) if leg.vat else "—",
                     eur(-leg.wh) if leg.wh else "—", eur(f.amount), f"{dmy(f.date)} ({it.due[0].offset}d {it.due[0].adjust})",
                     "this week" if f.date <= AS_OF + timedelta(days=6) else "later"))
    table(["supplier · document", "net", "VAT", "withholding kept", "cash", "due (terms, calibrated = contractual for AP)", "due this week?"], rows)
    p("Payables due this week replace a payment-run view: CashKit executes nothing.\n")

    # ---- calibration
    p("## Calibration — observed payment behaviour per customer (host statistics from `query_events`) (D14)\n")
    rows = []
    for it_id in ("ret_acme", "ret_borghi", "support_misc"):
        it = by_id[it_id]
        obs = OBSERVED[it_id]
        settled = [(k, o) for k, o in enumerate(obs) if o is not None]
        delays = [o - CONTRACTUAL_DAYS[it_id] for _, o in settled]
        offs = [o for _, o in settled]
        med, p25, p75, p90 = median(delays), nearest_rank(delays, D("0.25")), nearest_rank(delays, D("0.75")), nearest_rank(delays, D("0.9"))
        last6 = median(delays[-6:])
        prev6 = median(delays[-12:-6])
        modal_days = defaultdict(int)
        for k, o in settled:
            inv = eom(*divmod_month(PRE_AR.year, PRE_AR.month + k))
            modal_days[adjust(inv + timedelta(days=o), "next").day] += 1
        modal = max(sorted(modal_days), key=lambda d: modal_days[d])
        book = it.due[0].offset
        proposed = CONTRACTUAL_DAYS[it_id] + med
        trend = last6 - prev6
        rows.append((cust_name[it.tags['customer']], it.contractual, str(len(settled)), f"+{med} d", f"+{p25} / +{p75} / +{p90} d",
                     f"day {modal}", f"{'+' if trend > 0 else ''}{trend} d (last 6 vs previous 6)" + (" ▲ widening" if trend >= 2 else ""),
                     f"{book}d", f"{proposed}d" + (" (= book)" if proposed == book else f" → set_item {it_id} due[0].offset")))
    rows.append((cust_name["comune_monza"], by_id["ms_comune"].contractual, "1", "+88 d (one invoice)", "—", "—", "—", "120d", "n < 12 → segment fallback `PA` 120d in use"))
    rows.append((cust_name["veltro"], by_id["ms_veltro"].contractual, "2 legs", "+0 d", "—", "—", "—", "0d / 60d", "n < 12 → contract terms in use"))
    table(["customer", "contractual", "n settled", "median delay", "p25 / p75 / p90", "modal pay day", "trend", "book offset", "proposed"], rows)
    p("Percentiles: nearest-rank. Delay = observed offset − contractual days. `Adopt` opens one card per customer: "
      "`set_item(<every item tagged customer:x>, settlement.due[0].offset = \"<n>d\")`, listing the touched items; recalibration is one commit.\n")

    # ---- sweep
    p("## Sensitivity sweep — Acme settlement offset ±10 / ±20 d (one throwaway scenario per value, WHAT-IF)\n")
    rows = []
    for off in (48, 58, 68, 78, 88):
        fl_s, _, _ = run(items_sweep("ret_acme", off), P)
        bal_s = daily_balances(fl_s)
        ss = summary(bal_s, fl_s, P)
        rows.append((f"{off}d" + (" (book)" if off == 68 else ""), f"{eurp(ss['fl'])} · {dmy(ss['fl_d'])}", str(len(ss["below"])),
                     eurp(ss["close"]), eur(ss["close"] - sb["close"]), "BASE" if off == 68 else f"WHAT-IF · sweep ret_acme {off}d"))
    table(["ret_acme due[0].offset", "LOWEST POINT after cutover", "days below floor", "horizon close", "Δ close vs book", "stamp"], rows)

    # ---- featured events
    p("## Featured ledger events\n")
    def leg_of(item, acc):
        ln = next(l for l in lines_b if l.item == item and l.accrual_date == acc)
        return ln, ln.legs[0]
    acme_may, l_acme_may = leg_of("ret_acme", date(2026, 5, 31))
    acme_jul, l_acme_jul = leg_of("ret_acme", date(2026, 7, 31))
    acme_aug, l_acme_aug = leg_of("ret_acme", date(2026, 8, 31))
    borghi_jun, l_borghi_jun = leg_of("ret_borghi", date(2026, 6, 30))
    borghi_jul, l_borghi_jul = leg_of("ret_borghi", date(2026, 7, 31))
    comune_jul, l_comune_jul = leg_of("ms_comune", date(2026, 7, 31))
    rent_sep, l_rent_sep = leg_of("rent", date(2026, 9, 1))
    payroll_sep = next(l for l in lines_b if l.item == "payroll" and l.accrual_date.month == 9 and l.accrual_date.year == 2026)
    wrong_net = m("-4080.00")
    ev = [
        ("ev-0811", dmy(rent_sep.accrual_date), "actual", "rent", eur(wrong_net), "22 · std · 1.00", f"{l_rent_sep.raw_date.day - rent_sep.accrual_date.day}d next", eur(wrong_net + mul(wrong_net, P['vat_standard'])), dmy(l_rent_sep.cash_date), "erp:ap", "2026-09-RENT-0009",
         "CORRECTED · SEE ev-0812 (struck through)"),
        ("ev-0812", dmy(rent_sep.accrual_date), "actual", "rent", eur(l_rent_sep.net), "22 · std · 1.00", "13d next", eur(l_rent_sep.cash), dmy(l_rent_sep.cash_date), "erp:ap", "2026-09-RENT-0009-c1",
         f"corrects ev-0811 · was {eur(wrong_net)} · note: “Digits transposed in the CSV import (4,080.00). Invoice shows net 4,800.00; escalation applies from Jan 2027.”"),
        ("ev-0798", dmy(borghi_jun.accrual_date), "actual", "ret_borghi", eur(l_borghi_jun.net), "22 · std", f"{(l_borghi_jun.raw_date - borghi_jun.accrual_date).days}d next (observed; book 52d)", eur(l_borghi_jun.cash), dmy(l_borghi_jun.cash_date), "erp:ar", "INV-2026-0171-L1-S",
         "Jun retainer, contractual due 30 Jul, paid 24 Aug: moved 3 d vs calibrated 21 Aug, same month"),
        ("ev-0834", dmy(acme_may.accrual_date), "actual", "ret_acme", eur(l_acme_may.net), "22 · std", f"{(l_acme_may.raw_date - acme_may.accrual_date).days}d next (observed; book 68d)", eur(l_acme_may.cash), dmy(l_acme_may.cash_date), "erp:ar", "INV-2026-0142-L1-S",
         "May retainer, contractual due 30 Jul, calibrated 7 Aug, paid 3 Sep (moved, not changed). VAT sat in the May return."),
        ("(generated)", dmy(payroll_sep.accrual_date), "generated", "payroll", eur(payroll_sep.legs[0].cash), "N2.2 · out_of_scope", "0d", eur(payroll_sep.legs[0].cash), dmy(payroll_sep.legs[0].cash_date), "— (payroll:zucchetti file 24 Sep: net € 52,000.00, evidence)", "2026-09 on confirm",
         "Sep net salaries, 25 FTE: a generated occurrence, not a row. The payroll file and the statement line of 25 Sep both match it; Confirm writes the actual row (source payroll:zucchetti, ext_id 2026-09)"),
        ("ev-0802", dmy(comune_jul.accrual_date), "committed", "ms_comune", eur(l_comune_jul.net), "N6.9 · split payment", "120d next", eur(l_comune_jul.cash), dmy(l_comune_jul.cash_date), "erp:ar", "INV-2026-0188-L1",
         f"Milestone 2; contractual due 30 Aug (net 30 PA); expected {dmy(l_comune_jul.cash_date)} (28 Nov Sat → next bank day)"),
        ("ev-0829", dmy(acme_aug.accrual_date), "committed", "ret_acme", eur(l_acme_aug.net), "22 · std", "68d next", eur(l_acme_aug.cash), dmy(l_acme_aug.cash_date), "erp:ar", "INV-2026-0203-L1",
         f"Aug retainer; contractual due 30 Oct; calibrated {dmy(l_acme_aug.raw_date)} ({wd(l_acme_aug.raw_date)}) → {dmy(l_acme_aug.cash_date)}"),
        ("ev-0830", dmy(borghi_jul.accrual_date), "committed", "ret_borghi", eur(l_borghi_jul.net), "22 · std", "52d next", eur(l_borghi_jul.cash), dmy(l_borghi_jul.cash_date), "erp:ar", "INV-2026-0187-L1",
         "Jul retainer; expected 21 Sep; statement line 21 Sep found → match proposed (void + `-S` actual)"),
        ("ev-0831", "31 Jul 2026", "committed", "support_misc", eur(m("34500")), "22 · std", "40d next", eur(m("42090")), "9 Sep 2026", "erp:ar", "INV-2026-0189-L1",
         "Jul support invoices (one row per line); expected 9 Sep, not received at cutover — annotated 'not received' at r49; statement line 24 Sep found → match proposed, date moved +15 d"),
        ("ev-0840", dmy(FORECAST_EVENT["date"]), "forecast", "— (cat:capex)", eur(FORECAST_EVENT["net"]), "22 · std · 1.00", "0d",
         eur(FORECAST_EVENT["net"] + mul(FORECAST_EVENT["net"], P["vat_standard"])), dmy(FORECAST_EVENT["date"]), "manual", "—",
         f"Server refresh, Dell quote, net; input VAT {eurp(-mul(FORECAST_EVENT['net'], P['vat_standard']))} enters the Oct 2026 return · editable in place"),
        ("ev-0761", "22 Jul 2026", "actual", "— (no item)", eur(m("1240.00")), "N2.2 · out_of_scope", "0d", eur(m("1240.00")), "22 Jul 2026", "bank:intesa", "2026-07-22-0017",
         "Rimborso INAIL · uncovered (cat:uncovered)"),
    ]
    table(["id", "date (invoice)", "status", "item", "amount (net)", "VAT code · treatment · recov.", "settlement", "cash leg", "cash date", "source", "ext_id", "note / stamp"], ev)

    # ---- trace examples
    p("## Trace examples\n")
    jan27, l27 = leg_of("ret_acme", date(2027, 1, 31))
    p(f"### trace(run, item=ret_acme, period={l27.cash_date.strftime('%Y-%m')}, measure=cash, depth=3) → {eurp(l27.cash)}\n")
    p("| step | binding | value |\n|---|---|---:|")
    p(f"| segment amount | segments[1].amount.constant (segment 1 Jan 2026 → open) · net, excl. VAT | {eurp(jan27.base)} |")
    p(f"| × escalation | p.istat_index = {P['istat_index']} · anchor segment_start 1 Jan 2026 · year 1 → (1 + 0.02)^1 = {jan27.esc} | {eurp(mul(jan27.base, jan27.esc))} |")
    p(f"| × probability | segments[1].probability = {jan27.prob} | {eurp(jan27.accrual)} |")
    p(f"| × settlement share | due[0].share = 1.0 · due[0].offset = \"{by_id['ret_acme'].due[0].offset}d\" · basis accrual | {eurp(l27.net)} |")
    p(f"| − withholding | due[0].withholding = 0 | {eurp(l27.net - l27.wh)} |")
    p(f"| + VAT | vat.rate = p.vat_standard = {P['vat_standard']} · treatment standard | {eurp(l27.vat)} |")
    p(f"| → cash date | accrual {dmy(jan27.accrual_date)} + 68 d = {dmy(l27.raw_date)} ({wd(l27.raw_date)}) → adjust next → **{dmy(l27.cash_date)}** | |")
    p(f"| **= cash leg** | | **{eurp(l27.cash)}** |")
    p(f"\nStamp: `{stamp('ret_acme')}`\n")

    nord = next(l for l in lines_b if l.item == "pipe_nord")
    ln_ = nord.legs[0]
    p(f"### trace(run, item=pipe_nord, period={ln_.cash_date.strftime('%Y-%m')}, measure=cash) → {eurp(ln_.cash)}\n")
    p(f"{eurp(nord.base)} × escalation 1 × probability {nord.prob} = {eurp(nord.accrual)} · share 1.0 @ 40d "
      f"· withholding 0 · + VAT {eurp(ln_.vat)} = **{eurp(ln_.cash)}** · accrual {dmy(nord.accrual_date)} + 40 d = {dmy(ln_.raw_date)} ({wd(ln_.raw_date)}) → next bank day {dmy(ln_.cash_date)}\n")

    fl = next(l for l in lines_b if l.item == "freelance" and l.accrual_date == date(2026, 9, 30))
    lf = fl.legs[0]
    p(f"### trace(run, item=freelance, period={lf.cash_date.strftime('%Y-%m')}, measure=cash) → {eur(lf.cash)}\n")
    p(f"net {eur(fl.base)} × escalation 1 × probability 1 = {eur(fl.accrual)} · share 1.0 @ 30d → leg {eur(lf.net)} "
      f"· withholding 0.20 keeps back {eurp(-lf.wh)} → {eur(lf.net - lf.wh)} · VAT 22 % adds {eurp(-lf.vat)} → cash paid to the freelancers "
      f"**{eur(lf.cash)}** on {dmy(lf.cash_date)} ({dmy(lf.raw_date)} {wd(lf.raw_date)} → next). The {eurp(-lf.wh)} kept back is owed to the state on the 16th of the next month; no item generates it → CK-W004.\n")

    vel = next(l for l in lines_b if l.item == "ms_veltro" and l.accrual_date == date(2026, 9, 30))
    p(f"### trace(run, item=ms_veltro, period=2026-09 and 2026-11, measure=cash) — a split settlement\n")
    for k, lg in enumerate(vel.legs):
        p(f"- leg {k}: share {by_id['ms_veltro'].due[k].share} of {eurp(vel.accrual)} = {eurp(lg.net)} + VAT {eurp(lg.vat)} = **{eurp(lg.cash)}** · {dmy(vel.accrual_date)} + {by_id['ms_veltro'].due[k].offset} d = {dmy(lg.raw_date)} ({wd(lg.raw_date)}) → {dmy(lg.cash_date)}")
    p("In the grid each leg is its own cell in its own week; the row header carries a split glyph; the trace lists both legs.\n")

    p("### why_zero examples — the five causes\n")
    p("- (1) `why_zero(pipe_nord, 2026-10)` → period outside every segment: the only segment starts 1 Nov 2026.")
    p("- (2) `why_zero(pipe_nord, 2027-01, scenario=downside)` → probability 0: the overlay sets segments[0].probability = 0 (WHAT-IF · downside).")
    p("- (3) `why_zero(sub_vat_tax, 2026-W40)` → upstream zero propagated through the formula: every input of the derived item (`cat:contributions`, `cat:tax`) is zero in W40; no F24 section and no VAT payment fall in that week.")
    p("- (4) `why_zero(payroll, 2026-09)` → generation suppressed by cutover: the ledger row ev-0821 (committed, 25 Sep) carries September.")
    nxt = next(l for l in lines_b if l.item == "ms_comune" and l.accrual_date == date(2026, 11, 30))
    p(f"- (5) `why_zero(ms_comune, 2026-10)` → settlement produced no cash leg this period: the schedule lists no amount for 31 Oct 2026; next accrual {dmy(nxt.accrual_date)} → cash {dmy(nxt.legs[0].cash_date)} (120 d, next).")
    p("- also (1): `why_zero(insurance, 2026-10)` → the annual recurrence yields no occurrence in October; the segment covers the period but generates on 1 Apr only.\n")

    # ---- diagnostics
    p("## Diagnostics (validate()) — engine text verbatim\n")
    diags = [
        ("warning", "CK-W004", "freelance", "settlement.due[0].withholding",
         "Withholding in use but no cat:tax item covers the counter-leg",
         "Withholding reduces one cash leg only; the engine does not generate the other side. Model the counter-leg — the remittance when you withhold, the credit when someone withholds from you — as an item tagged cat:tax."),
        ("info", "CK-I001", "—", "tax_regimes[iva]",
         "A TaxRegime is present but no non-VAT cat:tax items exist",
         "The engine schedules only what a TaxRegime accumulates. Any other obligation is modelled as an ordinary item tagged cat:tax; which ones apply is a question about the entity, not about the engine."),
    ]
    table(["severity", "code", "item / event", "field", "message", "suggested_fix"], diags)
    p("Status strip: `diagnostics 0 E 1 W 1 I`. No CK-W003: every actual row is dated (invoice date) on or before "
      "the cutover. The Italian checklist (which mechanics, which dates, the € 1,200.00 remittance) lives in the coverage "
      "block above, never inside a diagnostic row (ADR-0021).\n")

    # ---- alerts
    p("## Alert rules (host table, stamped `app config · unversioned`) and fired alerts\n")
    table(["rule", "kind", "over", "state"], [
        ("floor", "`min_cash_below`", "day-grain `cash` < `p.min_cash` after cutover, evaluated by the host after every commit or import", "fired for r51, see below"),
        ("stale-bank", "`source_stale`", "bank:intesa older than its cadence (each bank day)", "quiet"),
        ("stale-payroll", "`source_stale`", "payroll:zucchetti older than 31 days", "quiet"),
    ])
    below = sb["below"]
    if below:
        back = next((d for d in sorted(bal_b) if d > below[0] and bal_b[d] >= P["min_cash"]), None)
        p(f"- fired `floor`: first day below {eurp(P['min_cash'])} is **{dmy(below[0])}** · closing balance that day {eurp(bal_b[below[0]])} · "
          f"{len(below)} day(s) below the floor in the horizon · lowest {eurp(sb['fl'])} on {dmy(sb['fl_d'])} · back above on {dmy(back) if back else 'not within horizon'} · run {REVISION} · base · engine {ENGINE}")
    if sb["below_hist"]:
        hd = min(sb["below_hist"], key=lambda k: bal_b[k])
        p(f"- historic (actuals): below the floor on {len(sb['below_hist'])} day(s) before cutover, lowest {eurp(bal_b[hd])} on {dmy(hd)} — shown in the Position summary, not as an alert")
    p("- no recommended action text on any alert (ADR-0021)\n")

    # ---- scenario compare
    p("## Scenario compare — compare([base, downside], metric=cash)\n")
    rows = [
        ("Horizon close 30 Jun 2027", eurp(sb["close"]), eurp(sd["close"]), eur(sd["close"] - sb["close"])),
        ("LOWEST POINT after cutover", f"{eurp(sb['fl'])} · {dmy(sb['fl_d'])}", f"{eurp(sd['fl'])} · {dmy(sd['fl_d'])}", eur(sd["fl"] - sb["fl"])),
        ("First day below min_cash", dmy(sb["below"][0]) if sb["below"] else "not within horizon", dmy(sd["below"][0]) if sd["below"] else "not within horizon", "—"),
        ("Days below floor", str(len(sb["below"])), str(len(sd["below"])), str(len(sd["below"]) - len(sb["below"]))),
        ("First negative day (runway_end)", dmy(sb["runway_end"]) if sb["runway_end"] else "not within horizon", dmy(sd["runway_end"]) if sd["runway_end"] else "not within horizon", "—"),
        ("Closing 31 Dec 2026", eurp(bal_b[date(2026, 12, 31)]), eurp(bal_d[date(2026, 12, 31)]), eur(bal_d[date(2026, 12, 31)] - bal_b[date(2026, 12, 31)])),
        ("Total inflow", eurp(sb["tin"]), eurp(sd["tin"]), eur(sd["tin"] - sb["tin"])),
    ]
    table(["metric", "BASE · r51", "WHAT-IF · downside", "Δ vs base (service-computed)"], rows)
    p("Config diff (overlay `downside`, parent base, forked at r51) — items only, events are book-level:\n")
    p("- `ret_acme`, `ret_borghi`, `support_misc`: segments split at 1 Sep 2026, amount × 0.85 from 1 Sep (segments atomic); due[0].offset 68d → 98d, 52d → 82d, 40d → 70d")
    p("- `ms_comune`: schedule amounts on/after 1 Sep × 0.85; due[0].offset 120d → 150d · `ms_veltro`: schedule amounts on/after 1 Sep × 0.85")
    p("- `pipe_nord`: segments[0].probability 0.6 → 0 (why_zero cause 2)")
    p("- params: none · events: none (committed open invoices keep their amounts; their cash dates follow the overlay's terms until settled)\n")

    # ---- revision diffs
    p("## Revision diffs — diff_revisions(r50, r51) and diff_revisions(r49, r51)\n")
    for label, old_cut, cfg in (("r50 · b91d3a4 → r51 · 7c2e19b", date(2026, 9, 13),
                                 "cutover 2026-09-13 → 2026-09-20 · events: +14 actual rows (W38 matches), +1 void, +1 correction pair (ev-0811/0812) · items: none · params: none"),
                                ("r49 · 41f0c8e → r51 · 7c2e19b", date(2026, 9, 13) - timedelta(days=7),
                                 "cutover 2026-09-06 → 2026-09-20 · events: +31 actual rows, +2 void, +1 correction pair, +1 forecast event (ev-0840, r50) · items: none · params: none")):
        fl_o, _, _ = run(base_items, P, cutover=old_cut)
        bal_o = daily_balances(fl_o)
        so = summary(bal_o, fl_o, P, cutover=old_cut)
        p(f"**{label}** — config diff: {cfg}\n")
        table(["outcome", "at old revision", "at r51", "Δ"], [
            ("Horizon close 30 Jun 2027", eurp(so["close"]), eurp(sb["close"]), eur(sb["close"] - so["close"])),
            ("LOWEST POINT after (old) cutover", f"{eurp(so['fl'])} · {dmy(so['fl_d'])}", f"{eurp(sb['fl'])} · {dmy(sb['fl_d'])}", eur(sb["fl"] - so["fl"])),
            ("Closing 31 Dec 2026", eurp(bal_o[date(2026, 12, 31)]), eurp(bal_b[date(2026, 12, 31)]), eur(bal_b[date(2026, 12, 31)] - bal_o[date(2026, 12, 31)])),
            ("Days below floor", str(len(so["below"])), str(len(sb["below"])), str(len(sb["below"]) - len(so["below"]))),
        ])

    # ---- history
    p("## History\n")
    hist = [
        ("r51 · 7c2e19b", "21 Sep 2026 09:40", "M. Conti (controller)", "Reconcile W38; set_cutover 2026-09-20; correct ev-0811 (rent Sep, digits transposed)", ENGINE),
        ("r50 · b91d3a4", "18 Sep 2026 16:12", "M. Conti", "Add forecast event ev-0840: server refresh Oct (Dell quote, € 22,500.00 net)", ENGINE),
        ("r49 · 41f0c8e", "14 Sep 2026 09:35", "M. Conti", "Reconcile W37; set_cutover 2026-09-13", ENGINE),
        ("r48 · 5d0e7a1", "7 Sep 2026 09:50", "M. Conti", "Reconcile W36; set_cutover 2026-09-06; add item bank_charges (uncovered Jan–Aug)", ENGINE),
        ("r47 · c3a9f02", "31 Aug 2026 09:20", "M. Conti", "Reconcile W35; set_cutover 2026-08-30", ENGINE),
    ]
    table(["revision", "when", "author", "message", "engine"], hist)
    p("Referenced elsewhere:\n")
    table(["revision", "when", "author", "message", "engine"], [
        ("r46 · 8b4c210", "10 Aug 2026 11:05", "M. Conti", "Sign coverage statement (checklist v3: ritenute, IRES/IRAP, acconto IVA, TFR, dividends, capex declared)", ENGINE),
        ("r38 · e07a552", "22 Jun 2026 09:30", "M. Conti", "Reconcile W25; set_cutover 2026-06-21", "1.3.2"),
        ("r37 · 9ac41d0", "28 May 2026 11:03", "L. Ferri (CFO)", "Recalibrate Acme 60d → 68d, Borghi 30d → 52d (set_item ret_acme, ret_borghi; Q1 calibration)", "1.3.2"),
        ("r12 · 2f7e0c9", "9 Jan 2026 15:20", "L. Ferri (CFO)", "set_param min_cash 60000, credit_line 150000", "1.3.0"),
    ])
    p("Engine moved 1.3.2 → 1.4.0 between r38 and r39 (own row type in the History list). `at(r38)` runs the engine recorded in r38 and reports CK-W011 when compared with r51.\n")


def divmod_month(y: int, mo: int):
    y2, m2 = divmod(mo - 1, 12)
    return y + y2, m2 + 1


def statement_lines(flows: list[Flow]):
    """The bank statement lines for (cutover, statement_to]: one line per document/occurrence, plus exceptions."""
    out = []
    seen = set()
    for (item, acc), off in WINDOW_OBSERVED.items():
        f = next(x for x in flows if x.item == item and x.accrual == acc and x.status == "committed")
        out.append((adjust(acc + timedelta(days=off), "next"), f.amount, f.item, "match"))
        seen.add((item, acc))
    for f in flows:
        if CUTOVER < f.date <= STATEMENT_TO and (f.item, f.accrual) not in seen:
            per = f.amount // f.docs
            for k in range(f.docs):
                amt = per if k < f.docs - 1 else f.amount - per * (f.docs - 1)
                out.append((f.date, amt, f.item, "match"))
    for (d, a, desc, why) in WINDOW_EXCEPTIONS:
        out.append((d, a, desc, "exception"))
    return sorted(out, key=lambda x: x[0])


if __name__ == "__main__":
    main()
    text = OUT.getvalue()
    import re as _re
    assert not _re.search(r"€ 0\.00\b", text), "a money cell printed 0.00"
    assert "-0." not in text.replace(MINUS, ""), "an ASCII hyphen slipped into a negative figure"
    sys.stdout.write(text)
