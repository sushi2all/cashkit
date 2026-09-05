"""The construction surface (PRD §6.1) after ADR-0031..0034: one write path.

The gate this file carries is the one thing no earlier phase could assert: that
a book can be **built through the SDK alone**, and — since ADR-0031 — that
every authoring write goes down one path whatever scenario it is addressed to.

What is proved here beyond "the functions exist":

* **The whole lifecycle runs on public calls only** — create, author, tax,
  ledger, cutover, run, summary, commit, history — from an empty directory, and
  ends with a clean ``validate()``.
* **``set_item`` parses and DAG-checks now.** A formula that does not parse
  never reaches the book; one that parses but cannot resolve is recorded with
  its diagnostic *at call time*, so an agent never meets it as a zero column
  three steps later. The same is true addressed to a fork: the admission rule
  does not depend on the target (ADR-0031).
* **Every write persists.** A reopened book holds what was authored, in base
  and in every scenario, with no ``save()`` anyone had to remember.
* **``reconcile`` reports drift exactly**, in int64 minor units, and the day it
  suggests feeds ``set_book(cutover=…)`` directly.
* **The CLI is now a caller like any other.** ``cashkit init`` and
  ``create_book`` produce books that are equal byte for byte under the
  canonical emitter.
* **A past revision is a type without write methods** (ADR-0033).
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cashkit.cli import EXIT_OK, main as cli_main
from cashkit.model import (
    Amount,
    CalendarSpec,
    ChangeReport,
    Event,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
    TaxRegime,
    VatSpec,
    to_canonical_yaml,
)
from cashkit.sdk import CashKit, ReadOnlyKit, RetagItems, create_book

YEAR = PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1))
QUARTER = PeriodRange(start=date(2026, 1, 1), end=date(2026, 4, 1))


def codes(report) -> set[str]:
    return {d.code for d in report.diagnostics}


def monthly(day: int = 1) -> Recurrence:
    return Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=day)


def flow(
    item_id: str,
    amount: str,
    *,
    direction: str,
    day: int = 1,
    tags: dict[str, str] | None = None,
    settlement: Settlement | None = None,
    vat: VatSpec | None = None,
) -> Item:
    """A one-segment generative flow. Built as a value and handed to the SDK —
    the SDK never gets a Book, only the item spec §6.1 says it takes."""
    return Item(
        id=item_id,
        name=item_id,
        kind="flow",
        direction=direction,  # type: ignore[arg-type]
        tags=tags or {},
        segments=[
            Segment(
                start=date(2026, 1, 1),
                recurrence=monthly(day),
                amount=Amount(constant=Decimal(amount)),
            )
        ],
        settlement=settlement,
        vat=vat,
    )


def derived(item_id: str, formula: str, *, kind: str = "derived", **tags: str) -> Item:
    return Item(id=item_id, name=item_id, kind=kind, formula=formula, tags=dict(tags))  # type: ignore[arg-type]


def new_kit(root: Path, **overrides) -> CashKit:
    fields = dict(id=root.name, horizon=QUARTER, opening_balance=Decimal("10000.0000"))
    fields.update(overrides)
    kit, problems = create_book(root, **fields)
    assert kit is not None, problems
    return kit


@pytest.fixture()
def kit(tmp_path: Path) -> CashKit:
    return new_kit(tmp_path / "book")


# --------------------------------------------------------------------------- #
# Gate 1 — the whole lifecycle, SDK calls only
# --------------------------------------------------------------------------- #


class TestEndToEndThroughTheSdkAlone:
    def test_an_empty_directory_becomes_a_working_book(self, tmp_path: Path) -> None:
        root = tmp_path / "acme"
        assert not root.exists()

        kit, problems = create_book(
            root,
            id="acme",
            horizon=YEAR,
            opening_balance=Decimal("250000.0000"),
            grain=Grain.DAY,
            calendar="IT",
        )
        assert kit is not None, problems
        assert problems == ()
        assert kit.book.cutover == YEAR.start, "cutover is authored, never today()"
        assert kit.book.calendar.holidays, "ADR-0010: the holiday set is resolved once"

        assert kit.set_param("vat_standard", Decimal("0.22")).changed == ("params.vat_standard",)

        added = [
            kit.set_item(
                flow(
                    "consulting",
                    "12000",
                    direction="in",
                    tags={"cat": "revenue", "customer": "acme"},
                    settlement=Settlement.net(30),
                    vat=VatSpec(),
                )
            ),
            kit.set_item(
                flow("rent", "-3000", direction="out", tags={"cat": "opex"}, settlement=Settlement.immediate())
            ),
            kit.set_item(
                flow(
                    "salaries",
                    "-8000",
                    direction="out",
                    day=27,
                    tags={"cat": "payroll"},
                    settlement=Settlement.split([(Decimal("0.5"), "0d"), (Decimal("0.5"), "15d")]),
                )
            ),
        ]
        assert all(item.ok for item in added), [item.diagnostics for item in added]
        assert [item.created for item in added] == [("consulting",), ("rent",), ("salaries",)]

        margin = kit.set_item(derived("margin", 'it("consulting") + it("rent")', cat="derived"))
        assert margin.ok and margin.created == ("margin",)

        regime = kit.set_book(
            tax_regimes=[TaxRegime(id="vat", accumulates="", periodicity="quarterly", payment_offset="16d")]
        )
        assert regime.ok and regime.changed == ("tax_regimes",)

        assert kit.ledger is not None
        appended = kit.add_event(
            Event(id="ev-jan", date=date(2026, 1, 15), amount=Decimal("4200.0000"), status="forecast", item="consulting")
        )
        assert appended.ok and appended.created == ("ev-jan",)
        imported = kit.import_events(
            [
                Event(id="bank-1", date=date(2026, 1, 5), amount=Decimal("-3050.0000"), status="actual", item="rent", source="bank:IT60X", ext_id="TX-1"),
                Event(id="bank-2", date=date(2026, 2, 5), amount=Decimal("-3000.0000"), status="actual", item="rent", source="bank:IT60X", ext_id="TX-2"),
            ],
            source="bank:IT60X",
        )
        assert imported.ok and imported.inserted == 2, imported.diagnostics

        assert kit.set_book(cutover=date(2026, 3, 1)).changed == ("cutover",)

        run = kit.run()
        summary = run.summary()
        assert summary.book_id == "acme"
        assert summary.opening_balance == Decimal("250000.0000")
        assert summary.periods == 365
        assert summary.total_inflow > 0 and summary.total_outflow < 0

        problems = [d for d in kit.validate() if d.severity == "error"]
        assert not problems, problems

        report = kit.commit("initial book", author="test")
        assert report.revision is not None, report.diagnostics
        assert kit.status().clean
        assert [revision.message for revision in kit.history()] == ["initial book"]

    def test_nothing_was_written_outside_the_sdk(self, tmp_path: Path) -> None:
        """The §3.3 layout appears because the SDK wrote it, not the test."""
        root = tmp_path / "layout"
        kit = new_kit(root, horizon=YEAR, opening_balance=Decimal(0))
        kit.set_item(flow("rent", "-1000", direction="out"))
        for path in (
            ".cashkit/version",
            ".cashkit/config.toml",
            "book.yaml",
            "params.yaml",
            "items/rent.yaml",
            "scenarios/base.yaml",
        ):
            assert (root / path).is_file(), path

    def test_a_reopened_book_holds_everything_that_was_authored(self, tmp_path: Path) -> None:
        """Every write persists (ADR-0031): the working tree on disk *is* the
        working state, in base and in every fork, with no save() to forget."""
        root = tmp_path / "persist"
        kit = new_kit(root, horizon=YEAR, opening_balance=Decimal(0))
        kit.set_item(flow("rent", "-1000", direction="out", tags={"cat": "opex"}))
        kit.set_param("inflation", Decimal("0.03"))
        kit.set_book(
            cutover=date(2026, 2, 1),
            tax_regimes=[TaxRegime(id="vat", accumulates="cat:opex", periodicity="monthly", payment_offset="16d")],
        )
        assert kit.fork("downside").ok
        assert kit.set_param("inflation", Decimal("0.05"), scenario="downside").ok
        assert kit.set_item(
            flow("rent", "-1500", direction="out", tags={"cat": "opex"}), scenario="downside"
        ).changed == ("segments",)

        reopened, _ = CashKit.open(root)
        assert reopened is not None
        assert set(reopened.book.items) == {"rent"}
        assert reopened.book.params["inflation"] == Decimal("0.03")
        assert reopened.book.cutover == date(2026, 2, 1)
        assert [regime.id for regime in reopened.book.tax_regimes] == ["vat"]
        downside = reopened.resolve("downside").book
        assert downside.params["inflation"] == Decimal("0.05")
        assert downside.items["rent"].segments[0].amount.constant == Decimal("-1500")
        assert reopened.book.items["rent"].segments[0].amount.constant == Decimal("-1000")

    def test_a_write_that_records_nothing_touches_nothing(self, kit: CashKit) -> None:
        kit.set_item(flow("rent", "-1000", direction="out"))
        stamp = (kit.root / "items" / "rent.yaml").stat().st_mtime_ns
        again = kit.set_item(flow("rent", "-1000", direction="out"))
        assert again.empty and codes(again) == {"CK-I002"}
        assert (kit.root / "items" / "rent.yaml").stat().st_mtime_ns == stamp


class TestOneObjectOneSurface:
    def test_every_verb_is_reachable_from_the_object_an_agent_holds(self, kit: CashKit) -> None:
        """A surface split across two import sites is one an agent gets wrong."""
        assert kit.set_item(flow("rent", "-1000", direction="out", tags={"cat": "opex"})).created == ("rent",)
        assert kit.set_item(derived("twice_rent", 'it("rent") * 2')).ok
        assert kit.set_param("inflation", Decimal("0.02")).changed == ("params.inflation",)
        assert kit.apply_macro(RetagItems(selector="cat:opex", tags={"team": "ops"})).changed == ("rent.tags",)
        assert kit.set_book(
            tax_regimes=[TaxRegime(id="vat", accumulates="cat:opex", periodicity="monthly", payment_offset="16d")]
        ).changed == ("tax_regimes",)
        assert kit.set_book(cutover=date(2026, 2, 1)).changed == ("cutover",)
        assert kit.fork("downside").created == ("downside",)
        assert kit.remove_item("twice_rent", scenario="downside").changed == ("removed",)
        assert kit.unset("twice_rent", scenario="downside").changed == ("removed",)
        assert kit.flatten("flat", scenario="downside").created == ("flat",)
        assert kit.provenance("rent", scenario="downside").exists
        assert kit.diff("base", "downside").empty
        assert kit.resolve("downside").book.items["rent"].tags["team"] == "ops"
        assert kit.query_events().columns
        assert kit.reconcile(date(2026, 2, 28)).since == date(2026, 2, 1)
        assert kit.describe_book("downside").items

    def test_every_write_returns_one_report_shape(self, kit: CashKit) -> None:
        """ADR-0032: an agent loops on one shape, not four."""
        reports = [
            kit.set_item(flow("rent", "-1000", direction="out", tags={"cat": "opex"})),
            kit.set_param("inflation", Decimal("0.02")),
            kit.apply_macro(RetagItems(selector="cat:opex", tags={"team": "ops"})),
            kit.set_book(cutover=date(2026, 2, 1)),
            kit.fork("downside"),
            kit.remove_item("rent", scenario="downside"),
            kit.unset("rent", scenario="downside"),
            kit.flatten("flat", scenario="downside"),
            kit.add_event(Event(id="e", date=date(2026, 1, 2), amount=Decimal("1.0000"), status="forecast")),
            kit.void_event("e", "oops"),
            kit.commit("all of it"),
            kit.discard(),
        ]
        assert all(isinstance(report, ChangeReport) for report in reports)

    def test_ledger_reads_survive_a_kit_with_no_ledger(self, kit: CashKit) -> None:
        """An in-memory kit is a legal kit; a query against no ledger is empty."""
        kit.ledger = None
        assert len(kit.query_events()) == 0
        with pytest.raises(ValueError, match="no ledger store"):
            kit.add_event(Event(id="x", date=date(2026, 1, 1), amount=Decimal("1.0000"), status="forecast"))


# --------------------------------------------------------------------------- #
# Gate 2 — set_item parses and DAG-checks at call time, in every scenario
# --------------------------------------------------------------------------- #


@pytest.fixture(params=["base", "downside"])
def target(request, kit: CashKit) -> str:
    """The same admission rule, addressed to base and to a fork (ADR-0031)."""
    if request.param != "base":
        assert kit.fork(request.param).ok
    return request.param


class TestSetItemChecksNow:
    @pytest.mark.parametrize(
        "formula",
        [
            'it("consulting") +',  # syntactically not an expression
            "__import__('os').system('true')",  # not on the §5.4 surface
            "",  # not a formula at all
            'p.Not_A_Key * 2',  # CK-E007: formulas address params as p.<key>
        ],
    )
    def test_a_formula_that_does_not_parse_never_reaches_the_book(
        self, kit: CashKit, target: str, formula: str
    ) -> None:
        report = kit.set_item(derived("broken", formula), scenario=target)
        assert not report.ok, "a formula that is not a formula must be refused"
        assert codes(report) & {"CK-E003", "CK-E007"}
        assert "broken" not in kit.resolve(target).book.items
        assert report.empty, "a refused write records nothing"

    def test_the_refusal_is_not_deferred_to_a_run(self, kit: CashKit, target: str) -> None:
        """Gate 2's real claim: never a later engine failure."""
        kit.set_item(derived("broken", 'it("x") +'), scenario=target)
        run = kit.run(target)
        assert not [d for d in run.diagnostics if d.item_id == "broken"]
        assert not [d for d in kit.validate(target) if d.item_id == "broken"]

    def test_an_unknown_reference_is_reported_at_call_time(self, kit: CashKit, target: str) -> None:
        report = kit.set_item(derived("orphan", 'it("nowhere")'), scenario=target)
        assert "CK-E001" in codes(report)
        assert not report.ok
        # Recorded all the same: the reference resolves the moment the item it
        # names is added, and refusing would make that order unreachable.
        assert "orphan" in kit.resolve(target).book.items

    def test_a_cycle_with_no_prev_edge_is_reported_at_call_time(self, kit: CashKit, target: str) -> None:
        kit.set_item(derived("a", 'it("b")'), scenario=target)
        report = kit.set_item(derived("b", 'it("a")'), scenario=target)
        assert "CK-E002" in codes(report)

    def test_a_prev_cycle_is_legal_and_reports_nothing(self, kit: CashKit, target: str) -> None:
        report = kit.set_item(
            derived("cash_balance", 'prev("cash_balance", init=p.opening_balance) + 100', kind="stock"),
            scenario=target,
        )
        assert report.ok, report.diagnostics
        assert kit.resolve(target).book.items["cash_balance"].kind == "stock"

    def test_an_agg_selector_matching_nothing_is_reported(self, kit: CashKit, target: str) -> None:
        report = kit.set_item(derived("total", 'agg(tag="cat:nothing")'), scenario=target)
        assert "CK-E001" in codes(report)

    def test_a_problem_the_book_already_had_is_not_blamed_on_the_new_item(self, kit: CashKit) -> None:
        kit.set_item(derived("orphan", 'it("nowhere")'))
        report = kit.set_item(flow("rent", "-1000", direction="out"))
        assert report.ok and codes(report) == set()


class TestSetItemValidatesInIsolation:
    def test_an_amount_whose_sign_contradicts_direction_is_refused(self, kit: CashKit, target: str) -> None:
        report = kit.set_item(flow("rent", "3000", direction="out"), scenario=target)
        assert "CK-E011" in codes(report)
        assert "rent" not in kit.resolve(target).book.items

    def test_a_generative_stock_is_refused(self, kit: CashKit, target: str) -> None:
        spec = flow("reserve", "1000", direction="in").model_copy(update={"kind": "stock"})
        report = kit.set_item(spec, scenario=target)
        assert "CK-E012" in codes(report)
        assert "reserve" not in kit.resolve(target).book.items

    def test_a_settlement_that_cannot_mean_anything_is_refused(self, kit: CashKit, target: str) -> None:
        spec = flow(
            "consulting",
            "1000",
            direction="in",
            settlement=Settlement.split([(Decimal("0.3"), "0d"), (Decimal("0.5"), "30d")]),
        )
        report = kit.set_item(spec, scenario=target)
        assert "CK-E004" in codes(report)
        assert "consulting" not in kit.resolve(target).book.items

    def test_a_formula_on_a_flow_item_is_refused(self, kit: CashKit, target: str) -> None:
        spec = flow("rent", "-1000", direction="out").model_copy(update={"formula": 'it("x")'})
        report = kit.set_item(spec, scenario=target)
        assert "CK-E003" in codes(report)
        assert "rent" not in kit.resolve(target).book.items

    def test_re_authoring_reports_the_fields_that_moved(self, kit: CashKit, target: str) -> None:
        kit.set_item(flow("rent", "-1000", direction="out", tags={"cat": "opex"}), scenario=target)
        again = kit.set_item(flow("rent", "-1200", direction="out", tags={"cat": "opex"}), scenario=target)
        assert again.ok
        assert again.created == ()
        assert again.changed == ("segments",)

    def test_an_identical_re_add_records_nothing(self, kit: CashKit, target: str) -> None:
        spec = flow("rent", "-1000", direction="out")
        kit.set_item(spec, scenario=target)
        again = kit.set_item(spec, scenario=target)
        assert again.ok and again.empty
        assert codes(again) == {"CK-I002"}

    def test_an_unknown_scenario_is_a_diagnostic(self, kit: CashKit) -> None:
        report = kit.set_item(flow("rent", "-1000", direction="out"), scenario="nowhere")
        assert codes(report) == {"CK-E021"} and report.empty
        assert "rent" not in kit.book.items


class TestBaseIsAddressedLikeAnyScenario:
    """ADR-0031: the caller never chooses a verb by whether the target is base."""

    def test_a_base_write_lands_in_the_authored_book_not_an_overlay(self, kit: CashKit) -> None:
        kit.set_item(flow("rent", "-1000", direction="out"))
        kit.set_param("inflation", Decimal("0.03"))
        assert "rent" in kit.book.items and kit.book.params["inflation"] == Decimal("0.03")
        assert kit.scenarios["base"].items == {} and kit.scenarios["base"].params == {}

    def test_a_fork_write_records_only_the_difference(self, kit: CashKit) -> None:
        kit.set_item(flow("rent", "-1000", direction="out", tags={"cat": "opex"}))
        kit.fork("downside")
        kit.set_item(flow("rent", "-1000", direction="out", tags={"cat": "opex", "tier": "b"}), scenario="downside")
        assert kit.scenarios["downside"].items["rent"].recorded_fields() == frozenset({"tags"})
        assert kit.book.items["rent"].tags == {"cat": "opex"}

    def test_remove_and_unset_address_base_the_same_way(self, kit: CashKit) -> None:
        kit.set_item(flow("rent", "-1000", direction="out"))
        assert kit.unset("rent", scenario="base").empty, "base records nothing sparsely"
        assert kit.remove_item("rent").changed == ("removed",)
        assert "rent" not in kit.book.items
        assert kit.remove_item("rent").empty

    def test_retag_is_a_macro_on_base(self, kit: CashKit) -> None:
        kit.set_item(flow("rent", "-3000", direction="out", tags={"cat": "opex"}))
        kit.set_item(flow("power", "-400", direction="out", tags={"cat": "opex"}))
        kit.set_item(flow("consulting", "9000", direction="in", tags={"cat": "revenue"}))

        report = kit.apply_macro(RetagItems(selector="cat:opex", tags={"team": "ops"}))
        assert report.ok and set(report.changed) == {"rent.tags", "power.tags"}
        assert kit.book.items["rent"].tags["team"] == "ops"
        assert "team" not in kit.book.items["consulting"].tags
        again = kit.apply_macro(RetagItems(selector="cat:opex", tags={"team": "ops"}))
        assert again.empty and codes(again) == {"CK-I002"}

    def test_a_selector_matching_nothing_and_a_malformed_one_are_different_answers(self, kit: CashKit) -> None:
        kit.set_item(flow("rent", "-3000", direction="out", tags={"cat": "opex"}))
        nothing = kit.apply_macro(RetagItems(selector="cat:nowhere", tags={"team": "ops"}))
        assert nothing.ok and nothing.empty and codes(nothing) == {"CK-I002"}
        typo = kit.apply_macro(RetagItems(selector="nocolon", tags={"team": "ops"}))
        assert not typo.ok and typo.empty and codes(typo) == {"CK-E003"}


# --------------------------------------------------------------------------- #
# Gate 3 — reconcile reports drift exactly and composes with set_book(cutover=)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def drifting(tmp_path: Path) -> CashKit:
    """Rent of 3 000 a month forecast; January actually cost 3 100."""
    kit = new_kit(tmp_path / "drift", opening_balance=Decimal("50000.0000"))
    kit.set_item(flow("rent", "-3000", direction="out", tags={"cat": "opex"}, settlement=Settlement.immediate()))
    assert kit.ledger is not None
    kit.import_events(
        [
            Event(id="b1", date=date(2026, 1, 1), amount=Decimal("-3100.0000"), status="actual", item="rent", source="bank", ext_id="JAN"),
            Event(id="b2", date=date(2026, 2, 1), amount=Decimal("-3000.0000"), status="actual", item="rent", source="bank", ext_id="FEB"),
        ],
        source="bank",
    )
    return kit


class TestReconcile:
    def test_it_reports_the_drift_exactly(self, drifting: CashKit) -> None:
        report = drifting.reconcile(until=date(2026, 2, 28))
        assert report.since == date(2026, 1, 1)
        assert report.actual_events == 2
        line = {item.item_id: item for item in report.lines}["rent"]
        assert line.forecast == Decimal("-6000.0000")
        assert line.actual == Decimal("-6100.0000")
        assert line.drift == Decimal("-100.0000")
        assert report.drift_total == Decimal("-100.0000")
        assert not report.reconciled

    def test_a_book_that_matched_its_actuals_reconciles(self, drifting: CashKit) -> None:
        """February alone was forecast exactly, so its window shows no drift."""
        report = drifting.reconcile(until=date(2026, 2, 28), since=date(2026, 2, 1))
        assert report.drift_total == 0
        assert report.reconciled

    def test_an_actual_referencing_no_item_is_named_not_absorbed(self, drifting: CashKit) -> None:
        drifting.add_event(
            Event(id="b3", date=date(2026, 1, 20), amount=Decimal("-500.0000"), status="actual", ext_id="FEE", source="bank")
        )
        report = drifting.reconcile(until=date(2026, 2, 28))
        carriers = [line for line in report.lines if line.item_id.startswith("_event:")]
        assert len(carriers) == 1
        assert carriers[0].forecast == 0
        assert carriers[0].drift == Decimal("-500.0000")

    def test_its_output_composes_with_set_book(self, drifting: CashKit) -> None:
        report = drifting.reconcile(until=date(2026, 2, 28))
        assert report.suggested_cutover == date(2026, 3, 1)

        moved = drifting.set_book(cutover=report.suggested_cutover)
        assert moved.changed == ("cutover",)

        # Generation before the new cutover is suppressed entirely, so the two
        # reconciled months now hold the ledger's numbers and nothing else.
        run = drifting.run()
        window = [
            index for index, start in enumerate(run.result.periods.starts) if start < date(2026, 3, 1)
        ]
        cash = sum(int(run.result.cash[item_id][index]) for item_id in run.result.cash for index in window)
        assert cash == -61_000_000, "actuals only: -3 100 + -3 000 at 4 dp minor units"

    def test_reconciling_twice_over_a_closed_window_reports_nothing(self, drifting: CashKit) -> None:
        report = drifting.reconcile(until=date(2026, 2, 28))
        drifting.set_book(cutover=report.suggested_cutover)
        again = drifting.reconcile(until=date(2026, 2, 28), since=date(2026, 1, 1))
        # The window is now history: no generation, and the actuals still there.
        assert again.forecast_total == 0
        assert again.actual_total == Decimal("-6100.0000")


# --------------------------------------------------------------------------- #
# Gate 4 — `cashkit init` produces a book create_book could have produced
# --------------------------------------------------------------------------- #


class TestCliAndSdkAgree:
    def test_the_two_books_are_byte_identical_under_the_canonical_emitter(self, tmp_path: Path) -> None:
        cli_root = tmp_path / "via-cli"
        out, err = io.StringIO(), io.StringIO()
        code = cli_main(
            [
                "init", str(cli_root), "--id", "acme", "--horizon", "2026-01-01:2027-01-01",
                "--opening-balance", "250000.00", "--calendar", "IT", "--fiscal-year-start", "7",
                "--cutover", "2026-02-01", "--no-commit",
            ],
            out=out,
            err=err,
        )
        assert code == EXIT_OK, err.getvalue()

        via_sdk, problems = create_book(
            tmp_path / "via-sdk",
            id="acme",
            horizon=YEAR,
            opening_balance=Decimal("250000.00"),
            grain=Grain.DAY,
            calendar=CalendarSpec(fiscal_year_start_month=7, country="IT"),
            cutover=date(2026, 2, 1),
        )
        assert via_sdk is not None, problems

        via_cli, _ = CashKit.open(cli_root)
        assert via_cli is not None
        assert to_canonical_yaml(via_cli.book) == to_canonical_yaml(via_sdk.book)

    def test_the_cli_no_longer_builds_a_book_itself(self) -> None:
        """The one construction path, proved from the source."""
        import ast

        import cashkit

        source = Path(cashkit.__file__).parent / "cli" / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        built = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"Book", "Item", "Segment", "Scenario"}
        ]
        assert not built, f"the CLI constructs models directly: {built}"


# --------------------------------------------------------------------------- #
# create_book's own refusals
# --------------------------------------------------------------------------- #


class TestCreateBook:
    def test_it_refuses_to_create_a_book_over_a_book(self, tmp_path: Path) -> None:
        root = tmp_path / "twice"
        first, _ = create_book(root, id="twice", horizon=YEAR, opening_balance=Decimal(0))
        assert first is not None
        again, problems = create_book(root, id="twice", horizon=YEAR, opening_balance=Decimal(0))
        assert again is None
        assert {d.code for d in problems} == {"CK-E031"}

    def test_a_malformed_argument_is_a_diagnostic_not_a_traceback(self, tmp_path: Path) -> None:
        kit, problems = create_book(tmp_path / "bad", id="Not An Id", horizon=YEAR, opening_balance=Decimal(0))
        assert kit is None
        assert {d.code for d in problems} == {"CK-E032"}
        assert not (tmp_path / "bad" / "book.yaml").exists()

    def test_money_past_four_decimal_places_is_refused(self, tmp_path: Path) -> None:
        kit, problems = create_book(tmp_path / "dp", id="dp", horizon=YEAR, opening_balance=Decimal("1.000005"))
        assert kit is None and {d.code for d in problems} == {"CK-E032"}

    def test_an_unknown_country_still_creates_the_book(self, tmp_path: Path) -> None:
        kit, _ = create_book(tmp_path / "zz", id="zz", horizon=YEAR, opening_balance=Decimal(0), calendar="ZZ")
        assert kit is not None
        assert kit.book.calendar.holidays == []


# --------------------------------------------------------------------------- #
# set_param and set_book
# --------------------------------------------------------------------------- #


class TestBookLevelWrites:
    def test_set_param_records_only_a_real_change(self, kit: CashKit) -> None:
        assert kit.set_param("inflation", Decimal("0.03")).changed == ("params.inflation",)
        again = kit.set_param("inflation", Decimal("0.03"))
        assert again.empty and codes(again) == {"CK-I002"}

    def test_a_param_key_a_formula_could_not_address_is_refused(self, kit: CashKit, target: str) -> None:
        report = kit.set_param("Vat.Standard", Decimal("0.22"), scenario=target)
        assert codes(report) == {"CK-E007"}
        assert "Vat.Standard" not in kit.resolve(target).book.params

    def test_the_reserved_opening_balance_param_is_checked_as_money(self, kit: CashKit, target: str) -> None:
        report = kit.set_param("opening_balance", Decimal("1.000005"), scenario=target)
        assert codes(report) == {"CK-E024"}
        assert "opening_balance" not in kit.resolve(target).book.params

    def test_set_book_reports_nothing_when_the_value_is_unchanged(self, kit: CashKit) -> None:
        assert kit.set_book(cutover=date(2026, 2, 1)).changed == ("cutover",)
        again = kit.set_book(cutover=date(2026, 2, 1))
        assert again.empty and codes(again) == {"CK-I002"}

    def test_set_book_refuses_a_field_it_does_not_own(self, kit: CashKit) -> None:
        with pytest.raises(ValueError, match="set_book accepts"):
            kit.set_book(base_grain=Grain.MONTH)

    def test_a_value_that_cannot_make_a_book_is_a_diagnostic(self, kit: CashKit) -> None:
        report = kit.set_book(horizon=PeriodRange.model_construct(start=date(2026, 4, 1), end=date(2026, 1, 1)))
        assert codes(report) == {"CK-E032"} and report.empty
        assert kit.book.horizon == QUARTER
        bad_money = kit.set_book(opening_balance=Decimal("1.000005"))
        assert codes(bad_money) == {"CK-E024"} and bad_money.empty

    def test_a_cutover_past_the_horizon_is_recorded_and_warned_about(self, kit: CashKit) -> None:
        """The quietest failure on this surface: a cutover past ``horizon.end``
        suppresses every generative occurrence there is. The book still
        compiles, the run still succeeds, and every number is zero — nothing
        anywhere says why. ``CK-W006`` is that sentence."""
        report = kit.set_book(cutover=date(2026, 5, 1))
        assert report.changed == ("cutover",), "warned, never refused"
        assert codes(report) == {"CK-W006"}
        assert kit.book.cutover == date(2026, 5, 1)

        (warning,) = report.diagnostics
        assert warning.severity == "warning" and warning.field == "cutover"
        assert "2026-04-01" in warning.message, "the horizon it is outside"
        assert "suppressed" in warning.message
        assert warning.suggested_fix

        # And the numbers back the message up: nothing is generated at all.
        kit.set_item(flow("rent", "-3000", direction="out"))
        assert kit.run().summary().net_cash == Decimal(0)

    def test_a_cutover_before_the_horizon_is_warned_about_as_a_no_op(self, kit: CashKit) -> None:
        report = kit.set_book(cutover=date(2025, 12, 1))
        assert report.changed == ("cutover",)
        assert codes(report) == {"CK-W006"}
        assert "no effect" in report.diagnostics[0].message

    @pytest.mark.parametrize("day", [date(2026, 1, 1), date(2026, 2, 1), date(2026, 4, 1)])
    def test_a_cutover_inside_the_horizon_says_nothing_new(self, kit: CashKit, day: date) -> None:
        """The horizon is half-open, so its own ``end`` is the last legal day."""
        assert codes(kit.set_book(cutover=day)) <= {"CK-I002"}

    def test_validate_reports_a_book_already_in_that_state(self, kit: CashKit) -> None:
        """The warning must not depend on having watched the write happen — a
        book opened from disk carries the condition, not the call."""
        kit.set_book(cutover=date(2026, 6, 1))
        reopened, problems = CashKit.open(kit.root)
        assert reopened is not None and problems == ()
        assert "CK-W006" in {d.code for d in reopened.validate()}
        assert "CK-W006" in {d.code for d in reopened.run().diagnostics}, "validate() is the run"

        assert reopened.set_book(cutover=date(2026, 3, 1)).changed == ("cutover",)
        assert "CK-W006" not in {d.code for d in reopened.validate()}

    def test_a_regime_that_cannot_work_on_its_own_terms_is_refused(self, kit: CashKit) -> None:
        report = kit.set_book(
            tax_regimes=[
                TaxRegime(id="vat", accumulates="cat:revenue", periodicity="annual", payment_offset="16d", credit_handling="refund_annual")
            ]
        )
        assert codes(report) == {"CK-E019"}
        assert kit.book.tax_regimes == []

    def test_a_regime_whose_selector_matches_nothing_yet_is_recorded_and_reported(self, kit: CashKit) -> None:
        report = kit.set_book(
            tax_regimes=[TaxRegime(id="vat", accumulates="cat:revenue", periodicity="quarterly", payment_offset="16d")]
        )
        assert "CK-E019" in codes(report)
        assert [regime.id for regime in kit.book.tax_regimes] == ["vat"]

        kit.set_item(flow("consulting", "9000", direction="in", tags={"cat": "revenue"}))
        assert not [d for d in kit.validate() if d.code == "CK-E019"]

    def test_the_regime_list_is_replaced_whole(self, kit: CashKit) -> None:
        kit.set_item(flow("consulting", "9000", direction="in", tags={"cat": "revenue"}))
        regime = TaxRegime(id="vat", accumulates="cat:revenue", periodicity="quarterly", payment_offset="16d")
        assert kit.set_book(tax_regimes=[regime]).changed == ("tax_regimes",)
        monthly_regime = regime.model_copy(update={"periodicity": "monthly"})
        replaced = kit.set_book(tax_regimes=[monthly_regime])
        assert replaced.changed == ("tax_regimes",)
        assert [r.periodicity for r in kit.book.tax_regimes] == ["monthly"]


# --------------------------------------------------------------------------- #
# query_events, and the past as a type
# --------------------------------------------------------------------------- #


class TestQueryEvents:
    def test_it_returns_the_prd_table_shape(self, drifting: CashKit) -> None:
        table = drifting.query_events()
        assert table.columns[:4] == ("id", "date", "amount", "status")
        assert len(table) == 2
        assert table.column("amount") == (Decimal("-3100.0000"), Decimal("-3000.0000"))

    def test_the_date_window_is_inclusive_on_both_ends(self, drifting: CashKit) -> None:
        assert len(drifting.query_events(since=date(2026, 2, 1))) == 1
        assert len(drifting.query_events(until=date(2026, 1, 1))) == 1
        assert len(drifting.query_events(since=date(2026, 3, 1))) == 0

    def test_it_uses_the_one_selector_grammar(self, drifting: CashKit) -> None:
        drifting.add_event(
            Event(id="tagged", date=date(2026, 1, 10), amount=Decimal("10.0000"), status="forecast", tags={"cat": "misc"})
        )
        assert len(drifting.query_events("cat:misc")) == 1
        assert len(drifting.query_events("cat:absent")) == 0


class TestThePastIsAType:
    """ADR-0033: ``at(ref)`` returns a kit with no write methods.

    The defect this replaces: ``at(ref)`` shares the live ``root``, so a write
    on a bound kit could mutate the *past* book and save it over the
    **present** working tree, while the live kit went on reporting
    ``status().clean``. A write that reads history and lands in the present is
    now not a diagnostic to remember to return but a method that does not
    exist.
    """

    @pytest.fixture()
    def past(self, drifting: CashKit) -> ReadOnlyKit:
        report = drifting.commit("state")
        assert report.revision is not None
        bound, problems = drifting.at(report.revision.id)
        assert bound is not None and problems == ()
        return bound

    def test_no_write_verb_exists_on_it(self, past: ReadOnlyKit) -> None:
        assert isinstance(past, ReadOnlyKit) and not isinstance(past, CashKit)
        for verb in (
            "set_item", "set_param", "set_book", "apply_macro", "remove_item", "unset", "fork",
            "flatten", "add_event", "import_events", "void_event", "correct_event", "commit",
            "discard",
        ):
            assert not hasattr(past, verb), f"{verb} exists on a read-only kit"

    def test_the_live_tree_is_untouched_by_holding_the_past(self, drifting: CashKit, past: ReadOnlyKit) -> None:
        before = (drifting.root / "book.yaml").read_bytes()
        past.run()
        past.validate()
        past.describe_book()
        assert (drifting.root / "book.yaml").read_bytes() == before
        assert drifting.status().clean
        assert len(drifting.query_events()) == 2, "the live ledger is untouched"

    def test_reads_on_the_bound_kit_still_work(self, past: ReadOnlyKit) -> None:
        assert "rent" in past.book.items
        assert past.run().summary().net_cash == Decimal("-15100.0000")
        assert past.describe_book().items
        assert past.resolve("base").book.items["rent"].name == "rent"
        assert past.at("HEAD")[0] is not None

    def test_a_read_only_kit_is_the_surface_of_the_live_one_minus_writes(self) -> None:
        """Structural: the live kit adds writes and nothing else, so a read on
        one is a read on the other, and a verb added later lands on the right
        side or this fails."""
        reads = {name for name in vars(ReadOnlyKit) if not name.startswith("_")}
        added = {name for name in vars(CashKit) if not name.startswith("_")}
        assert reads & added == set(), "a read-only verb is redefined on the live kit"
        assert added == {
            "init", "open", "set_item", "remove_item", "unset", "set_param", "apply_macro",
            "set_book", "fork", "flatten", "add_event", "import_events", "void_event",
            "correct_event", "commit", "status", "discard",
        }, "the write surface moved; check the ADR-0033 split still holds"
