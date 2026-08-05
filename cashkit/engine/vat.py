"""Per-line VAT: the last step of the canonical rounding order (PRD §4.5, ADR-0005).

All authored amounts are VAT-exclusive. The engine computes VAT per line from
the item's (or event's) :class:`~cashkit.model.VatSpec`, grosses up the
settlement cash leg — a 1,000 invoice at 22% collects 1,220 — and hands the VAT
component to the :class:`~cashkit.model.TaxRegime` schedule, which turns it into
a payment. There is no VAT-inclusive authoring mode.

**Position in the chain.** ADR-0003 fixes the order as base → escalation →
probability → settlement share split → withholding → **VAT**. VAT is therefore
computed from the split legs' base, not from the amount net of withholding:
withholding and VAT are two independent adjustments to the same taxable amount,
and a ritenuta d'acconto has never reduced the VAT on an invoice. On a 1,000
invoice at 22% VAT with a 20% ritenuta the customer pays 1,020 — 1,000 + 220
VAT − 200 withheld — and both engines reproduce exactly that.

What is shared between the two engines is the *classification*: which treatment
produces VAT, which side of the ledger it lands on, and how a line's VAT is
allocated across its cash legs. The arithmetic is duplicated, as everywhere
else — the vectorized engine multiplies int64 columns by exact integer ratios,
the reference multiplies ``Decimal``s and quantizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from cashkit.model import Diagnostic, Item, VatSpec
from cashkit.model.diagnostics import make_diagnostic

from .numeric import (
    INT64_MAX,
    RoundingPolicy,
    mul_ratio_array,
    ratio_of,
    round_div_elementwise,
)

__all__ = [
    "TREATMENTS",
    "VatColumns",
    "VatRule",
    "VatTreatment",
    "allocate_across_legs",
    "line_vat_columns",
    "output_mask",
    "resolve_vat",
    "treatment_of",
]


@dataclass(frozen=True)
class VatTreatment:
    """What a ``VatSpec.treatment`` actually does to cash and to the regime.

    ``active`` is the only thing separating the zero-rated treatments from each
    other in v1: exempt, out-of-scope, export and split payment all produce no
    VAT cash leg and no regime liability, and differ only in reporting, which
    this engine does not do. They are kept as distinct values because a return
    form does distinguish them and the authored intent is worth keeping.
    """

    #: Produces VAT at all. False for exempt / out_of_scope / export /
    #: split_payment — for split payment because the buyer remits the VAT
    #: directly to the state, so the supplier never owes it (PRD §7.2).
    active: bool
    #: VAT rides the settlement cash leg (a 1,000 invoice collects 1,220).
    collects_cash: bool
    #: Reverse charge: the buyer self-accounts, booking the same VAT as output
    #: and as (recoverable) input, so a fully deductible purchase nets to zero
    #: and a partly deductible one leaves the non-recoverable part payable.
    self_assess: bool


TREATMENTS: dict[str, VatTreatment] = {
    "standard": VatTreatment(active=True, collects_cash=True, self_assess=False),
    "reverse_charge": VatTreatment(active=True, collects_cash=False, self_assess=True),
    "exempt": VatTreatment(active=False, collects_cash=False, self_assess=False),
    "out_of_scope": VatTreatment(active=False, collects_cash=False, self_assess=False),
    "export": VatTreatment(active=False, collects_cash=False, self_assess=False),
    "split_payment": VatTreatment(active=False, collects_cash=False, self_assess=False),
}


@dataclass(frozen=True)
class VatRule:
    """A resolved VAT specification: rate and recoverability as exact Decimals."""

    rate: Decimal
    recoverable: Decimal
    treatment: VatTreatment
    #: ``Item.direction`` — display-only for storage, but authoritative here:
    #: it says whether a line is a sale or a purchase even when a credit note
    #: flips the sign of the amount.
    direction: str | None = None

    @property
    def inert(self) -> bool:
        """True when this rule can never produce a number. No diagnostics."""
        return not self.treatment.active or self.rate == 0


#: A rule that produces nothing — the absence of a VatSpec.
NO_VAT = VatRule(
    rate=Decimal(0), recoverable=Decimal(0), treatment=TREATMENTS["out_of_scope"]
)


def resolve_vat(
    spec: VatSpec | None,
    params: dict[str, Decimal],
    *,
    direction: str | None = None,
    item_id: str | None = None,
) -> tuple[VatRule, Diagnostic | None]:
    """Resolve a ``VatSpec`` against the book's params.

    ``VatSpec.rate`` is a param key by default (``"vat_standard"``) so that VAT
    rates are sweepable levers rather than literals buried in items (PRD §4.1).
    Returns ``(rule, diagnostic)``; the diagnostic is ``CK-E008`` for an unknown
    param key, in which case the rule is inert — a book that references a rate
    it never defined must not quietly charge 0% VAT and call it an answer, so
    the caller marks the item broken.
    """
    if spec is None:
        return NO_VAT, None
    rate = spec.rate
    if isinstance(rate, str):
        if rate not in params:
            return NO_VAT, make_diagnostic(
                "CK-E008",
                item_id=item_id,
                field="vat.rate",
                key=rate,
                referrer=f"item {item_id} VAT rate",
            )
        rate = params[rate]
    return (
        VatRule(
            rate=rate,
            recoverable=spec.recoverable,
            treatment=TREATMENTS[spec.treatment],
            direction=direction,
        ),
        None,
    )


def rule_for(item: Item, params: dict[str, Decimal]) -> tuple[VatRule, Diagnostic | None]:
    """Resolve an item's own ``VatSpec``. See :func:`resolve_vat`."""
    return resolve_vat(
        item.vat, params, direction=item.direction, item_id=item.id
    )


# --------------------------------------------------------------------------- #
# Which side of the return a line lands on
# --------------------------------------------------------------------------- #


def is_output_side(rule: VatRule, amount_positive: bool) -> bool:
    """Whether one line books output (sales) VAT rather than input (purchase) VAT.

    ``Item.direction`` decides when it is set: a credit note against a sale is
    still a sale, and classifying it by its negative sign would move it to the
    input side and reclaim VAT that was never paid. Without a direction the sign
    is all there is, which is right for the ordinary case where revenue is
    positive and costs are negative. No diagnostics.
    """
    if rule.direction == "in":
        return True
    if rule.direction == "out":
        return False
    return amount_positive


def output_mask(rule: VatRule, amounts: np.ndarray) -> np.ndarray:
    """Vectorized :func:`is_output_side` over an amount array. No diagnostics."""
    if rule.direction == "in":
        return np.ones(amounts.shape, dtype=bool)
    if rule.direction == "out":
        return np.zeros(amounts.shape, dtype=bool)
    return amounts >= 0


# --------------------------------------------------------------------------- #
# Allocation of a line's VAT across its cash legs
# --------------------------------------------------------------------------- #


def allocate_across_legs(
    totals: np.ndarray, legs: list[np.ndarray], policy: RoundingPolicy
) -> list[np.ndarray]:
    """Split a per-line total across its settlement legs, in proportion.

    The last leg absorbs the rounding residual, so the parts sum to the total
    **exactly** — the same residual-absorption rule ADR-0003 fixes for the share
    split, and the reason an invoice's VAT legs always add up to the VAT the
    invoice states.

    Proportions are taken against the sum of the legs rather than against the
    accrual: when a fixed-amount term clamps the remainder to zero the legs no
    longer sum to the accrual, and dividing by the accrual would hand a leg more
    VAT than the line carries.

    Returns one array per leg. Produces no diagnostics.
    """
    if not legs:
        return []
    if len(legs) == 1:
        return [totals.copy()]
    denominator = legs[0].astype(np.int64, copy=True)
    for leg in legs[1:]:
        denominator = denominator + leg
    parts: list[np.ndarray] = []
    running = np.zeros(totals.shape, dtype=np.int64)
    for leg in legs[:-1]:
        part = round_div_elementwise(_exact_mul(totals, leg), denominator, policy)
        running = running + part
        parts.append(part)
    parts.append(np.where(denominator == 0, 0, totals - running).astype(np.int64))
    return parts


def _exact_mul(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """``left * right`` without wraparound, widening only when it could."""
    if left.size == 0:
        return left.astype(np.int64)
    peak = int(np.abs(left).max()) * int(np.abs(right).max())
    if peak <= INT64_MAX:
        return left * right
    return left.astype(object) * right.astype(object)


# --------------------------------------------------------------------------- #
# Per-item VAT columns
# --------------------------------------------------------------------------- #


@dataclass
class VatColumns:
    """One item's VAT, placed both ways so either tax point can be served.

    ``*_accrual`` places a line's VAT in the period the line accrues — the
    Italian default, where you owe VAT on the invoice date and pay the state
    before the customer pays you. ``*_cash`` places it in the periods its cash
    legs land, which is ``IVA per cassa``. Both are computed always; the regime
    picks by ``TaxRegime.measure``.

    Signs are the contributions to the net liability: output VAT on a sale is
    positive, input VAT on a purchase negative, and a credit note is the mirror
    of the line it reverses. The regime's net is simply ``output + input``.
    """

    output_accrual: np.ndarray
    input_accrual: np.ndarray
    output_cash: np.ndarray
    input_cash: np.ndarray

    @classmethod
    def zeros(cls, length: int) -> "VatColumns":
        """Four zero columns of ``length`` periods. No diagnostics."""
        return cls(
            output_accrual=np.zeros(length, dtype=np.int64),
            input_accrual=np.zeros(length, dtype=np.int64),
            output_cash=np.zeros(length, dtype=np.int64),
            input_cash=np.zeros(length, dtype=np.int64),
        )

    def net_accrual(self) -> np.ndarray:
        """Net VAT per period on the accrual tax point. No diagnostics."""
        return self.output_accrual + self.input_accrual

    def net_cash(self) -> np.ndarray:
        """Net VAT per period on the cash tax point. No diagnostics."""
        return self.output_cash + self.input_cash

    def total(self) -> int:
        """Whole-horizon net on the accrual tax point, in minor units."""
        return int(self.net_accrual().sum())


def line_vat_columns(
    rule: VatRule, amounts: np.ndarray, policy: RoundingPolicy
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decompose a batch of net line amounts into ``(cash_vat, output, input)``.

    All three are signed and in minor units, aligned with ``amounts``.
    ``cash_vat`` is what grosses up the settlement legs; ``output`` and
    ``input`` are the contributions to the regime's net liability. Rounding
    happens twice at most — once for the line VAT and once more for partial
    deductibility — and never implicitly. Produces no diagnostics.
    """
    zero = np.zeros(amounts.shape, dtype=np.int64)
    if rule.inert or amounts.size == 0:
        return zero, zero.copy(), zero.copy()

    numerator, denominator = ratio_of(rule.rate)
    line = mul_ratio_array(amounts, numerator, denominator, policy)
    outputs = output_mask(rule, amounts)

    if rule.recoverable == Decimal(1):
        deductible = line
    elif rule.recoverable == Decimal(0):
        deductible = zero
    else:
        rec_num, rec_den = ratio_of(rule.recoverable)
        deductible = mul_ratio_array(line, rec_num, rec_den, policy)

    input_vat = np.where(outputs, 0, deductible).astype(np.int64)
    if rule.treatment.self_assess:
        # The buyer books the same VAT as output and as recoverable input.
        output_vat = np.where(outputs, 0, -line).astype(np.int64)
        cash_vat = zero.copy()
    else:
        output_vat = np.where(outputs, line, 0).astype(np.int64)
        cash_vat = line if rule.treatment.collects_cash else zero.copy()
    return cash_vat.astype(np.int64), output_vat, input_vat
