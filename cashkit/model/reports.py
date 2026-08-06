"""Structured results returned by fallible SDK operations (PRD §6.2, §6.3).

Errors are data, not exceptions: every operation an agent can get wrong returns
one of these, carrying :class:`~cashkit.model.Diagnostic` objects with a
``suggested_fix`` rather than raising. Exceptions stay reserved for programmer
error (wrong type, missing store, corrupt file).

These models live with the rest of the data model because both the stores and
the SDK return them and neither may depend on the other.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .primitives import (
    CashKitModel,
    Diagnostic,
    DiagnosticSubject,
    EventId,
    FiniteDecimal,
    ItemId,
    ParamKey,
    ScenarioId,
)

__all__ = [
    "ChangeReport",
    "EventRef",
    "FieldOrigin",
    "ImportReport",
    "ItemDiff",
    "ItemRef",
    "OutcomeDiff",
    "ParamDiff",
    "Provenance",
    "ReconciliationLine",
    "ReconciliationReport",
    "Reproduction",
    "RevisionDiff",
    "RunSummary",
    "ScenarioDiff",
    "WorkingState",
]


class EventRef(CashKitModel):
    """A handle on one ledger row: its id and its append-only sequence number.

    ``seq`` is the ledger's monotonic entry number — the basis of the ADR-0006
    watermark, and the reason the ledger may never delete or update a row.
    """

    id: EventId
    seq: int = Field(ge=1)


class ChangeReport(CashKitModel):
    """What a write actually recorded (PRD §6.3).

    ``changed`` lists the field paths recorded as *different*, so an agent that
    writes an unchanged value is told so rather than silently bloating the
    store; ``created`` names rows the operation appended. An operation that
    recorded nothing reports ``CK-I002``.
    """

    target: str | None = None
    changed: tuple[str, ...] = ()
    created: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no error-severity diagnostic was produced. No diagnostics."""
        return not any(d.severity == "error" for d in self.diagnostics)

    @property
    def empty(self) -> bool:
        """True when nothing was recorded or created. No diagnostics."""
        return not self.changed and not self.created


class ItemRef(ChangeReport):
    """What ``add_item()`` / ``add_derived()`` recorded about one item (PRD §6.1).

    PRD §6.1 types both as ``-> ItemRef`` and annotates ``add_item`` "validated;
    returns diagnostics", so the reference and the diagnostics are one object:
    an agent that has to fetch the item to discover it was refused has already
    lost the loop §6.5 exists to enable.

    ``created`` names the item when it was new to the book, ``changed`` the
    fields whose authored value moved when it was not, and both are empty when
    the write recorded nothing — either because the item was already exactly
    this (``CK-I002``) or because it was refused (an error diagnostic). ``ok``
    tells the two apart: ``ok`` means the book now holds the item as written.
    """

    item_id: ItemId


class ImportReport(ChangeReport):
    """The outcome of :meth:`import_events` (PRD §6.2).

    Idempotent on ``(source, ext_id)``: a row whose key exists with an identical
    payload is *skipped*; a row whose key exists with a different payload is a
    *conflict*, and any conflict aborts the whole batch (ADR-0008) with per-row
    ``CK-E010`` diagnostics. ``aborted`` says whether anything was written at
    all — on an aborted batch ``inserted`` is the count that *would* have been
    inserted, and the ledger is untouched.
    """

    source: str
    considered: int = Field(default=0, ge=0)
    inserted: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    conflicted: int = Field(default=0, ge=0)
    aborted: bool = False


# --------------------------------------------------------------------------- #
# Scenario results (PRD §6.3)
# --------------------------------------------------------------------------- #


class RunSummary(CashKitModel):
    """The headline numbers of one run (PRD §6.4).

    Every figure is derived from int64 minor units and returned as an exact
    ``Decimal`` at 4 dp — the summary is the number a founder reads before
    deciding whether to keep a company alive, so it is not allowed to be a
    rounded rendering of something else.

    ``balance_source`` names how the cash balance series was derived, because
    "when do we run out of cash" has no meaning until that is stated.
    """

    book_id: str
    grain: str
    balance_source: str
    periods: int = Field(ge=0)
    opening_balance: Decimal
    closing_balance: Decimal
    min_cash: Decimal
    min_cash_period: date | None = None
    #: Index and start date of the first period whose closing balance is
    #: negative — "when do we run out of cash". ``None`` when it never happens
    #: inside the horizon, which is not the same as "never".
    runway_periods: int | None = None
    runway_end: date | None = None
    #: First period from which net cash flow is non-negative and stays that way
    #: for the rest of the horizon. ``None`` when no such period exists.
    breakeven_period: date | None = None
    total_inflow: Decimal
    total_outflow: Decimal
    net_cash: Decimal
    total_accrual: Decimal
    diagnostics: tuple[Diagnostic, ...] = ()


class FieldOrigin(CashKitModel):
    """Which level of the chain supplied one field of one item (ADR-0009).

    ``scenario`` is ``None`` when the value comes from the authored book — base's
    content lives at top level for diff legibility (ADR-0007), so "the book" and
    "base's overlay" are two distinct sources and both are reportable.
    """

    field: str
    scenario: ScenarioId | None = None
    kind: Literal["book", "added", "overlay"]


class Provenance(CashKitModel):
    """Which ancestor set each field of an item (PRD §6.3).

    ``fields`` is empty when the item does not exist in the resolved scenario;
    ``removed_by`` then names the scenario that removed it, if one did.
    """

    scenario: ScenarioId
    item_id: ItemId
    exists: bool
    removed_by: ScenarioId | None = None
    fields: tuple[FieldOrigin, ...] = ()

    def origin_of(self, field: str) -> FieldOrigin | None:
        """Return the origin of one field, or ``None`` if it has none.

        Produces no diagnostics.
        """
        for origin in self.fields:
            if origin.field == field:
                return origin
        return None


class ItemDiff(CashKitModel):
    """One item's difference between two resolved books.

    ``fields`` is populated for ``status="changed"`` and lists exactly the
    fields whose resolved values differ.
    """

    item_id: ItemId
    status: Literal["added", "removed", "changed"]
    fields: tuple[str, ...] = ()


class ParamDiff(CashKitModel):
    """One param's difference between two resolved books.

    ``left``/``right`` are ``None`` where the param is absent on that side.
    """

    key: ParamKey
    left: FiniteDecimal | None = None
    right: FiniteDecimal | None = None


class ScenarioDiff(CashKitModel):
    """The semantic difference between two scenarios (PRD §6.3).

    Computed from **resolved books**, never from overlays: two scenarios that
    reach identical state by different overlay routes diff empty, which is what
    makes the diff a statement about the model rather than about its storage.
    """

    left: ScenarioId
    right: ScenarioId
    opening_balance: tuple[Decimal, Decimal] | None = None
    params: tuple[ParamDiff, ...] = ()
    items: tuple[ItemDiff, ...] = ()
    #: Event ids whose resolved overlay differs. Overlays are compared, not
    #: ledger rows: the ledger is shared by every scenario.
    event_overrides: tuple[EventId, ...] = ()

    @property
    def empty(self) -> bool:
        """True when the two resolved books are semantically identical.

        Produces no diagnostics.
        """
        return not (
            self.opening_balance
            or self.params
            or self.items
            or self.event_overrides
        )


# --------------------------------------------------------------------------- #
# Reconciliation (PRD §6.2)
# --------------------------------------------------------------------------- #


class ReconciliationLine(CashKitModel):
    """One item's forecast, actual and drift over the reconciliation window.

    ``item_id`` widens to :data:`DiagnosticSubject` because an actual that
    references no ``Item`` lands on the engine's synthetic ``_event:<digest>``
    carrier, and a reconciliation that could not name it would report the drift
    without saying where it came from.

    ``drift`` is ``actual - forecast``: positive means more cash arrived (or
    less left) than the model generated for the same window.
    """

    item_id: DiagnosticSubject
    forecast: Decimal
    actual: Decimal
    drift: Decimal


class ReconciliationReport(CashKitModel):
    """Actuals against what was forecast for the same window (PRD §6.2).

    The window is ``[since, until]`` inclusive, with ``since`` defaulting to the
    book's current ``cutover`` — the boundary from which generation is live and
    the ledger's actuals apply alongside it (ADR-0004). Both sides are computed
    by the engine over the same book, so the two numbers are commensurable: the
    forecast side is a run with **no ledger at all**, the actual side a run over
    the window's actuals with every generative segment stripped. Neither side is
    a re-derivation — the canonical rounding order applies to both because both
    went through the engine.

    ``suggested_cutover`` is the day after ``until``: reconciling through
    ``until`` means the ledger is the complete record up to and including it, so
    generation should resume the following day. Feed it to ``set_cutover()``.
    """

    book_id: str
    scenario: ScenarioId
    #: The measure compared. ``"cash"`` — a reconciliation answers "did the bank
    #: move the way the model said", and the bank moves cash.
    measure: str = "cash"
    since: date
    until: date
    suggested_cutover: date
    #: One line per item on which either side is non-zero, item id order.
    lines: tuple[ReconciliationLine, ...] = ()
    forecast_total: Decimal = Decimal(0)
    actual_total: Decimal = Decimal(0)
    drift_total: Decimal = Decimal(0)
    #: Ledger rows with ``status="actual"`` dated inside the window.
    actual_events: int = Field(default=0, ge=0)
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def reconciled(self) -> bool:
        """True when no line drifted at all. No diagnostics."""
        return not any(line.drift for line in self.lines)


# --------------------------------------------------------------------------- #
# Version control results (PRD §6.6)
# --------------------------------------------------------------------------- #


class OutcomeDiff(CashKitModel):
    """How one scenario's committed *numbers* moved between two revisions.

    PRD §10 requires the config diff and the outcome diff to appear in the same
    commit; this is the outcome half. ``left``/``right`` are the summaries as
    committed, so an outcome that moved because the engine changed is
    distinguishable from one that moved because the model did —
    ``engine_version_changed`` says which.
    """

    scenario: ScenarioId
    fields: tuple[str, ...] = ()
    left: RunSummary | None = None
    right: RunSummary | None = None
    engine_version_changed: bool = False

    @property
    def empty(self) -> bool:
        """True when nothing about this scenario's outcome moved. No diagnostics."""
        return not self.fields and self.left is not None and self.right is not None


class RevisionDiff(CashKitModel):
    """The semantic difference between two revisions (PRD §6.6).

    Semantic, not textual: both sides are parsed and compared as models, so a
    revision whose files were reformatted by hand — different key order, different
    quoting, different indentation — diffs **empty**. Textual comparison is the
    store's :class:`~cashkit.stores.revisions.StateDiff`; this is the answer an
    agent should be given, because "the file changed" is not the same statement
    as "the plan changed".
    """

    left: str
    right: str
    scenario: ScenarioId | None = None
    opening_balance: tuple[Decimal, Decimal] | None = None
    params: tuple[ParamDiff, ...] = ()
    items: tuple[ItemDiff, ...] = ()
    scenarios_added: tuple[ScenarioId, ...] = ()
    scenarios_removed: tuple[ScenarioId, ...] = ()
    scenarios_changed: tuple[ScenarioId, ...] = ()
    outcomes: tuple[OutcomeDiff, ...] = ()
    #: Paths whose *bytes* differ. Non-empty with everything else empty is
    #: exactly the reformat-only case, and saying so beats saying nothing.
    reformatted: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def empty(self) -> bool:
        """True when nothing semantic differs between the two revisions.

        A pure reformat is empty by this definition and non-empty by
        ``reformatted``. Produces no diagnostics.
        """
        return not (
            self.opening_balance
            or self.params
            or self.items
            or self.scenarios_added
            or self.scenarios_removed
            or self.scenarios_changed
            or any(not outcome.empty for outcome in self.outcomes)
        )


class WorkingState(CashKitModel):
    """The uncommitted difference between the working state and HEAD (PRD §6.6).

    Structured, never a git porcelain string: an agent can branch on
    ``items_changed``; it cannot branch on ``" M items/rent.yaml"``.
    ``revision`` names the revision the comparison is against — ``None`` before
    the first commit, when everything is new.
    """

    revision: str | None = None
    clean: bool = True
    items_added: tuple[ItemId, ...] = ()
    items_removed: tuple[ItemId, ...] = ()
    items_changed: tuple[ItemId, ...] = ()
    params_changed: tuple[ParamKey, ...] = ()
    book_fields_changed: tuple[str, ...] = ()
    scenarios_changed: tuple[ScenarioId, ...] = ()
    settings_changed: tuple[str, ...] = ()
    #: Tracked paths whose bytes differ, for the rare case where a semantic
    #: comparison sees nothing and the file still moved (a hand reformat).
    paths_changed: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


class Reproduction(CashKitModel):
    """The result of re-running a past revision against its committed snapshot.

    ADR-0006: exact historical reproduction is guaranteed **at matching engine
    version**, and an engine-version mismatch surfaces as a reported delta,
    never a silent failure. Both outcomes are represented here and neither is
    an absence: ``reproduced`` is the answer, ``deltas`` is the evidence, and
    ``engine_version_matches`` says which of the two guarantees applies.
    """

    ref: str
    revision: str
    scenario: ScenarioId
    engine_version_recorded: str
    engine_version_current: str
    engine_version_matches: bool
    reproduced: bool
    #: ``(field, committed, recomputed)`` for every summary field that moved.
    deltas: tuple[tuple[str, str, str], ...] = ()
    committed: RunSummary | None = None
    recomputed: RunSummary | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
