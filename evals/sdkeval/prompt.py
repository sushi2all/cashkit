"""What the model is told: the execution contract, the SDK context, the task.

The **contract** is fixed and short: which names are pre-bound, that the
script runs top to bottom in a fresh process, that one fenced Python block is
expected. The **context** is swappable (``--context``): the shipped SDK
reference, any file you want to test instead, or nothing at all — so the same
suite measures the docs as well as the model (an ablation: does the reference
actually help, and which sections?).
"""

from __future__ import annotations

from pathlib import Path

from .schema import TaskInstance

CONTEXT_DIR = Path(__file__).with_name("context")
DEFAULT_CONTEXT = CONTEXT_DIR / "sdk_reference.md"

CONTRACT = """\
You write Python scripts that use the CashKit SDK to build, change and query
cash-flow forecasts. Reply with exactly one fenced ```python code block holding
the complete script; you may add brief prose outside it.

How your script is executed:
- It runs top to bottom in a fresh Python process with the `cashkit` package
  installed. Import everything you use.
- Pre-bound names: `ROOT` (str) — the directory where the book lives or must be
  created; `kit` — the open `CashKit` for the book at `ROOT`, or `None` when no
  book exists yet; `answer` — `None`. When you create a book, assign the new kit
  to `kit`.
- For a question, store the result in `answer` with the exact type requested
  (money is `decimal.Decimal`, never float; a date is `datetime.date`).
- Every SDK write persists immediately. What is graded is the book on disk
  after your script finishes, plus `answer`.
- Do only what is asked. Do not commit unless asked.
"""


def load_context(spec: str | None) -> str:
    """``None``/``"reference"`` → the shipped reference; ``"none"`` → empty; else a file path."""
    if spec is None or spec == "reference":
        return DEFAULT_CONTEXT.read_text()
    if spec == "none":
        return ""
    return Path(spec).read_text()


def system_prompt(context: str) -> str:
    if not context.strip():
        return CONTRACT
    return CONTRACT + "\n\n# CashKit SDK reference\n\n" + context.strip() + "\n"


def user_prompt(task: TaskInstance) -> str:
    return task.prompt + "\n"


def feedback(observation: dict) -> str | None:
    """What an agent would see after running its script: the traceback, or the
    error diagnostics its writes and the final run returned. ``None`` when there
    is nothing an agent would act on. Never leaks the expected values."""
    status = observation.get("status")
    if status in ("exception", "syntax_error", "crash", "timeout"):
        return (
            "Your script failed:\n\n```\n" + str(observation.get("exception", ""))[-3000:] + "\n```\n\n"
            "Fix it and reply with the complete corrected script (it will run from scratch "
            "against the original book)."
        )
    problems: list[str] = []
    book = observation.get("observation") or {}
    for call in observation.get("calls", []):
        # Only refused *writes* are problems. A read used as a probe
        # (``CashKit.open`` answering CK-E029 "no book here") is the documented
        # way to ask a question, and ``create_book`` answering CK-E031 is
        # harmless once a book exists — flagging either would push a correct
        # script into a needless rewrite.
        if not call.get("write"):
            continue
        errors = [c for c in call.get("diagnostics", []) if c.startswith("error:")]
        if call["name"] == "create_book" and book.get("book"):
            errors = [e for e in errors if e != "error:CK-E031"]
        if errors:
            problems.append(f"- {call['name']}(scenario={call.get('scenario')!r}) returned {', '.join(e[6:] for e in errors)}")
    if not book.get("book"):
        problems.append("- after your script there is no book at ROOT (and `kit` holds none)")
    for scenario, data in (book.get("scenarios") or {}).items():
        for d in (data or {}).get("diagnostics", []):
            if d["severity"] == "error":
                problems.append(f"- run({scenario!r}): {d['code']} {d.get('item_id') or ''}: {d['message'][:200]}")
    if not problems:
        return None
    return (
        "Your script ran, but the SDK reported errors:\n\n" + "\n".join(dict.fromkeys(problems))[:4000]
        + "\n\nFix the cause and reply with the complete corrected script (it will run from scratch "
        "against the original book)."
    )
