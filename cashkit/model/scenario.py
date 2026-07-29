"""Scenario overlays (PRD §4.6, ADR-0009).

Scenarios are authored by value (``set_item`` takes the whole Item) and stored
field-sparse: only fields differing from the resolved parent are recorded.
Resolution (Phase 7) walks the parent chain field-sparse; ``segments`` is
atomic — recorded whole or not at all, never merged positionally.

Actuals are immutable across all scenarios: ``EventOverlay.status`` cannot
even represent ``"actual"``, so a scenario fabricating or rewriting an actual
is unrepresentable at the type level (the CK-E006 diagnostic guards the
remaining case — an overlay *targeting* an event whose ledger status is
actual — at apply time in Phase 7).
"""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .item import Item, Segment
from .primitives import (
    CashKitModel,
    CurrencyCode,
    EventId,
    FiniteDecimal,
    FlagName,
    ItemId,
    Money,
    ParamKey,
    ScenarioId,
    SparseOverlay,
    TagKey,
    TagValue,
)
from .settlement import Settlement
from .tax import VatSpec

__all__ = ["EventOverlay", "ItemOverlay", "Scenario"]


class ItemOverlay(SparseOverlay):
    """Field-sparse override of one Item. Only *recorded* fields participate
    in resolution (ADR-0009); ``segments`` is atomic. The item's ``id`` is the
    key in ``Scenario.items`` and is not overridable.
    """

    name: str = Field(default="", min_length=1)
    kind: Literal["flow", "derived", "stock"] = "flow"
    direction: Literal["in", "out"] | None = None
    tags: dict[TagKey, TagValue] = Field(default_factory=dict)
    flags: set[FlagName] = Field(default_factory=set)
    currency: CurrencyCode = "EUR"
    segments: list[Segment] = Field(default_factory=list)
    formula: str | None = None
    settlement: Settlement | None = None
    vat: VatSpec | None = None
    agg_rule: Literal["sum", "last", "mean"] = "sum"


class EventOverlay(SparseOverlay):
    """Field-sparse override of one committed/forecast event.

    Import identity (``source``, ``ext_id``) and the event ``id`` are not
    overridable; ``status`` can only take non-actual values (see module
    docstring). See DECISIONS.md for the field-set rationale.
    """

    date: _date = Field(default=_date(1970, 1, 1))
    amount: Money = Field(default=Decimal(0))
    status: Literal["committed", "forecast"] = "forecast"
    item: ItemId | None = None
    tags: dict[TagKey, TagValue] = Field(default_factory=dict)
    vat: VatSpec | None = None
    settlement: Settlement | None = None
    currency: CurrencyCode = "EUR"
    note: str | None = None


class Scenario(CashKitModel):
    """A sparse overlay over a parent scenario (or over base, ``parent=None``).

    Base is a scenario like any other — it is special in *storage location*
    only (ADR-0007); no code path may branch on "is this base".
    """

    id: ScenarioId
    parent: ScenarioId | None = None
    note: str = ""
    params: dict[ParamKey, FiniteDecimal] = Field(default_factory=dict)
    items: dict[ItemId, ItemOverlay] = Field(default_factory=dict)
    added: dict[ItemId, Item] = Field(default_factory=dict)
    removed: set[ItemId] = Field(default_factory=set)
    event_overrides: dict[EventId, EventOverlay] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _structural_consistency(self) -> "Scenario":
        for key, item in self.added.items():
            if key != item.id:
                raise ValueError(
                    f"added key {key!r} does not match item.id {item.id!r}"
                )
        if self.parent is not None and self.parent == self.id:
            raise ValueError("a scenario cannot be its own parent")
        overlap = self.items.keys() & self.added.keys()
        if overlap:
            raise ValueError(
                f"item ids present in both items (overlay) and added: {sorted(overlap)}"
            )
        return self
