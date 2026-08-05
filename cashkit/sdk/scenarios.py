"""Scenarios: sparse overlays, field-sparse resolution, macros, provenance.

PRD §4.6 and §6.3, ADR-0007 and ADR-0009. Three rules, and they stay boring:

1. Resolution is **field-sparse along the parent chain**: for each field of each
   item, the nearest ancestor overlay that *recorded* that field wins;
   unrecorded fields fall through to the parent.
2. ``segments`` is **atomic** — recorded whole or not at all. No positional
   patching, no segment-id matching, no partial merge.
3. Consequence of 1: a later correction to ``tags`` or ``settlement`` in base
   propagates into scenarios that did not override those fields, and does not
   propagate into ones that did.

Scenarios are **authored by value**: ``set_item`` takes the whole Item as you
want it, and only the fields differing from the resolved parent are recorded.
An agent that writes an item and changes nothing is told so (``CK-I002``)
rather than silently bloating the overlay.

**Base is a scenario with ``parent=None``.** It is privileged in *storage* only
(its content is the top-level book, ADR-0007); nothing here branches on "is this
base". A scenario with ``parent=None`` resolves against the authored book, and
that is the only rule — base and a flattened scenario go down the same path.

**Actuals are immutable across every scenario.** ``EventOverlay.status`` cannot
represent ``"actual"`` (D-P1-08), so fabricating one is impossible at the type
level; an overlay *targeting* a row whose ledger status is actual is refused
here with ``CK-E006`` and dropped.

**Synthetic items are invisible to scenarios.** ``_tax:<regime>:*`` and
``_event:<digest>`` are runtime products of the regimes and the ledger, rebuilt
on every compile (D-P5-09, D-P5-10). An overlay recording one would resurrect a
value the next run recomputes, so this module refuses a book that contains them
— handing it ``Engine.book`` instead of the authored book is programmer error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping

from cashkit.model import (
    Book,
    ChangeReport,
    Diagnostic,
    Event,
    EventId,
    EventOverlay,
    Item,
    ItemId,
    ItemOverlay,
    Scenario,
)
from cashkit.model.diagnostics import make_diagnostic
from cashkit.model.primitives import SYNTHETIC_ID_RE, ScenarioId, _require_money
from cashkit.model.reports import (
    FieldOrigin,
    ItemDiff,
    ParamDiff,
    Provenance,
    ScenarioDiff,
)

from .macros import Macro, resolve_selector

__all__ = [
    "EVENT_OVERLAY_FIELDS",
    "OVERLAY_FIELDS",
    "OPENING_BALANCE_PARAM",
    "Resolution",
    "ScenarioSet",
]

#: The Item fields an overlay may record. ``id`` is the key in
#: ``Scenario.items`` and is not overridable — an overlay that could rename an
#: item would break every reference to it.
OVERLAY_FIELDS: tuple[str, ...] = tuple(ItemOverlay.model_fields)

#: The Event fields an overlay may record (D-P1-08). ``source``/``ext_id`` are
#: absent on purpose: rewriting import identity would break
#: ``UNIQUE(source, ext_id)`` idempotency.
EVENT_OVERLAY_FIELDS: tuple[str, ...] = tuple(EventOverlay.model_fields)

#: Reserved param key: setting it in a scenario overrides ``Book.opening_balance``
#: so capital-injection cases are sweepable (PRD §4.1).
OPENING_BALANCE_PARAM = "opening_balance"

_SYNTHETIC = re.compile(rf"^{SYNTHETIC_ID_RE}$")

#: Fields whose containers are copied when an overlay is applied, so an overlay
#: and the item it produced never share a mutable object.
_CONTAINER_FIELDS = {"tags": dict, "flags": set, "segments": list}


def _copy_value(name: str, value: object) -> object:
    kind = _CONTAINER_FIELDS.get(name)
    if kind is None or value is None:
        return value
    return kind(value)  # type: ignore[call-arg]


def _record(overlay: ItemOverlay | EventOverlay | None) -> dict[str, object]:
    """The fields an overlay records, as a plain mapping. No diagnostics."""
    if overlay is None:
        return {}
    return {name: getattr(overlay, name) for name in sorted(overlay.model_fields_set)}


def _full_record(item: Item) -> dict[str, object]:
    return {name: getattr(item, name) for name in OVERLAY_FIELDS}


_MISSING = object()


def _record_delta(
    before: Mapping[str, object], after: Mapping[str, object]
) -> tuple[str, ...]:
    """Field names whose *record* differs — presence or value.

    This is the literal reading of "the fields actually recorded as different"
    (PRD §6.3), and it is empty exactly when nothing would be written. A field
    that stops being recorded counts: recordedness is what decides whether a
    later base correction propagates (ADR-0009), so dropping a record is a real
    change even when this scenario's resolved value does not move.
    """
    return tuple(
        sorted(
            name
            for name in set(before) | set(after)
            if before.get(name, _MISSING) != after.get(name, _MISSING)
        )
    )


def _apply_overlay(item: Item, overlay: ItemOverlay) -> Item:
    """Return ``item`` with the overlay's recorded fields applied.

    ``segments`` arrives whole or not at all — the atomicity rule is enforced by
    the shape of the overlay, not by a merge routine that could be asked to do
    otherwise. Produces no diagnostics.
    """
    update = {
        name: _copy_value(name, getattr(overlay, name))
        for name in overlay.model_fields_set
    }
    if not update:
        return item
    return item.model_copy(update=update)


def _diff_overlay(item: Item, parent: Item) -> ItemOverlay | None:
    """Record exactly the fields of ``item`` that differ from ``parent``.

    Returns ``None`` when nothing differs — an overlay recording nothing is not
    stored, so writing back the parent's value is indistinguishable from never
    having written (which is what makes the empty ``ChangeReport`` honest).
    Produces no diagnostics.
    """
    recorded = {
        name: _copy_value(name, getattr(item, name))
        for name in OVERLAY_FIELDS
        if getattr(item, name) != getattr(parent, name)
    }
    if not recorded:
        return None
    return ItemOverlay(**recorded)


def _item_field_diff(left: Item, right: Item) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name in OVERLAY_FIELDS
            if getattr(left, name) != getattr(right, name)
        )
    )


def _empty_report(target: str, reason_field: str | None = None) -> ChangeReport:
    return ChangeReport(
        target=target,
        diagnostics=(make_diagnostic("CK-I002", field=reason_field),),
    )


def _error_report(target: str, diagnostic: Diagnostic) -> ChangeReport:
    return ChangeReport(target=target, diagnostics=(diagnostic,))


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Resolution:
    """A scenario materialized into a concrete Book, with its audit trail.

    ``book`` is what an engine evaluates: no overlays, no chain. ``origins``
    answers "which ancestor set this field" per item, and ``diagnostics``
    carries everything resolution refused to guess about.
    """

    scenario: ScenarioId
    book: Book
    origins: dict[ItemId, dict[str, FieldOrigin]] = field(default_factory=dict)
    removed_by: dict[ItemId, ScenarioId] = field(default_factory=dict)
    #: Chain-merged event overlays, field-sparse exactly like item overlays.
    event_overlays: dict[EventId, EventOverlay] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()


# --------------------------------------------------------------------------- #
# The scenario set
# --------------------------------------------------------------------------- #


@dataclass
class ScenarioSet:
    """The authored book plus every scenario that forks from it (PRD §6.3).

    In-memory and storage-free: persisting scenarios is the config store's job
    (Session S5). Everything here is a pure function of ``book`` and
    ``scenarios`` except the write operations, which mutate ``scenarios`` and
    report what they recorded.

    Construct with :meth:`new` to get a book and its base scenario together.
    """

    book: Book
    scenarios: dict[ScenarioId, Scenario] = field(default_factory=dict)

    def __post_init__(self) -> None:
        synthetic = sorted(
            item_id for item_id in self.book.items if _SYNTHETIC.match(item_id)
        )
        if synthetic:
            raise ValueError(
                "scenario resolution operates on the authored book; it was given "
                f"one containing engine-synthesized items {synthetic}. These are "
                "rebuilt on every compile (DECISIONS D-P5-09/D-P5-10) and an "
                "overlay recording one would resurrect a recomputed value. Pass "
                "the book you authored, not Engine.book."
            )

    @classmethod
    def new(cls, book: Book, base_id: ScenarioId = "base") -> "ScenarioSet":
        """Return a set holding ``book`` and its (empty) base overlay shell.

        Base is a scenario with ``parent=None`` — privileged in storage only
        (ADR-0007). Produces no diagnostics.
        """
        return cls(book=book, scenarios={base_id: Scenario(id=base_id)})

    # -- chain ------------------------------------------------------------- #

    def _chain(
        self, scenario_id: ScenarioId
    ) -> tuple[list[Scenario], list[Diagnostic]]:
        """Root-first chain ending at ``scenario_id``, plus any diagnostics.

        A missing link or a cycle produces ``CK-E021`` and truncates the chain
        rather than raising: a corrupt scenario file must still be inspectable.
        """
        diagnostics: list[Diagnostic] = []
        chain: list[Scenario] = []
        seen: set[ScenarioId] = set()
        current: ScenarioId | None = scenario_id
        while current is not None:
            if current in seen:
                diagnostics.append(
                    make_diagnostic(
                        "CK-E021",
                        scenario_id=current,
                        reason=(
                            "the parent chain cycles back to it; a scenario cannot "
                            "be its own ancestor"
                        ),
                    )
                )
                break
            scenario = self.scenarios.get(current)
            if scenario is None:
                diagnostics.append(
                    make_diagnostic(
                        "CK-E021",
                        scenario_id=current,
                        reason="no scenario with that id exists in this set",
                    )
                )
                break
            seen.add(current)
            chain.append(scenario)
            current = scenario.parent
        chain.reverse()
        return chain, diagnostics

    def ancestry(self, scenario_id: ScenarioId) -> tuple[ScenarioId, ...]:
        """Return the scenario ids from the chain root down to ``scenario_id``.

        Returns an empty tuple for an unknown id. Produces no diagnostics — use
        :meth:`diagnostics` for the reason.
        """
        chain, _ = self._chain(scenario_id)
        return tuple(scenario.id for scenario in chain)

    # -- resolution -------------------------------------------------------- #

    def resolution(self, scenario_id: ScenarioId) -> Resolution:
        """Resolve ``scenario_id`` into a concrete Book with its audit trail.

        Returns a :class:`Resolution`. Diagnostics: ``CK-E021`` for a missing or
        cyclic chain link, ``CK-E023`` for an overlay targeting an item the
        parent chain does not define, and ``CK-E024`` for a reserved
        ``opening_balance`` param that is not valid money. Never raises on
        scenario content.
        """
        chain, diagnostics = self._chain(scenario_id)
        return self._resolve_chain(scenario_id, chain, diagnostics)

    def resolve(self, scenario_id: ScenarioId) -> Book:
        """Resolve ``scenario_id`` into the concrete Book an engine evaluates.

        Materialized and inspectable: no overlays, no chain (PRD §6.3). See
        :meth:`resolution` for the diagnostics resolution can produce —
        this method drops them, so call :meth:`diagnostics` alongside it before
        trusting the result.
        """
        return self.resolution(scenario_id).book

    def diagnostics(self, scenario_id: ScenarioId) -> tuple[Diagnostic, ...]:
        """Return the diagnostics resolving ``scenario_id`` produces.

        Same set as ``resolution(scenario_id).diagnostics``.
        """
        return self.resolution(scenario_id).diagnostics

    def _resolve_chain(
        self,
        scenario_id: ScenarioId,
        chain: list[Scenario],
        diagnostics: list[Diagnostic],
    ) -> Resolution:
        items: dict[ItemId, Item] = dict(self.book.items)
        params: dict[str, Decimal] = dict(self.book.params)
        origins: dict[ItemId, dict[str, FieldOrigin]] = {
            item_id: {
                name: FieldOrigin(field=name, scenario=None, kind="book")
                for name in OVERLAY_FIELDS
            }
            for item_id in items
        }
        removed_by: dict[ItemId, ScenarioId] = {}
        events: dict[EventId, dict[str, object]] = {}

        for scenario in chain:
            # Order inside one scenario: removals, then additions, then overlays.
            # `added` therefore wins over `removed` for the same id — you removed
            # the parent's version and authored a new one — while an overlay on a
            # removed id is contradictory and reports CK-E023 (D-P7-04).
            for item_id in sorted(scenario.removed):
                if items.pop(item_id, None) is not None:
                    origins.pop(item_id, None)
                    removed_by[item_id] = scenario.id
            for item_id, item in sorted(scenario.added.items()):
                items[item_id] = item
                removed_by.pop(item_id, None)
                origins[item_id] = {
                    name: FieldOrigin(field=name, scenario=scenario.id, kind="added")
                    for name in OVERLAY_FIELDS
                }
            for item_id, overlay in sorted(scenario.items.items()):
                base_item = items.get(item_id)
                if base_item is None:
                    diagnostics.append(
                        make_diagnostic(
                            "CK-E023",
                            item_id=item_id,
                            scenario_id=scenario.id,
                        )
                    )
                    continue
                items[item_id] = _apply_overlay(base_item, overlay)
                for name in overlay.model_fields_set:
                    origins[item_id][name] = FieldOrigin(
                        field=name, scenario=scenario.id, kind="overlay"
                    )
            params.update(scenario.params)
            for event_id, overlay in sorted(scenario.event_overrides.items()):
                events.setdefault(event_id, {}).update(_record(overlay))

        opening_balance = self.book.opening_balance
        if OPENING_BALANCE_PARAM in params:
            candidate = params[OPENING_BALANCE_PARAM]
            try:
                opening_balance = _require_money(candidate)
            except ValueError as exc:
                diagnostics.append(
                    make_diagnostic(
                        "CK-E024",
                        field=OPENING_BALANCE_PARAM,
                        key=OPENING_BALANCE_PARAM,
                        scenario_id=scenario_id,
                        reason=str(exc),
                    )
                )

        book = self.book.model_copy(
            update={
                "items": items,
                "params": params,
                "opening_balance": opening_balance,
            }
        )
        return Resolution(
            scenario=scenario_id,
            book=book,
            origins=origins,
            removed_by=removed_by,
            event_overlays={
                event_id: EventOverlay(**record)
                for event_id, record in sorted(events.items())
            },
            diagnostics=tuple(diagnostics),
        )

    def _parent_view(self, scenario: Scenario) -> Resolution:
        """Resolve everything *above* ``scenario`` — the value writes diff against."""
        if scenario.parent is None:
            return self._resolve_chain(scenario.id, [], [])
        chain, diagnostics = self._chain(scenario.parent)
        return self._resolve_chain(scenario.parent, chain, diagnostics)

    # -- writes ------------------------------------------------------------ #

    def fork(
        self, scenario_id: ScenarioId, new_id: ScenarioId, note: str = ""
    ) -> ChangeReport:
        """Fork ``scenario_id`` into a new empty scenario ``new_id``.

        Returns a :class:`ChangeReport` whose ``created`` names the new
        scenario. Diagnostics: ``CK-E021`` when the parent does not exist,
        ``CK-E022`` when ``new_id`` is taken. Scenarios fork from scenarios —
        forking base is the same operation as forking anything else.
        """
        if scenario_id not in self.scenarios:
            return _error_report(
                new_id,
                make_diagnostic(
                    "CK-E021",
                    scenario_id=scenario_id,
                    reason="cannot fork from a scenario that does not exist",
                ),
            )
        if new_id in self.scenarios:
            return _error_report(
                new_id, make_diagnostic("CK-E022", scenario_id=new_id)
            )
        self.scenarios[new_id] = Scenario(id=new_id, parent=scenario_id, note=note)
        return ChangeReport(target=new_id, created=(new_id,))

    def set_item(
        self, scenario_id: ScenarioId, item: Item, note: str = ""
    ) -> ChangeReport:
        """Write ``item`` into ``scenario_id`` by value (PRD §6.3, D4).

        Only the fields differing from the resolved parent are recorded; a write
        that changes nothing records nothing and reports ``CK-I002``. Returns a
        :class:`ChangeReport` whose ``changed`` lists the field names whose
        record moved and whose ``created`` names the item when it is new in this
        scenario. Diagnostics: ``CK-E021`` for an unknown scenario, ``CK-I002``
        for an empty write.
        """
        scenario = self.scenarios.get(scenario_id)
        if scenario is None:
            return _error_report(
                item.id,
                make_diagnostic(
                    "CK-E021",
                    item_id=item.id,
                    scenario_id=scenario_id,
                    reason="cannot write an item into a scenario that does not exist",
                ),
            )
        parent = self._parent_view(scenario)
        parent_item = parent.book.items.get(item.id)
        was_removed = item.id in scenario.removed

        if parent_item is None:
            before = _full_record(scenario.added[item.id]) if item.id in scenario.added else {}
            after = _full_record(item)
            changed = _record_delta(before, after)
            if not changed and not was_removed:
                return _empty_report(item.id)
            added = dict(scenario.added)
            added[item.id] = item
            self._replace(
                scenario,
                added=added,
                items=_without(scenario.items, item.id),
                removed=scenario.removed - {item.id},
            )
            created = (item.id,) if not before else ()
            return ChangeReport(target=item.id, changed=changed, created=created)

        overlay = _diff_overlay(item, parent_item)
        before = {} if was_removed else _record(scenario.items.get(item.id))
        after = _record(overlay)
        changed = _record_delta(before, after)
        if not changed and not was_removed:
            return _empty_report(item.id)
        overlays = dict(scenario.items)
        if overlay is None:
            overlays.pop(item.id, None)
        else:
            overlays[item.id] = overlay
        self._replace(
            scenario,
            items=overlays,
            added=_without(scenario.added, item.id),
            removed=scenario.removed - {item.id},
        )
        return ChangeReport(
            target=item.id,
            changed=changed,
            created=(item.id,) if was_removed else (),
        )

    def set_param(
        self, scenario_id: ScenarioId, key: str, value: Decimal, note: str = ""
    ) -> ChangeReport:
        """Set a named scalar in ``scenario_id``, sparsely.

        Recorded only when it differs from the resolved parent's value.
        ``opening_balance`` is the reserved key that overrides the Book field
        (PRD §4.1); it is checked as money here so a 5 dp sweep value fails at
        the door rather than inside the engine. Returns a
        :class:`ChangeReport`; diagnostics ``CK-E021``, ``CK-E024``,
        ``CK-I002``.
        """
        scenario = self.scenarios.get(scenario_id)
        if scenario is None:
            return _error_report(
                f"params.{key}",
                make_diagnostic(
                    "CK-E021",
                    scenario_id=scenario_id,
                    reason="cannot set a param on a scenario that does not exist",
                ),
            )
        if key == OPENING_BALANCE_PARAM:
            try:
                _require_money(value)
            except ValueError as exc:
                return _error_report(
                    f"params.{key}",
                    make_diagnostic(
                        "CK-E024",
                        field=key,
                        key=key,
                        scenario_id=scenario_id,
                        reason=str(exc),
                    ),
                )
        parent = self._parent_view(scenario)
        inherited = parent.book.params.get(key, _MISSING)
        recorded = scenario.params.get(key, _MISSING)
        target = value if inherited != value else _MISSING
        if recorded == target:
            return _empty_report(f"params.{key}")
        params = dict(scenario.params)
        if target is _MISSING:
            params.pop(key, None)
        else:
            params[key] = value
        self._replace(scenario, params=params)
        return ChangeReport(target=f"params.{key}", changed=(f"params.{key}",))

    def unset(self, scenario_id: ScenarioId, item_id: ItemId) -> ChangeReport:
        """Drop ``item_id``'s record in ``scenario_id``, reverting to the parent.

        Removes the overlay, the added item, or the removal — whichever this
        scenario recorded. Returns a :class:`ChangeReport` listing what was
        dropped; ``CK-E021`` for an unknown scenario, ``CK-I002`` when the
        scenario recorded nothing about the item.
        """
        scenario = self.scenarios.get(scenario_id)
        if scenario is None:
            return _error_report(
                item_id,
                make_diagnostic(
                    "CK-E021",
                    item_id=item_id,
                    scenario_id=scenario_id,
                    reason="cannot unset an item in a scenario that does not exist",
                ),
            )
        changed: list[str] = []
        if item_id in scenario.items:
            changed.extend(_record(scenario.items[item_id]))
        if item_id in scenario.added:
            changed.append("added")
        if item_id in scenario.removed:
            changed.append("removed")
        if not changed:
            return _empty_report(item_id)
        self._replace(
            scenario,
            items=_without(scenario.items, item_id),
            added=_without(scenario.added, item_id),
            removed=scenario.removed - {item_id},
        )
        return ChangeReport(target=item_id, changed=tuple(sorted(set(changed))))

    def remove_item(self, scenario_id: ScenarioId, item_id: ItemId) -> ChangeReport:
        """Remove ``item_id`` from ``scenario_id`` and its descendants.

        An item this scenario added is simply dropped; one inherited from the
        parent chain is recorded in ``removed``. Returns a
        :class:`ChangeReport`; ``CK-E021`` for an unknown scenario, ``CK-I002``
        when the item is already absent.
        """
        scenario = self.scenarios.get(scenario_id)
        if scenario is None:
            return _error_report(
                item_id,
                make_diagnostic(
                    "CK-E021",
                    item_id=item_id,
                    scenario_id=scenario_id,
                    reason="cannot remove an item from a scenario that does not exist",
                ),
            )
        if item_id in scenario.added:
            self._replace(scenario, added=_without(scenario.added, item_id))
            return ChangeReport(target=item_id, changed=("added",))
        parent = self._parent_view(scenario)
        if item_id not in parent.book.items:
            return _empty_report(item_id)
        if item_id in scenario.removed:
            return _empty_report(item_id)
        self._replace(
            scenario,
            items=_without(scenario.items, item_id),
            removed=scenario.removed | {item_id},
        )
        return ChangeReport(target=item_id, changed=("removed",))

    def apply_macro(
        self, scenario_id: ScenarioId, macro: Macro, note: str = ""
    ) -> ChangeReport:
        """Expand ``macro`` into concrete overrides, immediately (PRD §6.3).

        The macro is applied to every item its selector matches in the resolved
        scenario, and each rewritten item goes through :meth:`set_item` — so
        nothing is deferred, nothing is stored as a rule, and the post-macro
        state is indistinguishable from having typed the items out.

        Returns a :class:`ChangeReport` whose ``changed`` entries are
        ``"<item_id>.<field>"``. Diagnostics: ``CK-E021`` for an unknown
        scenario, ``CK-E003`` for a malformed selector, ``CK-I002`` when the
        macro changed nothing (including when the selector matched nothing).
        """
        scenario = self.scenarios.get(scenario_id)
        if scenario is None:
            return _error_report(
                scenario_id,
                make_diagnostic(
                    "CK-E021",
                    scenario_id=scenario_id,
                    reason="cannot apply a macro to a scenario that does not exist",
                ),
            )
        resolved = self.resolution(scenario_id)
        matched, problem = resolve_selector(macro.selector, resolved.book.items)
        if problem is not None:
            return _error_report(scenario_id, problem)
        changed: list[str] = []
        created: list[str] = []
        for item in matched:
            rewritten = macro.rewrite(item)
            if rewritten == item:
                continue
            report = self.set_item(scenario_id, rewritten, note=note)
            changed.extend(f"{item.id}.{name}" for name in report.changed)
            created.extend(report.created)
        if not changed and not created:
            return _empty_report(scenario_id, reason_field="macro")
        return ChangeReport(
            target=scenario_id, changed=tuple(changed), created=tuple(created)
        )

    def flatten(
        self, scenario_id: ScenarioId, new_id: ScenarioId, note: str = ""
    ) -> ChangeReport:
        """Collapse ``scenario_id``'s chain into a standalone scenario.

        The result has ``parent=None`` and records everything that differs from
        the authored book, so it depends on no other scenario while resolving to
        exactly the same Book. This is the same shape base has (ADR-0007) —
        flattening produces an ordinary scenario, not a second kind of thing.

        Returns a :class:`ChangeReport` naming the new scenario. Diagnostics:
        ``CK-E021`` for an unknown source, ``CK-E022`` when ``new_id`` is taken.
        """
        if scenario_id not in self.scenarios:
            return _error_report(
                new_id,
                make_diagnostic(
                    "CK-E021",
                    scenario_id=scenario_id,
                    reason="cannot flatten a scenario that does not exist",
                ),
            )
        if new_id in self.scenarios:
            return _error_report(
                new_id, make_diagnostic("CK-E022", scenario_id=new_id)
            )
        resolved = self.resolution(scenario_id)
        overlays: dict[ItemId, ItemOverlay] = {}
        added: dict[ItemId, Item] = {}
        for item_id, item in resolved.book.items.items():
            authored = self.book.items.get(item_id)
            if authored is None:
                added[item_id] = item
                continue
            overlay = _diff_overlay(item, authored)
            if overlay is not None:
                overlays[item_id] = overlay
        removed = {
            item_id for item_id in self.book.items if item_id not in resolved.book.items
        }
        params = {
            key: value
            for key, value in resolved.book.params.items()
            if self.book.params.get(key, _MISSING) != value
        }
        self.scenarios[new_id] = Scenario(
            id=new_id,
            parent=None,
            note=note,
            params=params,
            items=overlays,
            added=added,
            removed=removed,
            event_overrides=dict(resolved.event_overlays),
        )
        return ChangeReport(target=new_id, created=(new_id,))

    def _replace(self, scenario: Scenario, **update: object) -> None:
        self.scenarios[scenario.id] = scenario.model_copy(update=update)

    # -- reads -------------------------------------------------------------- #

    def provenance(self, scenario_id: ScenarioId, item_id: ItemId) -> Provenance:
        """Report which ancestor set each field of ``item_id`` (PRD §6.3).

        ``scenario=None`` on a :class:`FieldOrigin` means the authored book —
        base's content lives at top level (ADR-0007), so "the book" and "base's
        overlay" are distinct sources and both are reported. An item the chain
        removed comes back with ``exists=False`` and ``removed_by`` naming the
        scenario that removed it. Produces no diagnostics.
        """
        resolved = self.resolution(scenario_id)
        origins = resolved.origins.get(item_id)
        if origins is None:
            return Provenance(
                scenario=scenario_id,
                item_id=item_id,
                exists=False,
                removed_by=resolved.removed_by.get(item_id),
            )
        return Provenance(
            scenario=scenario_id,
            item_id=item_id,
            exists=True,
            fields=tuple(origins[name] for name in OVERLAY_FIELDS),
        )

    def diff(self, left: ScenarioId, right: ScenarioId) -> ScenarioDiff:
        """Compare two scenarios semantically, from their resolved books.

        Two scenarios that reach identical state by different overlay routes
        diff empty, because the comparison never looks at overlays. Returns a
        :class:`ScenarioDiff` whose ``empty`` property is the answer to "did
        anything actually change". Produces no diagnostics — resolve each side
        with :meth:`diagnostics` if you need to know whether it resolved cleanly.
        """
        a = self.resolution(left)
        b = self.resolution(right)
        items: list[ItemDiff] = []
        for item_id in sorted(set(a.book.items) | set(b.book.items)):
            in_a = a.book.items.get(item_id)
            in_b = b.book.items.get(item_id)
            if in_a is None:
                items.append(ItemDiff(item_id=item_id, status="added"))
            elif in_b is None:
                items.append(ItemDiff(item_id=item_id, status="removed"))
            else:
                fields = _item_field_diff(in_a, in_b)
                if fields:
                    items.append(
                        ItemDiff(item_id=item_id, status="changed", fields=fields)
                    )
        params: list[ParamDiff] = []
        for key in sorted(set(a.book.params) | set(b.book.params)):
            left_value = a.book.params.get(key)
            right_value = b.book.params.get(key)
            if left_value != right_value:
                params.append(ParamDiff(key=key, left=left_value, right=right_value))
        events = tuple(
            event_id
            for event_id in sorted(set(a.event_overlays) | set(b.event_overlays))
            if a.event_overlays.get(event_id) != b.event_overlays.get(event_id)
        )
        opening = (
            None
            if a.book.opening_balance == b.book.opening_balance
            else (a.book.opening_balance, b.book.opening_balance)
        )
        return ScenarioDiff(
            left=left,
            right=right,
            opening_balance=opening,
            params=tuple(params),
            items=tuple(items),
            event_overrides=events,
        )

    def resolve_events(
        self, scenario_id: ScenarioId, events: Iterable[Event]
    ) -> tuple[list[Event], tuple[Diagnostic, ...]]:
        """Apply the chain's event overlays to a ledger sequence.

        Actuals are immutable across every scenario: an overlay targeting a row
        whose ledger status is ``"actual"`` is refused with ``CK-E006`` and
        dropped, and the row passes through untouched. An overlay naming a row
        the ledger does not hold reports ``CK-E014``.

        Returns ``(events, diagnostics)`` in the input order. The ledger itself
        is never written — a scenario is a view over it.
        """
        resolved = self.resolution(scenario_id)
        overlays = dict(resolved.event_overlays)
        diagnostics: list[Diagnostic] = []
        out: list[Event] = []
        for event in events:
            overlay = overlays.pop(event.id, None)
            if overlay is None:
                out.append(event)
                continue
            if event.status == "actual":
                diagnostics.append(
                    make_diagnostic("CK-E006", field="event_overrides", event_id=event.id)
                )
                out.append(event)
                continue
            out.append(event.model_copy(update=_record(overlay)))
        for event_id in sorted(overlays):
            diagnostics.append(
                make_diagnostic("CK-E014", field="event_overrides", event_id=event_id)
            )
        return out, tuple(diagnostics)


def _without(mapping: Mapping[str, object], key: str) -> dict:
    if key not in mapping:
        return dict(mapping)
    return {k: v for k, v in mapping.items() if k != key}
