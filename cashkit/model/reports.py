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
    "ParamDiff",
    "Provenance",
    "RunSummary",
    "ScenarioDiff",
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
