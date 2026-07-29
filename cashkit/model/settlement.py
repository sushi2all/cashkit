"""Settlement terms (PRD §4.4).

List-level business rules — shares summing to exactly 1 (CK-E004), the
share/amount mixing and multiple-``remainder`` rules (CK-E005) — are validated
at ``add_item()`` time by the SDK and reported as diagnostics, not enforced
here (they are user-facing failure modes, and errors are Diagnostic objects).
Per-term structural invariants are enforced here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .primitives import CashKitModel, Duration, FiniteDecimal, Money

__all__ = ["DueTerm", "Settlement"]


class DueTerm(CashKitModel):
    """One settlement leg.

    Exactly one of ``share``, ``amount`` or ``remainder=True`` characterizes a
    term. ``withholding`` (ritenuta d'acconto) reduces the cash moved at this
    leg only; the counter-leg is modelled manually (ADR-0005, CK-W004).
    """

    share: FiniteDecimal | None = None
    amount: Money | None = None
    remainder: bool = False
    offset: Duration
    basis: Literal["accrual", "period_end", "month_end"] = "accrual"
    adjust: Literal["none", "prev", "next"] = "none"
    withholding: FiniteDecimal = Field(default=Decimal(0))

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> "DueTerm":
        kinds = sum(
            (self.share is not None, self.amount is not None, self.remainder)
        )
        if kinds != 1:
            raise ValueError(
                "DueTerm requires exactly one of share, amount, or remainder=True"
            )
        if not (Decimal(0) <= self.withholding <= Decimal(1)):
            raise ValueError("withholding must be a fraction in [0, 1]")
        return self


class Settlement(CashKitModel):
    """How an accrued amount turns into cash. Empty ``due`` = never settles
    (accrual only). ``due`` is the only representation — there is no ``lag``
    shortcut field, by design.
    """

    due: list[DueTerm] = Field(default_factory=list)

    @classmethod
    def immediate(cls) -> "Settlement":
        """Full amount due at accrual date. Returns a Settlement; no diagnostics."""
        return cls(due=[DueTerm(share=Decimal(1), offset="0d")])

    @classmethod
    def net(cls, days: int) -> "Settlement":
        """Full amount due ``days`` days after accrual. Returns a Settlement;
        no diagnostics. Raises ``ValueError`` on negative days (programmer error).
        """
        if days < 0:
            raise ValueError("net(days) requires days >= 0")
        return cls(due=[DueTerm(share=Decimal(1), offset=f"{days}d")])

    @classmethod
    def split(cls, legs: list[tuple[Decimal, str]]) -> "Settlement":
        """Share-based split: ``[(0.3, "0d"), (0.7, "90d")]``. Returns a
        Settlement; no diagnostics. The shares-sum-to-1 rule is validated at
        ``add_item()`` time (CK-E004), not here.
        """
        return cls(
            due=[DueTerm(share=Decimal(share), offset=offset) for share, offset in legs]
        )
