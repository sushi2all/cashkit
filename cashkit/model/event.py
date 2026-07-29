"""Event — the literal input (PRD §4.3).

``UNIQUE(source, ext_id)`` is the only thing preventing double-counted actuals
on re-import; the constraint itself lives in the ledger store (Phase 5), but
``ext_id`` requires ``source`` structurally so half a key can never exist.

Events are frozen models: an actual, once constructed, cannot be mutated.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .primitives import (
    CashKitModel,
    CurrencyCode,
    EventId,
    ItemId,
    Money,
    TagKey,
    TagValue,
)
from .settlement import Settlement
from .tax import VatSpec

__all__ = ["Event"]


class Event(CashKitModel):
    """One ledger row: a dated, signed, net (VAT-exclusive) amount.

    ``item`` links to an Item for inherited tags / vat / settlement; the
    event's own ``tags`` merge over the item's (event wins on conflict), and
    its ``vat`` / ``settlement`` override the item's when set.
    """

    id: EventId
    date: date
    amount: Money
    status: Literal["actual", "committed", "forecast"]
    item: ItemId | None = None
    tags: dict[TagKey, TagValue] = Field(default_factory=dict)
    vat: VatSpec | None = None
    settlement: Settlement | None = None
    currency: CurrencyCode = "EUR"
    source: str | None = Field(default=None, min_length=1, max_length=256)
    ext_id: str | None = Field(default=None, min_length=1, max_length=256)
    note: str | None = None

    @model_validator(mode="after")
    def _ext_id_requires_source(self) -> "Event":
        if self.ext_id is not None and self.source is None:
            raise ValueError(
                "ext_id requires source: idempotency is keyed on (source, ext_id)"
            )
        return self
