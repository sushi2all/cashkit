"""Executed in a child process: run setup + candidate code, observe the book.

This file is run by path under ``python -I`` (see :mod:`sdkeval.sandbox`), so
it imports nothing from ``sdkeval`` — only the standard library and
``cashkit``. It reads one JSON job on stdin and writes one JSON observation,
framed by a marker line, on stdout. Anything the candidate prints is captured
separately and returned truncated.

What it records:

* **calls** — every public ``CashKit`` / ``RunRef`` method and
  ``create_book`` the candidate invoked (not the setup), with the scenario it
  addressed and the diagnostic codes the call returned. That is what lets a
  hygiene check say "a refused write was ignored" or "a question task wrote".
* **state** — the book is re-opened **from disk** after the script ends.
  Every write persists (ADR-0031), so the on-disk book is the truth; a
  candidate that mutated a Python object without going through the SDK has
  changed nothing, and the observation says so.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

MARKER = "@@SDKEVAL-OBSERVATION@@"
_MINOR = Decimal("0.0001")


def _money(minor: int) -> str:
    return str((Decimal(int(minor)) * _MINOR).quantize(_MINOR))


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "value") and type(value).__module__.startswith("cashkit"):
        return value.value  # enums
    return value


def _answer(value) -> dict:
    if value is None:
        return {"type": "none", "value": None}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, int):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": repr(value)}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    return {"type": "repr", "value": repr(value)[:500]}


# --------------------------------------------------------------------------- #
# Call recording
# --------------------------------------------------------------------------- #

CALLS: list[dict] = []
RECORDING = {"on": False}

KIT_METHODS = (
    "set_item", "remove_item", "unset", "set_param", "apply_macro", "set_book",
    "fork", "flatten", "add_event", "import_events", "void_event", "correct_event",
    "commit", "discard", "run", "validate", "resolve", "diff", "provenance",
    "describe_book", "events_for", "query_events", "reconcile", "compare",
    "history", "at", "status", "diff_revisions", "reproduce", "read_export", "open",
)
RUN_METHODS = ("summary", "trace", "why_zero", "depends_on", "dependents_of", "frame", "pivot", "export")
WRITE_METHODS = {
    "set_item", "remove_item", "unset", "set_param", "apply_macro", "set_book", "fork",
    "flatten", "add_event", "import_events", "void_event", "correct_event", "commit",
    "discard", "create_book",
}


def _codes(result) -> list[str]:
    diagnostics = None
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], tuple):
        diagnostics = result[1]
    elif hasattr(result, "diagnostics"):
        diagnostics = getattr(result, "diagnostics")
    elif isinstance(result, list) and all(hasattr(d, "code") for d in result):
        diagnostics = result
    if not diagnostics:
        return []
    return [f"{d.severity}:{d.code}" for d in diagnostics if hasattr(d, "code")]


def _wrap(owner, name: str, label: str):
    original = getattr(owner, name)
    is_classmethod = isinstance(owner.__dict__.get(name), classmethod)

    def record(args, kwargs, result, error):
        if not RECORDING["on"]:
            return
        entry = {"name": label, "write": label in WRITE_METHODS}
        scenario = kwargs.get("scenario")
        if scenario is None and label in ("run", "validate", "resolve", "describe_book", "events_for") and args:
            scenario = args[0]
        if label == "fork":
            entry["new"] = args[0] if args else kwargs.get("new_id")
            entry["parent"] = kwargs.get("parent", "base")
        if label == "commit":
            entry["message"] = args[0] if args else kwargs.get("message")
        entry["scenario"] = scenario if isinstance(scenario, str) else None
        if error is not None:
            entry["raised"] = f"{type(error).__name__}: {error}"[:300]
        else:
            entry["diagnostics"] = _codes(result)
        CALLS.append(entry)

    if is_classmethod:
        func = original.__func__

        def wrapper(cls, *args, **kwargs):
            try:
                result = func(cls, *args, **kwargs)
            except BaseException as exc:
                record(args, kwargs, None, exc)
                raise
            record(args, kwargs, result, None)
            return result

        setattr(owner, name, classmethod(wrapper))
        return

    def wrapper(self, *args, **kwargs):
        try:
            result = original(self, *args, **kwargs)
        except BaseException as exc:
            record(args, kwargs, None, exc)
            raise
        record(args, kwargs, result, None)
        return result

    wrapper.__name__ = name
    wrapper.__doc__ = original.__doc__
    setattr(owner, name, wrapper)


def _instrument() -> None:
    import cashkit.sdk as sdk
    import cashkit.sdk.construction as construction
    from cashkit.sdk.kit import CashKit, ReadOnlyKit, RunRef

    for name in KIT_METHODS:
        for owner in (CashKit, ReadOnlyKit):
            if name in owner.__dict__:
                _wrap(owner, name, name)
    for name in RUN_METHODS:
        if name in RunRef.__dict__:
            _wrap(RunRef, name, name)

    original = construction.create_book

    def create_book(*args, **kwargs):
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            if RECORDING["on"]:
                CALLS.append({"name": "create_book", "write": True, "scenario": None,
                              "raised": f"{type(exc).__name__}: {exc}"[:300]})
            raise
        if RECORDING["on"]:
            CALLS.append({"name": "create_book", "write": True, "scenario": None,
                          "diagnostics": _codes(result)})
        return result

    create_book.__doc__ = original.__doc__
    create_book.__wrapped__ = original
    construction.create_book = create_book
    sdk.create_book = create_book


# --------------------------------------------------------------------------- #
# Observation
# --------------------------------------------------------------------------- #


def _observe_scenario(kit, scenario_id: str) -> dict:
    from cashkit.sdk.views import balance_series

    resolution = kit.resolve(scenario_id)
    book = resolution.book
    out: dict = {
        "params": {k: str(v) for k, v in sorted(book.params.items())},
        "opening_balance": str(book.opening_balance),
        "items": {iid: _jsonable(item.model_dump()) for iid, item in sorted(book.items.items())},
        "tax_regimes": [_jsonable(r.model_dump()) for r in book.tax_regimes],
    }
    events, event_diagnostics = kit.events_for(scenario_id)
    out["events"] = [_jsonable(e.model_dump()) for e in events]
    run = kit.run(scenario_id)
    result = run.result
    out["diagnostics"] = [
        {"severity": d.severity, "code": d.code, "item_id": d.item_id, "message": d.message}
        for d in run.diagnostics
    ]
    out["periods"] = [d.isoformat() for d in result.periods.starts]
    cash: dict[str, list[str]] = {}
    accrual: dict[str, list[str]] = {}
    synthetic = {"tax": [0] * len(out["periods"]), "events": [0] * len(out["periods"])}
    for item_id in sorted(result.cash):
        column = [int(v) for v in result.cash[item_id]]
        if item_id.startswith("_tax:"):
            if item_id.endswith(":liability"):
                synthetic["tax"] = [a + b for a, b in zip(synthetic["tax"], column)]
            continue
        if item_id.startswith("_event:"):
            synthetic["events"] = [a + b for a, b in zip(synthetic["events"], column)]
            continue
        if item_id.startswith("_"):
            continue
        cash[item_id] = [_money(v) for v in column]
    for item_id in sorted(result.accrual):
        if item_id.startswith("_"):
            continue
        accrual[item_id] = [_money(int(v)) for v in result.accrual[item_id]]
    out["cash"] = cash
    out["accrual"] = accrual
    out["synthetic_cash"] = {k: [_money(v) for v in col] for k, col in synthetic.items()}
    try:
        series, _source = balance_series(result, book)
        out["balance"] = [_money(int(v)) for v in series]
    except Exception as exc:  # pragma: no cover - surfaced, not hidden
        out["balance"] = None
        out["balance_error"] = repr(exc)
    try:
        summary = run.summary()
        out["summary"] = _jsonable(summary.model_dump(exclude={"diagnostics"}))
    except Exception as exc:
        out["summary"] = None
        out["summary_error"] = repr(exc)
    return out


def _observe(root: Path) -> dict:
    from cashkit.sdk import CashKit

    kit, problems = CashKit.open(root)
    if kit is None:
        return {"book": None, "open_diagnostics": [d.code for d in problems]}
    book = kit.book
    obs: dict = {
        "root": str(root),
        "book": {
            "id": book.id,
            "grain": book.base_grain.value,
            "horizon_start": book.horizon.start.isoformat(),
            "horizon_end": book.horizon.end.isoformat(),
            "opening_balance": str(book.opening_balance),
            "cutover": book.cutover.isoformat(),
            "calendar": _jsonable(book.calendar.model_dump(exclude={"holidays"})),
        },
        "scenarios": {},
        "parents": {},
    }
    for scenario_id, scenario in sorted(kit.scenarios.items()):
        obs["parents"][scenario_id] = scenario.parent
        try:
            obs["scenarios"][scenario_id] = _observe_scenario(kit, scenario_id)
        except Exception:
            obs["scenarios"][scenario_id] = {"error": traceback.format_exc(limit=4)[-2000:]}
    try:
        obs["history"] = [{"message": r.message, "author": r.author} for r in kit.history(limit=20)]
    except Exception as exc:
        obs["history"] = []
        obs["history_error"] = repr(exc)
    try:
        obs["clean"] = bool(kit.status().clean)
    except Exception:
        obs["clean"] = None
    return obs


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _exec(code: str, namespace: dict, label: str) -> tuple[str, str | None]:
    """Run ``code``; return ``(captured stdout, formatted exception or None)``."""
    buffer = io.StringIO()
    try:
        compiled = compile(code, label, "exec")
    except SyntaxError:
        return "", traceback.format_exc(limit=0)
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compiled, namespace)
    except SystemExit as exc:
        if exc.code not in (0, None):
            return buffer.getvalue(), f"SystemExit({exc.code!r})"
    except BaseException:
        lines = traceback.format_exc().splitlines()
        # Keep the frames inside the candidate script and the final error.
        return buffer.getvalue(), "\n".join(lines[-25:])
    return buffer.getvalue(), None


def main() -> None:
    job = json.loads(sys.stdin.read())
    root = Path(job["root"])
    _instrument()
    from cashkit.sdk import CashKit

    result: dict = {"status": "ok", "calls": CALLS, "stdout": "", "runs": 1}

    base_namespace = {"__name__": "__main__", "ROOT": str(root), "kit": None}
    if job.get("setup"):
        namespace = dict(base_namespace)
        _out, error = _exec(job["setup"], namespace, "<setup>")
        if error is not None:
            result.update(status="setup_error", exception=error)
            print(MARKER + json.dumps(result))
            return

    runs = 2 if job.get("run_twice") else 1
    namespace: dict = {}
    for run_index in range(runs):
        namespace = dict(base_namespace)
        if (root / ".cashkit").exists():
            kit, _ = CashKit.open(root)
            namespace["kit"] = kit
        namespace["answer"] = None
        RECORDING["on"] = True
        stdout, error = _exec(job["code"], namespace, "<candidate>")
        RECORDING["on"] = False
        result["stdout"] = (result["stdout"] + stdout)[-4000:]
        result["runs"] = run_index + 1
        if error is not None:
            kind = "syntax_error" if error.startswith("SyntaxError") or "SyntaxError:" in error.splitlines()[-1] else "exception"
            result.update(status=kind, exception=error, failed_run=run_index + 1)
            break

    result["answer"] = _answer(namespace.get("answer"))

    # Where is the book? The candidate's kit if it holds one, else ROOT.
    observed_root = root
    candidate_kit = namespace.get("kit")
    if isinstance(candidate_kit, CashKit):
        observed_root = Path(candidate_kit.root)
    elif not (root / ".cashkit").exists():
        observed_root = None  # type: ignore[assignment]

    if observed_root is None:
        result["observation"] = {"book": None}
    else:
        try:
            result["observation"] = _observe(observed_root)
        except Exception:
            result["observation"] = {"book": None, "observe_error": traceback.format_exc(limit=6)[-3000:]}
    print(MARKER + json.dumps(result, default=str))


if __name__ == "__main__":
    main()
