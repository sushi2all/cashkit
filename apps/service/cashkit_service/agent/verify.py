"""The bounded verification call (SPEC §2.3 step 4, ADR-0030 stage 2).

ADR-0030 stage 2 closes the one failure class the diagnostics loop cannot see:
an operation that is structurally valid and semantically wrong. The engine has
nothing to complain about, so the only evidence is the receipts — what the
change actually produced — held next to what the user asked for.

**Triggering operations are enumerated, not guessed**: the macros M3
``shift_items`` and M4 ``scale_items``, and any M1/M2 carrying escalation. The
escalation class is vacuous in the MLP: the v0 intent grammar has no escalation
slot on M1 or M2, and formula authoring is out of scope (SPEC §1), so the two
macros are the whole trigger set here. They earn it: a macro changes many lines
at once from one selector, which is precisely where "valid but not what was
meant" hides.

**Verification runs inside the turn, on the dry-run copy, before the card is
shown** — not after the user applies it. Two reasons, and the second is
decisive. A correction that arrives after the change landed asks the user to
confirm a second card to undo the first; a correction that arrives before means
the card they confirm is already the right one. And SPEC §8 budgets accept at
p95 ≤ 1 s, which no model call fits inside. Recorded as D-MLP-25.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from cashkit.sdk import CashKit

from ..books import scratch_copy
from ..ops.applier import apply_op
from ..serialize import period_starts, trace_out

#: The enumerated triggers. Nothing else calls the verifier.
VERIFY_TRIGGERS = frozenset({"shift_items", "scale_items"})

#: How many periods per changed item get a receipt. Three is enough to show a
#: shift (a period that lost the amount, one that gained it) without turning
#: the prompt into a data dump.
PERIODS_PER_ITEM = 3
ITEM_LIMIT = 8


def triggered(operations: list[dict[str, Any]]) -> bool:
    """Does this operation set need a verification call?"""
    return any(op.get("op") in VERIFY_TRIGGERS for op in operations)


def receipts(
    kit: CashKit,
    operations: list[dict[str, Any]],
    *,
    scenario: str,
    as_of: _dt.date,
    context: str | None = None,
) -> list[dict[str, Any]]:
    """Apply the operations to a throwaway copy and trace what moved.

    The real book is never touched. The receipts are ``trace()`` output — the
    engine's own explanation of each figure — for the periods where the change
    actually landed, so the model judges evidence rather than its own memory.
    """
    with scratch_copy(kit, Path(kit.root)) as scratch:
        before = _columns(scratch, scenario)
        for index, operation in enumerate(operations):
            result = apply_op(
                scratch, operation, scenario=scenario, as_of=as_of, context=context, seq=index
            )
            if not result.ok:
                break
        scratch.save()
        after = _columns(scratch, scenario)
        return _trace_changes(scratch, before, after, scenario=scenario)


def _columns(kit: CashKit, scenario: str) -> dict[str, list[tuple[_dt.date, int]]]:
    run = kit.run(scenario)
    starts = period_starts(run)
    columns: dict[str, list[tuple[_dt.date, int]]] = {}
    for item_id in run.book.items:
        column = run.result.cash.get(item_id)
        if column is None:
            continue
        columns[item_id] = list(zip(starts, (int(v) for v in column), strict=True))
    return columns


def _trace_changes(
    kit: CashKit,
    before: dict[str, list[tuple[_dt.date, int]]],
    after: dict[str, list[tuple[_dt.date, int]]],
    *,
    scenario: str,
) -> list[dict[str, Any]]:
    run = kit.run(scenario)
    out: list[dict[str, Any]] = []
    for item_id, column in list(after.items())[:ITEM_LIMIT]:
        was = dict(before.get(item_id, []))
        moved = [period for period, value in column if was.get(period, 0) != value]
        if not moved:
            continue
        for period in _sample(moved):
            try:
                trace = run.trace(item_id, period, measure="cash")
            except Exception as exc:  # noqa: BLE001 — a missing trace is not a 500
                out.append({"item": item_id, "period": period.isoformat(),
                            "unavailable": f"{type(exc).__name__}: {exc}"[:200]})
                continue
            out.append(
                {
                    "item": item_id,
                    "period": period.isoformat(),
                    "trace": trace_out(trace).model_dump(mode="json"),
                }
            )
    return out


def _sample(periods: list[_dt.date]) -> list[_dt.date]:
    """First, middle and last of the periods that moved."""
    if len(periods) <= PERIODS_PER_ITEM:
        return periods
    return [periods[0], periods[len(periods) // 2], periods[-1]]


__all__ = ["ITEM_LIMIT", "PERIODS_PER_ITEM", "VERIFY_TRIGGERS", "receipts", "triggered"]
