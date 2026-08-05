"""Phase 8 — frame store and views.

The gate lives in :class:`TestPhase8Gate`; the rest pins the format rules
(PRD §5.5) and the arithmetic that has to stay exact through DuckDB and Parquet.
"""

from __future__ import annotations

import ast
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import cashkit
from cashkit.engine.calendars import bucket_of
from cashkit.engine.numeric import COLUMN_CEILING, RoundingPolicy
from cashkit.engine.run import Engine
from cashkit.engine import run as vectorized_run
from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    DueTerm,
    Event,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
    Table,
)
from cashkit.sdk import balance_series, summary
from cashkit.stores.frames import (
    DECIMAL_CEILING_MINOR,
    DECIMAL_PRECISION,
    DECIMAL_SCALE,
    FRAME_COLUMNS,
    DuckdbFrameStore,
    FrameStore,
    effective_agg_rule,
)

from scenario_book import build_scenario_book, monthly_segment

RUN = "base"


@pytest.fixture
def store() -> DuckdbFrameStore:
    with DuckdbFrameStore() as opened:
        yield opened


@pytest.fixture
def book() -> Book:
    return build_scenario_book()


@pytest.fixture
def materialized(store: DuckdbFrameStore, book: Book) -> DuckdbFrameStore:
    result = vectorized_run(book)
    assert store.materialize(RUN, result, book) == ()
    return store


def cell(table: Table, measure: str | None = None) -> Decimal:
    """Sum a frame's value column, optionally filtered to one measure."""
    position = table.index_of("value")
    measure_at = table.index_of("measure")
    return sum(
        (row[position] for row in table.rows if measure is None or row[measure_at] == measure),
        Decimal(0),
    )


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


class TestPhase8Gate:
    """The three properties the phase is not allowed to pass without."""

    @pytest.mark.parametrize("grain", [Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.YEAR])
    def test_aggregation_preserves_flow_totals_exactly(
        self, materialized: DuckdbFrameStore, grain: Grain
    ) -> None:
        """Exactly: Decimal equality, not a tolerance."""
        day = materialized.frame(RUN, where="cat:revenue")
        coarse = materialized.frame(RUN, where="cat:revenue", grain=grain)

        assert len(coarse) < len(day)
        for measure in ("accrual", "cash"):
            assert cell(coarse, measure) == cell(day, measure)

    @pytest.mark.parametrize("grain", [Grain.MONTH, Grain.QUARTER, Grain.YEAR])
    def test_aggregation_takes_last_in_period_for_stocks(
        self, materialized: DuckdbFrameStore, book: Book, grain: Grain
    ) -> None:
        """A balance is a level: the bucket's value is its last period's."""
        result = vectorized_run(book)
        coarse = materialized.frame(RUN, grain=grain, measures=["accrual"])
        rows = [row for row in coarse.rows if row[2] == "cash"]
        assert rows

        starts = list(result.periods.starts)
        for period_start, period_end, _item, _measure, value, _currency, _status in rows:
            inside = [
                index
                for index, start in enumerate(starts)
                if period_start <= start < period_end
            ]
            assert value == result.value("cash", "accrual", inside[-1])
            # ...and emphatically not the sum of the levels in the bucket.
            if len(inside) > 1:
                assert value != sum(
                    (result.value("cash", "accrual", index) for index in inside),
                    Decimal(0),
                )

    def test_a_tag_sliced_sum_equals_the_sum_of_the_items(
        self, materialized: DuckdbFrameStore, book: Book
    ) -> None:
        result = vectorized_run(book)
        for selector, expected_items in (
            ("cat:opex", {"payroll", "rent"}),
            ("cat:revenue", {"acme"}),
            ("cat:opex site:milan", {"payroll", "rent"}),
            ("flag:committed", {"acme"}),
            ("customer:acme flag:committed", {"acme"}),
        ):
            assert set(materialized.items_matching(RUN, selector)) == expected_items
            sliced = materialized.frame(RUN, where=selector, measures=["cash"])
            direct = sum(
                (result.total(item_id, "cash") for item_id in expected_items),
                Decimal(0),
            )
            assert cell(sliced) == direct, selector

    def test_parquet_round_trips_without_precision_loss(
        self, materialized: DuckdbFrameStore, tmp_path: Path
    ) -> None:
        original = materialized.frame(RUN)
        path = materialized.export(RUN, tmp_path / "frame.parquet")

        restored = materialized.read_export(path)

        assert restored.columns == FRAME_COLUMNS
        assert len(restored) == len(original)
        for before, after in zip(original.rows, restored.rows):
            assert before == after
        # Exact, in the same type — not a float that prints the same.
        values = restored.column("value")
        assert all(isinstance(value, Decimal) for value in values)
        assert sum(values, Decimal(0)) == cell(original)

    def test_parquet_round_trip_holds_for_awkward_decimals(
        self, store: DuckdbFrameStore, tmp_path: Path
    ) -> None:
        """A 4 dp value with a non-terminating binary expansion: 0.1 is the
        canonical float trap, and a settlement split makes plenty of them."""
        book = _awkward_book()
        result = vectorized_run(book)
        store.materialize("awkward", result, book)
        original = store.frame("awkward")
        values = set(original.column("value"))
        assert {Decimal("9999.9999"), Decimal("3333.9999"), Decimal("6666.9999")} <= values

        restored = store.read_export(
            store.export("awkward", tmp_path / "awkward.parquet")
        )

        assert restored.rows == original.rows
        assert sum(restored.column("value"), Decimal(0)) == cell(original)


def _awkward_book() -> Book:
    """A book whose settlement split produces repeating decimals at 4 dp."""
    return Book(
        id="awkward",
        base_grain=Grain.DAY,
        calendar=CalendarSpec(),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1)),
        opening_balance=Decimal("0.1000"),
        cutover=date(2026, 1, 1),
        items={
            "thirds": Item(
                id="thirds",
                name="Split in thirds",
                kind="flow",
                direction="in",
                tags={"cat": "revenue"},
                segments=[monthly_segment(date(2026, 1, 1), None, "9999.9999")],
                settlement=Settlement(
                    due=[
                        DueTerm(share=Decimal("0.3333"), offset="0d"),
                        DueTerm(share=Decimal("0.3333"), offset="30d"),
                        DueTerm(share=Decimal("0.3334"), offset="60d"),
                    ]
                ),
            )
        },
    )


# --------------------------------------------------------------------------- #
# Format rules (PRD §5.5)
# --------------------------------------------------------------------------- #


class TestFrameFormat:
    def test_the_canonical_frame_is_tidy_long(
        self, materialized: DuckdbFrameStore, book: Book
    ) -> None:
        frame = materialized.frame(RUN)
        assert frame.columns == FRAME_COLUMNS
        result = vectorized_run(book)
        assert len(frame) == len(result.periods) * len(result.accrual) * 2
        first = frame.to_dicts()[0]
        assert first["period_start"] == date(2026, 1, 1)
        assert first["period_end"] == date(2026, 1, 2)
        assert first["measure"] in ("accrual", "cash")

    def test_period_end_is_exclusive(self, materialized: DuckdbFrameStore) -> None:
        """Half-open, consistently with PeriodRange (see DECISIONS C-P8-01)."""
        for grain, expected_end in (
            (Grain.DAY, date(2026, 1, 2)),
            (Grain.MONTH, date(2026, 2, 1)),
            (Grain.QUARTER, date(2026, 4, 1)),
            (Grain.YEAR, date(2027, 1, 1)),
        ):
            frame = materialized.frame(RUN, grain=grain, measures=["accrual"])
            assert frame.rows[0][1] == expected_end, grain

    def test_tags_are_not_denormalized_into_the_fact_table(
        self, materialized: DuckdbFrameStore
    ) -> None:
        """PRD §5.5. Structural, because the failure mode is a schema decision."""
        columns = {
            row[0]
            for row in materialized._db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'frame_facts'"
            ).fetchall()
        }
        assert columns == {
            "run_id",
            "period_index",
            "item_id",
            "measure",
            "value",
            "currency",
            "status",
        }
        # ...and the tags are reachable, on their own dimension table.
        tags = materialized.tags(RUN).to_dicts()
        assert {"item_id": "rent", "tag_key": "cat", "tag_value": "opex"} in tags

    def test_money_columns_are_declared_decimal_18_4(
        self, materialized: DuckdbFrameStore
    ) -> None:
        rows = materialized._db.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE column_name IN ('value', 'opening_balance')"
        ).fetchall()
        assert rows
        for _table, _column, data_type in rows:
            assert data_type == f"DECIMAL({DECIMAL_PRECISION},{DECIMAL_SCALE})"

    def test_decimal_18_4_can_hold_every_value_the_engine_can_produce(self) -> None:
        """The PRD picks DECIMAL(18,4) and the engine core is int64 minor units;
        this is the arithmetic that makes those two compatible rather than
        hopeful. `Money` alone permits 9e14 units, which would not fit — the
        addition-safe column ceiling is what rules it out."""
        assert COLUMN_CEILING < DECIMAL_CEILING_MINOR
        assert DECIMAL_CEILING_MINOR // COLUMN_CEILING > 400

    def test_synthetic_items_are_materialized_and_flagged(
        self, store: DuckdbFrameStore, book: Book
    ) -> None:
        """A tax item carries real cash and an event carrier real ledger rows;
        a frame that dropped them would not sum to the model."""
        events = (
            Event(
                id="fee1",
                date=date(2026, 2, 10),
                amount=Decimal("-120.0000"),
                status="actual",
                tags={"cat": "opex"},
            ),
        )
        engine = Engine(book, events=events)
        result = engine.run()
        store.materialize(RUN, result, engine.book)

        frame = store.frame(RUN)
        ids = set(frame.column("item_id"))
        carrier = next(item for item in ids if item.startswith("_event:"))
        assert cell(store.frame(RUN, where="cat:opex", measures=["accrual"])) == sum(
            (result.total(item, "accrual") for item in ("payroll", "rent", carrier)),
            Decimal(0),
        )
        synthetic = {
            row[0]: row[1]
            for row in store._db.execute(
                "SELECT item_id, synthetic FROM frame_items WHERE run_id = ?", [RUN]
            ).fetchall()
        }
        assert synthetic[carrier] is True
        assert synthetic["rent"] is False
        assert len(store.frame(RUN, include_synthetic=False)) < len(frame)

    def test_materialize_is_idempotent(
        self, store: DuckdbFrameStore, book: Book
    ) -> None:
        result = vectorized_run(book)
        store.materialize(RUN, result, book)
        once = store.frame(RUN)
        store.materialize(RUN, result, book)
        assert store.frame(RUN).rows == once.rows
        assert len(store.runs()) == 1

    def test_status_filters_what_the_run_reported(
        self, materialized: DuckdbFrameStore
    ) -> None:
        """The engine reports one status per run today (DECISIONS D-P8-06), so
        this pins the filter rather than a per-cell status it does not have."""
        assert len(materialized.frame(RUN, status="forecast")) == len(
            materialized.frame(RUN)
        )
        assert len(materialized.frame(RUN, status="actual")) == 0

    def test_unknown_run_measure_and_selector_are_programmer_errors(
        self, materialized: DuckdbFrameStore
    ) -> None:
        with pytest.raises(ValueError, match="no materialized run"):
            materialized.frame("nope")
        with pytest.raises(ValueError, match="unknown measures"):
            materialized.frame(RUN, measures=["vat"])
        with pytest.raises(ValueError, match="malformed selector"):
            materialized.frame(RUN, where="not a selector")

    def test_the_duckdb_store_satisfies_the_protocol(
        self, store: DuckdbFrameStore
    ) -> None:
        assert isinstance(store, FrameStore)


# --------------------------------------------------------------------------- #
# Aggregation rules
# --------------------------------------------------------------------------- #


class TestAggRule:
    def test_a_stock_never_sums(self) -> None:
        assert effective_agg_rule("stock", "sum") == "last"
        assert effective_agg_rule("stock", "mean") == "mean"
        assert effective_agg_rule("stock", "last") == "last"
        assert effective_agg_rule("flow", "sum") == "sum"
        assert effective_agg_rule("derived", "last") == "last"

    def test_mean_rounds_in_minor_units_under_the_declared_policy(
        self, book: Book
    ) -> None:
        """The only aggregation that rounds. It rounds like the engine, not like
        whatever a SQL division would do."""
        averaged = book.model_copy(
            update={
                "items": {
                    **book.items,
                    "cash": book.items["cash"].model_copy(update={"agg_rule": "mean"}),
                }
            }
        )
        result = vectorized_run(averaged)
        with DuckdbFrameStore(policy=RoundingPolicy.HALF_UP) as up:
            up.materialize(RUN, result, averaged)
            january = [
                row
                for row in up.frame(RUN, grain=Grain.MONTH, measures=["accrual"]).rows
                if row[2] == "cash" and row[0] == date(2026, 1, 1)
            ]
        assert len(january) == 1
        levels = [result.value("cash", "accrual", index) for index in range(31)]
        expected = (sum(levels, Decimal(0)) / 31).quantize(Decimal("0.0001"))
        assert january[0][4] == expected

    def test_a_flow_with_agg_rule_last_takes_the_last_period(
        self, store: DuckdbFrameStore, book: Book
    ) -> None:
        tweaked = book.model_copy(
            update={
                "items": {
                    **book.items,
                    "rent": book.items["rent"].model_copy(update={"agg_rule": "last"}),
                }
            }
        )
        result = vectorized_run(tweaked)
        store.materialize(RUN, result, tweaked)
        rows = [
            row
            for row in store.frame(RUN, grain=Grain.MONTH, measures=["accrual"]).rows
            if row[2] == "rent" and row[0] == date(2026, 1, 1)
        ]
        assert rows[0][4] == result.value("rent", "accrual", 30)


class TestBuckets:
    def test_calendar_alignment(self) -> None:
        assert bucket_of(date(2026, 3, 18), Grain.DAY) == (
            date(2026, 3, 18),
            date(2026, 3, 19),
        )
        assert bucket_of(date(2026, 3, 18), Grain.WEEK) == (
            date(2026, 3, 16),
            date(2026, 3, 23),
        )
        assert bucket_of(date(2026, 3, 18), Grain.MONTH) == (
            date(2026, 3, 1),
            date(2026, 4, 1),
        )
        assert bucket_of(date(2026, 3, 18), Grain.QUARTER) == (
            date(2026, 1, 1),
            date(2026, 4, 1),
        )
        assert bucket_of(date(2026, 3, 18), Grain.YEAR) == (
            date(2026, 1, 1),
            date(2027, 1, 1),
        )

    def test_quarters_and_years_follow_the_fiscal_year(self) -> None:
        """The field exists for a reason (D-P2-07, D-P6-06)."""
        assert bucket_of(date(2026, 3, 18), Grain.QUARTER, 7) == (
            date(2026, 1, 1),
            date(2026, 4, 1),
        )
        assert bucket_of(date(2026, 8, 3), Grain.QUARTER, 7) == (
            date(2026, 7, 1),
            date(2026, 10, 1),
        )
        assert bucket_of(date(2026, 3, 18), Grain.YEAR, 7) == (
            date(2025, 7, 1),
            date(2026, 7, 1),
        )
        assert bucket_of(date(2026, 8, 3), Grain.YEAR, 7) == (
            date(2026, 7, 1),
            date(2027, 7, 1),
        )

    def test_a_fiscal_year_book_aggregates_on_its_own_calendar(
        self, store: DuckdbFrameStore, book: Book
    ) -> None:
        fiscal = book.model_copy(
            update={"calendar": CalendarSpec(fiscal_year_start_month=7)}
        )
        result = vectorized_run(fiscal)
        store.materialize(RUN, result, fiscal)
        starts = sorted(
            set(store.frame(RUN, grain=Grain.YEAR, measures=["accrual"]).column("period_start"))
        )
        assert starts == [date(2025, 7, 1), date(2026, 7, 1), date(2027, 7, 1)]
        assert cell(store.frame(RUN, grain=Grain.YEAR, where="cat:revenue")) == cell(
            store.frame(RUN, where="cat:revenue")
        )

    def test_a_partial_bucket_keeps_its_calendar_identity(
        self, store: DuckdbFrameStore
    ) -> None:
        """A horizon opening mid-month reports March, not "the first 17 days"."""
        book = build_scenario_book().model_copy(
            update={"horizon": PeriodRange(start=date(2026, 3, 15), end=date(2026, 5, 1))}
        )
        result = vectorized_run(book)
        store.materialize(RUN, result, book)
        frame = store.frame(RUN, grain=Grain.MONTH, measures=["accrual"])
        assert frame.rows[0][0] == date(2026, 3, 1)
        assert frame.rows[0][1] == date(2026, 4, 1)


# --------------------------------------------------------------------------- #
# Pivot and compare
# --------------------------------------------------------------------------- #


class TestPivot:
    def test_pivot_by_tag(self, materialized: DuckdbFrameStore, book: Book) -> None:
        table = materialized.pivot(RUN, columns="tag:cat", values="cash", grain=Grain.YEAR)
        assert table.columns[0] == "period"
        assert set(table.columns[1:]) == {"balance", "opex", "revenue"}
        result = vectorized_run(book)
        revenue = table.index_of("revenue")
        assert sum((row[revenue] for row in table.rows), Decimal(0)) == result.total(
            "acme", "cash"
        )

    def test_pivot_columns_sum_back_to_the_frame(
        self, materialized: DuckdbFrameStore
    ) -> None:
        """A pivot whose columns do not reconcile is a quiet way to lose money."""
        table = materialized.pivot(RUN, columns="tag:customer", values="cash")
        assert "(untagged)" in table.columns
        total = sum(
            (value for row in table.rows for value in row[1:]), Decimal(0)
        )
        assert total == cell(materialized.frame(RUN, measures=["cash"]))

    def test_pivot_by_item_and_measure(self, materialized: DuckdbFrameStore) -> None:
        by_item = materialized.pivot(RUN, columns="item", values="accrual", grain=Grain.YEAR)
        assert "acme" in by_item.columns
        by_item_indexed = materialized.pivot(
            RUN, index="item", columns="measure", values="cash"
        )
        assert by_item_indexed.columns == ("item", "cash")

    def test_pivot_rejects_nonsense(self, materialized: DuckdbFrameStore) -> None:
        with pytest.raises(ValueError, match="unknown measure"):
            materialized.pivot(RUN, columns="item", values="vat")
        with pytest.raises(ValueError, match="unknown pivot index"):
            materialized.pivot(RUN, index="galaxy", columns="item")
        with pytest.raises(ValueError, match="unknown pivot columns"):
            materialized.pivot(RUN, columns="customer")
        with pytest.raises(ValueError, match="needs a tag key"):
            materialized.pivot(RUN, columns="tag:")


class TestCompare:
    def test_compare_aligns_runs_on_the_period(
        self, store: DuckdbFrameStore, book: Book
    ) -> None:
        halved = book.model_copy(
            update={
                "items": {
                    **book.items,
                    "acme": book.items["acme"].model_copy(
                        update={
                            "segments": [
                                segment.model_copy(
                                    update={
                                        "amount": Amount(
                                            constant=segment.amount.constant / 2
                                        )
                                    }
                                )
                                for segment in book.items["acme"].segments
                            ]
                        }
                    ),
                }
            }
        )
        store.materialize("base", vectorized_run(book), book)
        store.materialize("downside", vectorized_run(halved), halved)

        table = store.compare(["base", "downside"], grain=Grain.YEAR)

        assert table.columns == ("period_start", "base", "downside")
        assert len(table) == 2
        for _period, base_value, downside_value in table.rows:
            assert downside_value < base_value

    def test_compare_reports_none_for_a_period_a_run_does_not_have(
        self, store: DuckdbFrameStore, book: Book
    ) -> None:
        """Not evaluated and evaluated-to-zero are different answers."""
        short = book.model_copy(
            update={"horizon": PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1))}
        )
        store.materialize("long", vectorized_run(book), book)
        store.materialize("short", vectorized_run(short), short)

        table = store.compare(["long", "short"], grain=Grain.YEAR)

        assert table.rows[1][2] is None
        assert table.rows[1][1] is not None

    def test_compare_rejects_an_unknown_metric(
        self, materialized: DuckdbFrameStore
    ) -> None:
        with pytest.raises(ValueError, match="unknown metric"):
            materialized.compare([RUN], metric="vat")


# --------------------------------------------------------------------------- #
# summary()
# --------------------------------------------------------------------------- #


class TestSummary:
    def test_summary_answers_when_we_run_out_of_cash(self, book: Book) -> None:
        result = vectorized_run(book)
        report = summary(result, book, grain=Grain.MONTH)

        assert report.grain == "month"
        assert report.opening_balance == Decimal("100000.0000")
        assert report.periods == 24
        series, _ = balance_series(result, book)
        first_negative = next(index for index, value in enumerate(series) if value < 0)
        assert report.runway_periods == first_negative
        assert report.runway_end == result.periods.starts[first_negative]
        assert report.min_cash <= report.closing_balance
        assert report.net_cash == report.total_inflow + report.total_outflow

    def test_min_cash_and_runway_are_read_at_base_grain(self, book: Book) -> None:
        """A coarser report must not smooth away an intra-bucket trough."""
        result = vectorized_run(book)
        series, _ = balance_series(result, book)
        daily = summary(result, book)
        monthly = summary(result, book, grain=Grain.MONTH)

        assert daily.min_cash == monthly.min_cash
        assert daily.runway_end == monthly.runway_end
        assert monthly.min_cash == min(
            (Decimal(int(value)).scaleb(-4) for value in series), default=Decimal(0)
        )
        assert monthly.periods == 24

        # What reading bucket *closes* would have said — the tidy answer this
        # deliberately does not give. For this book the two differ, so the test
        # is a statement about behaviour and not a tautology.
        last_of_month = {}
        for index, start in enumerate(result.periods.starts):
            last_of_month[(start.year, start.month)] = index
        naive = next(
            result.periods.starts[index]
            for index in sorted(last_of_month.values())
            if series[index] < 0
        )
        assert monthly.runway_end < naive

    def test_summary_agrees_with_a_modelled_balance_item(self, book: Book) -> None:
        """The auto derivation and the book's own `prev()` fold are the same
        number — which is the point of checking rather than assuming."""
        result = vectorized_run(book)
        auto, auto_source = balance_series(result, book)
        modelled, modelled_source = balance_series(result, book, balance="cash")

        assert auto_source != modelled_source
        assert (auto == modelled).all()

    def test_summary_needs_no_frame_store(self, book: Book) -> None:
        """`summary()` is the core question and must work without the extra."""
        tree = ast.parse(
            (PACKAGE_ROOT / "sdk" / "views.py").read_text(encoding="utf-8")
        )
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(
            name == "duckdb" or name.startswith(("duckdb.", "cashkit.stores"))
            for name in imported
        ), sorted(imported)
        assert summary(vectorized_run(book), book).book_id == "scenario-book"

    def test_a_profitable_book_has_no_runway_end_and_a_breakeven(self) -> None:
        book = build_scenario_book()
        profitable = book.model_copy(
            update={
                "items": {
                    key: item
                    for key, item in book.items.items()
                    if key not in ("payroll", "rent")
                }
            }
        )
        report = summary(vectorized_run(profitable), profitable, grain=Grain.MONTH)

        assert report.runway_end is None
        assert report.runway_periods is None
        assert report.breakeven_period == date(2026, 1, 1)
        assert report.closing_balance > report.opening_balance

    def test_summary_carries_error_diagnostics_through(self, book: Book) -> None:
        broken = book.model_copy(
            update={
                "items": {
                    **book.items,
                    "cash": book.items["cash"].model_copy(
                        update={"formula": 'it("nowhere")'}
                    ),
                }
            }
        )
        report = summary(vectorized_run(broken), broken)
        assert [d.code for d in report.diagnostics] == ["CK-E001"]

    def test_a_designated_balance_item_can_be_named(self, book: Book) -> None:
        result = vectorized_run(book)
        report = summary(result, book, balance="cash", grain=Grain.MONTH)
        assert report.balance_source == "item 'cash'"
        assert report.closing_balance == result.value("cash", "accrual", 729)


# --------------------------------------------------------------------------- #
# Structural: storage stays swappable
# --------------------------------------------------------------------------- #


PACKAGE_ROOT = Path(cashkit.__file__).parent


def test_only_the_frame_store_imports_duckdb() -> None:
    """PRD §3.4: the FrameStore protocol is the swappability guarantee, and it
    is worth nothing if duckdb leaks above it."""
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".")[0] == "duckdb" for name in names):
                if path != PACKAGE_ROOT / "stores" / "frames.py":
                    offenders.append(f"{path}:{node.lineno}")
    assert not offenders, "duckdb imported outside the frame store:\n" + "\n".join(
        offenders
    )


def test_the_frame_store_never_touches_a_float() -> None:
    source = (PACKAGE_ROOT / "stores" / "frames.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Name) and node.id == "float")
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float))
    # No SQL floating-point type may appear either: a DECIMAL column fed by a
    # DOUBLE expression is exact in the schema and wrong in the data.
    assert not re.search(r"\b(DOUBLE|REAL|FLOAT4|FLOAT8)\b", source), re.findall(
        r"\b(DOUBLE|REAL|FLOAT4|FLOAT8)\b", source
    )
