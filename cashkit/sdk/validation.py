"""``validate()`` — the whole diagnostic catalogue over one book (PRD §6.1, §10.1).

An agent is told to run this after any structural change and before any commit
(PRD §9.3 rule 3), so it has one job: **say everything a run would say**.

It therefore *is* a run. Since ADR-0034 every book-level check the catalogue
knows — the compile-time ones (``CK-E001``…``CK-E003``, ``CK-E008``,
``CK-E019``, ``CK-E020``), the expansion-time ones (``CK-E004``, ``CK-E005``,
``CK-W001``, ``CK-W002``, ``CK-W005``), the ledger-and-book ones (``CK-E018``,
``CK-W003``) and the authoring ones (``CK-E011``, ``CK-E012``, ``CK-W004``,
``CK-W006``, ``CK-I001``) — is emitted by :func:`cashkit.engine.graph.compile_book`
or by evaluation, so ``run().diagnostics`` and ``validate()`` are the same set.
There is no second implementation to drift from the first; this module only
orders and de-duplicates.

**No content-bearing code lives here** (ADR-0021, superseding ADR-0020):
CashKit is a calculation engine, and an enumerated list of jurisdiction
mechanics is application domain, not engine domain.

**Some catalogue codes are not book properties and can never come from here**:
an import conflict, a held lock, a ledger row that does not exist. Those are
outcomes of *operations*, and :data:`OPERATION_TIME_CODES` names every one of
them. ``tests/test_validation.py`` asserts that the sets partition the
catalogue exactly, so a code can never quietly become unreachable — a diagnostic
nothing emits is a promise nothing keeps.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from cashkit.engine import Engine, RoundingPolicy
from cashkit.engine.graph import cutover_problem, sign_conflict
from cashkit.model import Book, Diagnostic, Event

__all__ = [
    "CONSTRUCTION_TIME_CODES",
    "OPERATION_TIME_CODES",
    "cutover_problem",
    "ordered",
    "sign_conflict",
    "validate",
]

#: Codes that are outcomes of an *operation*, not properties of a book. None of
#: them is reachable from ``validate()`` and each names where it does come from.
OPERATION_TIME_CODES: Mapping[str, str] = {
    "CK-E006": "a scenario overlay targeting an actual — ScenarioSet.resolve_events",
    "CK-E010": "an import conflict — LedgerStore.import_events",
    "CK-E013": "a held writer lock — WriterLock.acquire",
    "CK-E014": "a ledger row that does not exist — void_event / correct_event",
    "CK-E015": "a ledger row in the wrong state — void_event / correct_event",
    "CK-E016": "void_event refusing a bare actual",
    "CK-E017": "an import row with no ext_id — LedgerStore.import_events",
    "CK-E021": "an unknown scenario id — ScenarioSet writes and resolution",
    "CK-E022": "a scenario id already taken — ScenarioSet.fork / flatten",
    "CK-E023": "an overlay on an item the chain does not define — resolution",
    "CK-E024": "opening_balance set to a non-money value — set_param / set_book",
    "CK-E025": "stored state that will not parse — the config store",
    "CK-E026": "a book from a newer schema generation — the config store",
    "CK-E027": "a revision ref that does not resolve — CashKit.at / diff_revisions",
    "CK-E028": "a reproduction mismatch — CashKit.reproduce",
    "CK-E029": "no book at a path — CashKit.open",
    "CK-E031": "a book already exists at a path — create_book",
    "CK-E032": "an argument that cannot make a Book — create_book / set_book",
    "CK-W010": "a stale writer lock reclaimed — WriterLock.acquire",
    "CK-W011": "an engine-version move — CashKit.reproduce",
    "CK-I002": "a write that recorded nothing — every ChangeReport-returning call",
    "CK-E033": "the duckdb extra is not installed — the §6.4 frame surface "
    "(cashkit.sdk.execution), which is the only thing that needs it",
}

#: Codes the **model layer** enforces structurally, so a constructed Book can
#: never carry the condition (D-P1-07). They are reachable only where a raw
#: value is turned into a model — the SDK's construction boundary — and the
#: parser, which reads formula text rather than a model.
CONSTRUCTION_TIME_CODES: Mapping[str, str] = {
    "CK-E007": "a dotted or invalid param key — rejected by ParamKey, and by the "
    "formula parser when a formula writes one",
    "CK-E009": "an invalid Recurrence — rejected by the Recurrence validator",
}


def validate(
    book: Book,
    *,
    events: Sequence[Event] | Iterable[Event] = (),
    policy: RoundingPolicy = RoundingPolicy.HALF_UP,
) -> list[Diagnostic]:
    """Every diagnostic this book's state produces (PRD §6.1).

    Runs the engine and returns its diagnostics, ordered errors-first and
    de-duplicated. ``events`` is the ledger sequence to validate against;
    without it ``CK-W003`` (an actual dated on or after cutover) cannot be
    seen, because it is a statement about the ledger and the book together.

    Returns the diagnostics sorted by ``(severity, code, item_id)`` so two runs
    over the same state produce the same list. Never raises on book content.
    """
    return ordered(Engine(book, policy, tuple(events)).run().diagnostics)


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def ordered(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Errors first, then a stable key — two validations agree exactly."""
    seen: set[tuple] = set()
    unique: list[Diagnostic] = []
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.item_id, diagnostic.field, diagnostic.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(diagnostic)
    return sorted(
        unique,
        key=lambda d: (
            _SEVERITY_ORDER[d.severity],
            d.code,
            d.item_id or "",
            d.field or "",
        ),
    )
