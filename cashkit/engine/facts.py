"""The event side of the fact union (PRD §3.2, §5.1).

Two input kinds — generative ``Item`` and literal ``Event`` — are unioned into
fact rows **before** derived evaluation. If ``agg(tag="cat:revenue")`` cannot see
actuals, every derived item is wrong (non-negotiable #4), so this module runs
between segment expansion and the component loop in both engines.

What it decides is *structure*, and both engines share it: which column an event
lands in, which settlement governs it, and which diagnostics it raises. What it
deliberately does **not** do is arithmetic — the vectorized engine scatters int64
legs, the reference engine adds ``Decimal`` scalars, and the dual-engine gate
proves they agree.

Cutover: generative suppression before ``cutover`` is already implemented on the
expansion side (ADR-0004, DECISIONS D-P2-13). Events are **never** suppressed:
before cutover the ledger is the complete record and its rows are taken as-is
whatever their status; from cutover forward events apply alongside resumed
generation, and an ``actual`` dated on/after cutover raises ``CK-W003`` rather
than a dedup guess.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cashkit.model import Book, Diagnostic, Event, Item, ItemId, Settlement
from cashkit.model.diagnostics import make_diagnostic

__all__ = [
    "EventFact",
    "FactSet",
    "SYNTHETIC_EVENT_PREFIX",
    "resolve_facts",
]

#: Synthetic items holding events that reference no ``Item``. The prefix cannot
#: collide with an authored id: ``ItemId`` requires a leading lowercase letter,
#: so no book can ever contain one of these.
SYNTHETIC_EVENT_PREFIX = "_event:"


@dataclass(frozen=True)
class EventFact:
    """One ledger row placed against the column it contributes to.

    ``target`` is the item id whose accrual and cash columns receive the event:
    the referenced ``Item`` when the event has one, otherwise a synthetic item
    carrying the event's own dimensions. ``settlement_item`` is the item whose
    settlement terms govern the cash legs — the target item unless the event
    overrides ``settlement``, in which case it is a copy carrying the override.
    """

    event: Event
    target: ItemId
    settlement_item: Item


@dataclass(frozen=True)
class FactSet:
    """Events resolved against a book, ready for either engine."""

    facts: tuple[EventFact, ...] = ()
    #: Synthetic items to inject into the graph before condensation, so `agg()`
    #: selectors resolve against event dimensions like any other item.
    synthetic_items: dict[ItemId, Item] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    def by_target(self) -> dict[ItemId, list[EventFact]]:
        """Group the facts by the column they land in. No diagnostics."""
        grouped: dict[ItemId, list[EventFact]] = {}
        for fact in self.facts:
            grouped.setdefault(fact.target, []).append(fact)
        return grouped


def _synthetic_id(event: Event) -> ItemId:
    """A stable id for the synthetic item holding an unattached event.

    Derived from the event's *dimensions* — tags, currency, settlement, VAT —
    and not from its id, so a thousand bank-fee rows with the same shape share
    one column instead of a thousand. Stable across imports and machines: the
    same dimensions always produce the same id, which is what lets a UI keep
    pointing at the same row.
    """
    parts = [
        ";".join(f"{key}={value}" for key, value in sorted(event.tags.items())),
        event.currency,
        "" if event.settlement is None else event.settlement.model_dump_json(),
        "" if event.vat is None else event.vat.model_dump_json(),
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{SYNTHETIC_EVENT_PREFIX}{digest}"


def _synthetic_item(item_id: ItemId, event: Event) -> Item:
    """Build the synthetic carrier for an unattached event.

    Constructed with ``model_construct`` on purpose: the id is deliberately
    outside the authored ``ItemId`` grammar so it can never collide with a book
    item, and every field comes from an already-validated ``Event``.
    """
    return Item.model_construct(
        id=item_id,
        name=f"Ledger events {item_id[len(SYNTHETIC_EVENT_PREFIX):]}",
        kind="flow",
        direction=None,
        tags=dict(event.tags),
        flags=set(),
        currency=event.currency,
        segments=[],
        formula=None,
        settlement=event.settlement,
        vat=event.vat,
        agg_rule="sum",
    )


def _with_settlement(item: Item, settlement: Settlement | None) -> Item:
    if settlement is None or settlement == item.settlement:
        return item
    return item.model_copy(update={"settlement": settlement})


def resolve_facts(book: Book, events: tuple[Event, ...] | list[Event]) -> FactSet:
    """Place every live event against the column it contributes to.

    Events are processed in ``(date, id)`` order so the result never depends on
    the order the ledger happened to return. An event referencing an ``Item``
    inherits its tags, VAT and settlement and lands in that item's columns; an
    unattached event lands in a synthetic item keyed by its own dimensions.

    Returns a :class:`FactSet`. Diagnostics: ``CK-E001`` when ``event.item``
    names no item in the book, ``CK-E020`` when an event's currency differs from
    the item it attaches to (a cross-currency sum is never silent), and
    ``CK-W003`` for an ``actual`` dated on or after ``cutover``. Never raises on
    event content.
    """
    diagnostics: list[Diagnostic] = []
    facts: list[EventFact] = []
    synthetic: dict[ItemId, Item] = {}
    seen_codes: set[tuple[str, ItemId | None]] = set()

    def emit(code: str, item_id: ItemId | None, **details: object) -> None:
        # One diagnostic per (code, item): a bad import of a thousand rows is one
        # modelling fact, not a thousand (DECISIONS D-P2-11).
        if (code, item_id) in seen_codes:
            return
        seen_codes.add((code, item_id))
        diagnostics.append(make_diagnostic(code, item_id=item_id, **details))

    for event in sorted(events, key=lambda e: (e.date, e.id)):
        if event.status == "actual" and event.date >= book.cutover:
            emit(
                "CK-W003",
                event.item,
                event_id=event.id,
                event_date=event.date.isoformat(),
                cutover=book.cutover.isoformat(),
            )
        if event.item is not None:
            item = book.items.get(event.item)
            if item is None:
                emit(
                    "CK-E001",
                    None,
                    field="event.item",
                    reference=f'event {event.id} -> item("{event.item}")',
                )
                continue
            if item.kind != "flow":
                # A derived item's column is written by its formula; an event
                # added to it would be overwritten by the evaluator, which is
                # exactly the silent wrongness this engine refuses.
                emit(
                    "CK-E018",
                    item.id,
                    field="event.item",
                    event_id=event.id,
                    reason=f"kind={item.kind!r} takes its value from a formula",
                )
                continue
            if event.currency != item.currency:
                emit(
                    "CK-E020",
                    item.id,
                    field="event.currency",
                    currencies=f"{item.currency}, {event.currency}",
                )
                continue
            target = item.id
            carrier = item
        else:
            target = _synthetic_id(event)
            carrier = synthetic.setdefault(target, _synthetic_item(target, event))
        facts.append(
            EventFact(
                event=event,
                target=target,
                settlement_item=_with_settlement(carrier, event.settlement),
            )
        )

    return FactSet(
        facts=tuple(facts),
        synthetic_items=synthetic,
        diagnostics=tuple(diagnostics),
    )


def augmented_items(book: Book, factset: FactSet) -> dict[ItemId, Item]:
    """The book's items plus the synthetic carriers, ready for graph building.

    Returns a new dict; the book itself is never mutated. No diagnostics.
    """
    if not factset.synthetic_items:
        return dict(book.items)
    return {**book.items, **factset.synthetic_items}
