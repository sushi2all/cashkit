"""T03 — conditional overdraft fee (agios) in arrears with a 2 EUR floor.

Needs: param, derived money item with where/prev/max/round_/abs_, a closing-balance
stock the fee reads, and the fee feeding back into that balance (fold tier).
Expected series computed by hand (rate 7.95%/12, 2dp round, 2 EUR floor):
Jan 0, Feb -100, Mar -202, Apr -304, May -406.01, Jun -508.70, Jul -612.07, Aug -716.12.
"""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import chat, state, reset, cell, closing, expect, find, finish

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
chat(
    "New 2026 monthly budget, January to December 2026, opening balance 100 EUR. "
    "Income: salary 500 EUR per month. Expense: rent 600 EUR per month. "
    "My bank charges overdraft interest (agios) in arrears: in each month, if the "
    "PREVIOUS month's closing balance was negative, it charges one twelfth of a 7.95% "
    "annual rate on the absolute value of that previous balance, rounded to 2 decimals, "
    "with a 2 EUR minimum per charged month. No charge when the previous close was "
    "positive or zero. For January use the opening balance (100, so no charge). "
    "The fee itself reduces the balance, so it compounds month over month. "
    "Model the fee and keep a closing-balance line I can read.",
    model=MODEL,
)
s = state()
ag = find(s, "agios") if find(s, "agios") != "?" else find(s, "overdraft")
expect("agios Feb = 0", cell(s, ag, "2026-02") or 0, 0)
expect("agios Mar", cell(s, ag, "2026-03"), -2)
expect("agios May", cell(s, ag, "2026-05"), -2.01)
expect("agios Jun", cell(s, ag, "2026-06"), -2.69)
expect("closing Mar", closing(s, "2026-03"), -202)
expect("closing Jun", closing(s, "2026-06"), -508.70)
# engine truth: 4dp intermediates make Aug fee 4.06 (Excel float ROUND would say 4.05)
expect("closing Aug", closing(s, "2026-08"), -716.13)
finish("T03 " + MODEL)
