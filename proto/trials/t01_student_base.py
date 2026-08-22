"""T01 — student base budget, single instruction, model: lite."""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import chat, state, reset, cell, closing, expect, finish

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

reset()
chat(
    "Start a new 2026 monthly budget (January to December 2026), opening balance 150 EUR. "
    "Income each month: cafe wage 515 net, babysitting 48, parental transfer 150, "
    "CROUS scholarship 242 (paid January to June and September to December, NOT in July or August), "
    "APL housing aid 112. "
    "Expenses each month: rent share 455, groceries 245, transport pass 35.20, "
    "and a 70 EUR standing order to savings that runs January to June only.",
    model=MODEL,
)
s = state()
ids = {i["id"]: i["name"] for i in s["items"]}
print("  items:", ids)


import re


def find(*words):
    for iid, name in ids.items():
        t = (iid + " " + name).lower()
        if all(re.search(r"\b" + w, t) for w in words):
            return iid
    return "?"


expect("rent Jan", cell(s, find("rent"), "2026-01"), -455)
expect("transport Jan", cell(s, find("transport"), "2026-01"), -35.20)
expect("crous Jul = 0", cell(s, find("crous"), "2026-07") or cell(s, find("scholar"), "2026-07") or 0, 0)
expect("crous Sep", cell(s, find("crous"), "2026-09") or cell(s, find("scholar"), "2026-09"), 242)
expect("savings Aug = 0", cell(s, find("saving"), "2026-08") or cell(s, find("standing"), "2026-08") or 0, 0)
expect("closing Jan", closing(s, "2026-01"), 411.80)
expect("closing Dec", closing(s, "2026-12"), 3227.60)
finish("T01 " + MODEL)
