"""Structural model invariants (Phase 1).

Business-rule validation (share sums, sign vs direction, generative stocks…)
belongs to the SDK as §10.1 diagnostics in later sessions; what is tested here
is the structural layer that makes those models well-formed at all.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    Diagnostic,
    DueTerm,
    Escalation,
    Event,
    Item,
    PeriodRange,
    Recurrence,
    Grain,
    Scenario,
    Segment,
    Settlement,
    VatSpec,
    Watermark,
    make_diagnostic,
)


def _cal() -> CalendarSpec:
    return CalendarSpec()


def _horizon() -> PeriodRange:
    return PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1))


class TestMoneyBoundary:
    def test_rejects_more_than_4dp(self) -> None:
        with pytest.raises(ValidationError, match="4 decimal places"):
            Event(id="e", date=date(2026, 1, 1), amount=Decimal("1.00001"), status="actual")

    def test_rejects_nan_and_infinity(self) -> None:
        for bad in ("NaN", "Infinity", "-Infinity"):
            with pytest.raises(ValidationError):
                Event(id="e", date=date(2026, 1, 1), amount=Decimal(bad), status="actual")

    def test_rejects_beyond_engine_ceiling(self) -> None:
        with pytest.raises(ValidationError, match="ceiling"):
            Event(
                id="e",
                date=date(2026, 1, 1),
                amount=Decimal("9.0001E14"),
                status="actual",
            )

    def test_accepts_exactly_the_ceiling_and_4dp(self) -> None:
        Event(id="e", date=date(2026, 1, 1), amount=Decimal("9E14"), status="actual")
        Event(id="e", date=date(2026, 1, 1), amount=Decimal("-0.0001"), status="actual")

    def test_float_input_is_rejected(self) -> None:
        """Money fields never accept float — silent binary-fraction error."""
        with pytest.raises(ValidationError):
            Event.model_validate(
                {"id": "e", "date": date(2026, 1, 1), "amount": 10.1, "status": "actual"}
            )


class TestAmount:
    def test_exactly_one_of_constant_or_schedule(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            Amount()
        with pytest.raises(ValidationError, match="exactly one"):
            Amount(
                constant=Decimal("1"),
                schedule=[(date(2026, 1, 1), Decimal("1"))],
            )

    def test_empty_schedule_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not be empty"):
            Amount(schedule=[])


class TestRecurrence:
    def test_required_on_segment(self) -> None:
        """Recurrence is non-optional by design: one-offs are Events."""
        assert Segment.model_fields["recurrence"].is_required()
        with pytest.raises(ValidationError):
            Segment.model_validate(
                {"start": date(2026, 1, 1), "amount": {"constant": "1"}}
            )

    def test_day_iff_day_of_month(self) -> None:
        with pytest.raises(ValidationError):
            Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month")
        with pytest.raises(ValidationError):
            Recurrence(every=1, unit=Grain.MONTH, anchor="eom", day=15)
        Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=31)

    def test_day_and_every_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Recurrence(every=0, unit=Grain.MONTH)
        with pytest.raises(ValidationError):
            Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=32)


class TestDueTerm:
    def test_exactly_one_kind(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            DueTerm(offset="0d")
        with pytest.raises(ValidationError, match="exactly one"):
            DueTerm(share=Decimal("0.5"), amount=Decimal("100"), offset="0d")
        with pytest.raises(ValidationError, match="exactly one"):
            DueTerm(share=Decimal("0.5"), remainder=True, offset="0d")

    def test_duration_grammar(self) -> None:
        for bad in ("30", "d30", "-1d", "1.5m", "01d", "30 d"):
            with pytest.raises(ValidationError):
                DueTerm(share=Decimal(1), offset=bad)
        for good in ("0d", "30d", "2m", "1y", "12w"):
            DueTerm(share=Decimal(1), offset=good)

    def test_settlement_constructors(self) -> None:
        assert Settlement.immediate().due[0].offset == "0d"
        assert Settlement.net(60).due[0].offset == "60d"
        legs = Settlement.split([(Decimal("0.3"), "0d"), (Decimal("0.7"), "90d")]).due
        assert [t.share for t in legs] == [Decimal("0.3"), Decimal("0.7")]


class TestIdentifiers:
    def test_param_keys_reject_dots(self) -> None:
        with pytest.raises(ValidationError):
            Book(
                id="b",
                calendar=_cal(),
                horizon=_horizon(),
                opening_balance=Decimal("0"),
                cutover=date(2026, 1, 1),
                params={"vat.standard": Decimal("0.22")},
            )

    def test_item_key_must_match_item_id(self) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            Book(
                id="b",
                calendar=_cal(),
                horizon=_horizon(),
                opening_balance=Decimal("0"),
                cutover=date(2026, 1, 1),
                items={"other": Item(id="rent", name="x", kind="flow")},
            )

    def test_tag_values_reject_whitespace_and_colon(self) -> None:
        for bad in ("a b", "a:b", "a\tb", "a\nb", ""):
            with pytest.raises(ValidationError):
                Item(id="x", name="x", kind="flow", tags={"k": bad})

    def test_currency_code_shape(self) -> None:
        with pytest.raises(ValidationError):
            Item(id="x", name="x", kind="flow", currency="eur")

    def test_rate_string_is_key_or_literal(self) -> None:
        assert VatSpec(rate="vat_standard").rate == "vat_standard"
        assert VatSpec.model_validate({"rate": "0.22"}).rate == Decimal("0.22")
        assert Escalation.model_validate({"rate": "0.05"}).rate == Decimal("0.05")
        with pytest.raises(ValidationError):
            VatSpec(rate="Not A Key")


class TestStructural:
    def test_period_range_half_open(self) -> None:
        with pytest.raises(ValidationError, match="start < end"):
            PeriodRange(start=date(2026, 1, 1), end=date(2026, 1, 1))

    def test_segment_end_after_start(self) -> None:
        with pytest.raises(ValidationError, match="end > start"):
            Segment(
                start=date(2026, 1, 1),
                end=date(2026, 1, 1),
                recurrence=Recurrence(every=1, unit=Grain.MONTH),
                amount=Amount(constant=Decimal("1")),
            )

    def test_ext_id_requires_source(self) -> None:
        with pytest.raises(ValidationError, match="ext_id requires source"):
            Event(
                id="e",
                date=date(2026, 1, 1),
                amount=Decimal("1"),
                status="actual",
                ext_id="INV-1",
            )

    def test_correction_cannot_target_itself(self) -> None:
        """ADR-0012: corrects != id — no self-correction."""
        with pytest.raises(ValidationError, match="correct itself"):
            Event(
                id="e1",
                date=date(2026, 1, 1),
                amount=Decimal("1"),
                status="actual",
                corrects="e1",
                note="typo",
            )

    def test_correction_requires_nonempty_note(self) -> None:
        """ADR-0012: a correction without a stated reason is not auditable."""
        for bad_note in (None, "", "   "):
            with pytest.raises(ValidationError, match="non-empty note"):
                Event(
                    id="e2",
                    date=date(2026, 1, 1),
                    amount=Decimal("1"),
                    status="actual",
                    corrects="e1",
                    note=bad_note,
                )
        Event(
            id="e2",
            date=date(2026, 1, 1),
            amount=Decimal("-100.00"),
            status="actual",
            corrects="e1",
            note="bank feed recorded the wrong sign",
        )

    def test_events_are_immutable(self) -> None:
        event = Event(id="e", date=date(2026, 1, 1), amount=Decimal("1"), status="actual")
        with pytest.raises(ValidationError):
            event.amount = Decimal("2")  # type: ignore[misc]

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Watermark.model_validate(
                {"max_rowid": 1, "row_count": 1, "content_hash": "ab", "extra": 1}
            )

    def test_scenario_own_parent_rejected(self) -> None:
        with pytest.raises(ValidationError, match="own parent"):
            Scenario(id="s", parent="s")

    def test_scenario_added_and_overlay_disjoint(self) -> None:
        from cashkit.model import ItemOverlay

        with pytest.raises(ValidationError, match="both"):
            Scenario(
                id="s",
                items={"x": ItemOverlay(name="n")},
                added={"x": Item(id="x", name="n", kind="flow")},
            )

    def test_base_is_not_special(self) -> None:
        """Base is just a scenario with parent=None — same type, same fields."""
        base = Scenario(id="base")
        downside = Scenario(id="downside", parent="base")
        assert type(base) is type(downside)
        assert base.parent is None


class TestDiagnosticSubject:
    """A diagnostic must be able to name the items the engine synthesizes.

    ``_tax:<regime>:liability`` (ADR-0005) and the carriers holding unattached
    ledger events sit outside the authored ``ItemId`` grammar on purpose, so
    that no book can collide with them. A ``Diagnostic`` that refused to name
    one would raise out of the engine on book content — precisely what the
    errors-are-data policy forbids (DECISIONS D-P6-08).
    """

    def test_synthetic_ids_are_nameable(self) -> None:
        for item_id in (
            "_tax:vat:liability",
            "_tax:vat:credit",
            "_event:0f1e2d3c4b5a6978",
            "ordinary_item",
        ):
            assert make_diagnostic("CK-W004", item_id=item_id).item_id == item_id

    def test_garbage_ids_are_still_refused(self) -> None:
        for item_id in ("Has Spaces", "UPPER", "_no_colon", "trailing:", "9leading"):
            with pytest.raises(ValidationError):
                Diagnostic(
                    severity="error",
                    code="CK-E001",
                    item_id=item_id,
                    message="m",
                    suggested_fix="f",
                )
