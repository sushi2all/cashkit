"""VAT and tax regimes (PRD §4.5, ADR-0005).

All authored amounts are VAT-exclusive (net); the engine computes VAT per
line, grosses up the settlement cash leg, and routes the VAT component through
the ``TaxRegime`` schedule. There is no VAT-inclusive authoring mode.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .primitives import (
    IDENT_RE,
    CashKitModel,
    Duration,
    FiniteDecimal,
    _coerce_rate,
)

__all__ = ["TaxRegime", "VatSpec"]


class VatSpec(CashKitModel):
    """Per-line VAT: a rate at the line, scheduled at the entity by a regime."""

    rate: Annotated[str, Field(pattern=rf"^{IDENT_RE}$")] | FiniteDecimal = (
        "vat_standard"
    )
    treatment: Literal[
        "standard",
        "exempt",
        "reverse_charge",
        "out_of_scope",
        "export",
        "split_payment",
    ] = "standard"
    recoverable: FiniteDecimal = Field(default=Decimal(1))

    @field_validator("rate", mode="before")
    @classmethod
    def _rate_key_or_literal(cls, value: object) -> object:
        return _coerce_rate(value)

    @model_validator(mode="after")
    def _recoverable_fraction(self) -> "VatSpec":
        if not (Decimal(0) <= self.recoverable <= Decimal(1)):
            raise ValueError("recoverable must be a fraction in [0, 1]")
        return self


class TaxRegime(CashKitModel):
    """Entity-level tax schedule. VAT is one instance; the decomposition (rate
    at the line, schedule at the entity) also fits IRAP, IRES, contributions.

    Materializes as synthetic derived items (``_tax:<id>:liability``,
    ``_tax:<id>:credit``) injected into the dependency graph before
    condensation (ADR-0005) — a Phase 6 concern; only the shape lives here.
    """

    id: Annotated[str, Field(pattern=rf"^{IDENT_RE}$", max_length=64)]
    accumulates: str
    measure: Literal["accrual", "cash"] = "accrual"
    periodicity: Literal["monthly", "quarterly", "annual"]
    payment_offset: Duration
    surcharge: FiniteDecimal = Field(default=Decimal(0))
    credit_handling: Literal["carry", "refund_annual"] = "carry"
    annual_adjustment_month: int | None = Field(default=None, ge=1, le=12)
