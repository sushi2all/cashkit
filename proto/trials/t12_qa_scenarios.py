"""T12 — Q&A: compare two scenarios numerically from the results block."""
import re
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import FAILS, chat, finish, ops, reset

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
ops([
    {"op": "create_book", "start": "2026-01-01", "end": "2027-01-01",
     "opening_balance": "10000", "grain": "month",
     "params": {"day_rate": "450", "days_per_month": "12"}},
    {"op": "add_derived", "id": "revenue", "formula": "p.day_rate * p.days_per_month",
     "tags": {"cf": "cash"}, "name": "Consulting revenue"},
    {"op": "add_item", "item": {"id": "costs", "name": "Fixed costs", "kind": "flow",
     "direction": "out", "tags": {"cf": "cash"},
     "segments": [{"start": "2026-01-01", "recurrence": {"every": 1, "unit": "month"},
                   "amount": {"constant": "-3200"}}], "settlement": "immediate"}},
    {"op": "fork_scenario", "parent": "base", "id": "downside", "note": "8 days"},
    {"op": "set_scenario_param", "scenario": "downside", "key": "days_per_month",
     "value": "8"},
])
# base closes 36400, downside 14800 -> difference 21600

out = chat("Which scenario ends 2026 with more cash, base or downside, and by how "
           "much exactly?", model=MODEL)
reply = out.get("reply", "")
nops = sum(len(r.get("ops", [])) for r in out.get("rounds", []))
print(f"  reply: {reply[:200]}")
ok = nops == 0 and "base" in reply.lower() and re.search(r"21[,.']?600", reply)
print(f"  {'PASS' if ok else 'FAIL'} base wins by 21600 (ops={nops})")
if not ok:
    FAILS.append("scenario compare")
finish("T12 " + MODEL)
