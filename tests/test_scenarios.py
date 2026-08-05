"""Phase 7 — scenarios: sparse overlays, chain resolution, macros, provenance.

The gate lives in :class:`TestPhase7Gate`; everything else pins the rules the
gate rests on (PRD §4.6, §6.3, ADR-0007, ADR-0009).
"""

from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import cashkit
from cashkit.engine import run as vectorized_run
from cashkit.model import (
    Amount,
    DueTerm,
    Event,
    EventOverlay,
    Item,
    ItemOverlay,
    Scenario,
    Segment,
    Settlement,
)
from cashkit.reference import run as reference_run
from cashkit.sdk import RetagItems, ScaleItems, ScenarioSet, ShiftItems
from cashkit.sdk.scenarios import OVERLAY_FIELDS

from scenario_book import build_scenario_book, monthly_segment


@pytest.fixture
def kit() -> ScenarioSet:
    return ScenarioSet.new(build_scenario_book())


def codes(report) -> list[str]:
    return [diagnostic.code for diagnostic in report.diagnostics]


def retagged(item: Item, **tags: str) -> Item:
    return item.model_copy(update={"tags": {**item.tags, **tags}})


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


class TestPhase7Gate:
    """The four properties the phase is not allowed to pass without."""

    def test_unchanged_set_item_records_nothing(self, kit: ScenarioSet) -> None:
        """An agent that writes an item and changes nothing is told so."""
        kit.fork("base", "downside")
        unchanged = kit.resolve("downside").items["acme"]

        report = kit.set_item("downside", unchanged)

        assert report.empty
        assert report.changed == ()
        assert report.created == ()
        assert codes(report) == ["CK-I002"]
        # "writes nothing" is a statement about the store, not only the report.
        assert kit.scenarios["downside"] == Scenario(id="downside", parent="base")
        assert kit.scenarios["downside"].items == {}
        assert kit.resolve("downside") == kit.resolve("base")

    def test_unchanged_rewrite_of_an_existing_override_records_nothing(
        self, kit: ScenarioSet
    ) -> None:
        """The same, one level down: rewriting an override with its own value."""
        kit.fork("base", "downside")
        kit.set_item("downside", retagged(kit.resolve("base").items["acme"], tier="a"))
        before = kit.scenarios["downside"]

        report = kit.set_item("downside", kit.resolve("downside").items["acme"])

        assert report.empty
        assert codes(report) == ["CK-I002"]
        assert kit.scenarios["downside"] == before

    def test_three_level_fork_chain_resolves(self, kit: ScenarioSet) -> None:
        """Nearest ancestor wins per field, and unrecorded fields fall through."""
        kit.fork("base", "mid")
        kit.fork("mid", "leaf")

        base_acme = kit.resolve("base").items["acme"]
        kit.set_item("mid", retagged(base_acme, tier="silver"))
        mid_acme = kit.resolve("mid").items["acme"]
        kit.set_item(
            "leaf",
            mid_acme.model_copy(
                update={
                    "name": "Acme maintenance (renegotiated)",
                    "settlement": Settlement(
                        due=[DueTerm(share=Decimal(1), offset="90d")]
                    ),
                }
            ),
        )

        leaf = kit.resolve("leaf").items["acme"]
        # leaf's own records win
        assert leaf.name == "Acme maintenance (renegotiated)"
        assert leaf.settlement is not None
        assert leaf.settlement.due[0].offset == "90d"
        # mid's record wins over base for a field leaf did not touch
        assert leaf.tags["tier"] == "silver"
        # base falls through for everything nobody recorded
        assert leaf.segments == base_acme.segments
        assert leaf.flags == base_acme.flags
        # each level is only what it recorded
        assert set(kit.scenarios["mid"].items["acme"].recorded_fields()) == {"tags"}
        assert set(kit.scenarios["leaf"].items["acme"].recorded_fields()) == {
            "name",
            "settlement",
        }
        assert kit.ancestry("leaf") == ("base", "mid", "leaf")

    def test_base_tag_correction_propagates_only_where_not_overridden(
        self, kit: ScenarioSet
    ) -> None:
        """ADR-0009 rule 3, which is the whole reason resolution is field-sparse."""
        kit.fork("base", "keeps_tags")
        kit.fork("base", "owns_tags")

        base_acme = kit.resolve("base").items["acme"]
        # `keeps_tags` overrides a *different* field, so it must still track base.
        kit.set_item("keeps_tags", base_acme.model_copy(update={"name": "Acme (EU)"}))
        # `owns_tags` overrides tags, so it must not.
        kit.set_item("owns_tags", retagged(base_acme, customer="acme_spa"))

        corrected = retagged(base_acme, customer="acme_srl")
        correction = kit.set_item("base", corrected)

        assert correction.changed == ("tags",)
        assert kit.resolve("keeps_tags").items["acme"].tags["customer"] == "acme_srl"
        assert kit.resolve("keeps_tags").items["acme"].name == "Acme (EU)"
        assert kit.resolve("owns_tags").items["acme"].tags["customer"] == "acme_spa"

    def test_base_correction_in_the_authored_book_propagates_the_same_way(
        self, kit: ScenarioSet
    ) -> None:
        """Base is privileged in storage only: correcting the top-level book
        behaves exactly like correcting base's overlay (ADR-0007)."""
        kit.fork("base", "keeps_tags")
        kit.fork("base", "owns_tags")
        base_acme = kit.book.items["acme"]
        kit.set_item("keeps_tags", base_acme.model_copy(update={"name": "Acme (EU)"}))
        kit.set_item("owns_tags", retagged(base_acme, customer="acme_spa"))

        kit.book = kit.book.model_copy(
            update={
                "items": {
                    **kit.book.items,
                    "acme": retagged(base_acme, customer="acme_srl"),
                }
            }
        )

        assert kit.resolve("keeps_tags").items["acme"].tags["customer"] == "acme_srl"
        assert kit.resolve("owns_tags").items["acme"].tags["customer"] == "acme_spa"

    def test_identical_state_by_different_routes_diffs_empty(
        self, kit: ScenarioSet
    ) -> None:
        """Diffs come from resolved books, never from overlays."""
        kit.fork("base", "route_a")
        kit.fork("base", "route_a_child")
        kit.scenarios["route_a_child"] = kit.scenarios["route_a_child"].model_copy(
            update={"parent": "route_a"}
        )
        kit.fork("base", "route_b")

        base_acme = kit.resolve("base").items["acme"]
        target = base_acme.model_copy(
            update={
                "tags": {**base_acme.tags, "tier": "gold"},
                "name": "Acme maintenance (2027 terms)",
            }
        )
        # Route A: two levels, one field each, plus a param set then reverted.
        kit.set_item("route_a", base_acme.model_copy(update={"name": target.name}))
        kit.set_item("route_a_child", target)
        kit.set_param("route_a", "churn", Decimal("0.20"))
        kit.set_param("route_a_child", "churn", Decimal("0.10"))
        # Route B: one level, both fields at once.
        kit.set_item("route_b", target)

        assert kit.scenarios["route_a"].items != kit.scenarios["route_b"].items
        diff = kit.diff("route_a_child", "route_b")
        assert diff.empty, diff
        assert kit.resolve("route_a_child") == kit.resolve("route_b")
        # and the diff is not vacuously empty
        assert not kit.diff("base", "route_b").empty


# --------------------------------------------------------------------------- #
# Resolution rules
# --------------------------------------------------------------------------- #


class TestResolution:
    def test_base_resolves_to_the_authored_book(self, kit: ScenarioSet) -> None:
        assert kit.resolve("base") == kit.book
        assert kit.diagnostics("base") == ()

    def test_no_code_path_branches_on_base(self, kit: ScenarioSet) -> None:
        """A scenario named anything else, with parent=None, behaves identically."""
        kit.scenarios["origin"] = Scenario(id="origin")
        assert kit.resolve("origin") == kit.resolve("base")
        assert kit.ancestry("origin") == ("origin",)

    def test_segments_are_atomic(self, kit: ScenarioSet) -> None:
        """Touch one segment, replace the list — no positional patching."""
        acme = kit.resolve("base").items["acme"]
        kit.fork("base", "shorter")
        trimmed = acme.model_copy(update={"segments": [acme.segments[0]]})

        report = kit.set_item("shorter", trimmed)

        assert report.changed == ("segments",)
        overlay = kit.scenarios["shorter"].items["acme"]
        assert overlay.recorded_fields() == frozenset({"segments"})
        assert overlay.segments == [acme.segments[0]]
        assert kit.resolve("shorter").items["acme"].segments == [acme.segments[0]]

    def test_changing_one_segment_records_the_whole_list(self, kit: ScenarioSet) -> None:
        acme = kit.resolve("base").items["acme"]
        kit.fork("base", "cheaper")
        first = acme.segments[0].model_copy(
            update={"amount": Amount(constant=Decimal("9000.0000"))}
        )
        kit.set_item("cheaper", acme.model_copy(update={"segments": [first, acme.segments[1]]}))

        recorded = kit.scenarios["cheaper"].items["acme"].segments
        assert len(recorded) == 2
        assert recorded[1] == acme.segments[1]

    def test_recorded_none_clears_a_parent_value(self, kit: ScenarioSet) -> None:
        """`settlement=None` is a real state, distinct from not recording it."""
        kit.fork("base", "accrual_only")
        acme = kit.resolve("base").items["acme"]

        report = kit.set_item("accrual_only", acme.model_copy(update={"settlement": None}))

        assert report.changed == ("settlement",)
        overlay = kit.scenarios["accrual_only"].items["acme"]
        assert overlay.recorded_fields() == frozenset({"settlement"})
        assert overlay.settlement is None
        assert kit.resolve("accrual_only").items["acme"].settlement is None

    def test_added_item_is_a_full_item(self, kit: ScenarioSet) -> None:
        kit.fork("base", "expansion")
        new = Item(
            id="paris",
            name="Paris office",
            kind="flow",
            direction="out",
            tags={"cat": "opex", "site": "paris"},
            segments=[monthly_segment(date(2026, 6, 1), None, "-8000.0000")],
            settlement=Settlement(due=[DueTerm(share=Decimal(1), offset="0d")]),
        )

        report = kit.set_item("expansion", new)

        assert report.created == ("paris",)
        assert kit.scenarios["expansion"].added["paris"] == new
        assert "paris" in kit.resolve("expansion").items
        assert "paris" not in kit.resolve("base").items

    def test_removed_item_disappears_down_the_chain(self, kit: ScenarioSet) -> None:
        kit.fork("base", "lean")
        kit.fork("lean", "leaner")

        report = kit.remove_item("lean", "rent")

        assert report.changed == ("removed",)
        assert "rent" not in kit.resolve("lean").items
        assert "rent" not in kit.resolve("leaner").items
        assert "rent" in kit.resolve("base").items

    def test_a_descendant_can_reinstate_a_removed_item(self, kit: ScenarioSet) -> None:
        """The case D-P1-13 deferred to Phase 7."""
        rent = kit.resolve("base").items["rent"]
        kit.fork("base", "lean")
        kit.fork("lean", "restored")
        kit.remove_item("lean", "rent")

        report = kit.set_item("restored", rent)

        assert report.created == ("rent",)
        assert kit.resolve("restored").items["rent"] == rent
        assert "rent" not in kit.resolve("lean").items

    def test_re_adding_in_the_same_scenario_wins_over_its_own_removal(
        self, kit: ScenarioSet
    ) -> None:
        rent = kit.resolve("base").items["rent"]
        kit.fork("base", "swap")
        kit.remove_item("swap", "rent")
        replacement = rent.model_copy(update={"name": "Office rent (new lease)"})

        kit.set_item("swap", replacement)

        assert kit.resolve("swap").items["rent"].name == "Office rent (new lease)"
        assert "rent" not in kit.scenarios["swap"].removed

    def test_removing_an_item_this_scenario_added_just_drops_it(
        self, kit: ScenarioSet
    ) -> None:
        kit.fork("base", "expansion")
        new = Item(id="paris", name="Paris office", kind="flow", segments=[])
        kit.set_item("expansion", new)

        report = kit.remove_item("expansion", "paris")

        assert report.changed == ("added",)
        assert kit.scenarios["expansion"].added == {}
        assert kit.scenarios["expansion"].removed == set()

    def test_unset_reverts_to_the_parent(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        base_acme = kit.resolve("base").items["acme"]
        kit.set_item("downside", retagged(base_acme, tier="bronze"))

        report = kit.unset("downside", "acme")

        assert report.changed == ("tags",)
        assert kit.resolve("downside").items["acme"] == base_acme
        assert "acme" not in kit.scenarios["downside"].items

    def test_unset_of_an_untouched_item_records_nothing(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        report = kit.unset("downside", "acme")
        assert report.empty
        assert codes(report) == ["CK-I002"]

    def test_overlay_on_an_undefined_item_is_diagnosed(self, kit: ScenarioSet) -> None:
        kit.scenarios["broken"] = Scenario(
            id="broken", parent="base", items={"ghost": ItemOverlay(name="Ghost")}
        )
        resolution = kit.resolution("broken")
        assert [d.code for d in resolution.diagnostics] == ["CK-E023"]
        assert "ghost" not in resolution.book.items

    def test_overlay_on_an_item_this_scenario_removed_is_diagnosed(
        self, kit: ScenarioSet
    ) -> None:
        kit.scenarios["contradictory"] = Scenario(
            id="contradictory",
            parent="base",
            items={"rent": ItemOverlay(name="Rent")},
            removed={"rent"},
        )
        assert [d.code for d in kit.diagnostics("contradictory")] == ["CK-E023"]

    def test_unknown_parent_is_a_diagnostic_not_a_crash(self, kit: ScenarioSet) -> None:
        kit.scenarios["orphan"] = Scenario(id="orphan", parent="nowhere")
        resolution = kit.resolution("orphan")
        assert [d.code for d in resolution.diagnostics] == ["CK-E021"]
        assert resolution.book.items == kit.book.items

    def test_a_cyclic_chain_is_a_diagnostic_not_a_hang(self, kit: ScenarioSet) -> None:
        kit.scenarios["a"] = Scenario(id="a", parent="b")
        kit.scenarios["b"] = Scenario(id="b", parent="a")
        resolution = kit.resolution("a")
        assert [d.code for d in resolution.diagnostics] == ["CK-E021"]

    def test_unknown_scenario_on_every_write(self, kit: ScenarioSet) -> None:
        acme = kit.book.items["acme"]
        for report in (
            kit.set_item("nope", acme),
            kit.set_param("nope", "churn", Decimal("0.2")),
            kit.unset("nope", "acme"),
            kit.remove_item("nope", "acme"),
            kit.apply_macro("nope", RetagItems(selector="cat:opex", tags={"a": "b"})),
            kit.fork("nope", "child"),
            kit.flatten("nope", "flat"),
        ):
            assert codes(report) == ["CK-E021"], report

    def test_fork_onto_an_existing_id_is_refused(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        assert codes(kit.fork("base", "downside")) == ["CK-E022"]
        assert codes(kit.flatten("base", "downside")) == ["CK-E022"]

    def test_synthetic_items_are_refused_at_construction(self, kit: ScenarioSet) -> None:
        """Handing scenario resolution `Engine.book` is programmer error."""
        carrier = Item.model_construct(
            id="_event:deadbeef", name="carrier", kind="flow", segments=[]
        )
        augmented = kit.book.model_copy(
            update={"items": {**kit.book.items, "_event:deadbeef": carrier}}
        )
        with pytest.raises(ValueError, match="synthesized"):
            ScenarioSet.new(augmented)


# --------------------------------------------------------------------------- #
# Params
# --------------------------------------------------------------------------- #


class TestParams:
    def test_param_is_recorded_only_when_it_differs(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")

        same = kit.set_param("downside", "churn", Decimal("0.10"))
        different = kit.set_param("downside", "churn", Decimal("0.25"))

        assert same.empty and codes(same) == ["CK-I002"]
        assert different.changed == ("params.churn",)
        assert kit.scenarios["downside"].params == {"churn": Decimal("0.25")}
        assert kit.resolve("downside").params["churn"] == Decimal("0.25")

    def test_setting_a_param_back_to_the_parent_value_drops_the_record(
        self, kit: ScenarioSet
    ) -> None:
        kit.fork("base", "downside")
        kit.set_param("downside", "churn", Decimal("0.25"))

        report = kit.set_param("downside", "churn", Decimal("0.10"))

        assert report.changed == ("params.churn",)
        assert kit.scenarios["downside"].params == {}
        assert kit.resolve("downside").params["churn"] == Decimal("0.10")

    def test_opening_balance_is_the_reserved_key(self, kit: ScenarioSet) -> None:
        kit.fork("base", "raise_round")

        report = kit.set_param("raise_round", "opening_balance", Decimal("2500000.0000"))

        assert report.changed == ("params.opening_balance",)
        resolved = kit.resolve("raise_round")
        assert resolved.opening_balance == Decimal("2500000.0000")
        assert resolved.params["opening_balance"] == Decimal("2500000.0000")
        assert kit.resolve("base").opening_balance == Decimal("100000.0000")

    def test_opening_balance_override_reaches_the_engine(self, kit: ScenarioSet) -> None:
        kit.fork("base", "raise_round")
        kit.set_param("raise_round", "opening_balance", Decimal("2500000.0000"))

        base = vectorized_run(kit.resolve("base"))
        raised = vectorized_run(kit.resolve("raise_round"))

        assert raised.value("cash", "accrual", 0) - base.value("cash", "accrual", 0) == (
            Decimal("2400000.0000")
        )

    def test_opening_balance_must_be_money(self, kit: ScenarioSet) -> None:
        kit.fork("base", "sweep")
        report = kit.set_param("sweep", "opening_balance", Decimal("1.000005"))
        assert codes(report) == ["CK-E024"]
        assert kit.scenarios["sweep"].params == {}

    def test_a_hand_authored_bad_opening_balance_is_diagnosed_at_resolve(
        self, kit: ScenarioSet
    ) -> None:
        kit.scenarios["hand"] = Scenario(
            id="hand", parent="base", params={"opening_balance": Decimal("1.000005")}
        )
        resolution = kit.resolution("hand")
        assert [d.code for d in resolution.diagnostics] == ["CK-E024"]
        assert resolution.book.opening_balance == Decimal("100000.0000")


# --------------------------------------------------------------------------- #
# Macros
# --------------------------------------------------------------------------- #


class TestMacros:
    def test_retag_expands_to_concrete_overrides(self, kit: ScenarioSet) -> None:
        kit.fork("base", "recut")

        report = kit.apply_macro(
            "recut", RetagItems(selector="cat:opex", tags={"cost_centre": "hq"})
        )

        assert set(report.changed) == {"payroll.tags", "rent.tags"}
        overlays = kit.scenarios["recut"].items
        assert set(overlays) == {"payroll", "rent"}
        # Concrete values, not a stored rule: the overlay is a tags map.
        assert overlays["rent"].recorded_fields() == frozenset({"tags"})
        assert overlays["rent"].tags == {
            "cat": "opex",
            "site": "milan",
            "cost_centre": "hq",
        }
        # An item added afterwards is untouched — nothing re-applies.
        kit.set_item(
            "recut",
            Item(id="paris", name="Paris", kind="flow", tags={"cat": "opex"}, segments=[]),
        )
        assert kit.resolve("recut").items["paris"].tags == {"cat": "opex"}

    def test_post_macro_state_is_indistinguishable_from_typing_it_out(
        self, kit: ScenarioSet
    ) -> None:
        kit.fork("base", "by_macro")
        kit.fork("base", "by_hand")

        kit.apply_macro("by_macro", ScaleItems(selector="cat:opex", factor=Decimal("0.8")))
        for item_id in ("payroll", "rent"):
            item = kit.resolve("base").items[item_id]
            segments = [
                segment.model_copy(
                    update={
                        "amount": Amount(
                            constant=(segment.amount.constant * Decimal("0.8")).quantize(
                                Decimal("0.0001")
                            )
                        )
                    }
                )
                for segment in item.segments
            ]
            kit.set_item("by_hand", item.model_copy(update={"segments": segments}))

        assert kit.diff("by_macro", "by_hand").empty
        assert kit.scenarios["by_macro"].items == kit.scenarios["by_hand"].items

    def test_scale_rounds_at_the_authoring_boundary(self, kit: ScenarioSet) -> None:
        kit.fork("base", "trim")
        kit.apply_macro("trim", ScaleItems(selector="cat:revenue", factor=Decimal("0.333")))

        segments = kit.resolve("trim").items["acme"].segments
        assert segments[0].amount.constant == Decimal("3330.0000")
        assert segments[1].amount.constant == Decimal("3996.0000")
        assert all(
            -segment.amount.constant.as_tuple().exponent <= 4 for segment in segments
        )

    def test_shift_moves_the_generative_window_and_its_phase(
        self, kit: ScenarioSet
    ) -> None:
        kit.fork("base", "delayed")

        report = kit.apply_macro("delayed", ShiftItems(selector="customer:acme", by="2m"))

        assert report.changed == ("acme.segments",)
        segments = kit.resolve("delayed").items["acme"].segments
        assert segments[0].start == date(2026, 3, 1)
        assert segments[0].end == date(2027, 3, 1)
        assert segments[1].start == date(2027, 3, 1)
        assert segments[1].end is None
        # ...and the numbers move with it.
        base_total = vectorized_run(kit.resolve("base")).total("acme", "accrual")
        delayed_total = vectorized_run(kit.resolve("delayed")).total("acme", "accrual")
        assert delayed_total != base_total

    def test_shift_moves_explicit_schedule_dates(self, kit: ScenarioSet) -> None:
        kit.fork("base", "scheduled")
        acme = kit.resolve("base").items["acme"]
        scheduled = acme.model_copy(
            update={
                "segments": [
                    acme.segments[0].model_copy(
                        update={
                            "amount": Amount(
                                schedule=[
                                    (date(2026, 3, 31), Decimal("5000.0000")),
                                    (date(2026, 6, 30), Decimal("7000.0000")),
                                ]
                            )
                        }
                    )
                ]
            }
        )
        kit.set_item("scheduled", scheduled)

        kit.apply_macro("scheduled", ShiftItems(selector="customer:acme", by="1m"))

        schedule = kit.resolve("scheduled").items["acme"].segments[0].amount.schedule
        assert schedule == [
            (date(2026, 4, 30), Decimal("5000.0000")),
            (date(2026, 7, 30), Decimal("7000.0000")),
        ]

    def test_a_macro_matching_nothing_records_nothing(self, kit: ScenarioSet) -> None:
        kit.fork("base", "noop")
        report = kit.apply_macro("noop", RetagItems(selector="cat:nothing", tags={"a": "b"}))
        assert report.empty
        assert codes(report) == ["CK-I002"]
        assert kit.scenarios["noop"].items == {}

    def test_a_macro_changing_nothing_records_nothing(self, kit: ScenarioSet) -> None:
        kit.fork("base", "noop")
        report = kit.apply_macro("noop", ScaleItems(selector="cat:opex", factor=Decimal(1)))
        assert report.empty
        assert kit.scenarios["noop"].items == {}

    def test_a_malformed_selector_is_rejected(self, kit: ScenarioSet) -> None:
        kit.fork("base", "bad")
        report = kit.apply_macro("bad", RetagItems(selector="not a selector", tags={}))
        assert codes(report) == ["CK-E003"]

    def test_macros_compose_and_stay_field_sparse(self, kit: ScenarioSet) -> None:
        kit.fork("base", "combo")
        kit.apply_macro("combo", RetagItems(selector="cat:opex", tags={"scope": "core"}))
        kit.apply_macro("combo", ScaleItems(selector="scope:core", factor=Decimal("0.5")))

        overlay = kit.scenarios["combo"].items["rent"]
        assert overlay.recorded_fields() == frozenset({"tags", "segments"})
        assert overlay.segments[0].amount.constant == Decimal("-2000.0000")


# --------------------------------------------------------------------------- #
# Provenance, diff, flatten
# --------------------------------------------------------------------------- #


class TestProvenance:
    def test_provenance_names_the_recording_ancestor_per_field(
        self, kit: ScenarioSet
    ) -> None:
        kit.fork("base", "mid")
        kit.fork("mid", "leaf")
        base_acme = kit.resolve("base").items["acme"]
        kit.set_item("mid", retagged(base_acme, tier="silver"))
        kit.set_item(
            "leaf", kit.resolve("mid").items["acme"].model_copy(update={"name": "Acme+"})
        )

        provenance = kit.provenance("leaf", "acme")

        assert provenance.exists
        assert {origin.field for origin in provenance.fields} == set(OVERLAY_FIELDS)
        assert provenance.origin_of("name").scenario == "leaf"
        assert provenance.origin_of("name").kind == "overlay"
        assert provenance.origin_of("tags").scenario == "mid"
        assert provenance.origin_of("segments").scenario is None
        assert provenance.origin_of("segments").kind == "book"

    def test_provenance_of_an_added_item(self, kit: ScenarioSet) -> None:
        kit.fork("base", "expansion")
        kit.set_item("expansion", Item(id="paris", name="Paris", kind="flow", segments=[]))

        provenance = kit.provenance("expansion", "paris")

        assert provenance.exists
        assert all(origin.kind == "added" for origin in provenance.fields)
        assert all(origin.scenario == "expansion" for origin in provenance.fields)

    def test_provenance_of_a_removed_item(self, kit: ScenarioSet) -> None:
        kit.fork("base", "lean")
        kit.fork("lean", "leaner")
        kit.remove_item("lean", "rent")

        provenance = kit.provenance("leaner", "rent")

        assert not provenance.exists
        assert provenance.removed_by == "lean"
        assert provenance.fields == ()

    def test_provenance_of_an_unknown_item(self, kit: ScenarioSet) -> None:
        provenance = kit.provenance("base", "nope")
        assert not provenance.exists
        assert provenance.removed_by is None


class TestDiff:
    def test_diff_reports_added_removed_and_changed(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        kit.remove_item("downside", "rent")
        kit.set_item("downside", Item(id="paris", name="Paris", kind="flow", segments=[]))
        kit.set_item(
            "downside", retagged(kit.resolve("base").items["acme"], tier="bronze")
        )
        kit.set_param("downside", "churn", Decimal("0.30"))

        diff = kit.diff("base", "downside")

        assert not diff.empty
        assert {(d.item_id, d.status) for d in diff.items} == {
            ("rent", "removed"),
            ("paris", "added"),
            ("acme", "changed"),
        }
        assert next(d for d in diff.items if d.item_id == "acme").fields == ("tags",)
        assert diff.params == (
            kit.diff("base", "downside").params[0],
        )
        assert diff.params[0].key == "churn"
        assert diff.params[0].left == Decimal("0.10")
        assert diff.params[0].right == Decimal("0.30")

    def test_diff_reports_an_opening_balance_override(self, kit: ScenarioSet) -> None:
        kit.fork("base", "funded")
        kit.set_param("funded", "opening_balance", Decimal("500000.0000"))

        diff = kit.diff("base", "funded")

        assert diff.opening_balance == (Decimal("100000.0000"), Decimal("500000.0000"))
        assert not diff.empty

    def test_a_scenario_diffs_empty_against_itself(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        kit.set_param("downside", "churn", Decimal("0.3"))
        assert kit.diff("downside", "downside").empty


class TestFlatten:
    def test_flatten_collapses_the_chain(self, kit: ScenarioSet) -> None:
        kit.fork("base", "mid")
        kit.fork("mid", "leaf")
        base_acme = kit.resolve("base").items["acme"]
        kit.set_item("mid", retagged(base_acme, tier="silver"))
        kit.set_item("leaf", kit.resolve("mid").items["acme"].model_copy(update={"name": "A+"}))
        kit.remove_item("leaf", "rent")
        kit.set_item("leaf", Item(id="paris", name="Paris", kind="flow", segments=[]))
        kit.set_param("leaf", "churn", Decimal("0.4"))

        report = kit.flatten("leaf", "leaf_flat")

        assert report.created == ("leaf_flat",)
        flat = kit.scenarios["leaf_flat"]
        assert flat.parent is None
        assert flat.removed == {"rent"}
        assert set(flat.added) == {"paris"}
        assert flat.items["acme"].recorded_fields() == frozenset({"name", "tags"})
        assert kit.diff("leaf", "leaf_flat").empty
        assert kit.resolve("leaf_flat") == kit.resolve("leaf")

    def test_a_flattened_scenario_is_an_ordinary_scenario(self, kit: ScenarioSet) -> None:
        kit.fork("base", "mid")
        kit.set_item("mid", retagged(kit.resolve("base").items["acme"], tier="silver"))
        kit.flatten("mid", "standalone")

        kit.fork("standalone", "child")
        kit.set_item(
            "child", kit.resolve("standalone").items["rent"].model_copy(update={"name": "R"})
        )

        assert kit.resolve("child").items["acme"].tags["tier"] == "silver"
        assert kit.resolve("child").items["rent"].name == "R"


# --------------------------------------------------------------------------- #
# Events: actuals are immutable across every scenario
# --------------------------------------------------------------------------- #


class TestEventOverrides:
    def _events(self) -> list[Event]:
        return [
            Event(
                id="a1",
                date=date(2026, 2, 10),
                amount=Decimal("5000.0000"),
                status="actual",
                item="acme",
            ),
            Event(
                id="f1",
                date=date(2026, 3, 10),
                amount=Decimal("7000.0000"),
                status="forecast",
                item="acme",
            ),
        ]

    def test_a_forecast_event_can_be_overridden(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        kit.scenarios["downside"] = kit.scenarios["downside"].model_copy(
            update={"event_overrides": {"f1": EventOverlay(amount=Decimal("3000.0000"))}}
        )

        events, diagnostics = kit.resolve_events("downside", self._events())

        assert diagnostics == ()
        assert events[1].amount == Decimal("3000.0000")
        assert events[1].status == "forecast"
        assert events[0].amount == Decimal("5000.0000")

    def test_an_actual_is_never_touched(self, kit: ScenarioSet) -> None:
        kit.fork("base", "rewrite_history")
        kit.scenarios["rewrite_history"] = kit.scenarios["rewrite_history"].model_copy(
            update={"event_overrides": {"a1": EventOverlay(amount=Decimal("1.0000"))}}
        )

        events, diagnostics = kit.resolve_events("rewrite_history", self._events())

        assert [d.code for d in diagnostics] == ["CK-E006"]
        assert events[0].amount == Decimal("5000.0000")

    def test_an_overlay_cannot_even_spell_an_actual(self) -> None:
        with pytest.raises(Exception):
            EventOverlay(status="actual")

    def test_an_overlay_naming_a_missing_row_is_diagnosed(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        kit.scenarios["downside"] = kit.scenarios["downside"].model_copy(
            update={"event_overrides": {"gone": EventOverlay(note="x")}}
        )
        _, diagnostics = kit.resolve_events("downside", self._events())
        assert [d.code for d in diagnostics] == ["CK-E014"]

    def test_event_overlays_resolve_field_sparse_along_the_chain(
        self, kit: ScenarioSet
    ) -> None:
        kit.fork("base", "mid")
        kit.fork("mid", "leaf")
        kit.scenarios["mid"] = kit.scenarios["mid"].model_copy(
            update={
                "event_overrides": {
                    "f1": EventOverlay(amount=Decimal("3000.0000"), note="halved")
                }
            }
        )
        kit.scenarios["leaf"] = kit.scenarios["leaf"].model_copy(
            update={"event_overrides": {"f1": EventOverlay(date=date(2026, 4, 10))}}
        )

        events, _ = kit.resolve_events("leaf", self._events())

        assert events[1].amount == Decimal("3000.0000")
        assert events[1].note == "halved"
        assert events[1].date == date(2026, 4, 10)

    def test_event_overrides_show_up_in_diff(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        kit.scenarios["downside"] = kit.scenarios["downside"].model_copy(
            update={"event_overrides": {"f1": EventOverlay(amount=Decimal("1.0000"))}}
        )
        assert kit.diff("base", "downside").event_overrides == ("f1",)


# --------------------------------------------------------------------------- #
# A resolved scenario is a book both engines evaluate
# --------------------------------------------------------------------------- #


class TestResolvedBooksEvaluate:
    def test_both_engines_agree_on_a_resolved_scenario(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        kit.apply_macro("downside", ScaleItems(selector="cat:revenue", factor=Decimal("0.7")))
        kit.apply_macro("downside", ShiftItems(selector="cat:opex", by="1m"))
        kit.set_param("downside", "opening_balance", Decimal("80000.0000"))
        book = kit.resolve("downside")

        fast = vectorized_run(book)
        oracle = reference_run(book)

        assert fast.diagnostic_keys() == oracle.diagnostic_keys()
        for item_id in book.items:
            for measure in ("accrual", "cash"):
                assert (
                    fast.column(item_id, measure) == oracle.column(item_id, measure)
                ).all(), (item_id, measure)

    def test_a_downside_scenario_actually_moves_the_numbers(
        self, kit: ScenarioSet
    ) -> None:
        kit.fork("base", "downside")
        kit.apply_macro("downside", ScaleItems(selector="cat:revenue", factor=Decimal("0.7")))

        base = vectorized_run(kit.resolve("base"))
        downside = vectorized_run(kit.resolve("downside"))

        assert downside.total("acme", "accrual") == (
            base.total("acme", "accrual") * Decimal("0.7")
        )
        assert downside.value("cash", "accrual", 700) < base.value("cash", "accrual", 700)

    def test_scenario_resolution_survives_a_book_the_engine_augments(
        self, kit: ScenarioSet
    ) -> None:
        """The augmented book is the engine's; the authored one stays the SDK's."""
        events = [
            Event(
                id="fee1",
                date=date(2026, 2, 10),
                amount=Decimal("-120.0000"),
                status="actual",
                tags={"cat": "opex"},
            )
        ]
        from cashkit.engine import Engine

        engine = Engine(kit.resolve("base"), events=tuple(events))
        engine.run()

        assert any(item_id.startswith("_event:") for item_id in engine.book.items)
        assert kit.resolve("base") == kit.book
        with pytest.raises(ValueError, match="synthesized"):
            ScenarioSet.new(engine.book)

    def test_resolution_never_carries_synthetic_items(self, kit: ScenarioSet) -> None:
        kit.fork("base", "downside")
        events = [
            Event(
                id="fee1",
                date=date(2026, 2, 10),
                amount=Decimal("-120.0000"),
                status="actual",
                tags={"cat": "opex"},
            )
        ]
        book = kit.resolve("downside")
        result = vectorized_run(book, events=events)

        assert any(item_id.startswith("_event:") for item_id in result.accrual)
        # ...and none of them reached the authored side.
        assert not any(item_id.startswith("_") for item_id in book.items)
        assert not any(item_id.startswith("_") for item_id in kit.book.items)


# --------------------------------------------------------------------------- #
# Structural guarantees — proved from the source, not only from behaviour
# --------------------------------------------------------------------------- #


SDK_ROOT = Path(cashkit.__file__).parent / "sdk"


def _sdk_sources() -> list[Path]:
    paths = sorted(SDK_ROOT.rglob("*.py"))
    assert paths, "sdk source discovery is broken"
    return paths


#: The modules that can write an overlay. Phase 10 added read-only modules that
#: legitimately walk a segment list — ``trace()`` must be able to explain
#: "12 000 x 1.03^2 x 0.9" (ADR-0013), and ``validate()`` must be able to check
#: every authored amount's sign — so the positional-patching guard is scoped to
#: the write path and paired with
#: :func:`test_only_the_overlay_writers_can_construct_an_item_overlay`, which
#: proves that scope is the whole of it.
_OVERLAY_WRITERS = ("scenarios.py", "macros.py", "kit.py")


def _overlay_write_sources() -> list[Path]:
    paths = [path for path in _sdk_sources() if path.name in _OVERLAY_WRITERS]
    assert len(paths) == len(_OVERLAY_WRITERS), "overlay-writer discovery is broken"
    return paths


def test_only_the_overlay_writers_can_construct_an_item_overlay() -> None:
    """The scope of the positional-patching guard is the whole write path.

    If a module outside ``_OVERLAY_WRITERS`` ever builds an ``ItemOverlay``, the
    guard below stops covering everything it claims to, and this fails first.
    """
    builders = []
    for path in _sdk_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ItemOverlay"
            ):
                builders.append(path.name)
    assert set(builders) <= set(_OVERLAY_WRITERS), sorted(set(builders))


def test_segments_is_never_patched_positionally() -> None:
    """Non-negotiable #8, proved for inputs no test thought of.

    An overlay's ``segments`` is recorded whole or not at all. The failure this
    guards is a merge routine that indexes into the parent's list — the exact
    list-merge semantics PRD D5 says these systems die of. Scoped to the modules
    that can write an overlay: reading a segment list to *explain* it is what
    ``trace()`` exists for, and banning that would ban the feature ADR-0013
    requires.
    """
    violations: list[str] = []
    for path in _overlay_write_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _names_segments(node.value):
                violations.append(f"{path}:{node.lineno}: segments[...] subscript")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"zip", "enumerate"} and any(
                    _names_segments(arg) for arg in node.args
                ):
                    violations.append(
                        f"{path}:{node.lineno}: {node.func.id}() over segments"
                    )
    assert not violations, "positional segment patching:\n" + "\n".join(violations)


def _names_segments(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "segments"
    if isinstance(node, ast.Name):
        return node.id == "segments"
    return False


def test_no_code_path_branches_on_the_base_scenario() -> None:
    """ADR-0007's testable consequence: grep for base-conditionals, find none."""
    violations: list[str] = []
    for path in _sdk_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                for operand in operands:
                    if isinstance(operand, ast.Constant) and operand.value == "base":
                        violations.append(f"{path}:{node.lineno}: comparison to 'base'")
    assert not violations, "base is special-cased:\n" + "\n".join(violations)
