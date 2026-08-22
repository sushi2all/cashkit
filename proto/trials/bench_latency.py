"""Latency bench: 3 turn classes x both models, 3 reps each. Prints seconds."""
import statistics
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from runner import _history, chat, ops, reset

CLASSES = {
    "edit": "Add a gym membership of 40 EUR per month from March 2026.",
    "qa": "What is my closing balance in June and can I afford 900 EUR extra that month?",
    "formula": ("Add a bank fee rule: each month, if the previous month's closing "
                "balance was negative, charge 1% of its absolute value with a 5 EUR "
                "minimum. The fee reduces the balance. Keep a closing balance line."),
}
BOOK = [
    {"op": "create_book", "start": "2026-01-01", "end": "2027-01-01",
     "opening_balance": "500", "grain": "month"},
    {"op": "add_item", "item": {"id": "salary", "name": "Salary", "kind": "flow",
     "direction": "in", "tags": {"cf": "cash"},
     "segments": [{"start": "2026-01-01", "recurrence": {"every": 1, "unit": "month"},
                   "amount": {"constant": "1500"}}], "settlement": "immediate"}},
    {"op": "add_item", "item": {"id": "rent", "name": "Rent", "kind": "flow",
     "direction": "out", "tags": {"cf": "cash"},
     "segments": [{"start": "2026-01-01", "recurrence": {"every": 1, "unit": "month"},
                   "amount": {"constant": "-1100"}}], "settlement": "immediate"}},
]

for model in (sys.argv[1:] or ["lite", "flash"]):
    for cls, prompt in CLASSES.items():
        times = []
        for _ in range(3):
            reset()
            ops(BOOK)
            _history.clear()
            out = chat(prompt, model=model)
            times.append(out["elapsed"])
        print(f"{model:6s} {cls:8s} median {statistics.median(times):5.1f}s  "
              f"runs {[round(t,1) for t in times]}")
