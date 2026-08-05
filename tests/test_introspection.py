"""Phase 10 — introspection: trace, why_zero, depends_on, describe_book.

The gates (PROMPT §Phase 10):

1. ``trace()`` on **any** cell of a 50-item fixture returns formula, resolved
   bindings and arithmetic to depth 3 with **no ``None`` fields**.
2. ``why_zero()`` distinguishes all five zero causes.
3. ``describe_book()`` output is complete enough that a fresh agent, given only
   that output, writes a working ``pivot()`` call with no invalid field names.

Gate 3 is the awkward one to assert, because "a fresh agent" is not a fixture.
It is tested as its mechanical equivalent, in both directions: an agent
simulator that sees **only the serialized description** — never the Book —
builds every call the vocabulary licenses and they all run; and every field name
*outside* the description is rejected by the store. A description that omitted a
legal value would fail the first; one that invented an illegal value would fail
the second.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from gate_book import build_benchmark_book, build_gate_book
from scenario_book import build_scenario_book

from cashkit.engine.formula import parse_formula
from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    DueTerm,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
)
from cashkit.model.introspection import ZERO_CAUSES
from cashkit.sdk import CashKit, describe_book, depends_on, dependents_of, trace, why_zero
from cashkit.sdk.introspection import render_expr
from cashkit.stores.frames import DuckdbFrameStore


def _kit(tmp_path: Path, book: Book, name: str = "book") -> CashKit:
    return CashKit.init(tmp_path / name, book)


@pytest.fixture(scope="module")
def benchmark_run(tmp_path_factory):
    book = build_benchmark_book(items=50, years=5)
    kit = CashKit.init(tmp_path_factory.mktemp("bench") / "book", book)
    return kit.run("base")


@pytest.fixture()
def gate_run(tmp_path: Path):
    return _kit(tmp_path, build_gate_book()).run("base")


def _no_none_fields(node) -> list[str]:
    return [
        name for name in type(node).model_fields if getattr(node, name) is None
    ]


# --------------------------------------------------------------------------- #
# Gate 1 — trace() on any cell, depth 3, no None fields
# --------------------------------------------------------------------------- #


class TestGateTrace:
    def test_every_sampled_cell_of_a_50_item_fixture_traces_completely(
        self, benchmark_run
    ) -> None:
        """Both measures, every item, periods spread across a five-year horizon.

        1,100 cells rather than all 182,600: the sample strides the horizon so it
        crosses segment boundaries, escalation anniversaries and settlement lags,
        and every cell is checked to depth 3 for a ``None`` field, a missing
        binding and an explanation that does not add up.
        """
        length = len(benchmark_run.result.periods)
        problems: list[str] = []
        traced = 0
        for item_id in sorted(benchmark_run.result.accrual):
            for measure in ("accrual", "cash"):
                for period in range(0, length, 173):
                    traced += 1
                    result = trace(
                        benchmark_run, item_id, period, measure=measure, depth=3
                    )
                    for node in result.walk():
                        problems.extend(
                            f"{node.item_id}[{period}].{name} is None"
                            for name in _no_none_fields(node)
                        )
                        if not node.formula.strip():
                            problems.append(f"{node.item_id}[{period}] has no formula")
                        if not node.reconciles:
                            problems.append(
                                f"{node.item_id}[{period}] does not reconcile"
                            )
        assert traced >= 1000
        assert problems == [], problems[:10]

    def test_a_derived_cell_reports_formula_bindings_and_arithmetic(
        self, gate_run
    ) -> None:
        derived = [
            item_id
            for item_id, item in sorted(gate_run.book.items.items())
            if item.kind in ("derived", "stock") and item.formula
        ]
        assert derived, "the gate book must contain derived items"
        result = trace(gate_run, derived[0], 40, depth=3)
        assert result.kind == "formula"
        assert result.formula == gate_run.book.items[derived[0]].formula
        assert result.bindings, "a derived cell with a formula must resolve bindings"
        assert result.steps, "a derived cell must show its arithmetic"
        assert all(step.rounding for step in result.steps)
        assert result.value == result.steps[-1].value

    def test_depth_three_really_descends_three_levels(self, gate_run) -> None:
        """The gate says "to depth 3", so at least one chain must reach it."""
        reaches = []
        for item_id in sorted(gate_run.book.items):
            result = trace(gate_run, item_id, 60, depth=3)
            deepest = min(node.depth for node in result.walk())
            reaches.append((item_id, result.depth - deepest))
        assert max(reach for _, reach in reaches) == 3, reaches

    def test_a_truncated_trace_says_so_rather_than_looking_like_a_leaf(
        self, gate_run
    ) -> None:
        derived = next(
            item_id
            for item_id, item in sorted(gate_run.book.items.items())
            if item.kind == "derived" and item.formula
        )
        shallow = trace(gate_run, derived, 40, depth=0)
        assert shallow.children == ()
        assert shallow.truncated is True
        deep = trace(gate_run, derived, 40, depth=1)
        assert deep.children and deep.truncated is False

    def test_a_generated_cell_shows_the_canonical_rounding_order(
        self, tmp_path: Path
    ) -> None:
        """ADR-0013 asks the popover for '12 000 x 1.03^2 x 0.9'. This is it."""
        book = Book(
            id="popover",
            calendar=CalendarSpec(),
            horizon=PeriodRange(start=date(2026, 1, 1), end=date(2030, 1, 1)),
            opening_balance=Decimal(0),
            cutover=date(2026, 1, 1),
            params={"esc": Decimal("0.03")},
            items={
                "contract": Item(
                    id="contract",
                    name="Escalating retainer",
                    kind="flow",
                    direction="in",
                    segments=[
                        Segment(
                            start=date(2026, 1, 1),
                            recurrence=Recurrence(
                                every=1, unit=Grain.MONTH, anchor="day_of_month", day=1
                            ),
                            amount=Amount(constant=Decimal("12000.00")),
                            escalation={"rate": "esc", "every_years": 1},
                            probability=Decimal("0.9"),
                        )
                    ],
                )
            },
        )
        run = _kit(tmp_path, book).run("base")
        # 1 Jan 2028 is two escalation anniversaries in.
        index = run.result.periods.index_of(date(2028, 1, 1))
        result = trace(run, "contract", index, measure="accrual")

        operations = [step.operation for step in result.steps]
        assert operations == [
            "base amount",
            "escalation",
            "probability weighting",
            "segment total",
        ]
        assert result.steps[0].value == Decimal("12000.0000")
        assert result.steps[1].value == Decimal("12730.8000")  # 12000 x 1.03^2
        assert result.steps[2].value == Decimal("11457.7200")  # x 0.9
        assert result.value == result.steps[-1].value
        assert result.reconciles

        kinds = {binding.kind for binding in result.bindings}
        assert kinds == {"segment", "escalation", "probability"}
        escalation = next(b for b in result.bindings if b.kind == "escalation")
        assert escalation.value == Decimal("1.0609")
        assert escalation.target == "esc"

    def test_prev_reports_the_period_it_reached_and_when_it_used_init(
        self, tmp_path: Path
    ) -> None:
        book = Book(
            id="feedback",
            calendar=CalendarSpec(),
            horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 2, 1)),
            opening_balance=Decimal("1000.00"),
            cutover=date(2026, 1, 1),
            items={
                "balance": Item(
                    id="balance",
                    name="Cash balance",
                    kind="stock",
                    formula='prev("balance", n=1, init=p.opening_balance) + it("fee")',
                ),
                "fee": Item(
                    id="fee",
                    name="Daily fee",
                    kind="flow",
                    direction="out",
                    segments=[
                        Segment(
                            start=date(2026, 1, 1),
                            recurrence=Recurrence(every=1, unit=Grain.DAY),
                            amount=Amount(constant=Decimal("-10.00")),
                        )
                    ],
                ),
            },
        )
        run = _kit(tmp_path, book).run("base")

        first = trace(run, "balance", 0, depth=1)
        lagged = next(b for b in first.bindings if b.kind == "lagged")
        assert "init" in lagged.source
        assert lagged.value == Decimal("1000.0000")

        later = trace(run, "balance", 5, depth=1)
        lagged = next(b for b in later.bindings if b.kind == "lagged")
        assert lagged.source == "period 4"
        assert lagged.value == run.result.value("balance", "accrual", 4)

    def test_an_aggregate_binding_names_the_items_the_selector_resolved(
        self, gate_run
    ) -> None:
        found = None
        for item_id, item in sorted(gate_run.book.items.items()):
            if item.formula and "agg(" in item.formula:
                found = item_id
                break
        assert found is not None, "the gate book must exercise agg()"
        result = trace(gate_run, found, 45, depth=1)
        aggregate = next(b for b in result.bindings if b.kind == "aggregate")
        assert aggregate.target, "an agg() binding must name its resolved items"
        assert "resolved at graph-build time" in aggregate.source
        assert aggregate.detail

    def test_a_tax_item_traces_to_its_regime_not_to_nothing(self, tmp_path: Path) -> None:
        from vat_book import build_f24_book

        run = _kit(tmp_path, build_f24_book()).run("base")
        synthetic = [key for key in run.result.accrual if key.startswith("_tax:")]
        assert synthetic
        for item_id in synthetic:
            result = trace(run, item_id, 120)
            assert result.kind == "tax"
            assert "regime" in result.formula
            assert not _no_none_fields(result)

    def test_trace_refuses_a_bad_period_or_measure_as_programmer_error(
        self, gate_run
    ) -> None:
        item_id = sorted(gate_run.book.items)[0]
        with pytest.raises(ValueError):
            trace(gate_run, item_id, 10**6)
        with pytest.raises(ValueError):
            trace(gate_run, item_id, 0, measure="vat")
        with pytest.raises(ValueError):
            trace(gate_run, item_id, date(1999, 1, 1))
        with pytest.raises(KeyError):
            trace(gate_run, "no_such_item", 0)

    def test_a_trace_by_date_and_by_index_agree(self, gate_run) -> None:
        item_id = sorted(gate_run.book.items)[0]
        day = gate_run.result.periods.starts[30]
        assert trace(gate_run, item_id, 30) == trace(gate_run, item_id, day)


class TestRenderExpr:
    @pytest.mark.parametrize(
        "source",
        [
            'it("a") + it("b")',
            'prev("cash", n=2, init=0) * p.rate',
            'agg(tag="cat:revenue") - agg(tag="cat:opex")',
            'where(t.is_quarter_end, cum("a"), 0)',
            'clip(it("a"), 0, 100) + min(it("b"), 5) + abs_(it("c"))',
            'round_(it("a") / p.rate, ndigits=2)',
            'where(it("a") > 0 and it("b") <= 1, -it("a"), it("b"))',
        ],
    )
    def test_a_rendering_reparses_to_the_same_tree(self, source: str) -> None:
        """A trace quotes sub-expressions; a paraphrase would be a lie in a
        place a reader is entitled to trust."""
        first = parse_formula(source)
        assert first.expr is not None, first.diagnostics
        again = parse_formula(render_expr(first.expr))
        assert again.expr is not None, again.diagnostics
        assert again.expr == first.expr


# --------------------------------------------------------------------------- #
# Gate 2 — why_zero() distinguishes all five causes
# --------------------------------------------------------------------------- #


def _zero_cause_book() -> Book:
    """One book carrying all five causes, each on its own item."""
    monthly = Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=1)
    return Book(
        id="zeroes",
        calendar=CalendarSpec(),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1)),
        opening_balance=Decimal(0),
        cutover=date(2026, 3, 1),
        items={
            # (1) outside every segment: stops before the horizon ends.
            "short_contract": Item(
                id="short_contract",
                name="Contract ending early",
                kind="flow",
                direction="in",
                segments=[
                    Segment(
                        start=date(2026, 3, 1),
                        end=date(2026, 5, 1),
                        recurrence=monthly,
                        amount=Amount(constant=Decimal("1000.00")),
                    )
                ],
            ),
            # (2) probability 0.
            "dead_pipeline": Item(
                id="dead_pipeline",
                name="Lost opportunity",
                kind="flow",
                direction="in",
                segments=[
                    Segment(
                        start=date(2026, 3, 1),
                        recurrence=monthly,
                        amount=Amount(constant=Decimal("5000.00")),
                        probability=Decimal(0),
                    )
                ],
            ),
            # (5) accrues, never settles.
            "accrual_only": Item(
                id="accrual_only",
                name="Accrual with no cash leg",
                kind="flow",
                direction="in",
                segments=[
                    Segment(
                        start=date(2026, 3, 1),
                        recurrence=monthly,
                        amount=Amount(constant=Decimal("2000.00")),
                    )
                ],
                settlement=Settlement(due=[]),
            ),
            # (3) upstream zero propagated through the formula.
            "derived_from_dead": Item(
                id="derived_from_dead",
                name="Commission on the lost pipeline",
                kind="derived",
                direction="out",
                formula='it("dead_pipeline", measure="accrual") * 0.1',
            ),
        },
    )


class TestGateWhyZero:
    @pytest.fixture()
    def zero_run(self, tmp_path: Path):
        return _kit(tmp_path, _zero_cause_book(), "zeroes").run("base")

    def test_all_five_causes_are_reachable_and_distinguished(self, zero_run) -> None:
        periods = zero_run.result.periods
        may = periods.index_of(date(2026, 5, 2))
        march = periods.index_of(date(2026, 3, 1))
        january = periods.index_of(date(2026, 1, 15))

        cases = {
            # (1) the contract ended in April; May has no occurrence.
            "outside_segments": why_zero(zero_run, "short_contract", may, measure="accrual"),
            # (2) an occurrence exists in March, weighted to nothing.
            "probability_zero": why_zero(zero_run, "dead_pipeline", march, measure="accrual"),
            # (4) January is before cutover: generation is suppressed for all.
            "cutover_suppressed": why_zero(zero_run, "short_contract", january, measure="accrual"),
            # (5) empty `due`: it accrues and never settles.
            "no_settlement_leg": why_zero(zero_run, "accrual_only", march, measure="cash"),
            # (3) the formula's only input is zero.
            "upstream_zero": why_zero(zero_run, "derived_from_dead", march, measure="accrual"),
        }
        for expected, explanation in cases.items():
            assert explanation.cause == expected, (expected, explanation.message)
            assert explanation.message and explanation.suggested_fix
            assert explanation.value == Decimal("0.0000")

        assert set(cases) | {"not_zero"} == set(ZERO_CAUSES)

    def test_a_cell_that_is_not_zero_says_so_instead_of_guessing(self, zero_run) -> None:
        march = zero_run.result.periods.index_of(date(2026, 3, 1))
        explanation = why_zero(zero_run, "short_contract", march, measure="accrual")
        assert explanation.cause == "not_zero"
        assert explanation.value == Decimal("1000.0000")
        assert "not zero" in explanation.message

    def test_causes_that_are_also_true_are_listed_rather_than_hidden(
        self, zero_run
    ) -> None:
        """January is both pre-cutover and outside the contract's segment; a
        reader who fixed only one would still see zero."""
        january = zero_run.result.periods.index_of(date(2026, 1, 15))
        explanation = why_zero(zero_run, "short_contract", january, measure="accrual")
        assert explanation.cause == "cutover_suppressed"
        assert "outside_segments" in explanation.also

    def test_a_structurally_invalid_settlement_is_a_missing_leg_not_a_mystery(
        self, tmp_path: Path
    ) -> None:
        book = _zero_cause_book()
        broken = book.items["accrual_only"].model_copy(
            update={
                "settlement": Settlement(
                    due=[
                        DueTerm(share=Decimal("0.3"), offset="0d"),
                        DueTerm(share=Decimal("0.3"), offset="30d"),
                    ]
                )
            }
        )
        run = _kit(
            tmp_path,
            book.model_copy(update={"items": {**book.items, "accrual_only": broken}}),
            "broken",
        ).run("base")
        march = run.result.periods.index_of(date(2026, 3, 1))
        explanation = why_zero(run, "accrual_only", march, measure="cash")
        assert explanation.cause == "no_settlement_leg"
        assert "CK-E004" in explanation.detail

    def test_a_lagged_settlement_reports_the_cash_landing_elsewhere(
        self, tmp_path: Path
    ) -> None:
        book = _zero_cause_book()
        lagged = book.items["short_contract"].model_copy(
            update={"settlement": Settlement(due=[DueTerm(share=Decimal(1), offset="30d")])}
        )
        run = _kit(
            tmp_path,
            book.model_copy(update={"items": {**book.items, "short_contract": lagged}}),
            "lagged",
        ).run("base")
        march = run.result.periods.index_of(date(2026, 3, 1))
        explanation = why_zero(run, "short_contract", march, measure="cash")
        assert explanation.cause == "no_settlement_leg"
        assert "another period" in explanation.message


# --------------------------------------------------------------------------- #
# depends_on / dependents_of
# --------------------------------------------------------------------------- #


class TestDependencyGraphs:
    def test_a_derived_item_reports_what_it_reads(self, gate_run) -> None:
        derived = next(
            item_id
            for item_id, item in sorted(gate_run.book.items.items())
            if item.kind == "derived" and item.formula and "agg(" in item.formula
        )
        graph = depends_on(gate_run, derived)
        assert graph.direction == "depends_on"
        assert graph.root == derived
        assert len(graph.nodes) > 1
        assert {edge.relation for edge in graph.edges} & {"aggregate", "same_period"}
        assert all(edge.source == derived or edge.source in {n.item_id for n in graph.nodes}
                   for edge in graph.edges)

    def test_dependents_is_the_mirror(self, gate_run) -> None:
        for item_id in sorted(gate_run.book.items):
            for target in depends_on(gate_run, item_id, depth=1).edges:
                mirror = dependents_of(gate_run, target.target, depth=1)
                assert any(
                    edge.source == item_id and edge.target == target.target
                    for edge in mirror.edges
                ), (item_id, target.target)

    def test_a_prev_edge_is_labelled_lagged_and_a_loop_is_reported_as_cyclic(
        self, tmp_path: Path
    ) -> None:
        book = Book(
            id="loop",
            calendar=CalendarSpec(),
            horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 2, 1)),
            opening_balance=Decimal("100.00"),
            cutover=date(2026, 1, 1),
            items={
                "balance": Item(
                    id="balance",
                    name="Balance",
                    kind="stock",
                    formula='prev("balance", n=1, init=p.opening_balance) + it("interest")',
                ),
                "interest": Item(
                    id="interest",
                    name="Interest",
                    kind="derived",
                    formula='prev("balance", n=1, init=p.opening_balance) * 0.01',
                ),
            },
        )
        run = _kit(tmp_path, book, "loop").run("base")
        graph = depends_on(run, "balance")
        assert graph.cyclic
        assert set(graph.cycle_members) == {"balance", "interest"}
        assert any(edge.relation == "lagged" for edge in graph.edges)

    def test_an_unknown_item_is_a_diagnostic_not_an_exception(self, gate_run) -> None:
        graph = depends_on(gate_run, "not_an_item")
        assert graph.nodes == ()
        assert [d.code for d in graph.diagnostics] == ["CK-E001"]

    def test_a_book_works_as_well_as_a_run(self, tmp_path: Path) -> None:
        book = build_gate_book()
        run = _kit(tmp_path, book).run("base")
        derived = next(
            item_id for item_id, item in sorted(book.items.items()) if item.kind == "derived"
        )
        assert depends_on(book, derived).edges == depends_on(run, derived).edges


# --------------------------------------------------------------------------- #
# Gate 3 — describe_book() is sufficient to write a working pivot()
# --------------------------------------------------------------------------- #


def _fresh_agent_pivot_calls(description_json: str) -> list[dict]:
    """An 'agent' with access to nothing but the serialized description.

    It reads the vocabulary and emits every ``pivot()`` call the description
    licenses. It never sees the Book, so any field name it uses came from the
    description and nowhere else — which is exactly the claim the gate makes.
    """
    description = json.loads(description_json)
    vocabulary = description["pivot"]
    return [
        {"index": index, "columns": columns, "values": values}
        for index in vocabulary["index"]
        for columns in vocabulary["columns"]
        for values in vocabulary["values"]
    ]


class TestGateDescribeBook:
    @pytest.fixture()
    def described(self, tmp_path: Path):
        book = build_scenario_book()
        kit = _kit(tmp_path, book, "described")
        run = kit.run("base")
        store = DuckdbFrameStore()
        store.materialize("run", run.result, run.engine.book)
        description = describe_book(book, scenarios=("base",))
        yield book, run, store, description
        store.close()

    def test_every_pivot_call_the_description_licenses_actually_runs(
        self, described
    ) -> None:
        _, _, store, description = described
        serialized = description.model_dump_json()
        calls = _fresh_agent_pivot_calls(serialized)
        assert len(calls) >= 8, "the vocabulary must license a real range of calls"
        for call in calls:
            table = store.pivot("run", **call)
            assert table.columns[0] == call["index"]
            assert len(table) > 0, call

    def test_a_field_name_outside_the_description_is_rejected(self, described) -> None:
        """The other half of 'no field invention': the description is not merely
        a subset of what works, it is the whole of it."""
        _, _, store, description = described
        for bad_index in ("periods", "tag:customer", "scenario"):
            with pytest.raises(ValueError):
                store.pivot("run", index=bad_index, columns="item")
        for bad_columns in ("tags", "tag:", "customer", "value"):
            with pytest.raises(ValueError):
                store.pivot("run", index="period", columns=bad_columns)
        for bad_values in ("amount", "vat", "value"):
            with pytest.raises(ValueError):
                store.pivot("run", index="period", columns="item", values=bad_values)
        assert "tag:customer" not in description.pivot.index

    def test_the_description_enumerates_every_tag_key_and_value_in_use(
        self, described
    ) -> None:
        book, _, _, description = described
        expected: dict[str, set[str]] = {}
        for item in book.items.values():
            for key, value in item.tags.items():
                expected.setdefault(key, set()).add(value)
        assert set(description.tag_keys) == set(expected)
        assert {k: set(v) for k, v in description.tag_values.items()} == expected
        for key in description.tag_keys:
            assert f"tag:{key}" in description.pivot.columns

    def test_every_described_item_id_exists_and_every_item_is_described(
        self, described
    ) -> None:
        book, _, _, description = described
        assert {item.item_id for item in description.items} == set(book.items)
        for item in description.items:
            assert item.name and item.kind and item.currency and item.agg_rule
            assert item.settles, "how an item settles must never be blank"

    def test_selector_examples_from_the_description_all_match_something(
        self, described
    ) -> None:
        book, run, store, description = described
        for selector in description.selector_examples:
            table = store.frame("run", where=selector)
            assert len(table) > 0, selector

    def test_summary_fields_named_in_the_description_all_exist(self, described) -> None:
        _, run, _, description = described
        summary = run.summary()
        for field in description.summary_fields:
            assert hasattr(summary, field), field

    def test_frame_columns_named_in_the_description_are_the_real_ones(
        self, described
    ) -> None:
        _, _, store, description = described
        assert store.frame("run").columns == description.frame_columns

    def test_the_description_carries_no_float_anywhere(self, described) -> None:
        _, _, _, description = described
        payload = json.loads(description.model_dump_json())

        def walk(node) -> None:
            if isinstance(node, float):
                raise AssertionError(f"float in describe_book output: {node}")
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
            if isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)

    def test_the_description_states_the_things_an_agent_would_otherwise_guess(
        self, described
    ) -> None:
        _, _, _, description = described
        assert description.measures == ("accrual", "cash")
        assert set(description.grains) == {g.value for g in Grain}
        assert description.statuses == ("actual", "committed", "forecast")
        assert "where" in description.formula_builtins
        assert "if_" not in description.formula_builtins
        assert any("where`, not `if" in note for note in description.notes)
        assert "ANDed" in description.selector_grammar
        assert description.engine_version and description.rounding_policy

    def test_synthetic_items_are_hidden_unless_asked_for(self, tmp_path: Path) -> None:
        from vat_book import build_f24_book

        book = build_f24_book()
        run = _kit(tmp_path, book, "vat").run("base")
        plain = describe_book(run.engine.book)
        assert not any(item.synthetic for item in plain.items)
        full = describe_book(run.engine.book, include_synthetic=True)
        assert any(item.synthetic for item in full.items)
        assert set(plain.tax_regimes) == {regime.id for regime in book.tax_regimes}
