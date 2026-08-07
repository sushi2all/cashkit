"""Session S5.5 — the construction surface (PRD §6.1) and the two §6.2 gaps.

The gate this file carries is the one thing no earlier phase could assert: that
a book can be **built through the SDK alone**. Until now `cashkit init`
constructed a `Book` by hand because there was no `create_book()` to call, which
made the SDK-only non-negotiable true of every caller except the one that
mattered most.

Four things are proved here beyond "the functions exist":

* **The whole lifecycle runs on public calls only** — create, author, tax,
  ledger, cutover, run, summary, commit, history — from an empty directory, and
  ends with a clean `validate()`.
* **`add_derived` parses and DAG-checks now.** A formula that does not parse
  never reaches the book; one that parses but cannot resolve is recorded with
  its diagnostic *at call time*, so an agent never meets it as a zero column
  three steps later.
* **`reconcile` reports drift exactly**, in int64 minor units, and the day it
  suggests feeds `set_cutover` directly.
* **The CLI is now a caller like any other.** `cashkit init` and `create_book`
  produce books that are equal byte for byte under the canonical emitter — not
  "equivalent", identical, because a second construction path that happens to
  agree today is a second one that can stop agreeing.
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
from cashkit.sdk import (
    AffectedCount,
    CashKit,
    add_derived,
    add_item,
    add_tax_regime,
    create_book,
    query_events,
    reconcile,
    retag,
    set_cutover,
    set_param,
)

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


# --------------------------------------------------------------------------- #
# Gate 1 — the whole lifecycle, SDK calls only
# --------------------------------------------------------------------------- #


class TestEndToEndThroughTheSdkAlone:
    def test_an_empty_directory_becomes_a_working_book(self, tmp_path: Path) -> None:
        root = tmp_path / "acme"
        assert not root.exists()

        ref = create_book(
            root,
            id="acme",
            horizon=YEAR,
            opening_balance=Decimal("250000.0000"),
            grain=Grain.DAY,
            calendar="IT",
        )
        assert ref.ok, ref.diagnostics
        kit = ref.kit
        assert kit is not None
        assert kit.book.cutover == YEAR.start, "cutover is authored, never today()"
        assert kit.book.calendar.holidays, "ADR-0010: the holiday set is resolved once"

        assert set_param(kit, "vat_standard", Decimal("0.22")).changed == (
            "params.vat_standard",
        )

        added = [
            add_item(
                kit,
                flow(
                    "consulting",
                    "12000",
                    direction="in",
                    tags={"cat": "revenue", "customer": "acme"},
                    settlement=Settlement.net(30),
                    vat=VatSpec(),
                ),
            ),
            add_item(
                kit,
                flow(
                    "rent",
                    "-3000",
                    direction="out",
                    tags={"cat": "opex"},
                    settlement=Settlement.immediate(),
                ),
            ),
            add_item(
                kit,
                flow(
                    "salaries",
                    "-8000",
                    direction="out",
                    day=27,
                    tags={"cat": "payroll"},
                    settlement=Settlement.split(
                        [(Decimal("0.5"), "0d"), (Decimal("0.5"), "15d")]
                    ),
                ),
            ),
        ]
        assert all(item.ok for item in added), [item.diagnostics for item in added]
        assert [item.created for item in added] == [
            ("consulting",),
            ("rent",),
            ("salaries",),
        ]

        margin = add_derived(
            kit, "margin", 'it("consulting") + it("rent")', {"cat": "derived"}
        )
        assert margin.ok and margin.created == ("margin",)

        regime = add_tax_regime(
            kit,
            TaxRegime(
                id="vat",
                accumulates="",
                periodicity="quarterly",
                payment_offset="16d",
            ),
        )
        assert regime.ok and regime.created == ("vat",)

        assert kit.ledger is not None
        appended = kit.add_event(
            Event(
                id="ev-jan",
                date=date(2026, 1, 15),
                amount=Decimal("4200.0000"),
                status="forecast",
                item="consulting",
            )
        )
        assert appended.ok and appended.created == ("ev-jan",)
        imported = kit.import_events(
            [
                Event(
                    id="bank-1",
                    date=date(2026, 1, 5),
                    amount=Decimal("-3050.0000"),
                    status="actual",
                    item="rent",
                    source="bank:IT60X",
                    ext_id="TX-1",
                ),
                Event(
                    id="bank-2",
                    date=date(2026, 2, 5),
                    amount=Decimal("-3000.0000"),
                    status="actual",
                    item="rent",
                    source="bank:IT60X",
                    ext_id="TX-2",
                ),
            ],
            source="bank:IT60X",
        )
        assert imported.ok and imported.inserted == 2, imported.diagnostics

        assert set_cutover(kit, date(2026, 3, 1), note="February closed").changed == (
            "cutover",
        )

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
        kit = create_book(
            root, id="layout", horizon=YEAR, opening_balance=Decimal(0)
        ).kit
        assert kit is not None
        add_item(kit, flow("rent", "-1000", direction="out"))
        for path in (
            ".cashkit/version",
            ".cashkit/config.toml",
            "book.yaml",
            "params.yaml",
            "items/rent.yaml",
            "scenarios/base.yaml",
        ):
            assert (root / path).is_file(), path

    def test_a_reopened_book_holds_everything_that_was_authored(
        self, tmp_path: Path
    ) -> None:
        """Every verb saves: the working tree on disk *is* the working state."""
        root = tmp_path / "persist"
        kit = create_book(
            root, id="persist", horizon=YEAR, opening_balance=Decimal(0)
        ).kit
        assert kit is not None
        add_item(kit, flow("rent", "-1000", direction="out", tags={"cat": "opex"}))
        set_param(kit, "inflation", Decimal("0.03"))
        set_cutover(kit, date(2026, 2, 1))
        add_tax_regime(
            kit,
            TaxRegime(
                id="vat",
                accumulates="cat:opex",
                periodicity="monthly",
                payment_offset="16d",
            ),
        )

        reopened, _ = CashKit.open(root)
        assert reopened is not None
        assert set(reopened.book.items) == {"rent"}
        assert reopened.book.params["inflation"] == Decimal("0.03")
        assert reopened.book.cutover == date(2026, 2, 1)
        assert [regime.id for regime in reopened.book.tax_regimes] == ["vat"]


class TestTheKitMethodsAreTheSameSurface:
    def test_every_verb_is_reachable_from_the_object_an_agent_holds(
        self, tmp_path: Path
    ) -> None:
        """A surface split across two import sites is one an agent gets wrong."""
        kit = create_book(
            tmp_path / "methods", id="methods", horizon=QUARTER, opening_balance=Decimal(0)
        ).kit
        assert kit is not None

        assert kit.add_item(
            flow("rent", "-1000", direction="out", tags={"cat": "opex"})
        ).created == ("rent",)
        assert kit.add_derived("twice_rent", 'it("rent") * 2').ok
        assert kit.set_param("inflation", Decimal("0.02")).changed == (
            "params.inflation",
        )
        assert kit.retag("cat:opex", {"team": "ops"}) == 1
        assert kit.add_tax_regime(
            TaxRegime(
                id="vat",
                accumulates="cat:opex",
                periodicity="monthly",
                payment_offset="16d",
            )
        ).created == ("vat",)
        assert kit.set_cutover(date(2026, 2, 1)).changed == ("cutover",)
        assert kit.query_events().columns
        assert kit.reconcile(date(2026, 2, 28)).since == date(2026, 2, 1)

    def test_ledger_reads_survive_a_kit_with_no_ledger(self, tmp_path: Path) -> None:
        """An in-memory kit is a legal kit; a query against no ledger is empty."""
        kit = create_book(
            tmp_path / "none", id="none", horizon=QUARTER, opening_balance=Decimal(0)
        ).kit
        assert kit is not None
        kit.ledger = None
        assert len(query_events(kit)) == 0
        with pytest.raises(ValueError, match="no ledger store"):
            kit.add_event(
                Event(
                    id="x",
                    date=date(2026, 1, 1),
                    amount=Decimal("1.0000"),
                    status="forecast",
                )
            )


# --------------------------------------------------------------------------- #
# Gate 2 — add_derived parses and DAG-checks at call time
# --------------------------------------------------------------------------- #


@pytest.fixture()
def kit(tmp_path: Path) -> CashKit:
    ref = create_book(
        tmp_path / "book",
        id="book",
        horizon=QUARTER,
        opening_balance=Decimal("10000.0000"),
    )
    assert ref.kit is not None
    return ref.kit


class TestAddDerivedChecksNow:
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
        self, kit: CashKit, formula: str
    ) -> None:
        ref = add_derived(kit, "broken", formula)
        assert not ref.ok, "a formula that is not a formula must be refused"
        assert codes(ref) & {"CK-E003", "CK-E007"}
        assert "broken" not in kit.book.items
        assert ref.empty, "a refused write records nothing"

    def test_the_refusal_is_not_deferred_to_a_run(self, kit: CashKit) -> None:
        """Gate 2's real claim: never a later engine failure."""
        add_derived(kit, "broken", 'it("x") +')
        run = kit.run()
        assert not [d for d in run.diagnostics if d.item_id == "broken"]
        assert not [d for d in kit.validate() if d.item_id == "broken"]

    def test_an_unknown_reference_is_reported_at_call_time(self, kit: CashKit) -> None:
        ref = add_derived(kit, "orphan", 'it("nowhere")')
        assert "CK-E001" in codes(ref)
        assert not ref.ok
        # Recorded all the same: the reference resolves the moment the item it
        # names is added, and refusing would make that order unreachable.
        assert "orphan" in kit.book.items

    def test_a_cycle_with_no_prev_edge_is_reported_at_call_time(
        self, kit: CashKit
    ) -> None:
        add_derived(kit, "a", 'it("b")')
        ref = add_derived(kit, "b", 'it("a")')
        assert "CK-E002" in codes(ref)

    def test_a_prev_cycle_is_legal_and_reports_nothing(self, kit: CashKit) -> None:
        ref = add_derived(
            kit,
            "cash_balance",
            'prev("cash_balance", init=p.opening_balance) + 100',
            kind="stock",
        )
        assert ref.ok, ref.diagnostics
        assert kit.book.items["cash_balance"].kind == "stock"

    def test_an_agg_selector_matching_nothing_is_reported(self, kit: CashKit) -> None:
        ref = add_derived(kit, "total", 'agg(tag="cat:nothing")')
        assert "CK-E001" in codes(ref)


class TestAddItemValidatesInIsolation:
    def test_an_amount_whose_sign_contradicts_direction_is_refused(
        self, kit: CashKit
    ) -> None:
        ref = add_item(kit, flow("rent", "3000", direction="out"))
        assert "CK-E011" in codes(ref)
        assert "rent" not in kit.book.items

    def test_a_generative_stock_is_refused(self, kit: CashKit) -> None:
        spec = flow("reserve", "1000", direction="in").model_copy(
            update={"kind": "stock"}
        )
        ref = add_item(kit, spec)
        assert "CK-E012" in codes(ref)
        assert "reserve" not in kit.book.items

    def test_a_settlement_that_cannot_mean_anything_is_refused(
        self, kit: CashKit
    ) -> None:
        spec = flow(
            "consulting",
            "1000",
            direction="in",
            settlement=Settlement.split([(Decimal("0.3"), "0d"), (Decimal("0.5"), "30d")]),
        )
        ref = add_item(kit, spec)
        assert "CK-E004" in codes(ref)
        assert "consulting" not in kit.book.items

    def test_a_formula_on_a_flow_item_is_refused(self, kit: CashKit) -> None:
        spec = flow("rent", "-1000", direction="out").model_copy(
            update={"formula": 'it("x")'}
        )
        ref = add_item(kit, spec)
        assert "CK-E003" in codes(ref)
        assert "rent" not in kit.book.items

    def test_re_authoring_reports_the_fields_that_moved(self, kit: CashKit) -> None:
        add_item(kit, flow("rent", "-1000", direction="out", tags={"cat": "opex"}))
        again = add_item(
            kit, flow("rent", "-1200", direction="out", tags={"cat": "opex"})
        )
        assert again.ok
        assert again.created == ()
        assert again.changed == ("segments",)

    def test_an_identical_re_add_records_nothing(self, kit: CashKit) -> None:
        spec = flow("rent", "-1000", direction="out")
        add_item(kit, spec)
        again = add_item(kit, spec)
        assert again.ok and again.empty
        assert codes(again) == {"CK-I002"}


# --------------------------------------------------------------------------- #
# Gate 3 — reconcile reports drift exactly and composes with set_cutover
# --------------------------------------------------------------------------- #


@pytest.fixture()
def drifting(tmp_path: Path) -> CashKit:
    """Rent of 3 000 a month forecast; January actually cost 3 100."""
    ref = create_book(
        tmp_path / "drift",
        id="drift",
        horizon=QUARTER,
        opening_balance=Decimal("50000.0000"),
    )
    kit = ref.kit
    assert kit is not None
    add_item(
        kit,
        flow(
            "rent",
            "-3000",
            direction="out",
            tags={"cat": "opex"},
            settlement=Settlement.immediate(),
        ),
    )
    assert kit.ledger is not None
    kit.import_events(
        [
            Event(
                id="b1",
                date=date(2026, 1, 1),
                amount=Decimal("-3100.0000"),
                status="actual",
                item="rent",
                source="bank",
                ext_id="JAN",
            ),
            Event(
                id="b2",
                date=date(2026, 2, 1),
                amount=Decimal("-3000.0000"),
                status="actual",
                item="rent",
                source="bank",
                ext_id="FEB",
            ),
        ],
        source="bank",
    )
    return kit


class TestReconcile:
    def test_it_reports_the_drift_exactly(self, drifting: CashKit) -> None:
        report = reconcile(drifting, until=date(2026, 2, 28))
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
        report = reconcile(drifting, until=date(2026, 2, 28), since=date(2026, 2, 1))
        assert report.drift_total == 0
        assert report.reconciled

    def test_an_actual_referencing_no_item_is_named_not_absorbed(
        self, drifting: CashKit
    ) -> None:
        assert drifting.ledger is not None
        drifting.add_event(
            Event(
                id="b3",
                date=date(2026, 1, 20),
                amount=Decimal("-500.0000"),
                status="actual",
                ext_id="FEE",
                source="bank",
            )
        )
        report = reconcile(drifting, until=date(2026, 2, 28))
        carriers = [
            line for line in report.lines if line.item_id.startswith("_event:")
        ]
        assert len(carriers) == 1
        assert carriers[0].forecast == 0
        assert carriers[0].drift == Decimal("-500.0000")

    def test_its_output_composes_with_set_cutover(self, drifting: CashKit) -> None:
        report = reconcile(drifting, until=date(2026, 2, 28))
        assert report.suggested_cutover == date(2026, 3, 1)

        moved = set_cutover(drifting, report.suggested_cutover, note="Feb closed")
        assert moved.changed == ("cutover",)

        # Generation before the new cutover is suppressed entirely, so the two
        # reconciled months now hold the ledger's numbers and nothing else.
        run = drifting.run()
        window = [
            index
            for index, start in enumerate(run.result.periods.starts)
            if start < date(2026, 3, 1)
        ]
        cash = sum(
            int(run.result.cash[item_id][index])
            for item_id in run.result.cash
            for index in window
        )
        assert cash == -61_000_000, "actuals only: -3 100 + -3 000 at 4 dp minor units"

    def test_reconciling_twice_over_a_closed_window_reports_nothing(
        self, drifting: CashKit
    ) -> None:
        report = reconcile(drifting, until=date(2026, 2, 28))
        set_cutover(drifting, report.suggested_cutover)
        again = reconcile(drifting, until=date(2026, 2, 28), since=date(2026, 1, 1))
        # The window is now history: no generation, and the actuals still there.
        assert again.forecast_total == 0
        assert again.actual_total == Decimal("-6100.0000")


# --------------------------------------------------------------------------- #
# Gate 4 — retag returns the affected count
# --------------------------------------------------------------------------- #


class TestRetag:
    @pytest.fixture()
    def tagged(self, kit: CashKit) -> CashKit:
        add_item(kit, flow("rent", "-3000", direction="out", tags={"cat": "opex"}))
        add_item(kit, flow("power", "-400", direction="out", tags={"cat": "opex"}))
        add_item(
            kit, flow("consulting", "9000", direction="in", tags={"cat": "revenue"})
        )
        return kit

    def test_it_returns_the_number_of_items_it_changed(self, tagged: CashKit) -> None:
        affected = retag(tagged, "cat:opex", {"team": "ops"})
        assert affected == 2
        assert isinstance(affected, int)
        assert affected.ok and not affected.diagnostics
        assert tagged.book.items["rent"].tags["team"] == "ops"
        assert "team" not in tagged.book.items["consulting"].tags

    def test_a_selector_matching_nothing_is_zero_and_no_error(
        self, tagged: CashKit
    ) -> None:
        affected = retag(tagged, "cat:nowhere", {"team": "ops"})
        assert affected == 0
        assert affected.ok
        assert affected.diagnostics == ()

    def test_a_malformed_selector_is_zero_and_says_so(self, tagged: CashKit) -> None:
        """A typo and an honest miss must not be the same answer."""
        affected = retag(tagged, "nocolon", {"team": "ops"})
        assert affected == 0
        assert not affected.ok
        assert {d.code for d in affected.diagnostics} == {"CK-E003"}

    def test_retagging_the_same_tags_twice_changes_nothing_the_second_time(
        self, tagged: CashKit
    ) -> None:
        assert retag(tagged, "cat:opex", {"team": "ops"}) == 2
        assert retag(tagged, "cat:opex", {"team": "ops"}) == 0

    def test_it_is_the_integer_the_prd_types_it_as(self, tagged: CashKit) -> None:
        affected = retag(tagged, "cat:opex", {"team": "ops"})
        assert isinstance(affected, AffectedCount)
        assert affected + 1 == 3
        assert f"{affected}" == "2"


# --------------------------------------------------------------------------- #
# Gate 5 — `cashkit init` produces a book create_book could have produced
# --------------------------------------------------------------------------- #


class TestCliAndSdkAgree:
    def test_the_two_books_are_byte_identical_under_the_canonical_emitter(
        self, tmp_path: Path
    ) -> None:
        cli_root = tmp_path / "via-cli"
        out, err = io.StringIO(), io.StringIO()
        code = cli_main(
            [
                "init",
                str(cli_root),
                "--id",
                "acme",
                "--horizon",
                "2026-01-01:2027-01-01",
                "--opening-balance",
                "250000.00",
                "--calendar",
                "IT",
                "--fiscal-year-start",
                "7",
                "--cutover",
                "2026-02-01",
                "--no-commit",
            ],
            out=out,
            err=err,
        )
        assert code == EXIT_OK, err.getvalue()

        sdk_ref = create_book(
            tmp_path / "via-sdk",
            id="acme",
            horizon=YEAR,
            opening_balance=Decimal("250000.00"),
            grain=Grain.DAY,
            calendar=CalendarSpec(fiscal_year_start_month=7, country="IT"),
            cutover=date(2026, 2, 1),
        )
        assert sdk_ref.kit is not None

        via_cli, _ = CashKit.open(cli_root)
        assert via_cli is not None
        assert to_canonical_yaml(via_cli.book) == to_canonical_yaml(sdk_ref.kit.book)

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
        assert create_book(
            root, id="twice", horizon=YEAR, opening_balance=Decimal(0)
        ).ok
        again = create_book(
            root, id="twice", horizon=YEAR, opening_balance=Decimal(0)
        )
        assert not again.ok
        assert again.kit is None
        assert codes(again) == {"CK-E031"}

    def test_a_malformed_argument_is_a_diagnostic_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        ref = create_book(
            tmp_path / "bad", id="Not An Id", horizon=YEAR, opening_balance=Decimal(0)
        )
        assert not ref.ok
        assert codes(ref) == {"CK-E032"}
        assert not (tmp_path / "bad" / "book.yaml").exists()

    def test_money_past_four_decimal_places_is_refused(self, tmp_path: Path) -> None:
        ref = create_book(
            tmp_path / "dp",
            id="dp",
            horizon=YEAR,
            opening_balance=Decimal("1.000005"),
        )
        assert codes(ref) == {"CK-E032"}

    def test_an_unknown_country_still_creates_the_book(self, tmp_path: Path) -> None:
        ref = create_book(
            tmp_path / "zz",
            id="zz",
            horizon=YEAR,
            opening_balance=Decimal(0),
            calendar="ZZ",
        )
        assert ref.ok and ref.kit is not None
        assert ref.kit.book.calendar.holidays == []


# --------------------------------------------------------------------------- #
# set_param, add_tax_regime, set_cutover
# --------------------------------------------------------------------------- #


class TestBookLevelWrites:
    def test_set_param_records_only_a_real_change(self, kit: CashKit) -> None:
        assert set_param(kit, "inflation", Decimal("0.03")).changed == (
            "params.inflation",
        )
        again = set_param(kit, "inflation", Decimal("0.03"))
        assert again.empty and codes(again) == {"CK-I002"}

    def test_a_param_key_a_formula_could_not_address_is_refused(
        self, kit: CashKit
    ) -> None:
        report = set_param(kit, "Vat.Standard", Decimal("0.22"))
        assert codes(report) == {"CK-E007"}
        assert "Vat.Standard" not in kit.book.params

    def test_the_reserved_opening_balance_param_is_checked_as_money(
        self, kit: CashKit
    ) -> None:
        report = set_param(kit, "opening_balance", Decimal("1.000005"))
        assert codes(report) == {"CK-E024"}
        assert "opening_balance" not in kit.book.params

    def test_set_cutover_reports_nothing_when_the_day_is_unchanged(
        self, kit: CashKit
    ) -> None:
        assert set_cutover(kit, date(2026, 2, 1)).changed == ("cutover",)
        again = set_cutover(kit, date(2026, 2, 1))
        assert again.empty and codes(again) == {"CK-I002"}

    def test_a_cutover_past_the_horizon_is_recorded_and_warned_about(
        self, kit: CashKit
    ) -> None:
        """Session S5.6: the quietest failure on this surface.

        A cutover past ``horizon.end`` suppresses every generative occurrence
        there is. The book still compiles, the run still succeeds, and every
        number is zero — nothing anywhere says why. ``CK-W006`` is that
        sentence.
        """
        report = set_cutover(kit, date(2026, 5, 1))
        assert report.changed == ("cutover",), "warned, never refused"
        assert codes(report) == {"CK-W006"}
        assert kit.book.cutover == date(2026, 5, 1)

        (warning,) = report.diagnostics
        assert warning.severity == "warning" and warning.field == "cutover"
        assert "2026-04-01" in warning.message, "the horizon it is outside"
        assert "suppressed" in warning.message
        assert warning.suggested_fix

        # And the numbers back the message up: nothing is generated at all.
        add_item(kit, flow("rent", "-3000", direction="out"))
        assert kit.run().summary().net_cash == Decimal(0)

    def test_a_cutover_before_the_horizon_is_warned_about_as_a_no_op(
        self, kit: CashKit
    ) -> None:
        report = set_cutover(kit, date(2025, 12, 1))
        assert report.changed == ("cutover",)
        assert codes(report) == {"CK-W006"}
        assert "no effect" in report.diagnostics[0].message

    @pytest.mark.parametrize("day", [date(2026, 1, 1), date(2026, 2, 1), date(2026, 4, 1)])
    def test_a_cutover_inside_the_horizon_says_nothing_new(
        self, kit: CashKit, day: date
    ) -> None:
        """The horizon is half-open, so its own ``end`` is the last legal day:
        the boundary the model's arithmetic reaches is not a mistake."""
        assert codes(set_cutover(kit, day)) <= {"CK-I002"}

    def test_validate_reports_a_book_already_in_that_state(self, kit: CashKit) -> None:
        """The warning must not depend on having watched the write happen — a
        book opened from disk carries the condition, not the call."""
        set_cutover(kit, date(2026, 6, 1))
        reopened, problems = CashKit.open(kit.root)
        assert reopened is not None and problems == ()
        assert "CK-W006" in {d.code for d in reopened.validate()}

        assert set_cutover(reopened, date(2026, 3, 1)).changed == ("cutover",)
        assert "CK-W006" not in {d.code for d in reopened.validate()}

    def test_a_regime_that_cannot_work_on_its_own_terms_is_refused(
        self, kit: CashKit
    ) -> None:
        report = add_tax_regime(
            kit,
            TaxRegime(
                id="vat",
                accumulates="cat:revenue",
                periodicity="annual",
                payment_offset="16d",
                credit_handling="refund_annual",
            ),
        )
        assert codes(report) == {"CK-E019"}
        assert kit.book.tax_regimes == []

    def test_a_regime_whose_selector_matches_nothing_yet_is_recorded_and_reported(
        self, kit: CashKit
    ) -> None:
        report = add_tax_regime(
            kit,
            TaxRegime(
                id="vat",
                accumulates="cat:revenue",
                periodicity="quarterly",
                payment_offset="16d",
            ),
        )
        assert "CK-E019" in codes(report)
        assert [regime.id for regime in kit.book.tax_regimes] == ["vat"]

        add_item(
            kit, flow("consulting", "9000", direction="in", tags={"cat": "revenue"})
        )
        assert not [d for d in kit.validate() if d.code == "CK-E019"]

    def test_a_regime_is_replaced_rather_than_duplicated(self, kit: CashKit) -> None:
        add_item(
            kit, flow("consulting", "9000", direction="in", tags={"cat": "revenue"})
        )
        regime = TaxRegime(
            id="vat",
            accumulates="cat:revenue",
            periodicity="quarterly",
            payment_offset="16d",
        )
        assert add_tax_regime(kit, regime).created == ("vat",)
        monthly_regime = regime.model_copy(update={"periodicity": "monthly"})
        replaced = add_tax_regime(kit, monthly_regime)
        assert replaced.created == () and replaced.changed == ("tax_regimes.vat",)
        assert [r.periodicity for r in kit.book.tax_regimes] == ["monthly"]


# --------------------------------------------------------------------------- #
# query_events and the ledger writes a bound kit must refuse
# --------------------------------------------------------------------------- #


class TestQueryEvents:
    def test_it_returns_the_prd_table_shape(self, drifting: CashKit) -> None:
        table = query_events(drifting)
        assert table.columns[:4] == ("id", "date", "amount", "status")
        assert len(table) == 2
        assert table.column("amount") == (
            Decimal("-3100.0000"),
            Decimal("-3000.0000"),
        )

    def test_the_date_window_is_inclusive_on_both_ends(self, drifting: CashKit) -> None:
        assert len(query_events(drifting, since=date(2026, 2, 1))) == 1
        assert len(query_events(drifting, until=date(2026, 1, 1))) == 1
        assert len(query_events(drifting, since=date(2026, 3, 1))) == 0

    def test_it_uses_the_one_selector_grammar(self, drifting: CashKit) -> None:
        assert drifting.ledger is not None
        drifting.add_event(
            Event(
                id="tagged",
                date=date(2026, 1, 10),
                amount=Decimal("10.0000"),
                status="forecast",
                tags={"cat": "misc"},
            )
        )
        assert len(query_events(drifting, "cat:misc")) == 1
        assert len(query_events(drifting, "cat:absent")) == 0


class TestARevisionBoundKitRefusesToWrite:
    def test_every_ledger_write_refuses(self, drifting: CashKit) -> None:
        drifting.commit("state")
        past, _ = drifting.at("HEAD")
        assert past is not None
        event = Event(
            id="new", date=date(2026, 1, 9), amount=Decimal("1.0000"), status="forecast"
        )
        for report in (
            past.add_event(event),
            past.import_events([event], source="bank"),
            past.void_event("b1", "note"),
            past.correct_event("b1", event, "note"),
        ):
            assert codes(report) == {"CK-E030"}
        assert len(query_events(drifting)) == 2, "the live ledger is untouched"

    # -- Session S5.6 addendum ------------------------------------------- #
    #
    # S5.5 closed this for the ledger and left the authored book open. The hole
    # was worse than the one it mirrored: `at(ref)` shares the live `root`, so a
    # §6.1 verb on a bound kit mutated the *past* book and saved it over the
    # **present** working tree, while the live in-memory kit went on reporting
    # `status().clean is True`. A write that reads history and lands in the
    # present, invisible from the object that owns the present.

    @pytest.fixture()
    def past(self, kit: CashKit) -> CashKit:
        """A kit bound to a committed revision, over a book with one item."""
        assert add_item(kit, flow("rent", "-3000", direction="out", tags={"cat": "opex"})).ok
        report = kit.commit("one item")
        assert report.revision is not None
        bound, problems = kit.at(report.revision.id)
        assert bound is not None and problems == ()
        return bound

    @pytest.mark.parametrize(
        "verb, call",
        [
            ("add_item", lambda k: add_item(k, flow("new", "1", direction="in"))),
            ("add_derived", lambda k: add_derived(k, "total", 'it("rent")')),
            ("set_param", lambda k: set_param(k, "inflation", Decimal("0.03"))),
            ("retag", lambda k: retag(k, "cat:opex", {"cat": "fixed"})),
            (
                "add_tax_regime",
                lambda k: add_tax_regime(
                    k,
                    TaxRegime(
                        id="iva",
                        accumulates="",
                        periodicity="quarterly",
                        payment_offset="16d",
                    ),
                ),
            ),
            ("set_cutover", lambda k: set_cutover(k, date(2026, 3, 1))),
        ],
    )
    def test_every_authored_write_refuses(
        self, kit: CashKit, past: CashKit, verb: str, call
    ) -> None:
        """The reproduction from the S5.6 handoff, inverted, verb by verb.

        Four assertions, because the defect had four visible faces: the caller
        was told nothing, the file moved, the live kit disagreed with the disk,
        and a reopened kit saw the change.
        """
        book_yaml = kit.root / "book.yaml"
        before = book_yaml.read_bytes()

        report = call(past)
        assert codes(report) == {"CK-E030"}, verb
        recorded = (
            report != 0
            if isinstance(report, AffectedCount)
            else not report.empty
        )
        assert not recorded, f"{verb} recorded something on a read-only kit"

        assert book_yaml.read_bytes() == before, f"{verb} wrote the live book.yaml"
        assert kit.status().clean, f"{verb} dirtied the live tree"
        reopened, problems = CashKit.open(kit.root)
        assert reopened is not None and problems == ()
        assert reopened.status().clean, f"{verb} is visible to a reopened kit"

    def test_the_refusal_comes_before_any_opinion_about_the_argument(
        self, past: CashKit
    ) -> None:
        """A kit that will not record the item has nothing to say about it.

        `add_derived` parses the formula before writing, and an unparseable one
        is normally `CK-E003`. On a bound kit the answer must be `CK-E030` alone
        — reporting a validation verdict would imply the write would otherwise
        have happened.
        """
        assert codes(add_derived(past, "broken", "it(")) == {"CK-E030"}
        assert codes(add_item(past, flow("bad", "3000", direction="out"))) == {
            "CK-E030"
        }

    def test_reads_on_the_bound_kit_still_work(self, past: CashKit) -> None:
        """The guard is on writes. `at(ref)` exists to be read."""
        assert "rent" in past.book.items
        assert past.run().summary().net_cash == Decimal("-9000.0000")
        assert past.describe_book().items

    def test_every_verb_that_saves_is_behind_the_guard(self) -> None:
        """Structural, so it holds for verbs nobody has written yet.

        `__all__` drives the list rather than a hand-kept copy of it: a §6.1 verb
        added later is guarded or this fails. `create_book` is exempt because it
        creates the kit it writes to — there is no revision to be bound to — and
        `resolve_holidays` is a pure function.
        """
        import ast

        from cashkit.sdk import construction

        source = Path(construction.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        bodies = {
            node.name: ast.unparse(node)
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        exempt = {"create_book", "resolve_holidays"}
        verbs = {
            name
            for name in construction.__all__
            if name in bodies and name not in exempt
        }
        assert verbs == {
            "add_derived",
            "add_item",
            "add_tax_regime",
            "retag",
            "set_cutover",
            "set_param",
        }, "the §6.1 verb set moved; check the new one is guarded"
        unguarded = sorted(
            name for name in verbs if "_authored_write" not in bodies[name]
        )
        assert not unguarded, (
            "§6.1 verbs that write the authored book without checking "
            f"_authored_write(): {unguarded}"
        )
