"""Unit tests pinning the canonical emitter's format rules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fixture_book import build_fixture_book

from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    EventOverlay,
    Item,
    ItemOverlay,
    PeriodRange,
    Scenario,
    Settlement,
    from_canonical_yaml,
    to_canonical_yaml,
)

FIXTURE = Path(__file__).parent / "fixtures" / "canonical_book.yaml"


def _minimal_book(**overrides: object) -> Book:
    kwargs: dict[str, object] = dict(
        id="b",
        calendar=CalendarSpec(),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        opening_balance=Decimal("0"),
        cutover=date(2026, 1, 1),
    )
    kwargs.update(overrides)
    return Book(**kwargs)  # type: ignore[arg-type]


class TestGoldenFixture:
    def test_serialize_matches_committed_bytes(self) -> None:
        """The committed golden file is byte-identical to a fresh serialize.

        This is the cross-version phantom-diff guard: any emitter change that
        alters the canonical form of existing documents fails here.
        """
        expected = FIXTURE.read_text(encoding="utf-8")
        assert to_canonical_yaml(build_fixture_book()) == expected

    def test_parse_of_committed_fixture_roundtrips(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        assert to_canonical_yaml(from_canonical_yaml(text, Book)) == text

    def test_fixture_has_lf_endings_and_trailing_newline(self) -> None:
        raw = FIXTURE.read_bytes()
        assert b"\r" not in raw
        assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")


class TestFormatRules:
    def test_field_order_is_declaration_order(self) -> None:
        text = to_canonical_yaml(_minimal_book())
        top_level = [line.split(":")[0] for line in text.splitlines() if line[0] != " "]
        assert top_level == [
            "id",
            "base_grain",
            "calendar",
            "horizon",
            "opening_balance",
            "cutover",
            "params",
            "items",
            "tax_regimes",
        ]

    def test_none_fields_are_omitted(self) -> None:
        text = to_canonical_yaml(_minimal_book())
        # ledger_watermark is None and CalendarSpec.country is None.
        assert "ledger_watermark" not in text
        assert "country" not in text

    def test_decimals_are_quoted_and_exact(self) -> None:
        book = _minimal_book(opening_balance=Decimal("0.10"))
        text = to_canonical_yaml(book)
        assert 'opening_balance: "0.10"' in text
        parsed = from_canonical_yaml(text, Book)
        assert str(parsed.opening_balance) == "0.10"  # no 0.1 phantom diff

    def test_dates_are_quoted_iso(self) -> None:
        assert 'cutover: "2026-01-01"' in to_canonical_yaml(_minimal_book())

    def test_no_flow_style_for_nonempty_collections(self) -> None:
        text = to_canonical_yaml(build_fixture_book())
        for line in text.splitlines():
            # Brackets/braces may appear only inside quoted strings or as the
            # empty-collection forms "[]" / "{}".
            bare = line.split('"')[0] if '"' in line else line
            stripped = bare.replace(": []", "").replace(": {}", "")
            assert "[" not in stripped and "{" not in stripped, line

    def test_empty_collections_are_explicit(self) -> None:
        item = Item(id="x", name="x", kind="derived", settlement=Settlement(due=[]))
        text = to_canonical_yaml(item)
        assert "tags: {}" in text
        assert "flags: []" in text
        assert "segments: []" in text
        # Settlement(due=[]) (never settles) is distinguishable from no
        # settlement at all — semantically load-bearing (PRD §4.4).
        assert "due: []" in text
        no_settlement = Item(id="x", name="x", kind="derived")
        assert "settlement" not in to_canonical_yaml(no_settlement)

    def test_mapping_keys_sorted_and_quoted(self) -> None:
        book = _minimal_book(
            params={"zeta": Decimal("1"), "alpha": Decimal("2"), "mid": Decimal("3")}
        )
        text = to_canonical_yaml(book)
        assert text.index('"alpha"') < text.index('"mid"') < text.index('"zeta"')

    def test_string_escaping_is_yaml_safe(self) -> None:
        item = Item(id="x", name='line1\nline2\t"quoted" \\ 100% ©', kind="flow")
        text = to_canonical_yaml(item)
        assert from_canonical_yaml(text, Item).name == item.name

    def test_sets_serialize_sorted(self) -> None:
        item = Item(id="x", name="x", kind="flow", flags={"zeta", "alpha"})
        text = to_canonical_yaml(item)
        assert text.index('"alpha"') < text.index('"zeta"')

    def test_holidays_canonicalized_sorted_deduped(self) -> None:
        cal = CalendarSpec(
            holidays=[date(2026, 12, 25), date(2026, 1, 1), date(2026, 1, 1)]
        )
        assert cal.holidays == [date(2026, 1, 1), date(2026, 12, 25)]

    def test_schedule_serializes_as_date_amount_maps(self) -> None:
        amount = Amount(
            schedule=[(date(2026, 1, 31), Decimal("100.00"))]
        )
        text = to_canonical_yaml(amount)
        assert 'date: "2026-01-31"' in text
        assert 'amount: "100.00"' in text
        parsed = from_canonical_yaml(text, Amount)
        assert parsed.schedule == ((date(2026, 1, 31), Decimal("100.00")),) or (
            parsed.schedule == [(date(2026, 1, 31), Decimal("100.00"))]
        )


class TestOverlayRecordedness:
    def test_unrecorded_fields_are_omitted(self) -> None:
        overlay = ItemOverlay(name="only this")
        assert to_canonical_yaml(overlay) == 'name: "only this"\n'

    def test_recorded_none_emits_explicit_null(self) -> None:
        overlay = ItemOverlay(settlement=None)
        text = to_canonical_yaml(overlay)
        assert text == "settlement: null\n"
        parsed = from_canonical_yaml(text, ItemOverlay)
        assert parsed.recorded_fields() == frozenset({"settlement"})
        assert parsed.settlement is None

    def test_recorded_set_distinguishes_equal_values(self) -> None:
        recorded = ItemOverlay(tags={})
        unrecorded = ItemOverlay()
        assert recorded != unrecorded
        assert to_canonical_yaml(recorded) != to_canonical_yaml(unrecorded)

    def test_empty_overlay_serializes_as_empty_document(self) -> None:
        overlay = ItemOverlay()
        text = to_canonical_yaml(overlay)
        assert text == "{}\n"
        assert from_canonical_yaml(text, ItemOverlay).recorded_fields() == frozenset()

    def test_event_overlay_cannot_represent_actual(self) -> None:
        with pytest.raises(Exception):
            EventOverlay(status="actual")


class TestScenarioSerialization:
    def test_base_scenario_shell(self) -> None:
        """Base is a scenario with parent=None — an (almost) empty overlay."""
        base = Scenario(id="base")
        text = to_canonical_yaml(base)
        assert text == (
            'id: "base"\n'
            'note: ""\n'
            "params: {}\n"
            "items: {}\n"
            "added: {}\n"
            "removed: []\n"
            "event_overrides: {}\n"
        )
        assert from_canonical_yaml(text, Scenario) == base
