"""Phase 10 — ``validate()`` over the §10.1 catalogue.

Two things are asserted here that no individual code test can give:

* **The catalogue is partitioned.** Every code is either something
  ``validate()`` can emit, something an *operation* emits, or something the
  model layer rejects structurally. A code in none of the three is unreachable —
  a promise nothing keeps — and this test fails when one appears.
* **``validate()`` agrees with the engine.** It runs the engine rather than
  re-deriving compile-time diagnostics, so a validator that said a formula was
  fine while the run refused it is not a possible state.

CashKit ships **no content-bearing diagnostics** (ADR-0021, superseding
ADR-0020): the catalogue describes model and engine conditions, never
jurisdiction mechanics. An enumerated tax checklist is application domain, and
a test here guards that the catalogue stays free of one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from gate_book import build_gate_book

from cashkit.engine import Engine
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
    TaxRegime,
    VatSpec,
)
from cashkit.model.diagnostics import CATALOGUE
from cashkit.sdk import CashKit, validate
from cashkit.sdk.validation import CONSTRUCTION_TIME_CODES, OPERATION_TIME_CODES

#: Codes ``validate()`` itself can produce, each proved reachable below or by
#: the phase that minted it.
VALIDATE_TIME_CODES = {
    "CK-E001",
    "CK-E002",
    "CK-E003",
    "CK-E004",
    "CK-E005",
    "CK-E008",
    "CK-E011",
    "CK-E012",
    "CK-E018",
    "CK-E019",
    "CK-E020",
    "CK-W001",
    "CK-W002",
    "CK-W003",
    "CK-W004",
    "CK-W005",
    "CK-I001",
}


def _book(**overrides) -> Book:
    base = dict(
        id="validate-book",
        calendar=CalendarSpec(),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1)),
        opening_balance=Decimal(0),
        cutover=date(2026, 1, 1),
        items={},
    )
    base.update(overrides)
    return Book(**base)


def _monthly() -> Recurrence:
    return Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=1)


def _flow(item_id: str, amount: str, **overrides) -> Item:
    fields = dict(
        id=item_id,
        name=item_id,
        kind="flow",
        segments=[
            Segment(
                start=date(2026, 1, 1),
                recurrence=_monthly(),
                amount=Amount(constant=Decimal(amount)),
            )
        ],
    )
    fields.update(overrides)
    return Item(**fields)


def _codes(diagnostics) -> set[str]:
    return {d.code for d in diagnostics}


class TestCataloguePartition:
    def test_every_catalogue_code_is_reachable_from_somewhere(self) -> None:
        """A diagnostic nothing emits is a promise nothing keeps."""
        classified = (
            VALIDATE_TIME_CODES | set(OPERATION_TIME_CODES) | set(CONSTRUCTION_TIME_CODES)
        )
        assert set(CATALOGUE) == classified, {
            "unclassified": sorted(set(CATALOGUE) - classified),
            "classified but absent": sorted(classified - set(CATALOGUE)),
        }

    def test_the_three_sets_do_not_overlap(self) -> None:
        assert not (VALIDATE_TIME_CODES & set(OPERATION_TIME_CODES))
        assert not (VALIDATE_TIME_CODES & set(CONSTRUCTION_TIME_CODES))
        assert not (set(OPERATION_TIME_CODES) & set(CONSTRUCTION_TIME_CODES))

    def test_every_operation_time_code_names_where_it_comes_from(self) -> None:
        """The classification is only useful if it says where to look."""
        for code, origin in OPERATION_TIME_CODES.items():
            assert len(origin.strip()) > 20, (
                f"{code} must say where it comes from, not merely that it exists: "
                f"{origin!r}"
            )

    def test_the_catalogue_carries_no_jurisdiction_content(self) -> None:
        """ADR-0021: CashKit is a calculation engine.

        A code enumerating a country's tax mechanics is application domain, not
        engine domain — the engine cannot keep such a list correct and should
        not imply that it does. CK-W004 and CK-I001 stay: they are statements
        about the *model* (a withholding term with no counter-leg item; a tax
        regime with no non-VAT tax item), not about any jurisdiction's rules.
        """
        banned = (
            "ires",
            "irap",
            "inps",
            "inail",
            "tfr",
            "acconto",
            "ravvedimento",
            "transizione",
        )
        for code, spec in CATALOGUE.items():
            text = f"{spec.message} {spec.suggested_fix}".lower()
            for word in banned:
                assert word not in text, f"{code} names a jurisdiction mechanic: {word}"


class TestValidateAgreesWithTheEngine:
    def test_a_well_formed_book_produces_no_errors(self) -> None:
        """The gate book is deliberately full of edge cases, so it carries
        warnings (a clamped remainder, a masked division, a withholding term with
        no counter-leg item). None of them is an error."""
        diagnostics = validate(build_gate_book())
        assert [d for d in diagnostics if d.severity == "error"] == []
        assert _codes(diagnostics) <= {"CK-W001", "CK-W002", "CK-W004", "CK-W005"}

    def test_validate_reports_everything_the_run_reports(self) -> None:
        book = _book(
            items={
                "broken": Item(
                    id="broken",
                    name="Broken formula",
                    kind="derived",
                    formula='it("does_not_exist")',
                ),
                "ok": _flow("ok", "100.00"),
            }
        )
        from_run = {d.code for d in Engine(book).run().diagnostics}
        from_validate = _codes(validate(book))
        assert from_run <= from_validate
        assert "CK-E001" in from_validate

    def test_the_order_is_stable_and_errors_come_first(self) -> None:
        book = _book(
            items={
                "wrong_sign": _flow("wrong_sign", "500.00", direction="out"),
                "clamped": _flow(
                    "clamped",
                    "100.00",
                    settlement=Settlement(
                        due=[
                            DueTerm(amount=Decimal("1000.00"), offset="0d"),
                            DueTerm(remainder=True, offset="30d"),
                        ]
                    ),
                ),
            }
        )
        first = validate(book)
        assert first == validate(book)
        severities = [d.severity for d in first]
        assert severities == sorted(
            severities, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s]
        )


class TestAuthoringChecksTheEngineDoesNotMake:
    def test_a_positive_amount_on_an_out_item_is_an_error(self) -> None:
        """The engine happily evaluates it — and silently produces an inflow."""
        book = _book(items={"rent": _flow("rent", "4000.00", direction="out")})
        diagnostics = validate(book)
        found = next(d for d in diagnostics if d.code == "CK-E011")
        assert found.item_id == "rent"
        assert found.field == "segments[0].amount.constant"
        assert not any(d.code == "CK-E011" for d in Engine(book).run().diagnostics)

    def test_the_correct_sign_produces_nothing(self) -> None:
        book = _book(items={"rent": _flow("rent", "-4000.00", direction="out")})
        assert "CK-E011" not in _codes(validate(book))

    def test_zero_never_conflicts_with_a_direction(self) -> None:
        book = _book(items={"idle": _flow("idle", "0.00", direction="out")})
        assert "CK-E011" not in _codes(validate(book))

    def test_a_schedule_entry_with_the_wrong_sign_is_caught_too(self) -> None:
        book = _book(
            items={
                "mixed": Item(
                    id="mixed",
                    name="Mixed schedule",
                    kind="flow",
                    direction="in",
                    segments=[
                        Segment(
                            start=date(2026, 1, 1),
                            recurrence=_monthly(),
                            amount=Amount(
                                schedule=[
                                    (date(2026, 1, 1), Decimal("100.00")),
                                    (date(2026, 2, 1), Decimal("-50.00")),
                                ]
                            ),
                        )
                    ],
                )
            }
        )
        found = next(d for d in validate(book) if d.code == "CK-E011")
        assert found.field == "segments[0].amount.schedule[1]"

    def test_a_generative_stock_is_ck_e012_and_not_also_ck_e003(self) -> None:
        """One modelling mistake, one code: two codes reads as two mistakes."""
        book = _book(
            items={
                "inventory": Item(
                    id="inventory",
                    name="Generative stock",
                    kind="stock",
                    segments=[
                        Segment(
                            start=date(2026, 1, 1),
                            recurrence=_monthly(),
                            amount=Amount(constant=Decimal("10.00")),
                        )
                    ],
                )
            }
        )
        diagnostics = validate(book)
        assert "CK-E012" in _codes(diagnostics)
        segment_errors = [
            d for d in diagnostics if d.code == "CK-E003" and d.field == "segments"
        ]
        assert segment_errors == []
        # The engine still reports its own view; validate() is where the dedup
        # lives, so the engine's contract is untouched.
        assert any(
            d.code == "CK-E003" and d.field == "segments"
            for d in Engine(book).run().diagnostics
        )


class TestValidateAgainstTheLedger:
    def test_an_actual_after_cutover_is_ck_w003(self, tmp_path: Path) -> None:
        book = _book(
            cutover=date(2026, 3, 1), items={"sales": _flow("sales", "100.00")}
        )
        kit = CashKit.init(tmp_path / "book", book)
        assert kit.ledger is not None
        kit.ledger.add_event(
            Event(
                id="late",
                date=date(2026, 4, 1),
                amount=Decimal("500.00"),
                status="actual",
                item="sales",
            )
        )
        assert "CK-W003" in _codes(kit.validate("base"))
        # Without the ledger the condition is invisible, which is why validate()
        # takes events at all.
        assert "CK-W003" not in _codes(validate(book))

    def test_an_event_on_a_derived_item_is_ck_e018(self, tmp_path: Path) -> None:
        book = _book(
            items={
                "sales": _flow("sales", "100.00"),
                "commission": Item(
                    id="commission",
                    name="Commission",
                    kind="derived",
                    formula='it("sales") * 0.1',
                ),
            }
        )
        kit = CashKit.init(tmp_path / "book", book)
        assert kit.ledger is not None
        kit.ledger.add_event(
            Event(
                id="e1",
                date=date(2026, 2, 1),
                amount=Decimal("10.00"),
                status="actual",
                item="commission",
            )
        )
        assert "CK-E018" in _codes(kit.validate("base"))


class TestTaxDiagnosticsSurvive:
    def test_withholding_with_no_tax_item_is_ck_w004(self) -> None:
        book = _book(
            items={
                "consultant": _flow(
                    "consultant",
                    "-1000.00",
                    direction="out",
                    settlement=Settlement(
                        due=[
                            DueTerm(
                                share=Decimal(1),
                                offset="30d",
                                withholding=Decimal("0.20"),
                            )
                        ]
                    ),
                )
            }
        )
        assert "CK-W004" in _codes(validate(book))

    def test_a_regime_with_no_non_vat_tax_item_is_ck_i001(self) -> None:
        book = _book(
            items={
                "sales": _flow(
                    "sales", "1000.00", direction="in", vat=VatSpec(rate=Decimal("0.22"))
                )
            },
            tax_regimes=[
                TaxRegime(
                    id="iva",
                    accumulates="",
                    periodicity="quarterly",
                    payment_offset="16d",
                )
            ],
        )
        assert "CK-I001" in _codes(validate(book))

    def test_a_cat_tax_item_silences_it(self) -> None:
        book = _book(
            items={
                "sales": _flow(
                    "sales", "1000.00", direction="in", vat=VatSpec(rate=Decimal("0.22"))
                ),
                "ires": _flow(
                    "ires",
                    "-5000.00",
                    direction="out",
                    tags={"cat": "tax"},
                    flags={"manual_tax"},
                ),
            },
            tax_regimes=[
                TaxRegime(
                    id="iva",
                    accumulates="",
                    periodicity="quarterly",
                    payment_offset="16d",
                )
            ],
        )
        assert "CK-I001" not in _codes(validate(book))
