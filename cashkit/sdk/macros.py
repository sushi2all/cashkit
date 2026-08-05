"""Scenario macros (PRD §6.3).

Macros expand **immediately** to concrete overrides. Nothing is deferred,
nothing is stored as a rule: the post-macro state is indistinguishable from
having typed the items out by hand, which is what keeps `provenance()` and
`diff()` honest and keeps a scenario file readable without an interpreter.

Every macro is therefore a pure function from the resolved items a selector
matches to the items as they should become. Applying them is
:meth:`cashkit.sdk.scenarios.ScenarioSet.apply_macro`, which routes each
rewritten item through ``set_item`` — so a macro that changes nothing records
nothing, exactly like a hand-written no-op write.

``segments`` is atomic (non-negotiable #8): ``ShiftItems`` and ``ScaleItems``
rebuild the whole list and hand it over whole. There is no positional patching
and no segment-id matching anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Protocol

from cashkit.engine.calendars import add_duration
from cashkit.engine.formula import Selector, parse_selector
from cashkit.model import Amount, Diagnostic, Item, Segment
from cashkit.model.diagnostics import make_diagnostic

__all__ = ["Macro", "RetagItems", "ScaleItems", "ShiftItems", "resolve_selector"]

#: Authored money carries at most 4 decimal places (D-P1-06), so a macro that
#: multiplies an amount has to round somewhere. It rounds here, at the authoring
#: boundary, under the same half-up default the engine uses (D-P2-01) — the
#: stored amount is then exactly what a human would have typed, and the engine
#: never has to round an authored value silently.
_MONEY_QUANTUM = Decimal("0.0001")


class Macro(Protocol):
    """A scenario macro: resolved items in, rewritten items out.

    Implementations are frozen dataclasses so a macro is a value, not a rule
    with state.
    """

    selector: str

    def rewrite(self, item: Item) -> Item:
        """Return ``item`` as this macro would have it. No diagnostics."""
        ...  # pragma: no cover - protocol


def resolve_selector(
    source: str, items: dict[str, Item]
) -> tuple[list[Item], Diagnostic | None]:
    """Resolve a §5.4 selector against a resolved item map.

    Returns ``(matched items in id order, None)``, or ``([], diagnostic)`` with
    ``CK-E003`` when the selector is malformed — the selector grammar is part of
    the formula surface, so a bad one is a rejected expression rather than a new
    failure class (D-P2-10). A selector matching nothing is *not* an error: it
    matched nothing, and the empty ``ChangeReport`` says so.
    """
    selector, reason = parse_selector(source)
    if selector is None:
        return [], make_diagnostic(
            "CK-E003", field="selector", reason=f"selector {source!r}: {reason}"
        )
    return _matching(selector, items), None


def _matching(selector: Selector, items: dict[str, Item]) -> list[Item]:
    return [
        item
        for _, item in sorted(items.items())
        if selector.matches(item.tags, item.flags)
    ]


def _shift_date(day: date, by: str) -> date:
    return add_duration(day, by)


def _quantize(value: Decimal, banker: bool) -> Decimal:
    return value.quantize(
        _MONEY_QUANTUM, rounding=ROUND_HALF_EVEN if banker else ROUND_HALF_UP
    )


@dataclass(frozen=True)
class ShiftItems:
    """Move every matched item's generative window by a ``Duration``.

    Shifts each segment's ``start`` and ``end`` and every explicit schedule date
    (a schedule's dates *are* its occurrence dates, D-P2-02). Recurrence phase
    follows the segment start (D-P2-03), so a shifted monthly segment falls due
    on the shifted day of the month rather than drifting back to the original.

    Durations are calendar-semantic: ``"2m"`` clamps the day to month end, so
    shifting a 31 January start by two months lands on 31 March and shifting it
    by one lands on 28 February. The shift is not reversible for such dates,
    which is a property of calendar arithmetic and not of this macro.
    """

    selector: str
    by: str

    def rewrite(self, item: Item) -> Item:
        """Return ``item`` with every segment window shifted. No diagnostics."""
        if not item.segments:
            return item
        return item.model_copy(
            update={"segments": [self._shift(segment) for segment in item.segments]}
        )

    def _shift(self, segment: Segment) -> Segment:
        amount = segment.amount
        if amount.schedule is not None:
            amount = Amount(
                schedule=[
                    (_shift_date(day, self.by), value) for day, value in amount.schedule
                ]
            )
        return segment.model_copy(
            update={
                "start": _shift_date(segment.start, self.by),
                "end": None if segment.end is None else _shift_date(segment.end, self.by),
                "amount": amount,
            }
        )


@dataclass(frozen=True)
class ScaleItems:
    """Multiply every matched item's authored amounts by ``factor``.

    Constants and every entry of an explicit schedule are scaled in ``Decimal``
    and rounded to 4 dp at this authoring boundary. ``probability`` and
    ``escalation`` are untouched: scaling revenue by 0.8 is a statement about
    the amounts, not about how likely they are or how they grow.
    """

    selector: str
    factor: Decimal
    #: Round halves to even instead of away from zero, for a book running the
    #: banker's policy. The default matches the engine default (D-P2-01).
    banker: bool = False

    def rewrite(self, item: Item) -> Item:
        """Return ``item`` with every authored amount scaled. No diagnostics."""
        if not item.segments:
            return item
        return item.model_copy(
            update={"segments": [self._scale(segment) for segment in item.segments]}
        )

    def _scale(self, segment: Segment) -> Segment:
        amount = segment.amount
        if amount.constant is not None:
            scaled = Amount(
                constant=_quantize(amount.constant * self.factor, self.banker)
            )
        else:
            assert amount.schedule is not None  # Amount enforces the xor
            scaled = Amount(
                schedule=[
                    (day, _quantize(value * self.factor, self.banker))
                    for day, value in amount.schedule
                ]
            )
        return segment.model_copy(update={"amount": scaled})


@dataclass(frozen=True)
class RetagItems:
    """Merge ``tags`` into every matched item's tag map; the macro wins.

    Tags are dimensional, so retagging moves `agg()` membership and every
    tag-sliced view. That is the point, and it is why the change is written into
    the overlay as a concrete ``tags`` value rather than kept as a rule: a rule
    would silently re-apply to items added later.
    """

    selector: str
    tags: dict[str, str] = field(default_factory=dict)

    def rewrite(self, item: Item) -> Item:
        """Return ``item`` with ``tags`` merged over its own. No diagnostics."""
        merged = {**item.tags, **self.tags}
        if merged == item.tags:
            return item
        return item.model_copy(update={"tags": merged})
