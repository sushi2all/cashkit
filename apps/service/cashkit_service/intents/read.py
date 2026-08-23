"""The twelve read intents, executed deterministically.

ADR-0019 rule 1: **every reportable question is one call.** The model emits one
intent and quotes the number the engine produced; it never composes an analysis
and never derives a figure. That only works if the host can answer each of the
twelve intents on its own, which is what this module does — with no model call
anywhere in it.

Two of the twelve have no single-call SDK verb yet. R5 ``top_categories`` and
R6 ``item_total`` are marked ``[SDK gap]`` in the intent schema and are
**host-composed** for the MLP (SPEC §14); the model still emits one intent, and
the composition never reaches it. The SDK review owns the real verbs.

S1 builds and tests this. S2 wires the turn pipeline to it (`POST /turns` and
the Q&A read loop are S2's scope, not S1's).
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from decimal import Decimal
from typing import Any

from cashkit.sdk import CashKit

from ..books import scratch_copy
from ..money import Money, from_minor_units, money, money_or_none
from ..serialize import (
    diagnostics_out, explanation_out, period_starts, revisions_out, summary_out, trace_out,
)


class UnknownIntent(ValueError):
    """The intent is not one of the twelve. A programmer error, not a diagnostic."""


def execute(
    kit: CashKit,
    intent: dict[str, Any],
    *,
    scenario: str,
    as_of: _dt.date,
) -> dict[str, Any]:
    """Answer one read intent against ``kit``.

    Returns a plain payload whose money is already canonically serialized, so a
    caller can quote it without touching a Decimal.
    """
    name = intent.get("intent") or intent.get("op")
    target = intent.get("scenario") or scenario
    handler = _HANDLERS.get(name)
    if handler is None:
        raise UnknownIntent(f"{name!r} is not a read intent")
    return handler(kit, intent, target, as_of)


# --- R1–R4: figures straight off summary() -------------------------------- #


def _project_balance(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    """R1 — ``project_balance``, optionally with a hypothetical delta.

    The delta is applied on a throwaway copy of the book, never on the book:
    a question must not change anything (ADR-0029), and the answer is stamped
    as a what-if because it is one.

    The payload carries the **closing balance for every month**, before and
    after the delta, not only the horizon summary. "Can I afford this in
    September" is answered by September's figure, and if the caller were given
    only the summary it would have to work September out for itself. The engine
    computes; the caller quotes (D-MLP-26).
    """
    from ..ops.applier import apply_op

    baseline = _closing_by_month(kit.run(scenario))
    delta = intent.get("delta")
    if delta is None:
        run = kit.run(scenario)
        return {
            "summary": summary_out(run).model_dump(mode="json"),
            "closing_by_month": baseline,
            "hypothetical": False,
        }

    operation = {
        "op": "add_event",
        "date": intent.get("delta_date") or as_of.isoformat(),
        "amount": str(delta),
        "note": "hypothetical",
    }
    with scratch_copy(kit, kit.root) as scratch:
        result = apply_op(scratch, operation, scenario=scenario, as_of=as_of, seq=0)
        run = scratch.run(scenario)
        payload = {
            "summary": summary_out(run).model_dump(mode="json"),
            "closing_by_month_before": baseline,
            "closing_by_month": _closing_by_month(run),
            "hypothetical": True,
            "diagnostics": [d.model_dump() for d in diagnostics_out(result.diagnostics)],
        }
    return payload


def _closing_by_month(run) -> dict[str, dict]:
    """The closing balance per month, canonically serialized."""
    from ..serialize import closing_series

    return {
        period.isoformat()[:7]: money(value).model_dump()
        for period, value in zip(period_starts(run), closing_series(run), strict=True)
    }


def _summary_field(field: str):
    def handler(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
        summary = kit.run(scenario).summary()
        value = getattr(summary, field)
        if isinstance(value, Decimal):
            return {field: money(value).model_dump()}
        return {field: value.isoformat() if isinstance(value, _dt.date) else value}

    return handler


def _runway(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    """R2 — ``runway``. ``None`` means "not inside the horizon", not "zero"."""
    summary = kit.run(scenario).summary()
    return {
        "runway_periods": summary.runway_periods,
        "runway_end": summary.runway_end.isoformat() if summary.runway_end else None,
    }


def _min_cash(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    """R3 — ``min_cash``, optionally bounded by a horizon date."""
    run = kit.run(scenario)
    summary = run.summary()
    horizon = intent.get("horizon")
    if horizon is None:
        return {
            "min_cash": money(summary.min_cash).model_dump(),
            "min_cash_period": summary.min_cash_period.isoformat() if summary.min_cash_period else None,
        }
    from ..serialize import closing_series

    until = _dt.date.fromisoformat(str(horizon))
    pairs = [
        (period, value)
        for period, value in zip(period_starts(run), closing_series(run), strict=True)
        if period <= until
    ]
    if not pairs:
        return {"min_cash": None, "min_cash_period": None}
    period, value = min(pairs, key=lambda pair: pair[1])
    return {"min_cash": money(value).model_dump(), "min_cash_period": period.isoformat()}


# --- R5/R6: host-composed (SPEC §14, [SDK gap]) --------------------------- #


def _top_categories(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    """R5 — ``top_categories``. Host-composed from the run's own columns.

    The composition is a grouped sum of engine figures, done in exact minor
    units and serialized once. It is not an estimate and it is not the model's
    arithmetic — that is the whole point of composing it here.
    """
    direction = intent.get("direction", "out")
    limit = int(intent.get("n", 5))
    since, until = _window(intent)

    run = kit.run(scenario)
    starts = period_starts(run)
    totals: dict[str, int] = defaultdict(int)
    for item in run.book.items.values():
        column = run.result.cash.get(item.id)
        if column is None or item.kind == "stock":
            continue
        key = item.tags.get("cat") or "(untagged)"
        for period, raw in zip(starts, column, strict=True):
            if since is not None and period < since:
                continue
            if until is not None and period > until:
                continue
            value = int(raw)
            if (direction == "in" and value > 0) or (direction == "out" and value < 0):
                totals[key] += value

    ranked = sorted(totals.items(), key=lambda kv: abs(kv[1]), reverse=True)[:limit]
    return {
        "direction": direction,
        "categories": [
            {"category": key, "total": money(from_minor_units(value)).model_dump()}
            for key, value in ranked
        ],
    }


def _item_total(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    """R6 — ``item_total``. Host-composed from ``frame()``'s own columns."""
    measure = intent.get("measure", "cash")
    selector = intent.get("item")
    since, until = _window(intent)

    run = kit.run(scenario)
    starts = period_starts(run)
    matched = _match_items(run, selector)
    total = 0
    columns = run.result.cash if measure == "cash" else run.result.accrual
    for item_id in matched:
        column = columns.get(item_id)
        if column is None:
            continue
        for period, raw in zip(starts, column, strict=True):
            if since is not None and period < since:
                continue
            if until is not None and period > until:
                continue
            total += int(raw)
    return {
        "items": sorted(matched),
        "measure": measure,
        "total": money(from_minor_units(total)).model_dump(),
    }


def _match_items(run, selector: str | None) -> list[str]:
    """An item id, or the enumerated ``key:value`` tag subset (schema note Q3)."""
    if not selector:
        return list(run.book.items)
    if selector in run.book.items:
        return [selector]
    if ":" in selector:
        key, _, value = selector.partition(":")
        return [i.id for i in run.book.items.values() if i.tags.get(key) == value]
    return []


def _window(intent: dict) -> tuple[_dt.date | None, _dt.date | None]:
    period = intent.get("period") or {}
    if isinstance(period, str) and period:
        return _dt.date.fromisoformat(period), _dt.date.fromisoformat(period)
    since = period.get("since") or intent.get("since")
    until = period.get("until") or intent.get("until")
    return (
        _dt.date.fromisoformat(str(since)) if since else None,
        _dt.date.fromisoformat(str(until)) if until else None,
    )


# --- R7–R12 --------------------------------------------------------------- #


def _explain_cell(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    trace = kit.run(scenario).trace(
        intent["item"], _dt.date.fromisoformat(str(intent["period"])),
        measure=intent.get("measure", "accrual"),
    )
    return {"trace": trace_out(trace).model_dump(mode="json")}


def _explain_zero(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    explanation = kit.run(scenario).why_zero(
        intent["item"], _dt.date.fromisoformat(str(intent["period"])),
        measure=intent.get("measure", "cash"),
    )
    return {"explanation": explanation_out(explanation).model_dump(mode="json")}


def _compare_scenarios(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    ids = list(intent.get("scenarios") or [])
    runs = [kit.run(s) for s in ids]
    table = kit.compare(runs, metric=intent.get("metric", "cash"))
    keys = [c for c in table.columns if c != "period_start"]
    periods: list[dict[str, Any]] = []
    for row in table.to_dicts():
        values: dict[str, dict | None] = {}
        for scenario_id, key in zip(ids, keys, strict=False):
            serialized: Money | None = money_or_none(row.get(key))
            values[scenario_id] = None if serialized is None else serialized.model_dump()
        # SPEC §5-F4 wants a delta column beside the two columns. Computing it
        # here keeps the subtraction exact and keeps it out of the caller: a
        # difference worked out downstream is a derived number, and derived
        # numbers are the thing this product exists to avoid.
        if len(ids) == 2:
            left, right = (values[i] for i in ids)
            values["delta"] = (
                None
                if left is None or right is None
                else money(
                    Decimal(right["exact"]) - Decimal(left["exact"])
                ).model_dump()
            )
        periods.append({"period_start": row["period_start"].isoformat(), "values": values})
    return {"scenarios": ids, "periods": periods,
            "diagnostics": [d.model_dump() for d in diagnostics_out(table.diagnostics)]}


def _coverage(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    """R10 — ``validate()`` diagnostics, rendered verbatim.

    Model-consistency diagnostics only (ADR-0021); the engine has no
    domain-coverage checks and the consumer MLP defers that duty (D-MLP-02).
    R10 renders; it does not judge.
    """
    return {"diagnostics": [d.model_dump() for d in diagnostics_out(kit.validate(scenario))]}


def _list_items(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    book = kit.scenarios.resolve(scenario)
    tag = intent.get("tag")
    items = [
        {"id": item.id, "name": item.name, "kind": item.kind,
         "direction": item.direction, "tags": dict(item.tags), "formula": item.formula}
        for item in book.items.values()
        if not tag or item.id in _match_items(kit.run(scenario), tag)
    ]
    return {"items": sorted(items, key=lambda i: i["id"])}


def _history(kit: CashKit, intent: dict, scenario: str, as_of: _dt.date) -> dict:
    limit = int(intent.get("n", 10))
    return {"revisions": [r.model_dump() for r in revisions_out(kit.history(limit=limit))]}


_HANDLERS = {
    "project_balance": _project_balance,   # R1
    "runway": _runway,                     # R2
    "min_cash": _min_cash,                 # R3
    "breakeven": _summary_field("breakeven_period"),  # R4
    "top_categories": _top_categories,     # R5  [SDK gap] host-composed
    "item_total": _item_total,             # R6  [SDK gap] host-composed
    "explain_cell": _explain_cell,         # R7
    "explain_zero": _explain_zero,         # R8
    "compare_scenarios": _compare_scenarios,  # R9
    "coverage": _coverage,                 # R10
    "list_items": _list_items,             # R11
    "history": _history,                   # R12
}

#: The twelve, in schema order. S2 builds the model's tool surface from this.
READ_INTENTS = tuple(_HANDLERS)

__all__ = ["READ_INTENTS", "UnknownIntent", "execute"]
