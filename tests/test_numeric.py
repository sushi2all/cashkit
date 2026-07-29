"""The int64 money core: exactness, rounding, overflow (PRD §5.3, ADR-0002/0003).

These tests pin the arithmetic the whole engine stands on. The Decimal
cross-checks matter because the reference engine rounds with
``Decimal.quantize`` while the vectorized engine rounds with integer division —
byte-equality between the two engines is only possible if those agree here.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cashkit.engine.numeric import (
    INT64_MAX,
    MINOR_SCALE,
    MoneyOverflowError,
    RoundingPolicy,
    escalation_factor,
    from_minor,
    mul_ratio,
    mul_ratio_array,
    ratio_of,
    round_div,
    round_div_array,
    to_minor,
)

QUANTUM = Decimal("0.0001")


def test_minor_unit_roundtrip_is_exact() -> None:
    for text in ("0", "1", "-1", "0.0001", "-0.0001", "1234.5678", "-9999.9999", "9E14"):
        value = Decimal(text)
        assert from_minor(to_minor(value)) == value.quantize(QUANTUM)


def test_more_than_four_decimals_is_programmer_error() -> None:
    with pytest.raises(ValueError):
        to_minor(Decimal("0.00001"))


def test_overflow_raises_rather_than_wrapping() -> None:
    huge = np.array([INT64_MAX // 2], dtype=np.int64)
    with pytest.raises(MoneyOverflowError):
        mul_ratio_array(huge, 5, 1, RoundingPolicy.HALF_UP)


def test_half_up_is_sign_symmetric() -> None:
    """A credit note must round as the mirror image of the invoice it reverses."""
    assert round_div(5, 10, RoundingPolicy.HALF_UP) == 1
    assert round_div(-5, 10, RoundingPolicy.HALF_UP) == -1
    assert round_div(15, 10, RoundingPolicy.HALF_UP) == 2
    assert round_div(-15, 10, RoundingPolicy.HALF_UP) == -2


def test_half_even_breaks_ties_to_even() -> None:
    assert round_div(5, 10, RoundingPolicy.HALF_EVEN) == 0
    assert round_div(15, 10, RoundingPolicy.HALF_EVEN) == 2
    assert round_div(25, 10, RoundingPolicy.HALF_EVEN) == 2
    assert round_div(-5, 10, RoundingPolicy.HALF_EVEN) == 0
    assert round_div(-15, 10, RoundingPolicy.HALF_EVEN) == -2


@given(
    numerator=st.integers(min_value=-(10**18), max_value=10**18),
    denominator=st.integers(min_value=1, max_value=10**12),
    policy=st.sampled_from(list(RoundingPolicy)),
)
@settings(max_examples=400, deadline=None)
def test_round_div_agrees_with_decimal_quantize(
    numerator: int, denominator: int, policy: RoundingPolicy
) -> None:
    """The integer path and the Decimal path must never disagree — this is the
    contract the dual-engine gate rests on."""
    mode = ROUND_HALF_UP if policy is RoundingPolicy.HALF_UP else ROUND_HALF_EVEN
    expected = int(
        (Decimal(numerator) / Decimal(denominator)).quantize(Decimal(1), rounding=mode)
    )
    assert round_div(numerator, denominator, policy) == expected


@given(
    values=st.lists(
        st.integers(min_value=-(10**12), max_value=10**12), min_size=1, max_size=25
    ),
    denominator=st.integers(min_value=1, max_value=10**9),
    policy=st.sampled_from(list(RoundingPolicy)),
)
@settings(max_examples=200, deadline=None)
def test_round_div_array_matches_the_scalar_path(
    values: list[int], denominator: int, policy: RoundingPolicy
) -> None:
    array = np.array(values, dtype=np.int64)
    got = round_div_array(array, denominator, policy)
    want = [round_div(value, denominator, policy) for value in values]
    assert got.tolist() == want


@given(
    minor=st.integers(min_value=-(10**14), max_value=10**14),
    rate=st.decimals(
        min_value=Decimal("-5"), max_value=Decimal("5"), places=6, allow_nan=False
    ),
)
@settings(max_examples=300, deadline=None)
def test_mul_ratio_matches_exact_decimal_multiplication(minor: int, rate: Decimal) -> None:
    numerator, denominator = ratio_of(rate)
    got = mul_ratio(minor, numerator, denominator, RoundingPolicy.HALF_UP)
    want = int(
        (Decimal(minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )
    assert got == want


def test_ratio_of_is_exact_for_authored_rates() -> None:
    assert ratio_of(Decimal("0.22")) == (11, 50)
    assert ratio_of(Decimal("0.0725")) == (29, 400)
    assert ratio_of(Decimal("-1.5")) == (-3, 2)


def test_escalation_factor_is_exact_not_approximated() -> None:
    """ADR-0002: the factor table is Decimal and exact — no float, no drift."""
    assert escalation_factor(Decimal("0.05"), 0) == Decimal(1)
    assert escalation_factor(Decimal("0.05"), 1) == Decimal("1.05")
    assert escalation_factor(Decimal("0.03"), 2) == Decimal("1.0609")
    assert escalation_factor(Decimal("0.03"), 3) == Decimal("1.092727")
    # A 4 dp rate to the 30th power has 120 exact decimal places. The default
    # Decimal context (28 digits) would silently truncate it; the factor table
    # must not, or the two engines would disagree on a long-horizon escalation.
    factor = escalation_factor(Decimal("0.0725"), 30)
    assert -factor.as_tuple().exponent == 120
    with localcontext() as ctx:
        ctx.prec = 200
        assert factor == Decimal("1.0725") ** 30


@given(
    rate=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=4, allow_nan=False),
    exponent=st.integers(min_value=0, max_value=12),
)
@settings(max_examples=200, deadline=None)
def test_escalation_factor_equals_repeated_multiplication(
    rate: Decimal, exponent: int
) -> None:
    with localcontext() as ctx:
        # Exact repeated multiplication: a 4 dp rate to the 12th power needs 48
        # decimal places, far more than the default 28-digit context allows.
        ctx.prec = 200
        expected = Decimal(1)
        for _ in range(exponent):
            expected = expected * (Decimal(1) + rate)
    assert escalation_factor(rate, exponent) == expected


def test_escalation_exponent_is_bounded() -> None:
    with pytest.raises(ValueError):
        escalation_factor(Decimal("0.05"), 10_000)
    with pytest.raises(ValueError):
        escalation_factor(Decimal("0.05"), -1)


def test_mul_ratio_array_widens_instead_of_wrapping() -> None:
    """A product beyond int64 is computed in Python ints, then narrowed — the
    result is exact and nothing silently wraps."""
    values = np.array([10**17, -(10**17)], dtype=np.int64)
    got = mul_ratio_array(values, 10**6, 10**6 * 2, RoundingPolicy.HALF_UP)
    assert got.tolist() == [10**17 // 2, -(10**17) // 2]


def test_minor_scale_is_four_decimal_places() -> None:
    assert MINOR_SCALE == 10_000
