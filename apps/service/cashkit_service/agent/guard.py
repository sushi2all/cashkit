"""The structural guard (ADR-0029): a turn's mutations are held, never applied.

ADR-0029 exists because a prompt rule was demonstrably not enough. Asked "can I
afford a 1500 EUR laptop in September?", the proto's model emitted two write
operations — it changed the book while answering a question. The ADR's answer is
that enforcement is **structural and post-interpretation**:

1. read operations and change operations are separate sets;
2. a turn's change operations are *held*, never auto-applied, whatever the turn
   looked like;
3. applying a held change is the user's act, not the model's.

This module is rule 1 and the sorting half of rule 2. It runs on the model's
**artifact**, after interpretation — never on the raw instruction, because
classifying the instruction up front is pre-interpretation routing and ADR-0028
bans it.

Three things it also guarantees, none of which a prompt could:

* **The model cannot reach a host operation.** ``set_horizon`` and its four
  siblings exist only on the interface→service path (SPEC §2.5, D-MLP-03). An
  operation named here that is not one of the 21 intents or ``query_ledger`` is
  dropped with a diagnostic, whatever the model called it.
* **The model cannot save.** M9 is a real intent and a turn may express it, but
  committing is the user's own action on the book header (D-MLP-18), so a
  ``save`` intent is reported and never executed.
* **A malformed operation is a diagnostic, not a crash.** Every operation is
  validated against the typed grammar before it can reach a book.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cashkit.model import Diagnostic
from pydantic import TypeAdapter, ValidationError

from ..intents.read import READ_INTENTS
from ..ops.applier import CK_E901, CK_E902, app_diagnostic
from ..ops.schema import HOST_OPS, MUTATION_INTENTS, PROPOSABLE_OPS, MutationOp

#: The one host read tool the model is allowed (SPEC §2.3, ADR-0030 stage 3).
QUERY_LEDGER = "query_ledger"

#: Everything the model may name. Deliberately built from the intent grammar
#: plus one tool — never from "whatever the applier happens to accept", which
#: is how a host operation would leak into a prompt's reach.
READ_OPS = frozenset(READ_INTENTS) | {QUERY_LEDGER}
MODEL_OPS = READ_OPS | MUTATION_INTENTS

#: M9. Expressible, reportable, never executed by a turn (D-MLP-18).
DEFERRED_OPS = frozenset({"save"})

_MUTATION_ADAPTER = TypeAdapter(MutationOp)


@dataclass
class Guarded:
    """The model's operations, sorted into what may happen to each."""

    #: Read intents and ``query_ledger`` calls. These execute immediately.
    reads: list[dict[str, Any]] = field(default_factory=list)
    #: Change intents. These become a proposal. They are never applied here.
    mutations: list[dict[str, Any]] = field(default_factory=list)
    #: Intents the user must perform themselves — ``save``.
    deferred: list[dict[str, Any]] = field(default_factory=list)
    #: What was dropped, and why, verbatim for the payload.
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def writes(self) -> bool:
        return bool(self.mutations)

    def all_operations(self) -> list[dict[str, Any]]:
        return [*self.reads, *self.mutations, *self.deferred]


def guard(intents: Any) -> Guarded:
    """Sort one turn's model output. Nothing here touches a book.

    The sorting is by operation name against a fixed set, not by any judgement
    about the user's phrasing. A question that carries changes still yields
    changes — held, and shown on a confirmation card, which is exactly the
    outcome ADR-0029 asks for: the unexpected operations surface where the user
    can see them, instead of landing silently.
    """
    result = Guarded()
    if not isinstance(intents, list):
        if intents:
            result.diagnostics.append(
                app_diagnostic(
                    CK_E902,
                    "The model returned intents in a shape the host cannot read.",
                    fix="Say what you want again, in your own words.",
                )
            )
        return result

    for raw in intents:
        if not isinstance(raw, dict):
            result.diagnostics.append(
                app_diagnostic(CK_E902, f"Ignored an intent that is not an object: {raw!r:.120}")
            )
            continue
        name = raw.get("op") or raw.get("intent")
        operation = {k: v for k, v in raw.items() if k != "intent"}
        operation["op"] = name

        if name not in MODEL_OPS:
            result.diagnostics.append(_out_of_surface(name))
            continue
        if name in READ_OPS:
            result.reads.append(operation)
            continue
        if name in DEFERRED_OPS:
            result.deferred.append(operation)
            result.diagnostics.append(
                Diagnostic(
                    severity="info",
                    code=CK_E901,
                    message="Saving is your own action: use Save on the book header.",
                    suggested_fix="Apply the changes you want first, then Save.",
                    item_id=None,
                    field=None,
                )
            )
            continue

        validated = _validate(operation)
        if isinstance(validated, Diagnostic):
            result.diagnostics.append(validated)
            continue
        result.mutations.append(validated)

    return result


def _out_of_surface(name: Any) -> Diagnostic:
    """An operation the model may not name.

    Host operations land here by construction: they are not in ``MODEL_OPS``,
    so a model that invents one is refused by the same rule that refuses a
    typo. The message names the surface rather than the operation, because the
    operation is not the user's vocabulary.
    """
    reserved = name in HOST_OPS
    return app_diagnostic(
        CK_E901,
        f"{name!r} is not something a turn can do."
        + (" That change is made from the interface." if reserved else ""),
        fix="Say what you want to change and it will come back as a card to confirm.",
    )


def _validate(operation: dict[str, Any]) -> dict[str, Any] | Diagnostic:
    """Typed validation before anything reaches a book."""
    try:
        model = _MUTATION_ADAPTER.validate_python(operation)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'][1:] or e['loc'])}: {e['msg']}"
            for e in exc.errors(include_url=False)
        )
        return app_diagnostic(
            CK_E902,
            f"{operation.get('op')}: {details}",
            fix="Say the change again with the missing detail.",
        )
    payload = model.model_dump(mode="json")
    if payload["op"] not in PROPOSABLE_OPS:  # pragma: no cover — DEFERRED_OPS covers it
        return app_diagnostic(CK_E901, f"{payload['op']!r} is not a change to the plan.")
    return payload


__all__ = [
    "DEFERRED_OPS",
    "Guarded",
    "MODEL_OPS",
    "QUERY_LEDGER",
    "READ_OPS",
    "guard",
]
