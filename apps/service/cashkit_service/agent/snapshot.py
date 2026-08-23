"""The compact book state handed to the model, results block included.

This is the fix proto trial T11 forced. Before it, the state given to the model
carried the book's *structure* and no computed figure at all, so a numeric
question left the model doing arithmetic over segments — and it answered "yes,
you can afford 2500" against a balance of 1900, confidently and with no
diagnostic. The cure was to hand it the engine's own results and forbid
recomputation.

So every snapshot carries, per scenario: the closing balance for each month,
the minimum cash and the month it falls in, the closing balance at the horizon,
the runway, and the year total per item. Every one of those is an engine number
through the canonical serializer (:mod:`cashkit_service.money`). The model's
job is to quote them.

Money appears here as the 2dp ``display`` string. The model is a reader, not a
calculator: it needs the figure the user will see, and shipping it 4dp would
invite it to present a number the interface never shows. Any figure that needs
full precision travels in the payload, not the prompt (D-MLP-06).

Nothing in this module calls a model, and it must be built while the book lock
is held — the model call that consumes it happens after the lock is released
(SPEC §2.2).
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any

from cashkit.sdk import CashKit, balance_series

from ..money import display_str, from_minor_units
from ..serialize import period_starts

#: Enough one-off events for a turn to reason about without pasting a ledger.
EVENT_LIMIT = 40


def build(kit: CashKit, *, scenario: str, as_of: _dt.date) -> dict[str, Any]:
    """The whole snapshot: what the book is, and what it computes."""
    book = kit.scenarios.resolve(scenario)
    scenarios = sorted(kit.scenarios.scenarios) or ["base"]
    return {
        "as_of": as_of.isoformat(),
        "active_scenario": scenario,
        "book": {
            "horizon_start": book.horizon.start.isoformat(),
            "horizon_end_exclusive": book.horizon.end.isoformat(),
            "grain": book.base_grain.value,
            "currency": "EUR",
            "opening_balance": display_str(book.opening_balance),
            "cutover": book.cutover.isoformat() if book.cutover else None,
            "params": {k: str(v) for k, v in book.params.items()},
            "scenarios": scenarios,
        },
        "items": [_item(item) for item in book.items.values()],
        "events": _events(kit),
        "results": {sid: _results(kit, sid) for sid in scenarios},
    }


def _item(item: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "kind": item.kind,
        "direction": item.direction,
    }
    if item.tags:
        payload["tags"] = dict(item.tags)
    segments = []
    for segment in item.segments:
        entry: dict[str, Any] = {
            "start": segment.start.isoformat(),
            "end_exclusive": segment.end.isoformat() if segment.end else None,
            "recurrence": f"{segment.recurrence.every}{segment.recurrence.unit.value[0]}",
        }
        if segment.amount.constant is not None:
            entry["amount"] = display_str(segment.amount.constant)
        if segment.amount.schedule:
            entry["schedule"] = [
                [when.isoformat(), display_str(value)] for when, value in segment.amount.schedule
            ]
        if segment.escalation is not None:
            entry["escalation"] = {
                "rate": str(segment.escalation.rate),
                "every_years": segment.escalation.every_years,
            }
        segments.append(entry)
    if segments:
        payload["segments"] = segments
    return payload


def _events(kit: CashKit) -> list[dict[str, Any]]:
    """The ledger's own rows — the one-offs and the recorded actuals.

    ``status`` is shown because the model must be able to *see* what is already
    recorded; it still never chooses one (SPEC §5-F5, the discriminator).
    """
    try:
        rows = kit.query_events().to_dicts()
    except Exception:  # noqa: BLE001 — a ledger problem must not kill the turn
        return []
    return [
        {
            "id": row["id"],
            "date": row["date"].isoformat(),
            "amount": display_str(row["amount"]),
            "status": row["status"],
            "item": row["item"],
            "note": row["note"],
        }
        for row in rows[:EVENT_LIMIT]
    ]


def _results(kit: CashKit, scenario: str) -> dict[str, Any]:
    """Engine output, per scenario. Quote these; never recompute them."""
    try:
        run = kit.run(scenario)
        series, _source = balance_series(run.result, run.book)
        summary = run.summary()
        starts = period_starts(run)
        closing = [from_minor_units(int(v)) for v in series]
        return {
            "closing_by_month": {
                start.isoformat()[:7]: display_str(value)
                for start, value in zip(starts, closing, strict=True)
            },
            "min_cash": display_str(summary.min_cash),
            "min_cash_month": (
                summary.min_cash_period.isoformat()[:7] if summary.min_cash_period else None
            ),
            "closing_balance": display_str(summary.closing_balance),
            "total_inflow": display_str(summary.total_inflow),
            "total_outflow": display_str(summary.total_outflow),
            "runway_end": summary.runway_end.isoformat() if summary.runway_end else None,
            "item_totals": _item_totals(run),
        }
    except Exception as exc:  # noqa: BLE001 — a broken run must not kill the turn
        # Silence would be worse than the error: the model must know the
        # figures are missing rather than assume the book is empty.
        return {"unavailable": f"{type(exc).__name__}: {exc}"[:300]}


def _item_totals(run: Any) -> dict[str, str]:
    totals: dict[str, str] = {}
    for item in run.book.items.values():
        column = run.result.cash.get(item.id)
        if column is None:
            continue
        total = sum(int(v) for v in column)
        totals[item.id] = display_str(from_minor_units(total))
    return totals


def compact(payload: dict[str, Any]) -> str:
    """Serialize the snapshot the way the prompt carries it."""
    import json

    return json.dumps(payload, separators=(",", ":"), default=_default)


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return str(value)


__all__ = ["EVENT_LIMIT", "build", "compact"]
