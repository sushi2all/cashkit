"""Executing the model's read operations, and the one host read tool.

The twelve read intents already execute deterministically in
:mod:`cashkit_service.intents.read` — S1 built and tested them, and this module
does not reimplement any of it (D-MLP-20). It adds ``query_ledger``, the single
host tool ADR-0030 stage 3 allows alongside the intents, and turns every answer
into a **receipt**: the operation, the scenario it ran against, and the engine's
payload with its money already canonically serialized.

The receipts are what the interface renders and what the model quotes. Neither
of them computes anything: the figure in a receipt is the figure the engine
produced.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from cashkit.model import Diagnostic
from cashkit.sdk import CashKit
from pydantic import BaseModel

from ..intents.read import UnknownIntent, execute as execute_intent
from ..ops.applier import CK_E902, CK_E903, app_diagnostic
from ..serialize import events_out
from .guard import QUERY_LEDGER

#: A ledger read is a read; it still gets a ceiling so one turn cannot paste a
#: whole ledger into a prompt.
LEDGER_ROW_LIMIT = 100


class Receipt(BaseModel):
    """One executed read operation, and what the engine answered."""

    op: str
    scenario: str
    request: dict[str, Any]
    payload: dict[str, Any]


def execute_reads(
    kit: CashKit,
    operations: list[dict[str, Any]],
    *,
    scenario: str,
    as_of: _dt.date,
) -> tuple[list[Receipt], list[Diagnostic]]:
    """Run every read operation against the book. Nothing here writes.

    A failing read is a diagnostic, never an exception and never a silent
    empty answer: the model must be able to tell "there is no such item" from
    "the total is zero", because the user's next sentence depends on it.
    """
    receipts: list[Receipt] = []
    diagnostics: list[Diagnostic] = []
    for operation in operations:
        name = operation.get("op")
        target = operation.get("scenario") or scenario
        try:
            if name == QUERY_LEDGER:
                payload = _query_ledger(kit, operation)
            else:
                payload = execute_intent(kit, operation, scenario=target, as_of=as_of)
        except UnknownIntent as exc:  # pragma: no cover — the guard filters these
            diagnostics.append(app_diagnostic(CK_E902, str(exc)))
            continue
        except (KeyError, ValueError, TypeError) as exc:
            diagnostics.append(
                app_diagnostic(
                    CK_E903,
                    f"{name}: {type(exc).__name__}: {exc}"[:400],
                    fix="Check the item id or the dates in the question.",
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 — a read must not 500 a turn
            diagnostics.append(app_diagnostic(CK_E902, f"{name}: {type(exc).__name__}: {exc}"[:400]))
            continue
        receipts.append(
            Receipt(op=str(name), scenario=target, request=operation, payload=payload)
        )
    return receipts, diagnostics


def _query_ledger(kit: CashKit, operation: dict[str, Any]) -> dict[str, Any]:
    """The host read tool: the ledger's own rows (SPEC §2.3, §14).

    It wraps ``query_events`` and nothing more. Corrections are visible with
    ``include_voided``, because a corrected row and its original are two facts
    and hiding one of them would misrepresent the ledger (ADR-0012).
    """
    limit = min(int(operation.get("n", LEDGER_ROW_LIMIT)), LEDGER_ROW_LIMIT)
    table = kit.query_events(
        where=operation.get("where"),
        since=_date_or_none(operation.get("since")),
        until=_date_or_none(operation.get("until")),
        include_voided=bool(operation.get("include_voided", False)),
    )
    rows = [row.model_dump(mode="json") for row in events_out(table)]
    status = operation.get("status")
    if status:
        rows = [row for row in rows if row["status"] == status]
    item = operation.get("item")
    if item:
        rows = [row for row in rows if row["item"] == item]
    return {"events": rows[:limit], "truncated": len(rows) > limit}


def _date_or_none(value: Any) -> _dt.date | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, _dt.date) else _dt.date.fromisoformat(str(value))


def receipts_for_model(receipts: list[Receipt]) -> list[dict[str, Any]]:
    """The receipts as the model sees them: request in, engine answer out."""
    return [
        {"op": r.op, "scenario": r.scenario, "asked": r.request, "engine_answer": r.payload}
        for r in receipts
    ]


__all__ = ["LEDGER_ROW_LIMIT", "Receipt", "execute_reads", "receipts_for_model"]
