"""Primitive types and shared model machinery (PRD §4.0).

All models are Pydantic v2, frozen (immutable — updates go through
``model_copy``), and reject unknown fields. Money is ``Decimal`` at this
boundary; the engine core (Phases 2-3) holds int64 minor units at 4 dp and
never sees ``Decimal`` or ``float``.

Structural invariants (types, ranges, patterns, xor-fields) are enforced here
with Pydantic validators; violating them raises ``ValidationError``, which at
this layer is programmer error. Anything a user or agent could plausibly do
wrong is pre-validated by the SDK (later sessions) into the §10.1 diagnostic
catalogue *before* model construction — see DECISIONS.md.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

__all__ = [
    "Amount",
    "BookId",
    "CalendarSpec",
    "CashKitModel",
    "CountryCode",
    "CurrencyCode",
    "Diagnostic",
    "DiagnosticSubject",
    "Duration",
    "Escalation",
    "EventId",
    "FiniteDecimal",
    "Grain",
    "IDENT_RE",
    "ItemId",
    "Money",
    "PeriodRange",
    "PeriodRef",
    "SparseOverlay",
    "TagKey",
    "TagValue",
    "Watermark",
]

# --------------------------------------------------------------------------- #
# Identifier grammar
# --------------------------------------------------------------------------- #

#: Formula-addressable identifier: item ids, param keys, tag keys, flags,
#: tax-regime ids. Matches PRD §4.1 (param keys ``[a-z][a-z0-9_]*``); applied
#: uniformly so every id can appear in a formula or selector (DECISIONS.md).
IDENT_RE = r"[a-z][a-z0-9_]*"

#: Book / scenario ids additionally allow ``-`` (they name files and
#: directories, e.g. ``cashkit init ./acme-cashflow``), never formulas.
FILE_IDENT_RE = r"[a-z][a-z0-9_-]*"

ItemId = Annotated[str, Field(pattern=rf"^{IDENT_RE}$", max_length=64)]

#: Ids the engine synthesizes for items no one authored: a tax regime's
#: ``_tax:<regime>:liability`` and ``_tax:<regime>:credit`` (ADR-0005) and the
#: carriers that hold unattached ledger events. Deliberately outside
#: :data:`ItemId` — an authored id must start with a lowercase letter, so
#: collision is structurally impossible.
SYNTHETIC_ID_RE = rf"_{IDENT_RE}(?::[a-z0-9_]+)+"

#: What a ``Diagnostic`` may name. Wider than :data:`ItemId` because a
#: diagnostic about a synthetic item must be *reportable*: refusing to build it
#: would raise an exception out of the engine on book content, which the error
#: policy forbids (PRD §6.5).
DiagnosticSubject = Annotated[
    str, Field(pattern=rf"^(?:{IDENT_RE}|{SYNTHETIC_ID_RE})$", max_length=128)
]
ParamKey = Annotated[str, Field(pattern=rf"^{IDENT_RE}$", max_length=64)]
TagKey = Annotated[str, Field(pattern=rf"^{IDENT_RE}$", max_length=64)]
#: Tag values must contain no whitespace and no ``:`` so that every authored
#: tag is addressable by the §5.4 selector grammar (``key:value`` terms,
#: space-separated). A tag that cannot be selected is a silent-mismatch bug
#: waiting to happen (DECISIONS.md).
TagValue = Annotated[str, Field(pattern=r"^[^\s:]+$", max_length=128)]
FlagName = Annotated[str, Field(pattern=rf"^{IDENT_RE}$", max_length=64)]
BookId = Annotated[str, Field(pattern=rf"^{FILE_IDENT_RE}$", max_length=64)]
ScenarioId = Annotated[str, Field(pattern=rf"^{FILE_IDENT_RE}$", max_length=64)]
#: Event ids come from the ledger (SQLite) and are opaque; any non-empty
#: string without control characters.
EventId = Annotated[str, Field(min_length=1, max_length=128)]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]

# --------------------------------------------------------------------------- #
# Numeric boundary types
# --------------------------------------------------------------------------- #

#: Engine ceiling: int64 minor units at 4 dp — max ≈ 9×10¹⁴ currency units
#: (PRD §5.3). Enforced at the boundary so overflow fails loudly at parse
#: time, never silently inside the engine.
MONEY_MAX = Decimal("9E14")


def _reject_float(value: object) -> object:
    """No float ever enters a Decimal field.

    A float has already lost the author's decimal digits to binary fractions;
    coercing it silently would be exactly the silent numerical error this
    system exists to prevent.
    """
    if isinstance(value, float):
        raise ValueError(
            "float is not accepted for Decimal fields; pass a Decimal or a string"
        )
    return value


def _require_finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("Decimal value must be finite (no NaN/Infinity)")
    return value


def _require_money(value: Decimal) -> Decimal:
    _require_finite(value)
    if value.as_tuple().exponent < -4:  # type: ignore[operator]
        raise ValueError(
            "money values carry at most 4 decimal places; "
            "the engine core is int64 minor units at 4 dp and must never round "
            "authored amounts silently"
        )
    if value.copy_abs() > MONEY_MAX:
        raise ValueError(f"money magnitude exceeds the engine ceiling of {MONEY_MAX}")
    return value


#: Any finite Decimal (rates, probabilities, params) — unbounded precision.
#: Rejects float input outright (silent binary-fraction loss).
FiniteDecimal = Annotated[
    Decimal, BeforeValidator(_reject_float), AfterValidator(_require_finite)
]

#: Boundary money type (PRD §4.0): Decimal here, int64 minor units at 4 dp
#: inside the engine. Finite, ≤ 4 decimal places, |x| ≤ 9×10¹⁴; rejects float.
Money = Annotated[
    Decimal, BeforeValidator(_reject_float), AfterValidator(_require_money)
]

#: Calendar-semantic duration: ``"<n>d" | "<n>w" | "<n>m" | "<n>y"``
#: ("2m" = two calendar months, day clamped to month end). No leading zeros.
Duration = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)[dwmy]$")]

#: Segment boundaries are concrete ISO dates in v1 (PRD §4.0).
PeriodRef = date


# --------------------------------------------------------------------------- #
# Base classes
# --------------------------------------------------------------------------- #


class CashKitModel(BaseModel):
    """Base for all CashKit models: frozen, no unknown fields.

    Frozen models make immutability the default — in particular, nothing can
    mutate an ``Event`` after construction (actuals are immutable). Updates
    are functional: ``model_copy(update=...)``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class SparseOverlay(CashKitModel):
    """Base for field-sparse overlay models (ADR-0009).

    A field is *recorded* iff it was explicitly provided at construction
    (tracked via ``model_fields_set``). Unrecorded fields fall through to the
    parent during resolution; their in-memory default values are never read.

    Recording a field as ``None`` (e.g. clearing ``settlement``) is a real,
    representable state distinct from not recording it: the canonical
    serializer emits recorded ``None`` as an explicit ``null`` and omits
    unrecorded fields entirely.

    Equality includes the recorded-field set: two overlays with identical
    values but different recorded fields resolve differently and must not
    compare equal.
    """

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return (
            self.model_fields_set == other.model_fields_set  # type: ignore[union-attr]
            and super().__eq__(other)
        )

    __hash__ = None  # type: ignore[assignment]

    def recorded_fields(self) -> frozenset[str]:
        """Return the set of field names this overlay records.

        Returns a frozenset of field names; produces no diagnostics.
        """
        return frozenset(self.model_fields_set)


# --------------------------------------------------------------------------- #
# Primitives (PRD §4.0)
# --------------------------------------------------------------------------- #


class Grain(str, Enum):
    """Time grain. Base grain is DAY (D1); coarser grains are aggregations."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class PeriodRange(CashKitModel):
    """Half-open date interval ``[start, end)``."""

    start: date
    end: date

    @model_validator(mode="after")
    def _check_order(self) -> "PeriodRange":
        if self.start >= self.end:
            raise ValueError("PeriodRange requires start < end ([start, end) is half-open)")
        return self


class CalendarSpec(CashKitModel):
    """Fiscal calendar, holiday set and weekend definition.

    ``holidays`` is the RESOLVED list of dates for the whole horizon, computed
    at book creation and committed; the ``holidays`` package is only a seed and
    runtime never consults it (ADR-0010: reproducibility must not depend on a
    dependency's data files). The list is canonicalized sorted and de-duplicated.

    ``weekend`` uses Python ``date.weekday()`` indices 0-6 (Mon=0); the default
    ``{5, 6}`` is Sat/Sun. The PRD's "ISO weekday" label contradicts its own
    default — see DECISIONS.md, "PRD conflicts".
    """

    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    country: CountryCode | None = None
    holidays: list[date] = Field(default_factory=list)
    weekend: set[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=lambda: {5, 6}
    )

    @field_validator("holidays")
    @classmethod
    def _canonicalize_holidays(cls, value: list[date]) -> list[date]:
        return sorted(set(value))


class Watermark(CashKitModel):
    """Ledger watermark stamped by ``commit()`` (ADR-0006)."""

    max_rowid: int = Field(ge=0)
    row_count: int = Field(ge=0)
    content_hash: str = Field(min_length=1, max_length=128)


class Amount(CashKitModel):
    """Segment amount: exactly one of ``constant`` or ``schedule`` set.

    An ``expression`` variant is deliberately absent in v1: formulas belong to
    derived items; computed schedules are authored via the SDK (PRD §4.0).
    """

    constant: Money | None = None
    schedule: list[tuple[date, Money]] | None = None

    @field_validator("schedule", mode="before")
    @classmethod
    def _schedule_from_maps(cls, value: object) -> object:
        """Accept the canonical YAML form ``[{date: ..., amount: ...}, ...]``."""
        if isinstance(value, list):
            converted: list[object] = []
            for entry in value:
                if isinstance(entry, dict):
                    if set(entry.keys()) != {"date", "amount"}:
                        raise ValueError(
                            "schedule entries must have exactly the keys 'date' and 'amount'"
                        )
                    converted.append((entry["date"], entry["amount"]))
                else:
                    converted.append(entry)
            return converted
        return value

    @model_validator(mode="after")
    def _exactly_one(self) -> "Amount":
        if (self.constant is None) == (self.schedule is None):
            raise ValueError("Amount requires exactly one of 'constant' or 'schedule'")
        if self.schedule is not None and len(self.schedule) == 0:
            raise ValueError("Amount.schedule must not be empty; use Events for one-offs")
        return self


def _coerce_rate(value: object) -> object:
    """Disambiguate ``str | Decimal`` rate fields at the parse boundary.

    Canonical YAML quotes both param keys and Decimal literals, so on input a
    string matching the param-key grammar is a key and anything else must
    parse as a Decimal literal. See DECISIONS.md.
    """
    if isinstance(value, str) and not re.fullmatch(IDENT_RE, value):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                f"rate {value!r} is neither a param key ([a-z][a-z0-9_]*) "
                "nor a Decimal literal"
            ) from exc
    return value


class Escalation(CashKitModel):
    """Annual escalation applied to a segment (PRD §4.0, ADR-0002)."""

    rate: Annotated[str, Field(pattern=rf"^{IDENT_RE}$")] | FiniteDecimal
    every_years: int = Field(default=1, ge=1)
    anchor: Literal["segment_start", "calendar_year"] = "segment_start"

    @field_validator("rate", mode="before")
    @classmethod
    def _rate_key_or_literal(cls, value: object) -> object:
        return _coerce_rate(value)


class Diagnostic(CashKitModel):
    """Structured error/warning/info object (PRD §4.0, §10.1).

    Errors are data, not exceptions: every fallible SDK operation returns
    ``Diagnostic`` objects an agent can loop on. Exceptions are reserved for
    programmer error.
    """

    severity: Literal["error", "warning", "info"]
    code: str = Field(pattern=r"^CK-[EWI][0-9]{3}$")
    item_id: DiagnosticSubject | None = None
    field: str | None = None
    message: str
    suggested_fix: str
