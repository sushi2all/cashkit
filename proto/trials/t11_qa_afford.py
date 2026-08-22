"""T11 — Q&A: affordability questions must be answered from computed results, no ops."""
import re
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import FAILS, chat, expect, finish, ops, reset

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
ops([
    {"op": "create_book", "start": "2026-01-01", "end": "2027-01-01",
     "opening_balance": "100", "grain": "month"},
    {"op": "add_item", "item": {"id": "salary", "name": "Salary", "kind": "flow",
     "direction": "in", "tags": {"cf": "cash"},
     "segments": [{"start": "2026-01-01", "recurrence": {"every": 1, "unit": "month"},
                   "amount": {"constant": "1000"}}], "settlement": "immediate"}},
    {"op": "add_item", "item": {"id": "rent", "name": "Rent", "kind": "flow",
     "direction": "out", "tags": {"cf": "cash"},
     "segments": [{"start": "2026-01-01", "recurrence": {"every": 1, "unit": "month"},
                   "amount": {"constant": "-800"}}], "settlement": "immediate"}},
])
# closing Sep = 100 + 9*200 = 1900


def ask(q, pattern, label):
    out = chat(q, model=MODEL)
    nops = sum(len(r.get("ops", [])) for r in out.get("rounds", []))
    reply = out.get("reply", "")
    print(f"  reply: {reply[:160]}")
    ok = nops == 0 and re.search(pattern, reply, re.I) is not None
    print(f"  {'PASS' if ok else 'FAIL'} {label} (ops={nops})")
    if not ok:
        FAILS.append(label)


ask("Can I afford a 1500 EUR laptop in September without going negative that month?",
    r"\b400\b|yes", "afford 1500 in Sep -> yes, 400 left")
ask("And a 2500 EUR one instead?",
    r"\b600\b|\bno\b|cannot|not afford|short", "afford 2500 in Sep -> no, 600 short")
finish("T11 " + MODEL)
