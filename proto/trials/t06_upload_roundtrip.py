"""T06 — round-trip: export our own budget xlsx, reset, upload it, compare numbers."""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import chat, state, reset, cell, closing, expect, find, finish, ops, export, upload

MODEL = sys.argv[1] if len(sys.argv) > 1 else "lite"

STUDENT_OPS = [
    {"op": "create_book", "start": "2026-01-01", "end": "2027-01-01",
     "opening_balance": "150", "grain": "month"},
]


def item(iid, name, direction, amount, start="2026-01-01", end=None, extra_segs=None):
    segs = [{"start": start, "end": end, "recurrence": {"every": 1, "unit": "month"},
             "amount": {"constant": amount}}]
    if extra_segs:
        segs += extra_segs
    return {"op": "add_item", "item": {
        "id": iid, "name": name, "kind": "flow", "direction": direction,
        "tags": {"cf": "cash"}, "segments": segs, "settlement": "immediate"}}


STUDENT_OPS += [
    item("cafe_wage", "Cafe wage", "in", "515"),
    item("babysitting", "Babysitting", "in", "48"),
    item("parents", "Parental transfer", "in", "150"),
    {"op": "add_item", "item": {"id": "crous", "name": "CROUS scholarship", "kind": "flow",
     "direction": "in", "tags": {"cf": "cash"}, "settlement": "immediate", "segments": [
        {"start": "2026-01-01", "end": "2026-07-01",
         "recurrence": {"every": 1, "unit": "month"}, "amount": {"constant": "242"}},
        {"start": "2026-09-01", "end": None,
         "recurrence": {"every": 1, "unit": "month"}, "amount": {"constant": "242"}}]}},
    item("apl", "APL housing aid", "in", "112"),
    item("rent", "Rent share", "out", "-455"),
    item("groceries", "Groceries", "out", "-245"),
    item("transport", "Transport pass", "out", "-35.20"),
    item("livret", "Savings order", "out", "-70", end="2026-07-01"),
]

reset()
ops(STUDENT_OPS)
orig = state()
xlsx = export("mode=budget&months=12")
print(f"  exported {len(xlsx)} bytes")

reset()
upload(xlsx, "student-budget.xlsx", model=MODEL)
s = state()
if not s.get("book"):
    print("  no book created — FAIL")
    sys.exit(1)
print("  items:", {i["id"]: i["name"] for i in s["items"]})

expect("rent Jan", cell(s, find(s, "rent"), "2026-01"), -455)
expect("wage Jan", cell(s, find(s, "wage") if find(s, "wage") != "?" else find(s, "cafe"), "2026-01"), 515)
crous = find(s, "crous") if find(s, "crous") != "?" else find(s, "scholar")
expect("crous Jul = 0", cell(s, crous, "2026-07") or 0, 0)
expect("crous Sep", cell(s, crous, "2026-09"), 242)
expect("savings Aug = 0", cell(s, find(s, "saving"), "2026-08") or 0, 0)
for m in ("2026-01", "2026-06", "2026-07", "2026-09", "2026-12"):
    expect(f"closing {m}", closing(s, m), closing(orig, m))
finish("T06 " + MODEL)
