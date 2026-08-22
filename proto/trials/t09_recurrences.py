"""T09 — non-monthly recurrences: quarterly premium and a weekly wage."""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import chat, state, reset, cell, closing, expect, find, finish

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
chat(
    "New 2026 monthly budget, January to December 2026, opening balance 0. "
    "Two things: an insurance premium of 300 EUR due every 3 months starting in March "
    "(so March, June, September, December), and my part-time wage of 120 EUR paid "
    "every week starting Monday January 5th, all year.",
    model=MODEL,
)
s = state()
ins = find(s, "insurance")
wage = find(s, "wage") if find(s, "wage") != "?" else find(s, "part")
expect("insurance Mar", cell(s, ins, "2026-03"), -300)
expect("insurance Apr = 0", cell(s, ins, "2026-04") or 0, 0)
expect("insurance Jun", cell(s, ins, "2026-06"), -300)
expect("insurance Dec", cell(s, ins, "2026-12"), -300)
# Mondays in Jan 2026 from the 5th: 5,12,19,26 -> 4 payments
expect("wage Jan (4 Mondays)", cell(s, wage, "2026-01"), 480)
# 52 Mondays from Jan 5 to Dec 28 2026 -> 6240; insurance 4*300
expect("closing Dec", closing(s, "2026-12"), 6240 - 1200)
finish("T09 " + MODEL)
