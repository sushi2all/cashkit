"""Book — the resolved, concrete model (PRD §4.1).

``cutover`` is a committed value, never ``date.today()`` (D6): reading the
clock during evaluation destroys reproducibility. Nothing in this package
reads the wall clock — enforced by a lint test.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from .item import Item
from .primitives import (
    BookId,
    CalendarSpec,
    CashKitModel,
    FiniteDecimal,
    Grain,
    ItemId,
    Money,
    ParamKey,
    PeriodRange,
    Watermark,
)
from .tax import TaxRegime

__all__ = ["Book"]


class Book(CashKitModel):
    """The concrete model a run evaluates: no overlays, no scenario chain.

    ``params`` is the lever surface — anything an agent might sweep must be a
    param, not a literal in a formula. ``opening_balance`` is a reserved param
    key: setting it in a scenario overrides the Book field.
    """

    id: BookId
    base_grain: Grain = Grain.DAY
    calendar: CalendarSpec
    horizon: PeriodRange
    opening_balance: Money
    cutover: date
    ledger_watermark: Watermark | None = None
    params: dict[ParamKey, FiniteDecimal] = Field(default_factory=dict)
    items: dict[ItemId, Item] = Field(default_factory=dict)
    tax_regimes: list[TaxRegime] = Field(default_factory=list)

    @model_validator(mode="after")
    def _keys_match_ids(self) -> "Book":
        for key, item in self.items.items():
            if key != item.id:
                raise ValueError(
                    f"items key {key!r} does not match item.id {item.id!r}"
                )
        regime_ids = [regime.id for regime in self.tax_regimes]
        if len(regime_ids) != len(set(regime_ids)):
            raise ValueError("tax_regimes ids must be unique")
        return self
