"""Item — the generative input (PRD §4.2).

Business rules that are user-facing failure modes (generative stock CK-E012,
sign vs direction CK-E011, formula-only-on-derived) are validated by the SDK
at ``add_item()`` time and reported as diagnostics — not enforced here.
Structural invariants are.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .primitives import (
    Amount,
    CashKitModel,
    CurrencyCode,
    Escalation,
    FiniteDecimal,
    FlagName,
    Grain,
    ItemId,
    PeriodRef,
    TagKey,
    TagValue,
)
from .settlement import Settlement
from .tax import VatSpec

__all__ = ["Item", "Recurrence", "Segment"]


class Recurrence(CashKitModel):
    """Recurrence pattern. REQUIRED on every Segment — one-offs are Events
    with ``status="forecast"``, never degenerate segments.

    ``day`` is set iff ``anchor == "day_of_month"``; values past the month's
    end clamp to the last day (31 → Feb 28/29).
    """

    every: int = Field(ge=1)
    unit: Grain
    anchor: Literal["period_start", "period_end", "day_of_month", "eom"] = (
        "period_start"
    )
    day: int | None = Field(default=None, ge=1, le=31)
    business_day_adjust: Literal["none", "prev", "next"] = "none"

    @model_validator(mode="after")
    def _day_iff_day_of_month(self) -> "Recurrence":
        if (self.anchor == "day_of_month") != (self.day is not None):
            raise ValueError(
                "Recurrence.day must be set exactly when anchor == 'day_of_month'"
            )
        return self


class Segment(CashKitModel):
    """One generative phase of an Item: a recurrence over [start, end)."""

    start: PeriodRef
    end: PeriodRef | None = None
    recurrence: Recurrence
    amount: Amount
    escalation: Escalation | None = None
    probability: FiniteDecimal = Field(default=Decimal(1))

    @model_validator(mode="after")
    def _end_after_start(self) -> "Segment":
        if self.end is not None and self.end <= self.start:
            raise ValueError("Segment requires end > start (or end=None for open-ended)")
        return self


class Item(CashKitModel):
    """Generative or derived model line.

    ``direction`` is display-only; storage is signed. ``kind="stock"`` is
    valid on derived items only in v1 (CK-E012 at add_item time).
    """

    id: ItemId
    name: str = Field(min_length=1)
    kind: Literal["flow", "derived", "stock"]
    direction: Literal["in", "out"] | None = None
    tags: dict[TagKey, TagValue] = Field(default_factory=dict)
    flags: set[FlagName] = Field(default_factory=set)
    currency: CurrencyCode = "EUR"
    segments: list[Segment] = Field(default_factory=list)
    formula: str | None = None
    settlement: Settlement | None = None
    vat: VatSpec | None = None
    agg_rule: Literal["sum", "last", "mean"] = "sum"
