"""The canonical money serializer — one definition, used by every payload.

SPEC §3 says "Money is Decimal strings, 2dp for display, never floats". The
engine's money is exact at 4dp (int64 minor units). Those two statements
disagree about which number travels, and the ambiguity procedure
(PROMPT §When you hit ambiguity, rule 1) says to preserve exactness.

So a money figure travels as **both**, in one object:

    {"exact": "-3000.0000", "display": "-3000.00"}

* ``exact`` is the engine value, lossless, always exactly four decimal places.
* ``display`` is that same value quantized to two, with the engine's own
  banker's rounding, so the client renders a string it did not compute.

This satisfies both halves of SPEC §3 and both non-negotiables it serves: the
service never truncates an engine number, and the client never does money
arithmetic to show one. Recorded as D-MLP-06.

Every money figure in every payload goes through :func:`money`. The SDK-parity
test calls this same function on the value it read straight from the SDK, so
"string-equal to the canonically serialized value" has exactly one meaning.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

#: The engine's money scale: int64 minor units at four decimal places.
ENGINE_DP = 4
DISPLAY_DP = 2

_EXACT_QUANTUM = Decimal(1).scaleb(-ENGINE_DP)  # Decimal('0.0001')
_DISPLAY_QUANTUM = Decimal(1).scaleb(-DISPLAY_DP)  # Decimal('0.01')

#: Engine columns are int64 minor units at 4dp; dividing by this recovers the
#: authored Decimal exactly, with no float on the way through.
MINOR_UNIT_SCALE = Decimal(10) ** ENGINE_DP


class Money(BaseModel):
    """One money figure, in the only two forms the API ever ships."""

    model_config = ConfigDict(frozen=True)

    exact: str
    display: str


def to_decimal(value: Any) -> Decimal:
    """Coerce an SDK money value to :class:`~decimal.Decimal`, refusing floats.

    A float in a money path is the failure this project exists to prevent, so
    it raises rather than rounding. That is programmer error, not a user-facing
    diagnostic, which is why it is an exception (CLAUDE.md, PRD §10.1).
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int; never a money value
        raise TypeError(f"not a money value: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(
        f"money must be Decimal, int or str, not {type(value).__name__} "
        f"({value!r}) — no float ever enters the money path"
    )


def from_minor_units(units: int) -> Decimal:
    """Recover a :class:`~decimal.Decimal` from an int64 engine column value."""
    return Decimal(int(units)) / MINOR_UNIT_SCALE


def exact_str(value: Any) -> str:
    """The canonical lossless string form: always exactly four decimal places."""
    dec = to_decimal(value)
    if -dec.as_tuple().exponent > ENGINE_DP:  # type: ignore[operator]
        raise ValueError(
            f"money value {dec} carries more than {ENGINE_DP} decimal places; "
            "the service will not silently round an engine number"
        )
    return f"{dec.quantize(_EXACT_QUANTUM, rounding=ROUND_HALF_EVEN):f}"


def display_str(value: Any) -> str:
    """The canonical 2dp display string, rounded the way the engine rounds."""
    dec = to_decimal(value)
    return f"{dec.quantize(_DISPLAY_QUANTUM, rounding=ROUND_HALF_EVEN):f}"


def money(value: Any) -> Money:
    """Serialize one money figure canonically. The only money serializer."""
    dec = to_decimal(value)
    return Money(exact=exact_str(dec), display=display_str(dec))


def money_or_none(value: Any) -> Money | None:
    """As :func:`money`, but ``None`` passes through.

    The engine distinguishes absent from zero (SPEC §5-F4). This keeps that
    distinction intact instead of collapsing ``None`` to ``0``.
    """
    return None if value is None else money(value)
