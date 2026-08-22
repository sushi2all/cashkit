"""T08 — settlement terms: net-45 invoices and a 50/50 split payment.

Accrual vs cash: invoices accrue on the 1st, cash lands 45 days later (next month).
December's invoice settles outside the horizon and must simply drop off.
"""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import chat, state, reset, cell, closing, expect, find, finish

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
chat(
    "New 2026 monthly budget for my consulting business, January to December 2026, "
    "opening balance 5000 EUR. I issue a 6000 EUR consulting invoice on the 1st of "
    "every month and clients pay 45 days after the invoice date. "
    "I also pay 300 EUR per month of software subscriptions, immediately.",
    model=MODEL,
)
chat(
    "One more thing: a fixed-price project in June, 12000 EUR invoiced on June 1st. "
    "The client pays half on signing that same day and the other half 60 days later.",
    model=MODEL,
)
s = state()
cons = find(s, "consult") if find(s, "consult") != "?" else find(s, "invoice")
proj = find(s, "project")
expect("consulting accrual Jan", cell(s, cons, "2026-01", "value"), 6000)
expect("consulting cash Jan = 0", cell(s, cons, "2026-01", "cash") or 0, 0)
expect("consulting cash Feb", cell(s, cons, "2026-02", "cash"), 6000)
expect("project cash Jun", cell(s, proj, "2026-06", "cash"), 6000)
expect("project cash Jul", cell(s, proj, "2026-07", "cash"), 6000)
expect("project cash Aug = 0", cell(s, proj, "2026-08", "cash") or 0, 0)
# closing: 5000 + 11*6000 (Dec invoice settles in 2027) + 12000 - 12*300 = 79400
expect("closing Dec", closing(s, "2026-12"), 79400)
finish("T08 " + MODEL)
