"""Phase 1 gate: canonical serialization round-trips, byte-for-byte.

``parse(serialize(x)) == x`` and ``serialize(parse(s)) == s`` for arbitrary
valid models. Phantom diffs are a build failure, not a warning: any assertion
here failing fails the suite.
"""

from __future__ import annotations

from hypothesis import given, settings
from pydantic import BaseModel

from strategies import books, event_overlays, events, item_overlays, scenarios

from cashkit.model import (
    Book,
    Event,
    EventOverlay,
    ItemOverlay,
    Scenario,
    from_canonical_yaml,
    to_canonical_yaml,
)


def _assert_roundtrip(model: BaseModel, model_type: type[BaseModel]) -> None:
    text = to_canonical_yaml(model)

    # Canonical text invariants: LF only, exactly one trailing newline.
    assert "\r" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")

    parsed = from_canonical_yaml(text, model_type)
    assert parsed == model, "parse(serialize(x)) != x"

    reserialized = to_canonical_yaml(parsed)
    assert reserialized == text, "serialize(parse(s)) != s (phantom diff)"


@settings(max_examples=250, deadline=None)
@given(books())
def test_book_roundtrip(book: Book) -> None:
    """The gate property: 200+ arbitrary valid Books, zero failures."""
    _assert_roundtrip(book, Book)


@settings(max_examples=150, deadline=None)
@given(scenarios())
def test_scenario_roundtrip(scenario: Scenario) -> None:
    """Scenarios exercise the sparse-overlay recordedness machinery."""
    _assert_roundtrip(scenario, Scenario)


@settings(max_examples=150, deadline=None)
@given(events())
def test_event_roundtrip(event: Event) -> None:
    _assert_roundtrip(event, Event)


@settings(max_examples=150, deadline=None)
@given(item_overlays())
def test_item_overlay_roundtrip(overlay: ItemOverlay) -> None:
    """Recorded-field sets survive the round trip exactly (ADR-0009)."""
    text = to_canonical_yaml(overlay)
    parsed = from_canonical_yaml(text, ItemOverlay)
    assert parsed.recorded_fields() == overlay.recorded_fields()
    _assert_roundtrip(overlay, ItemOverlay)


@settings(max_examples=150, deadline=None)
@given(event_overlays())
def test_event_overlay_roundtrip(overlay: EventOverlay) -> None:
    text = to_canonical_yaml(overlay)
    parsed = from_canonical_yaml(text, EventOverlay)
    assert parsed.recorded_fields() == overlay.recorded_fields()
    _assert_roundtrip(overlay, EventOverlay)
