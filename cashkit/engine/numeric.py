"""Exact integer money arithmetic (PRD §5.3, ADR-0002, ADR-0003).

The engine core holds money as **int64 minor units at 4 decimal places**.
Addition and subtraction are exact. Every multiplication by a rate is a
``scale -> multiply -> divide`` with one declared rounding policy, applied at a
declared boundary and never implicitly.

Two properties are load-bearing and are the reason this module exists:

* **No float anywhere.** Rates arrive as ``Decimal`` and are converted to exact
  integer ratios; escalation factors ``(1+r)**n`` are computed in ``Decimal`` at
  a precision that makes them exact (ADR-0002), memoized per ``(rate, n)`` pair.
* **No silent wraparound.** Intermediates run through arbitrary-precision
  Python ints whenever an int64 product could overflow, and results that leave
  the int64 range raise :class:`MoneyOverflowError` rather than truncating.

The reference engine (``cashkit.reference``) implements the same rounding
*semantics* in ``Decimal`` with an independent implementation; the dual-engine
gate proves the two agree byte-for-byte.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from enum import Enum
from functools import lru_cache

import numpy as np

__all__ = [
    "INT64_MAX",
    "MINOR_SCALE",
    "MINOR_SCALE_EXPONENT",
    "MoneyOverflowError",
    "RoundingPolicy",
    "escalation_factor",
    "from_minor",
    "mul_elementwise",
    "mul_ratio",
    "mul_ratio_array",
    "ratio_of",
    "round_div",
    "round_div_array",
    "round_div_elementwise",
    "scale_numerator",
    "to_minor",
]

#: Decimal places held by the int64 core (PRD §5.3).
MINOR_SCALE_EXPONENT = 4
#: One currency unit expressed in minor units.
MINOR_SCALE = 10**MINOR_SCALE_EXPONENT

INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)


class MoneyOverflowError(ArithmeticError):
    """An int64 money value would wrap around.

    Raised, never swallowed: silent truncation is precisely the numerical-error
    class this engine exists to prevent. The model layer bounds authored
    amounts at 9x10^14 units (DECISIONS D-P1-06), so reaching this is either a
    pathological book or a programmer error.
    """


class RoundingPolicy(str, Enum):
    """Declared rounding policy for every rate multiplication and division.

    ``HALF_UP`` rounds halves away from zero (matching ``decimal.ROUND_HALF_UP``)
    and is the default; ``HALF_EVEN`` is banker's rounding. The policy is fixed
    for a whole run — mixing policies inside one run would make the canonical
    rounding order (ADR-0003) meaningless.
    """

    HALF_UP = "half_up"
    HALF_EVEN = "half_even"


# --------------------------------------------------------------------------- #
# Boundary conversion
# --------------------------------------------------------------------------- #


def to_minor(value: Decimal) -> int:
    """Convert a boundary ``Decimal`` to int64 minor units, exactly.

    Returns the integer number of minor units. Raises ``ValueError`` if the
    value carries more than 4 decimal places (the model layer rejects those at
    parse time, so this is programmer error) and :class:`MoneyOverflowError` if
    it leaves the int64 range. Produces no diagnostics.
    """
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError(f"non-finite Decimal cannot be money: {value}")
    scaled = value.scaleb(MINOR_SCALE_EXPONENT)
    minor = int(scaled)
    if Decimal(minor) != scaled:
        raise ValueError(
            f"money value {value} carries more than {MINOR_SCALE_EXPONENT} decimal "
            "places; the boundary must reject it before it reaches the engine"
        )
    if not INT64_MIN <= minor <= INT64_MAX:
        raise MoneyOverflowError(f"money value {value} exceeds the int64 minor-unit range")
    return minor


def from_minor(minor: int) -> Decimal:
    """Convert int64 minor units back to a boundary ``Decimal`` at 4 dp.

    Returns a ``Decimal`` with exactly 4 decimal places; produces no diagnostics.
    """
    return Decimal(int(minor)).scaleb(-MINOR_SCALE_EXPONENT)


def ratio_of(value: Decimal) -> tuple[int, int]:
    """Return ``value`` as an exact integer ratio ``(numerator, denominator>0)``.

    Exactness matters: a rate that entered as ``Decimal("0.0725")`` multiplies
    money as ``725/10000``, never as an approximation. Produces no diagnostics;
    raises ``ValueError`` for a non-finite Decimal.
    """
    if not value.is_finite():
        raise ValueError(f"rate must be finite, got {value}")
    numerator, denominator = value.as_integer_ratio()
    if denominator < 0:  # pragma: no cover - Decimal never produces this
        numerator, denominator = -numerator, -denominator
    return numerator, denominator


# --------------------------------------------------------------------------- #
# Rounding
# --------------------------------------------------------------------------- #


def _split_rounding(remainder: int, denominator: int, quotient: int, policy: RoundingPolicy) -> bool:
    half_floor = denominator // 2
    strictly_over = remainder > half_floor
    tie = denominator % 2 == 0 and remainder == half_floor
    if policy is RoundingPolicy.HALF_UP:
        return strictly_over or tie
    return strictly_over or (tie and quotient % 2 == 1)


def round_div(numerator: int, denominator: int, policy: RoundingPolicy) -> int:
    """Divide two integers, rounding under ``policy``, sign-symmetrically.

    Returns the rounded integer quotient. Rounding is applied to the magnitude
    and the sign reapplied, so a credit note rounds as the mirror image of the
    invoice it reverses. Raises ``ZeroDivisionError`` on a zero denominator
    (callers mask division by zero before reaching here). Produces no diagnostics.
    """
    if denominator == 0:
        raise ZeroDivisionError("round_div denominator must be non-zero")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    negative = numerator < 0
    magnitude = -numerator if negative else numerator
    quotient, remainder = divmod(magnitude, denominator)
    if _split_rounding(remainder, denominator, quotient, policy):
        quotient += 1
    return -quotient if negative else quotient


def mul_ratio(minor: int, numerator: int, denominator: int, policy: RoundingPolicy) -> int:
    """Multiply minor units by the exact ratio ``numerator/denominator``.

    Returns the rounded product in minor units — one rounding, at this declared
    boundary. Intermediates are Python ints, so nothing wraps. Produces no
    diagnostics.
    """
    return round_div(minor * numerator, denominator, policy)


# --------------------------------------------------------------------------- #
# Vectorized forms
# --------------------------------------------------------------------------- #


def _as_exact(values: np.ndarray, factor: int) -> np.ndarray:
    """Return ``values * factor`` without wraparound, widening to Python ints
    only when the int64 product could overflow."""
    if values.dtype == object:
        return values * factor
    if values.size == 0:
        return values.astype(np.int64) * np.int64(0)
    peak = int(np.abs(values).max())
    if peak * abs(factor) <= INT64_MAX and abs(factor) <= INT64_MAX:
        return values * np.int64(factor)
    return values.astype(object) * factor


def _narrow(values: np.ndarray) -> np.ndarray:
    """Cast an exact (possibly object-dtype) integer array back to int64,
    raising rather than wrapping."""
    if values.dtype != object:
        return values.astype(np.int64, copy=False)
    if values.size:
        peak = max(abs(int(v)) for v in values.ravel())
        if peak > INT64_MAX:
            raise MoneyOverflowError(
                f"money magnitude {peak} minor units exceeds the int64 range"
            )
    return np.array([int(v) for v in values.ravel()], dtype=np.int64).reshape(values.shape)


def round_div_array(
    numerators: np.ndarray, denominator: int, policy: RoundingPolicy
) -> np.ndarray:
    """Elementwise :func:`round_div` over an integer array.

    Returns an ``int64`` array. Raises ``ZeroDivisionError`` on a zero
    denominator and :class:`MoneyOverflowError` if a result leaves int64.
    Produces no diagnostics.
    """
    if denominator == 0:
        raise ZeroDivisionError("round_div_array denominator must be non-zero")
    if denominator < 0:
        numerators, denominator = -numerators, -denominator
    negative = numerators < 0
    magnitude = np.where(negative, -numerators, numerators)
    quotient = magnitude // denominator
    remainder = magnitude - quotient * denominator
    half_floor = denominator // 2
    bump = remainder > half_floor
    if denominator % 2 == 0:
        tie = remainder == half_floor
        if policy is RoundingPolicy.HALF_UP:
            bump = bump | tie
        else:
            bump = bump | (tie & (quotient % 2 == 1))
    quotient = quotient + bump.astype(quotient.dtype)
    return _narrow(np.where(negative, -quotient, quotient))


def mul_ratio_array(
    values: np.ndarray, numerator: int, denominator: int, policy: RoundingPolicy
) -> np.ndarray:
    """Elementwise :func:`mul_ratio` over an int64 minor-unit array.

    Returns an ``int64`` array; intermediates widen to Python ints when an
    int64 product could overflow. Produces no diagnostics.
    """
    if numerator == 0:
        return np.zeros(values.shape, dtype=np.int64)
    if numerator == denominator:
        return values.astype(np.int64, copy=False)
    return round_div_array(_as_exact(values, numerator), denominator, policy)


def round_div_elementwise(
    numerators: np.ndarray, denominators: np.ndarray, policy: RoundingPolicy
) -> np.ndarray:
    """Elementwise integer division with a per-element denominator.

    A zero denominator yields ``0`` rather than raising: formula division is
    masked-safe by design, because both ``where`` branches always evaluate
    (PRD §5.4). The caller decides whether that zero deserves ``CK-W005``.

    Returns an ``int64`` array. Produces no diagnostics.
    """
    zero = denominators == 0
    safe = np.where(zero, 1, denominators)
    negative = (numerators < 0) != (safe < 0)
    top = np.where(numerators < 0, -numerators, numerators)
    bottom = np.where(safe < 0, -safe, safe)
    quotient = top // bottom
    remainder = top - quotient * bottom
    half_floor = bottom // 2
    bump = remainder > half_floor
    tie = (bottom % 2 == 0) & (remainder == half_floor)
    if policy is RoundingPolicy.HALF_UP:
        bump = bump | tie
    else:
        bump = bump | (tie & (quotient % 2 == 1))
    quotient = quotient + bump.astype(quotient.dtype)
    signed = np.where(negative, -quotient, quotient)
    return _narrow(np.where(zero, 0, signed))


def mul_elementwise(
    left: np.ndarray, right: np.ndarray, policy: RoundingPolicy
) -> np.ndarray:
    """Multiply two 4 dp money columns, rounding once to 4 dp.

    Returns an ``int64`` array; intermediates widen to Python ints when the
    product could leave int64. Produces no diagnostics.
    """
    product = _exact_product(left, right)
    return round_div_array(product, MINOR_SCALE, policy)


def _exact_product(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    if left.size == 0:
        return left.astype(np.int64)
    peak_left = int(np.abs(left).max())
    peak_right = int(np.abs(right).max())
    if peak_left * peak_right <= INT64_MAX:
        return left * right
    return left.astype(object) * right.astype(object)


def scale_numerator(values: np.ndarray, factor: int) -> np.ndarray:
    """Return ``values * factor`` exactly, widening past int64 when needed.

    Used to build division numerators, where the ``x10000`` scaling routinely
    exceeds int64 for large balances. Produces no diagnostics.
    """
    return _as_exact(values, factor)


#: Headroom factor for additions. Every *stored* column is checked against
#: ``INT64_MAX // ADDITION_HEADROOM``, which makes any sum of up to
#: ADDITION_HEADROOM columns — or any chain of that many additions inside one
#: formula — provably safe without a check per operation. Formula nesting is
#: capped far below this (``MAX_AST_DEPTH``), and an ``agg()`` over more members
#: than this is checked explicitly. One O(1) check per column write replaces one
#: reduction per add, which is the difference between meeting the performance
#: budget and missing it by 5x.
ADDITION_HEADROOM = 4096

#: Largest magnitude a stored money column may hold, in minor units.
COLUMN_CEILING = INT64_MAX // ADDITION_HEADROOM


def check_column(values: np.ndarray, what: str) -> np.ndarray:
    """Verify a column stays inside the addition-safe ceiling.

    Returns ``values`` unchanged, or raises :class:`MoneyOverflowError`. Checked
    on every stored column so downstream additions need no per-operation guard.
    Produces no diagnostics.
    """
    if values.size:
        peak = int(np.abs(values).max())
        if peak > COLUMN_CEILING:
            raise MoneyOverflowError(
                f"{what} reaches {peak} minor units, beyond the addition-safe "
                f"ceiling of {COLUMN_CEILING}"
            )
    return values


def guard_total(peak: int, count: int, what: str) -> None:
    """Raise if summing ``count`` values of magnitude ``peak`` could overflow.

    Used where the headroom argument does not apply — a running total over a long
    horizon, or an aggregate over more members than the headroom covers.
    Produces no diagnostics.
    """
    if peak * max(1, count) > INT64_MAX:
        raise MoneyOverflowError(
            f"{what} would reach {peak * count} minor units, beyond int64"
        )


# --------------------------------------------------------------------------- #
# Escalation factor table (ADR-0002)
# --------------------------------------------------------------------------- #

#: An escalation exponent beyond this is refused rather than allowed to build a
#: multi-thousand-digit factor. 200 escalation steps is far outside any horizon
#: a cash forecast models.
MAX_ESCALATION_EXPONENT = 200


@lru_cache(maxsize=4096)
def escalation_factor(rate: Decimal, exponent: int) -> Decimal:
    """Return ``(1 + rate) ** exponent`` computed **exactly** in ``Decimal``.

    ADR-0002: escalation factors are Decimal, computed once per distinct
    ``(rate, exponent)`` pair and applied as an exact ratio, so no float ever
    touches the money path. The working precision is sized to the exact digit
    count of the result, so the returned Decimal is the true value, not a
    rounded one.

    Returns a ``Decimal``; raises ``ValueError`` for a negative exponent or one
    beyond :data:`MAX_ESCALATION_EXPONENT` (programmer error). Produces no
    diagnostics.
    """
    if exponent < 0:
        raise ValueError("escalation exponent must be >= 0")
    if exponent > MAX_ESCALATION_EXPONENT:
        raise ValueError(
            f"escalation exponent {exponent} exceeds the supported maximum "
            f"{MAX_ESCALATION_EXPONENT}"
        )
    base = Decimal(1) + rate
    if exponent == 0:
        return Decimal(1)
    digits = len(base.as_tuple().digits)
    places = max(0, -int(base.as_tuple().exponent))
    with localcontext() as ctx:
        # An exact power of a finite decimal needs at most digits*exponent
        # significant digits; the guard makes the context comfortably exact.
        ctx.prec = max(digits, places) * exponent + digits + 10
        return base**exponent
