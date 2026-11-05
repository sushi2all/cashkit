"""Graders: one function per check kind, run against an observation.

A grader takes the check's ``args`` and the observation the runtime produced
and returns ``(passed, detail)``. ``detail`` says what was observed when the
check fails — the report is read by someone deciding whether a model, a prompt
or the SDK is at fault, so "expected 1220.0000, got 1000.0000 in 2026-03-01"
beats "failed".

Money compares as exact ``Decimal`` (``1220`` equals ``1220.0000``): the
engine is exact, so the eval is too. There is no tolerance argument, on
purpose — a forecast that is off by a cent is wrong, and the PRD calls silent
numerical error the worst failure mode.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .schema import Check, CheckResult
from . import static

Observation = dict[str, Any]
Grader = Callable[[dict[str, Any], Observation, str], tuple[bool, str]]

GRADERS: dict[str, Grader] = {}


def grader(kind: str):
    def register(fn: Grader) -> Grader:
        GRADERS[kind] = fn
        return fn

    return register


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def same(a: Any, b: Any) -> bool:
    """Exact equality; numbers compare as Decimals, everything else as text."""
    da, db = _dec(a), _dec(b)
    if da is not None and db is not None:
        return da == db
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return str(a) == str(b)


def _scenario(obs: Observation, scenario: str) -> dict[str, Any] | None:
    book = obs.get("observation") or {}
    scenarios = book.get("scenarios") or {}
    data = scenarios.get(scenario)
    if data is None or "error" in data:
        return None
    return data


def _need(obs: Observation, scenario: str) -> tuple[dict[str, Any] | None, str]:
    data = _scenario(obs, scenario)
    if data is None:
        book = (obs.get("observation") or {}).get("scenarios")
        if book is None:
            return None, "no book was found to grade"
        return None, f"scenario {scenario!r} does not exist (have: {sorted(book)})"
    return data, ""


def _column(data: dict[str, Any], item: str, measure: str) -> list[str] | None:
    return (data.get(measure) or {}).get(item)


def _at(data: dict[str, Any], column: list[str], period: str) -> str | None:
    periods = data.get("periods") or []
    if period not in periods:
        return None
    return column[periods.index(period)]


def _get_path(value: Any, path: str) -> tuple[bool, Any]:
    for part in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return False, None
        elif isinstance(value, dict):
            if part not in value:
                return False, None
            value = value[part]
        else:
            return False, None
    return True, value


def _tags_match(item: dict[str, Any], tags: dict[str, str]) -> bool:
    item_tags = item.get("tags") or {}
    return all(item_tags.get(k) == v for k, v in tags.items())


# --------------------------------------------------------------------------- #
# State graders
# --------------------------------------------------------------------------- #


@grader("no_errors")
def _no_errors(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    errors = [d for d in data.get("diagnostics", []) if d["severity"] == "error"]
    if errors:
        return False, "; ".join(f"{d['code']} {d.get('item_id') or ''}: {d['message'][:120]}" for d in errors[:4])
    return True, ""


@grader("no_code")
def _no_code(args, obs, code):
    """The named diagnostic code is absent from the run (e.g. a warning)."""
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    present = [d for d in data.get("diagnostics", []) if d["code"] == args["code"]]
    return (not present), (f"{args['code']} present: {present[0]['message'][:160]}" if present else "")


@grader("closing")
def _closing(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    summary = data.get("summary") or {}
    got = summary.get("closing_balance")
    return same(got, args["value"]), f"closing balance {got}, expected {args['value']}"


@grader("balance_at")
def _balance_at(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    balance = data.get("balance")
    if balance is None:
        return False, "no balance series"
    got = _at(data, balance, args["period"])
    return same(got, args["value"]), f"balance at {args['period']}: {got}, expected {args['value']}"


@grader("series")
def _series(args, obs, code):
    """Item column equals the expected figures at every listed period."""
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    measure = args.get("measure", "cash")
    column = _column(data, args["item"], measure)
    if column is None:
        return False, f"item {args['item']!r} has no {measure} column (items: {sorted(data.get('items', {}))})"
    wrong = []
    for period, expected in args["values"].items():
        got = _at(data, column, period)
        if not same(got, expected):
            wrong.append(f"{period}: {got} ≠ {expected}")
    return (not wrong), "; ".join(wrong[:4]) + (" …" if len(wrong) > 4 else "")


@grader("item_total")
def _item_total(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    measure = args.get("measure", "cash")
    column = _column(data, args["item"], measure)
    if column is None:
        return False, f"item {args['item']!r} has no {measure} column (items: {sorted(data.get('items', {}))})"
    got = sum((Decimal(v) for v in column), Decimal(0))
    return same(got, args["value"]), f"{measure} total {got}, expected {args['value']}"


@grader("tag_total")
def _tag_total(args, obs, code):
    """Sum of a measure over every item whose tags include ``tags`` — id-agnostic."""
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    measure = args.get("measure", "cash")
    period = args.get("period")
    got, matched = Decimal(0), []
    for item_id, item in (data.get("items") or {}).items():
        if not _tags_match(item, args["tags"]):
            continue
        column = _column(data, item_id, measure) or []
        matched.append(item_id)
        if period is None:
            got += sum((Decimal(v) for v in column), Decimal(0))
        else:
            value = _at(data, column, period)
            got += Decimal(value) if value is not None else Decimal(0)
    if not matched:
        return False, f"no item carries tags {args['tags']}"
    return same(got, args["value"]), f"{measure} total over {matched} = {got}, expected {args['value']}"


@grader("synthetic_total")
def _synthetic_total(args, obs, code):
    """Total of the engine's synthetic columns: ``tax`` liabilities or ``events``."""
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    column = (data.get("synthetic_cash") or {}).get(args["kind"]) or []
    got = sum((Decimal(v) for v in column), Decimal(0))
    return same(got, args["value"]), f"{args['kind']} cash total {got}, expected {args['value']}"


@grader("synthetic_series")
def _synthetic_series(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    column = (data.get("synthetic_cash") or {}).get(args["kind"]) or []
    wrong = []
    for period, expected in args["values"].items():
        got = _at(data, column, period) if column else None
        if not same(got, expected):
            wrong.append(f"{period}: {got} ≠ {expected}")
    return (not wrong), "; ".join(wrong[:4])


@grader("item_exists")
def _item_exists(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    present = args["item"] in (data.get("items") or {})
    expected = args.get("exists", True)
    return present == expected, f"item {args['item']!r} {'present' if present else 'absent'}"


@grader("item_field")
def _item_field(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    item = (data.get("items") or {}).get(args["item"])
    if item is None:
        return False, f"item {args['item']!r} does not exist (items: {sorted(data.get('items', {}))})"
    found, value = _get_path(item, args["path"])
    op = args.get("op", "eq")
    expected = args.get("value")
    if op == "absent":
        return (not found or value in (None, [], {})), f"{args['path']} = {value!r}"
    if not found:
        return False, f"{args['item']}.{args['path']} is missing"
    if op == "eq":
        return same(value, expected), f"{args['item']}.{args['path']} = {value!r}, expected {expected!r}"
    if op == "contains":
        if isinstance(value, str):
            ok = str(expected) in value
        else:
            ok = any(same(v, expected) for v in (value or []))
        return ok, f"{args['item']}.{args['path']} = {value!r} does not contain {expected!r}"
    if op == "len":
        return len(value or []) == expected, f"len({args['item']}.{args['path']}) = {len(value or [])}, expected {expected}"
    if op == "in":
        return any(same(value, e) for e in expected), f"{args['item']}.{args['path']} = {value!r}, expected one of {expected!r}"
    raise ValueError(f"unknown op {op!r}")


@grader("param")
def _param(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    params = data.get("params") or {}
    expected = args.get("value")
    if expected is None:
        return args["key"] not in params, f"param {args['key']!r} = {params.get(args['key'])}"
    got = params.get(args["key"])
    return same(got, expected), f"param {args['key']!r} = {got}, expected {expected}"


@grader("opening")
def _opening(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    got = data.get("opening_balance")
    return same(got, args["value"]), f"opening balance {got}, expected {args['value']}"


@grader("scenario")
def _scenario_check(args, obs, code):
    parents = (obs.get("observation") or {}).get("parents") or {}
    if args["id"] not in parents:
        return False, f"scenario {args['id']!r} does not exist (have: {sorted(parents)})"
    if "parent" in args and parents[args["id"]] != args["parent"]:
        return False, f"scenario {args['id']!r} has parent {parents[args['id']]!r}, expected {args['parent']!r}"
    return True, ""


@grader("book_field")
def _book_field(args, obs, code):
    book = (obs.get("observation") or {}).get("book")
    if not book:
        return False, "no book was found to grade"
    found, value = _get_path(book, args["field"])
    return found and same(value, args["value"]), f"book.{args['field']} = {value!r}, expected {args['value']!r}"


@grader("events")
def _events(args, obs, code):
    """Count events whose fields match ``match`` (money compared exactly)."""
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    match = args.get("match", {})
    hits = [
        e for e in data.get("events", [])
        if all(same(_get_path(e, k)[1], v) for k, v in match.items())
    ]
    expected = args.get("count", 1)
    return len(hits) == expected, f"{len(hits)} event(s) match {match}, expected {expected}"


@grader("events_total")
def _events_total(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    status = args.get("status")
    total = sum(
        (Decimal(e["amount"]) for e in data.get("events", []) if status is None or e["status"] == status),
        Decimal(0),
    )
    return same(total, args["value"]), f"sum of {status or 'all'} event amounts {total}, expected {args['value']}"


@grader("tax_regime")
def _tax_regime(args, obs, code):
    data, why = _need(obs, args.get("scenario", "base"))
    if data is None:
        return False, why
    regimes = data.get("tax_regimes") or []
    match = args.get("match", {})
    hits = [r for r in regimes if all(same(r.get(k), v) for k, v in match.items())]
    return bool(hits), f"no tax regime matches {match} (have: {regimes})"


@grader("answer")
def _answer(args, obs, code):
    answer = obs.get("answer") or {"type": "none", "value": None}
    expected, kind = args["value"], args.get("type", "decimal")
    got = answer.get("value")
    if kind == "decimal":
        if answer.get("type") == "float":
            return False, f"answer is a float ({got}); money must be a Decimal"
        if answer.get("type") != "decimal":
            return False, f"answer is {answer.get('type')} {got!r}, expected a Decimal {expected}"
        if args.get("abs"):
            return abs(Decimal(got)) == abs(Decimal(expected)), f"answer {got}, expected ±{expected}"
        return same(got, expected), f"answer {got}, expected {expected}"
    if kind == "date":
        if answer.get("type") != "date":
            return False, f"answer is {answer.get('type')} {got!r}, expected a date {expected}"
        return got == expected, f"answer {got}, expected {expected}"
    if kind == "str":
        return str(got).strip().lower() == str(expected).strip().lower(), f"answer {got!r}, expected {expected!r}"
    if kind == "bool":
        return got is expected, f"answer {got!r}, expected {expected!r}"
    if kind == "none":
        return got is None, f"answer {got!r}, expected None"
    raise ValueError(f"unknown answer type {kind!r}")


@grader("committed")
def _committed(args, obs, code):
    history = (obs.get("observation") or {}).get("history") or []
    message = args.get("message")
    if not history:
        return False, "no revision was committed"
    if message is not None and not any(message.lower() in (r.get("message") or "").lower() for r in history):
        return False, f"no revision message contains {message!r} (have: {[r['message'] for r in history]})"
    return True, ""


@grader("clean")
def _clean(args, obs, code):
    clean = (obs.get("observation") or {}).get("clean")
    return clean is True, f"working tree clean = {clean}"


# --------------------------------------------------------------------------- #
# Behaviour graders (the call log) and hygiene (the code)
# --------------------------------------------------------------------------- #


@grader("not_committed")
def _not_committed(args, obs, code):
    history = (obs.get("observation") or {}).get("history") or []
    commits = [c for c in obs.get("calls", []) if c["name"] == "commit"]
    ok = not history and not commits
    return ok, "committed without being asked" if not ok else ""


@grader("no_writes")
def _no_writes(args, obs, code):
    writes = sorted({c["name"] for c in obs.get("calls", []) if c.get("write")})
    return (not writes), f"a read-only question made writes: {writes}"


@grader("called")
def _called(args, obs, code):
    names = {c["name"] for c in obs.get("calls", [])}
    return args["name"] in names, f"{args['name']}() was never called"


@grader("not_called")
def _not_called(args, obs, code):
    names = {c["name"] for c in obs.get("calls", [])}
    return args["name"] not in names, f"{args['name']}() was called"


@grader("static")
def _static(args, obs, code):
    findings = static.scan(code or "", args["rule"])
    return (not findings), "; ".join(findings[:3])


# --------------------------------------------------------------------------- #
# Grading a whole observation
# --------------------------------------------------------------------------- #

#: Hygiene checks every task gets unless it states its own of the same kind.
DEFAULT_HYGIENE: tuple[Check, ...] = (
    Check("static", {"rule": "float"}, "no float literals or float() in money code", critical=False),
    Check("static", {"rule": "store_access"}, "no direct access to store files (PRD §9.3 rule 1)", critical=False),
    Check("static", {"rule": "git"}, "no git commands (PRD §9.3 rule 2)", critical=False),
    Check("static", {"rule": "wall_clock"}, "no today()/now() — dates are authored (PRD §9.3 rule 4)", critical=False),
    Check("not_committed", {}, "no commit unless asked (PRD §6.7)", critical=False),
)


def checks_for(task_checks: tuple[Check, ...], *, read_only: bool) -> tuple[Check, ...]:
    kinds = {c.kind for c in task_checks}
    extra = [c for c in DEFAULT_HYGIENE if c.kind not in kinds or c.kind == "static"]
    if "committed" in kinds:
        extra = [c for c in extra if c.kind != "not_committed"]
    if read_only and "no_writes" not in kinds:
        extra.append(Check("no_writes", {}, "a question changes nothing", critical=False))
    return tuple(task_checks) + tuple(extra)


def grade(checks: tuple[Check, ...], obs: Observation, code: str | None) -> list[CheckResult]:
    results = []
    for check in checks:
        fn = GRADERS[check.kind]
        try:
            passed, detail = fn(check.args, obs, code or "")
        except Exception as exc:  # a grader bug must be visible, not a pass
            passed, detail = False, f"grader error: {type(exc).__name__}: {exc}"
        results.append(CheckResult(check.kind, check.label, check.critical, bool(passed), "" if passed else detail))
    return results


def classify(obs: Observation | None, code: str | None, results: list[CheckResult]) -> str | None:
    """The single failure class of a sample, or ``None`` when it passed."""
    if code is None:
        return "no_code"
    if obs is None:
        return "exception"
    status = obs.get("status")
    if status == "syntax_error":
        return "syntax_error"
    if status == "timeout":
        return "timeout"
    if status in ("exception", "crash", "setup_error"):
        return "exception"
    failed = [r for r in results if r.critical and not r.passed]
    if not failed:
        return None
    if not (obs.get("observation") or {}).get("book"):
        return "no_book"
    kinds = {r.kind for r in failed}
    if "no_errors" in kinds:
        return "error_diagnostics"
    if "answer" in kinds:
        return "wrong_answer"
    if kinds & {"item_exists", "item_field", "param", "scenario", "book_field", "tax_regime", "events", "committed", "clean", "called", "opening"}:
        return "wrong_structure"
    return "wrong_numbers"
