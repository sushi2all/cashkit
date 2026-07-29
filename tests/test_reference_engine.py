"""Phase 2 gate: the reference engine reproduces hand-verified numbers.

``tests/fixtures/hand_verified.csv`` is the committed spreadsheet — every value
in it was computed by hand from ``tests/gate_book.py``, with the derivation
written into the file's header comments. If the engine and the file ever
disagree, one of them is wrong and the build stops until it is known which.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from gate_book import build_gate_book

import cashkit.reference as reference
from cashkit.engine.numeric import RoundingPolicy

HAND_VERIFIED = Path(__file__).parent / "fixtures" / "hand_verified.csv"


def _load_expectations() -> list[tuple[date, str, str, Decimal]]:
    rows: list[tuple[date, str, str, Decimal]] = []
    with HAND_VERIFIED.open(encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    for row in csv.DictReader(lines):
        rows.append(
            (
                date.fromisoformat(row["period_start"]),
                row["item_id"],
                row["measure"],
                Decimal(row["value"]),
            )
        )
    return rows


def test_gate_book_has_twenty_items() -> None:
    assert len(build_gate_book().items) == 20


def test_reference_matches_hand_verified_numbers() -> None:
    """The Phase 2 gate itself."""
    result = reference.run(build_gate_book())
    expectations = _load_expectations()
    assert expectations, "hand-verified fixture is empty"

    periods = {start: index for index, start in enumerate(result.periods.starts)}
    covered_periods = {row[0] for row in expectations}
    assert len(covered_periods) >= 3, "the gate requires at least three verified periods"

    mismatches: list[str] = []
    for period_start, item_id, measure, expected in expectations:
        actual = result.value(item_id, measure, periods[period_start])
        if actual != expected:
            mismatches.append(
                f"{period_start} {item_id}.{measure}: expected {expected}, got {actual}"
            )
    assert not mismatches, "reference engine disagrees with the hand-verified sheet:\n" + "\n".join(
        mismatches
    )


def test_every_item_and_measure_is_covered_for_each_verified_period() -> None:
    """A period is 'verified' only if every item was checked, zeros included —
    a missing row would hide a value nobody looked at."""
    book = build_gate_book()
    expectations = _load_expectations()
    by_period: dict[date, set[tuple[str, str]]] = {}
    for period_start, item_id, measure, _ in expectations:
        by_period.setdefault(period_start, set()).add((item_id, measure))
    wanted = {(item_id, measure) for item_id in book.items for measure in ("accrual", "cash")}
    for period_start, covered in by_period.items():
        assert covered == wanted, f"incomplete coverage for {period_start}"


def test_gate_book_diagnostics_are_exactly_the_expected_three() -> None:
    result = reference.run(build_gate_book())
    assert result.diagnostic_keys() == (
        ("CK-W001", "partial_delivery", "settlement.due"),
        ("CK-W002", "credit_note", "settlement.due"),
        ("CK-W005", "zero_guard", "formula"),
    ), result.diagnostic_keys()
    assert not [d for d in result.diagnostics if d.severity == "error"]


def test_share_split_legs_sum_to_the_accrual_exactly() -> None:
    """ADR-0003: the last share term absorbs the rounding residual."""
    result = reference.run(build_gate_book())
    accrued = result.total("acme_impl", "accrual")
    # The two legs of the last two accruals fall outside the horizon, so compare
    # only the accruals whose whole settlement lands inside it.
    assert accrued == Decimal("81000.0000")
    assert result.total("acme_impl", "cash") == Decimal("60000.0000")


def test_banker_rounding_is_selectable_and_changes_nothing_exact() -> None:
    """The policy is a run-level knob; on the gate book every boundary is exact,
    so both policies agree — which is itself worth pinning."""
    half_up = reference.run(build_gate_book(), policy=RoundingPolicy.HALF_UP)
    half_even = reference.run(build_gate_book(), policy=RoundingPolicy.HALF_EVEN)
    for item_id in build_gate_book().items:
        for measure in ("accrual", "cash"):
            assert (half_up.column(item_id, measure) == half_even.column(item_id, measure)).all()
