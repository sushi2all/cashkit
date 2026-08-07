"""``validate()`` — the whole diagnostic catalogue over one book (PRD §6.1, §10.1).

An agent is told to run this after any structural change and before any commit
(PRD §9.3 rule 3), so it has one job: **say everything a run would say, plus
everything a run cannot see**.

It therefore *runs the engine*. Compiling and evaluating a 50-item book costs
tens of milliseconds (BENCHMARKS.md), and re-deriving the compile-time and
expansion-time diagnostics in a second implementation is precisely the drift the
dual-engine gate exists to prevent — a validator that disagreed with the engine
about whether a formula is broken would be worse than no validator.

On top of the run it adds the checks the engine has no reason to make:

* ``CK-E011`` — an amount whose sign contradicts ``direction``. The engine does
  not care (storage is signed); an agent authoring rent as a positive "out"
  silently creates an inflow, which is the whole reason the code exists.
* ``CK-E012`` — a generative item with ``kind="stock"``.
* ``CK-W006`` — a cutover outside the horizon. The engine applies the rule it is
  given and reports nothing: a cutover past ``horizon.end`` suppresses every
  generative occurrence there is, and the result is a book that computes cleanly
  and produces nothing. That is the quietest failure on this surface.
* ``CK-W004`` / ``CK-I001`` — withholding without a remittance leg, and a tax
  regime with no non-VAT ``cat:tax`` items (Phase 6 already computes both).

**Some catalogue codes are not book properties and can never come from here**:
an import conflict, a held lock, a ledger row that does not exist. Those are
outcomes of *operations*, and :data:`OPERATION_TIME_CODES` names every one of
them. ``tests/test_validation.py`` asserts that the two sets partition the
catalogue exactly, so a code can never quietly become unreachable — a diagnostic
nothing emits is a promise nothing keeps.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from cashkit.engine import Engine, RoundingPolicy
from cashkit.engine.tax import tax_diagnostics
from cashkit.model import Book, Diagnostic, Event, Item
from cashkit.model.diagnostics import make_diagnostic

__all__ = [
    "CONSTRUCTION_TIME_CODES",
    "OPERATION_TIME_CODES",
    "cutover_problem",
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
    "CK-E024": "opening_balance set to a non-money value — ScenarioSet.set_param",
    "CK-E025": "stored state that will not parse — the config store",
    "CK-E026": "a book from a newer schema generation — the config store",
    "CK-E027": "a revision ref that does not resolve — CashKit.at / diff_revisions",
    "CK-E028": "a reproduction mismatch — CashKit.reproduce",
    "CK-E029": "no book at a path — CashKit.open",
    "CK-E030": "a write on a revision-bound kit — CashKit.commit / discard / "
    "the ledger writes",
    "CK-E031": "a book already exists at a path — create_book",
    "CK-E032": "an argument that cannot make a Book — create_book",
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

    Runs the engine and returns its diagnostics — ``CK-E001``, ``CK-E002``,
    ``CK-E003``, ``CK-E004``, ``CK-E005``, ``CK-E008``, ``CK-E018``,
    ``CK-E019``, ``CK-E020``, ``CK-W001``, ``CK-W002``, ``CK-W003``,
    ``CK-W005`` — plus the checks a run has no reason to make: ``CK-E011``
    (sign contradicts direction), ``CK-E012`` (generative stock), and
    ``CK-W004`` / ``CK-I001`` (withholding with no remittance leg, and a tax
    regime with no non-VAT ``cat:tax`` items), and ``CK-W006`` (a cutover
    outside the horizon).

    **No content-bearing code lives here** (ADR-0021, superseding ADR-0020):
    CashKit is a calculation engine, and an enumerated list of jurisdiction
    mechanics is application domain, not engine domain. ``validate()``
    implements the §10.1 catalogue and nothing beyond it.

    ``events`` is the ledger sequence to validate against; without it
    ``CK-W003`` (an actual dated on or after cutover) cannot be seen, because it
    is a statement about the ledger and the book together.

    Returns the diagnostics sorted by ``(severity, code, item_id)`` so two runs
    over the same state produce the same list. Never raises on book content.
    """
    rows = tuple(events)
    found: list[Diagnostic] = []
    horizon = cutover_problem(book.cutover, book)
    if horizon is not None:
        found.append(horizon)
    found.extend(_authoring_problems(book))
    generative_stocks = {d.item_id for d in found if d.code == "CK-E012"}

    result = Engine(book, policy, rows).run()
    for diagnostic in result.diagnostics:
        # A stock carrying segments is one modelling mistake, and CK-E012 is the
        # code that names it. The compiler also reports CK-E003 ("a
        # formula-valued kind must have no segments") for the same item; two
        # codes for one mistake reads as two mistakes.
        if (
            diagnostic.code == "CK-E003"
            and diagnostic.field == "segments"
            and diagnostic.item_id in generative_stocks
        ):
            continue
        found.append(diagnostic)

    found.extend(tax_diagnostics(book))
    return _ordered(found)


def cutover_problem(day: date, book: Book) -> Diagnostic | None:
    """``CK-W006`` when ``day`` falls outside ``book.horizon``, else ``None``.

    Lives here rather than inside :func:`validate` because ``set_cutover()`` has
    to ask the same question at call time — a warning that only surfaces on the
    next ``validate()`` is a warning the agent that caused it never sees. One
    function, so the two answers cannot drift.

    **A warning, not an error.** Both directions are things a user can
    plausibly mean. A cutover before ``horizon.start`` is the natural state of a
    book that has never been reconciled, and it changes nothing: generation is
    suppressed strictly *before* the cutover, so there is nothing in the horizon
    to suppress. A cutover past ``horizon.end`` is the ordering an agent lands
    in when it closes a window and then extends the horizon to cover the next
    one — legitimate in the next call, silently total suppression until then.
    Refusing either would make a legal sequence of writes unconstructible, which
    is the D-S55-01 test.

    The horizon is half-open, so ``horizon.end`` itself is inside it: a cutover
    *at* the end suppresses everything too, but it is the boundary the model's
    own arithmetic reaches and naming it a mistake would be naming the horizon a
    mistake. Produces no other diagnostics; never raises.
    """
    if book.horizon.start <= day <= book.horizon.end:
        return None
    effect = (
        "no occurrence is suppressed and the setting has no effect"
        if day < book.horizon.start
        else "every generative occurrence in the horizon is suppressed, so the "
        "model produces nothing and only ledger rows remain"
    )
    return make_diagnostic(
        "CK-W006",
        field="cutover",
        cutover=day.isoformat(),
        start=book.horizon.start.isoformat(),
        end=book.horizon.end.isoformat(),
        effect=effect,
    )


def _authoring_problems(book: Book) -> list[Diagnostic]:
    """``CK-E011`` and ``CK-E012``: rules the engine has no reason to enforce."""
    out: list[Diagnostic] = []
    for item_id, item in sorted(book.items.items()):
        if item.kind == "stock" and item.segments:
            out.append(make_diagnostic("CK-E012", item_id=item_id, field="kind"))
        wrong = _sign_conflict(item)
        if wrong is not None:
            out.append(
                make_diagnostic(
                    "CK-E011",
                    item_id=item_id,
                    field=wrong,
                    direction=item.direction,
                )
            )
    return out


def _sign_conflict(item: Item) -> str | None:
    """The first authored amount whose sign contradicts ``direction``.

    ``direction`` is display-only and storage is signed (PRD §4.2), so the
    engine happily evaluates a positive "out" — and produces an inflow where the
    author meant an outflow. Zero never conflicts: it has no sign.
    """
    if item.direction is None:
        return None
    wanted = 1 if item.direction == "in" else -1
    for index, segment in enumerate(item.segments):
        amounts: list[tuple[str, Decimal]] = []
        if segment.amount.constant is not None:
            amounts.append((f"segments[{index}].amount.constant", segment.amount.constant))
        for position, (_, value) in enumerate(segment.amount.schedule or ()):
            amounts.append((f"segments[{index}].amount.schedule[{position}]", value))
        for field_path, value in amounts:
            if value != 0 and (value > 0) != (wanted > 0):
                return field_path
    return None


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _ordered(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
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
