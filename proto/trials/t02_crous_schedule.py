"""T02 — edge case: CROUS instalment slips; October pays double at revalued rate.

Tests: editing an EXISTING book, month-varying schedule, one-time arithmetic
(2 * ROUND(242 * 1.021) = 494), and leaving other months intact.
"""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import chat, state, reset, cell, closing, expect, find, finish

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
chat(
    "New 2026 monthly budget, Jan to Dec 2026, opening balance 150 EUR. "
    "One income: CROUS scholarship, 242 EUR per month, paid January to June and "
    "September to December (not July, not August). "
    "One expense: rent 455 EUR per month all year.",
    model=MODEL,
)
chat(
    "Change of plan for the scholarship. My file is validated late: September pays "
    "nothing. In October CROUS pays September and October together, both instalments "
    "at the revalued rate: the 242 instalment increased by 2.1%, rounded to whole "
    "euros, so each instalment is 247 and October receives 494. November and December "
    "pay one instalment each at 247. January to June stay at 242.",
    model=MODEL,
)
s = state()
crous = find(s, "crous") if find(s, "crous") != "?" else find(s, "scholar")
expect("crous Jan", cell(s, crous, "2026-01"), 242)
expect("crous Jun", cell(s, crous, "2026-06"), 242)
expect("crous Jul", cell(s, crous, "2026-07") or 0, 0)
expect("crous Sep", cell(s, crous, "2026-09") or 0, 0)
expect("crous Oct", cell(s, crous, "2026-10"), 494)
expect("crous Nov", cell(s, crous, "2026-11"), 247)
expect("crous Dec", cell(s, crous, "2026-12"), 247)
expect("rent untouched", cell(s, find(s, "rent"), "2026-10"), -455)
# closing Dec = 150 + income(6*242 + 494 + 247 + 247) - 12*455 = 150 + 2440 - 5460
expect("closing Dec", closing(s, "2026-12"), -2870)
finish("T02 " + MODEL)
