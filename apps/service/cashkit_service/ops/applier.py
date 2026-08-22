"""The applier: one operation onto one kit.

Every failure comes back as a :class:`~cashkit.model.Diagnostic`, never an
exception — the proto's rule, and the SDK's own contract (CLAUDE.md: errors are
Diagnostic objects; exceptions only for programmer error).

The normalizations here are the ones the proto earned (TESTLOG "what moved the
needle", item 4). They belong in the applier and nowhere else — certainly not
in a prompt, where they would be advice a model may or may not follow:

* a schedule **is** the occurrence series, so the required-but-inert
  ``recurrence`` is defaulted rather than bounced;
* settlement shorthands (``immediate``, ``net30``) expand to due terms;
* a bare-number offset means days.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from cashkit.model import (
    Amount, Diagnostic, DueTerm, Event, Grain, Item, PeriodRange, Recurrence,
    Segment, Settlement,
)
from cashkit.sdk import CashKit, ScaleItems as ScaleMacro, ShiftItems as ShiftMacro
from pydantic import ValidationError

from ..envelope import BASE_SCENARIO
from .schema import MutationOp

_GRAIN_LETTERS = {"d": Grain.DAY, "w": Grain.WEEK, "m": Grain.MONTH, "y": Grain.YEAR}


def app_diagnostic(code: str, message: str, *, fix: str = "", item: str | None = None) -> Diagnostic:
    """An app-layer diagnostic.

    It uses the ``CK-`` shape so a client renders it exactly like an engine
    one, and it never claims to be an engine code: the catalogue owns those.
    App codes live in the E9xx band, which the engine catalogue does not use.
    """
    return Diagnostic(
        severity="error", code=code, message=message, suggested_fix=fix, item_id=item, field=None
    )


# Codes this layer issues. Each says what the host refused and why.
CK_E901 = "CK-E901"  # operation refused by a host rule
CK_E902 = "CK-E902"  # operation payload could not be built
CK_E903 = "CK-E903"  # operation needs something the book does not have


@dataclass
class OpResult:
    """What one operation did, and everything that went wrong doing it."""

    op: dict[str, Any]
    ok: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    touched_items: list[str] = field(default_factory=list)
    touched_events: list[str] = field(default_factory=list)


# --- normalizations (proto TESTLOG item 4) -------------------------------- #


def parse_recurrence(text: str) -> Recurrence:
    """``"3m"`` → every 3 months. A bare number means months at book grain."""
    value = (text or "1m").strip().lower()
    unit = _GRAIN_LETTERS.get(value[-1:], Grain.MONTH)
    digits = value[:-1] if value[-1:] in _GRAIN_LETTERS else value
    every = int(digits) if digits.isdigit() and int(digits) > 0 else 1
    return Recurrence(every=every, unit=unit)


def normalize_offset(offset: Any) -> str:
    """A bare number means days (proto normalization)."""
    text = str(offset).strip().lower()
    return text if text[-1:] in _GRAIN_LETTERS else f"{text}d"


def parse_settlement(text: str | None) -> Settlement:
    """``None``/``immediate`` → immediate; ``net30``/``net 30`` → 30 days."""
    if text is None:
        return Settlement.immediate()
    value = text.strip().lower().replace(" ", "")
    if value in ("", "immediate", "0d"):
        return Settlement.immediate()
    if value.startswith("net"):
        return Settlement(due=[DueTerm(share=Decimal(1), offset=normalize_offset(value[3:] or "0"))])
    return Settlement(due=[DueTerm(share=Decimal(1), offset=normalize_offset(value))])


def signed(amount: str, direction: str | None) -> Decimal:
    """Make the sign agree with the direction.

    ``direction="out"`` requires a negative amount (``CK-E011``). A model or a
    UI that sends ``out`` with a positive number meant an outflow, and the sign
    is a notation detail — but the reverse never happens silently: a sign is
    only ever flipped to match a direction the caller stated.
    """
    value = Decimal(amount)
    if direction == "out" and value > 0:
        return -value
    if direction == "in" and value < 0:
        return -value
    return value


def event_id_for(op_dict: dict[str, Any], seq: int) -> str:
    """A deterministic id for an authored event.

    Deterministic, so re-running the same accepted proposal produces the same
    id rather than a duplicate row with a fresh uuid.
    """
    payload = repr(sorted((k, str(v)) for k, v in op_dict.items() if k != "id"))
    digest = hashlib.sha256(f"{seq}:{payload}".encode("utf-8")).hexdigest()[:24]
    return f"evt_{digest}"


# --- the record-actual discriminator (SPEC §5-F5) ------------------------- #


@dataclass(frozen=True)
class StatusDecision:
    status: str | None
    clarification: str | None = None


def discriminate_event_status(
    op: dict[str, Any], *, context: str | None, as_of: _dt.date
) -> StatusDecision:
    """Decide an event's status. The model never chooses it.

    SPEC §5-F5, verbatim: *an M5 intent maps to* ``status="actual"`` *if and
    only if the turn arrived with* ``context: "actuals_record"`` *(set by the
    client only on the Actuals record flow) AND the event date is ≤* ``as_of``.
    *A future-dated entry on that flow, or any M5 from any other surface, stays*
    ``forecast``. *If the flow is* ``actuals_record`` *and the date is ambiguous
    or missing, the turn returns* ``kind: clarification`` *— never a guess.*

    ``record_actual`` is that flow in typed form, so it carries the context
    itself; an ``add_event`` gets it from the turn.
    """
    kind = op.get("op")
    on_the_flow = kind == "record_actual" or (
        kind == "add_event" and context == "actuals_record"
    )
    if not on_the_flow:
        return StatusDecision(status="forecast")
    when = op.get("date")
    if when is None:
        return StatusDecision(
            status=None,
            clarification=(
                "I need the date this happened before I can record it. "
                "Which day was it?"
            ),
        )
    if isinstance(when, str):
        when = _dt.date.fromisoformat(when)
    return StatusDecision(status="actual" if when <= as_of else "forecast")


# --- the applier ---------------------------------------------------------- #


def apply_op(
    kit: CashKit,
    op: MutationOp | dict[str, Any],
    *,
    scenario: str,
    as_of: _dt.date,
    context: str | None = None,
    seq: int = 0,
) -> OpResult:
    """Apply one operation to ``kit``. Never raises for a bad operation."""
    payload = op if isinstance(op, dict) else op.model_dump(mode="json")
    target = payload.get("scenario") or scenario
    try:
        return _dispatch(kit, payload, target=target, as_of=as_of, context=context, seq=seq)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors(include_url=False)
        )
        return OpResult(
            op=payload,
            ok=False,
            diagnostics=[
                app_diagnostic(CK_E902, f"{payload.get('op')}: {details}",
                               fix="Correct the operation and try again.")
            ],
        )
    except Exception as exc:  # noqa: BLE001 — a bad op is a diagnostic, not a 500
        return OpResult(
            op=payload,
            ok=False,
            diagnostics=[app_diagnostic(CK_E902, f"{type(exc).__name__}: {exc}")],
        )


def _result(payload: dict, report: Any, *, items=(), events=()) -> OpResult:
    diagnostics = list(getattr(report, "diagnostics", ()) or ())
    ok = not any(d.severity == "error" for d in diagnostics)
    return OpResult(
        op=payload, ok=ok, diagnostics=diagnostics,
        touched_items=list(items), touched_events=list(events),
    )


def _dispatch(
    kit: CashKit, payload: dict[str, Any], *, target: str, as_of: _dt.date,
    context: str | None, seq: int,
) -> OpResult:
    kind = payload["op"]
    on_base = target == BASE_SCENARIO

    if kind == "add_item":
        item = _build_item(payload)
        if on_base:
            return _result(payload, kit.add_item(item), items=[item.id])
        return _result(payload, kit.scenarios.set_item(target, item), items=[item.id])

    if kind == "set_amount":
        return _set_amount(kit, payload, target=target)

    if kind in ("shift_items", "scale_items"):
        macro = (
            ShiftMacro(selector=payload["selector"], by=payload["by"])
            if kind == "shift_items"
            else ScaleMacro(selector=payload["selector"], factor=Decimal(payload["factor"]))
        )
        return _result(payload, kit.scenarios.apply_macro(target, macro))

    if kind in ("add_event", "record_actual"):
        decision = discriminate_event_status(payload, context=context, as_of=as_of)
        if decision.status is None:
            return OpResult(
                op=payload, ok=False,
                diagnostics=[app_diagnostic(CK_E903, decision.clarification or "",
                                            fix="Give the date of the entry.")],
            )
        event = Event(
            id=payload.get("id") or event_id_for(payload, seq),
            date=_as_date(payload["date"]),
            amount=signed(payload["amount"], payload.get("direction")),
            status=decision.status,
            item=payload.get("item"),
            note=payload.get("note"),
        )
        return _result(payload, kit.add_event(event), events=[event.id])

    if kind == "correct_actual":
        return _correct(kit, payload)

    if kind == "fork_scenario":
        parent = payload.get("parent") or target
        return _result(payload, kit.scenarios.fork(parent, payload["name"], note=payload.get("note", "")))

    if kind == "set_cutover":
        return _result(payload, kit.set_cutover(_as_date(payload["date"])))

    if kind == "set_horizon":
        return _set_horizon(kit, payload)

    if kind == "set_opening_balance":
        changed = kit.scenarios.set_book(opening_balance=Decimal(payload["amount"]))
        kit.save()
        return OpResult(op=payload, ok=True, diagnostics=[], touched_items=list(changed))

    if kind == "remove_event":
        return _remove_event(kit, payload)

    if kind == "edit_schedule_date":
        return _edit_schedule_date(kit, payload, target=target)

    return OpResult(
        op=payload, ok=False,
        diagnostics=[app_diagnostic(CK_E901, f"Unknown operation {kind!r}.")],
    )


def _as_date(value: Any) -> _dt.date:
    return value if isinstance(value, _dt.date) else _dt.date.fromisoformat(str(value))


def _build_item(payload: dict[str, Any]) -> Item:
    return Item(
        id=payload["id"],
        name=payload.get("name") or payload["id"].replace("_", " ").title(),
        kind="flow",
        direction=payload["direction"],
        tags=dict(payload.get("tags") or {}),
        segments=[
            Segment(
                start=_as_date(payload["start"]),
                end=_as_date(payload["end"]) if payload.get("end") else None,
                recurrence=parse_recurrence(payload.get("recurrence") or "1m"),
                amount=Amount(constant=signed(payload["amount"], payload["direction"])),
            )
        ],
        settlement=parse_settlement(payload.get("settlement")),
    )


def _set_amount(kit: CashKit, payload: dict[str, Any], *, target: str) -> OpResult:
    """M2 — change an amount, splitting the segment at ``from_date``.

    Splitting rather than overwriting is the point: segments are the history
    (ADR-0009, SPEC §6-S9 "SEGMENTS · CHANGES KEEP HISTORY"). A change from a
    date closes the old segment and opens a new one; it never rewrites what the
    amount used to be.
    """
    book = kit.scenarios.resolve(target)
    item = book.items.get(payload["item"])
    if item is None:
        return OpResult(
            op=payload, ok=False,
            diagnostics=[app_diagnostic(CK_E903, f"No item {payload['item']!r} in this book.",
                                        fix="Add the item first.", item=payload["item"])],
        )
    amount = signed(payload["amount"], item.direction)
    from_date = _as_date(payload["from_date"]) if payload.get("from_date") else None
    segments = [s.model_copy(deep=True) for s in item.segments]

    if from_date is None:
        segments = [s.model_copy(update={"amount": Amount(constant=amount)}) for s in segments]
    else:
        rebuilt: list[Segment] = []
        for segment in segments:
            if segment.end is not None and segment.end <= from_date:
                rebuilt.append(segment)
                continue
            if segment.start >= from_date:
                rebuilt.append(segment.model_copy(update={"amount": Amount(constant=amount)}))
                continue
            rebuilt.append(segment.model_copy(update={"end": from_date}))
            rebuilt.append(
                segment.model_copy(
                    update={"start": from_date, "end": segment.end,
                            "amount": Amount(constant=amount)}
                )
            )
        segments = rebuilt

    updated = item.model_copy(update={"segments": segments})
    if target == BASE_SCENARIO:
        return _result(payload, kit.add_item(updated), items=[updated.id])
    return _result(payload, kit.scenarios.set_item(target, updated), items=[updated.id])


def _correct(kit: CashKit, payload: dict[str, Any]) -> OpResult:
    """M6 — append a correction (ADR-0012). The original stays, tombstoned."""
    rows = {r["id"]: r for r in kit.query_events(include_voided=True).to_dicts()}
    original = rows.get(payload["event"])
    if original is None:
        return OpResult(
            op=payload, ok=False,
            diagnostics=[app_diagnostic(CK_E903, f"No event {payload['event']!r} in the ledger.",
                                        fix="Check the event id.")],
        )
    corrected = Event(
        id=f"{original['id']}_c{len(rows)}",
        date=_as_date(payload["date"]) if payload.get("date") else original["date"],
        amount=Decimal(payload["amount"]),
        status=original["status"],
        item=original["item"],
        note=payload["note"],
        corrects=original["id"],
    )
    report = kit.correct_event(original["id"], corrected, payload["note"])
    return _result(payload, report, events=[original["id"], corrected.id])


def _set_horizon(kit: CashKit, payload: dict[str, Any]) -> OpResult:
    start, end = _as_date(payload["start"]), _as_date(payload["end"])
    if end <= start:
        return OpResult(
            op=payload, ok=False,
            diagnostics=[app_diagnostic(CK_E901, "The horizon must end after it starts.",
                                        fix="Give an end date after the start date.")],
        )
    changed = kit.scenarios.set_book(horizon=PeriodRange(start=start, end=end))
    kit.save()
    return OpResult(op=payload, ok=True, diagnostics=[], touched_items=list(changed))


def _remove_event(kit: CashKit, payload: dict[str, Any]) -> OpResult:
    """Host op — refused on an actual (SPEC §2.5, ADR-0012).

    ``void_event`` already refuses with ``CK-E016``; the host refuses first so
    the message names the correction path the user actually wants.
    """
    rows = {r["id"]: r for r in kit.query_events(include_voided=True).to_dicts()}
    original = rows.get(payload["event"])
    if original is None:
        return OpResult(
            op=payload, ok=False,
            diagnostics=[app_diagnostic(CK_E903, f"No event {payload['event']!r} in the ledger.",
                                        fix="Check the event id.")],
        )
    if original["status"] == "actual":
        return OpResult(
            op=payload, ok=False,
            diagnostics=[
                app_diagnostic(
                    CK_E901,
                    "This is a recorded actual, so it cannot be removed.",
                    fix="Record a correction instead; it keeps the original and the reason.",
                )
            ],
        )
    return _result(payload, kit.void_event(payload["event"], payload["note"]), events=[payload["event"]])


def _edit_schedule_date(kit: CashKit, payload: dict[str, Any], *, target: str) -> OpResult:
    """Host op — one explicit date on a schedule item (SPEC §6-S11)."""
    book = kit.scenarios.resolve(target)
    item = book.items.get(payload["item"])
    if item is None:
        return OpResult(
            op=payload, ok=False,
            diagnostics=[app_diagnostic(CK_E903, f"No item {payload['item']!r} in this book.",
                                        fix="Check the item id.", item=payload["item"])],
        )
    segment_index = next(
        (i for i, s in enumerate(item.segments) if s.amount.schedule is not None), None
    )
    if segment_index is None:
        return OpResult(
            op=payload, ok=False,
            diagnostics=[
                app_diagnostic(
                    CK_E901,
                    f"Item {item.id!r} has no explicit dates to edit.",
                    fix="This item repeats on a rule; change the rule instead.",
                    item=item.id,
                )
            ],
        )
    segment = item.segments[segment_index]
    schedule = list(segment.amount.schedule or [])
    when = _as_date(payload["date"])
    action = payload["action"]

    if action == "remove":
        schedule = [entry for entry in schedule if entry[0] != when]
    elif action == "add":
        if payload.get("amount") is None:
            return OpResult(
                op=payload, ok=False,
                diagnostics=[app_diagnostic(CK_E901, "Adding a date needs an amount.",
                                            fix="Give the amount for the new date.", item=item.id)],
            )
        schedule.append((when, signed(payload["amount"], item.direction)))
    else:  # change
        new_date = _as_date(payload["new_date"]) if payload.get("new_date") else when
        replacement = [
            (
                new_date if entry[0] == when else entry[0],
                signed(payload["amount"], item.direction)
                if entry[0] == when and payload.get("amount") is not None
                else entry[1],
            )
            for entry in schedule
        ]
        if replacement == schedule and not any(entry[0] == when for entry in schedule):
            return OpResult(
                op=payload, ok=False,
                diagnostics=[app_diagnostic(CK_E903, f"{when.isoformat()} is not a scheduled date.",
                                            fix="Add it instead of changing it.", item=item.id)],
            )
        schedule = replacement

    if not schedule:
        return OpResult(
            op=payload, ok=False,
            diagnostics=[
                app_diagnostic(
                    CK_E901,
                    "That would leave the item with no dates at all.",
                    fix="Remove the item instead of its last date.",
                    item=item.id,
                )
            ],
        )
    schedule.sort(key=lambda entry: entry[0])
    segments = [s.model_copy(deep=True) for s in item.segments]
    segments[segment_index] = segment.model_copy(update={"amount": Amount(schedule=schedule)})
    updated = item.model_copy(update={"segments": segments})
    if target == BASE_SCENARIO:
        return _result(payload, kit.add_item(updated), items=[updated.id])
    return _result(payload, kit.scenarios.set_item(target, updated), items=[updated.id])


__all__ = [
    "CK_E901", "CK_E902", "CK_E903", "OpResult", "StatusDecision",
    "app_diagnostic", "apply_op", "discriminate_event_status", "event_id_for",
    "normalize_offset", "parse_recurrence", "parse_settlement", "signed",
]
