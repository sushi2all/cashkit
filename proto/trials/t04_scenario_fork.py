"""T04 — params as levers + scenario fork with a sparse param override."""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import chat, state, reset, cell, closing, expect, find, finish

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
chat(
    "New 2026 monthly budget for my freelance business, January to December 2026, "
    "opening balance 10000 EUR. Revenue: I bill a day rate of 450 EUR for 12 working "
    "days per month — set both up as parameters I can sweep later. "
    "Fixed costs: 3200 EUR per month, all in.",
    model=MODEL,
)
chat(
    "Now fork a scenario called downside from base where I only bill 8 days per month. "
    "Leave base untouched.",
    model=MODEL,
)
base = state("base")
down = state("downside")
rev_b = find(base, "revenue") if find(base, "revenue") != "?" else find(base, "billing")
rev_d = find(down, "revenue") if find(down, "revenue") != "?" else find(down, "billing")
expect("base revenue Jan", cell(base, rev_b, "2026-01", "value"), 5400)
expect("base closing Dec", closing(base, "2026-12"), 36400)
expect("downside revenue Jan", cell(down, rev_d, "2026-01", "value"), 3600)
expect("downside closing Dec", closing(down, "2026-12"), 14800)
finish("T04 " + MODEL)
