"""Hypothesis strategies generating arbitrary *valid* model instances.

Strings deliberately include unicode, quotes, newlines and control characters
wherever the model permits them, so the canonical emitter's quoting is
exercised hard by the round-trip gate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hypothesis import strategies as st

from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    DueTerm,
    Escalation,
    Event,
    EventOverlay,
    Grain,
    Item,
    ItemOverlay,
    PeriodRange,
    Recurrence,
    Scenario,
    Segment,
    Settlement,
    TaxRegime,
    VatSpec,
    Watermark,
)

# --------------------------------------------------------------------------- #
# Scalars
# --------------------------------------------------------------------------- #

idents = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)
file_idents = st.from_regex(r"[a-z][a-z0-9_-]{0,15}", fullmatch=True)

# Any unicode the codec can encode (no surrogates); exercises emitter quoting.
tricky_text = st.text(
    alphabet=st.characters(codec="utf-8"), min_size=0, max_size=25
)
nonempty_text = st.text(
    alphabet=st.characters(codec="utf-8"), min_size=1, max_size=25
)
# Tag values: no whitespace (unicode-aware), no colon.
tag_values = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=("Z", "C")),
    min_size=1,
    max_size=12,
).filter(lambda s: ":" not in s)

currencies = st.sampled_from(["EUR", "USD", "GBP", "CHF"])

dates = st.dates(min_value=date(1995, 1, 1), max_value=date(2100, 12, 28))

durations = st.builds(
    lambda n, u: f"{n}{u}", st.integers(0, 999), st.sampled_from("dwmy")
)


def _decimals(lo: str, hi: str, max_places: int) -> st.SearchStrategy[Decimal]:
    return st.integers(0, max_places).flatmap(
        lambda places: st.decimals(
            min_value=Decimal(lo), max_value=Decimal(hi), places=places
        )
    )


money = st.one_of(
    _decimals("-9E14", "9E14", 4),
    st.sampled_from(
        [
            Decimal("0"),
            Decimal("-0.0001"),
            Decimal("0.10"),
            Decimal("9E+14"),
            Decimal("-9E+14"),
            Decimal("1E+2"),
        ]
    ),
)

rates = _decimals("-10", "10", 6)
fractions = _decimals("0", "1", 4)
param_values = st.one_of(rates, money)

rate_refs = st.one_of(idents, rates)

# --------------------------------------------------------------------------- #
# Composite models
# --------------------------------------------------------------------------- #

recurrences = st.sampled_from(
    ["period_start", "period_end", "day_of_month", "eom"]
).flatmap(
    lambda anchor: st.builds(
        Recurrence,
        every=st.integers(1, 12),
        unit=st.sampled_from(Grain),
        anchor=st.just(anchor),
        day=st.integers(1, 31) if anchor == "day_of_month" else st.none(),
        business_day_adjust=st.sampled_from(["none", "prev", "next"]),
    )
)

schedules = st.lists(st.tuples(dates, money), min_size=1, max_size=3)

amounts = st.one_of(
    st.builds(Amount, constant=money),
    st.builds(Amount, schedule=schedules),
)

escalations = st.builds(
    Escalation,
    rate=rate_refs,
    every_years=st.integers(1, 5),
    anchor=st.sampled_from(["segment_start", "calendar_year"]),
)


@st.composite
def segments(draw: st.DrawFn) -> Segment:
    start = draw(dates)
    end = draw(
        st.one_of(
            st.none(),
            st.integers(1, 2000).map(
                lambda days: date.fromordinal(start.toordinal() + days)
            ),
        )
    )
    return Segment(
        start=start,
        end=end,
        recurrence=draw(recurrences),
        amount=draw(amounts),
        escalation=draw(st.one_of(st.none(), escalations)),
        probability=draw(fractions),
    )


@st.composite
def due_terms(draw: st.DrawFn) -> DueTerm:
    kind = draw(st.sampled_from(["share", "amount", "remainder"]))
    return DueTerm(
        share=draw(fractions) if kind == "share" else None,
        amount=draw(money) if kind == "amount" else None,
        remainder=kind == "remainder",
        offset=draw(durations),
        basis=draw(st.sampled_from(["accrual", "period_end", "month_end"])),
        adjust=draw(st.sampled_from(["none", "prev", "next"])),
        withholding=draw(fractions),
    )


settlements = st.builds(Settlement, due=st.lists(due_terms(), max_size=3))

vat_specs = st.builds(
    VatSpec,
    rate=st.one_of(idents, fractions),
    treatment=st.sampled_from(
        ["standard", "exempt", "reverse_charge", "out_of_scope", "export", "split_payment"]
    ),
    recoverable=fractions,
)

tags = st.dictionaries(idents, tag_values, max_size=3)
flags = st.sets(idents, max_size=3)


@st.composite
def items(draw: st.DrawFn, item_id: str | None = None) -> Item:
    return Item(
        id=item_id if item_id is not None else draw(idents),
        name=draw(nonempty_text),
        kind=draw(st.sampled_from(["flow", "derived", "stock"])),
        direction=draw(st.sampled_from([None, "in", "out"])),
        tags=draw(tags),
        flags=draw(flags),
        currency=draw(currencies),
        segments=draw(st.lists(segments(), max_size=2)),
        formula=draw(st.one_of(st.none(), tricky_text)),
        settlement=draw(st.one_of(st.none(), settlements)),
        vat=draw(st.one_of(st.none(), vat_specs)),
        agg_rule=draw(st.sampled_from(["sum", "last", "mean"])),
    )


tax_regimes = st.builds(
    TaxRegime,
    id=idents,
    accumulates=tricky_text,
    measure=st.sampled_from(["accrual", "cash"]),
    periodicity=st.sampled_from(["monthly", "quarterly", "annual"]),
    payment_offset=durations,
    surcharge=fractions,
    credit_handling=st.sampled_from(["carry", "refund_annual"]),
    annual_adjustment_month=st.one_of(st.none(), st.integers(1, 12)),
)

calendars = st.builds(
    CalendarSpec,
    fiscal_year_start_month=st.integers(1, 12),
    country=st.one_of(st.none(), st.sampled_from(["IT", "DE", "FR", "US"])),
    holidays=st.lists(dates, max_size=4),
    weekend=st.sets(st.integers(0, 6), max_size=3),
)

watermarks = st.builds(
    Watermark,
    max_rowid=st.integers(0, 10**12),
    row_count=st.integers(0, 10**12),
    content_hash=st.from_regex(r"[0-9a-f]{8,64}", fullmatch=True),
)


@st.composite
def period_ranges(draw: st.DrawFn) -> PeriodRange:
    start = draw(dates)
    length = draw(st.integers(1, 4000))
    return PeriodRange(start=start, end=date.fromordinal(start.toordinal() + length))


@st.composite
def books(draw: st.DrawFn) -> Book:
    item_ids = draw(st.lists(idents, max_size=4, unique=True))
    regimes = draw(st.lists(tax_regimes, max_size=2, unique_by=lambda r: r.id))
    return Book(
        id=draw(file_idents),
        base_grain=draw(st.sampled_from(Grain)),
        calendar=draw(calendars),
        horizon=draw(period_ranges()),
        opening_balance=draw(money),
        cutover=draw(dates),
        ledger_watermark=draw(st.one_of(st.none(), watermarks)),
        params=draw(st.dictionaries(idents, param_values, max_size=4)),
        items={item_id: draw(items(item_id=item_id)) for item_id in item_ids},
        tax_regimes=regimes,
    )


event_ids = st.text(
    alphabet=st.characters(codec="utf-8"), min_size=1, max_size=20
)


@st.composite
def events(draw: st.DrawFn) -> Event:
    source = draw(st.one_of(st.none(), nonempty_text))
    event_id = draw(event_ids)
    # A correcting event (ADR-0012) targets a different event id and must
    # carry a note.
    corrects = draw(
        st.one_of(st.none(), event_ids.filter(lambda other: other != event_id))
    )
    note = (
        draw(nonempty_text.filter(lambda s: bool(s.strip())))
        if corrects is not None
        else draw(st.one_of(st.none(), tricky_text))
    )
    return Event(
        id=event_id,
        date=draw(dates),
        amount=draw(money),
        status=draw(st.sampled_from(["actual", "committed", "forecast"])),
        item=draw(st.one_of(st.none(), idents)),
        tags=draw(tags),
        vat=draw(st.one_of(st.none(), vat_specs)),
        settlement=draw(st.one_of(st.none(), settlements)),
        currency=draw(currencies),
        source=source,
        ext_id=draw(st.one_of(st.none(), nonempty_text)) if source else None,
        note=note,
        corrects=corrects,
    )


_ITEM_OVERLAY_FIELDS: dict[str, st.SearchStrategy[object]] = {
    "name": nonempty_text,
    "kind": st.sampled_from(["flow", "derived", "stock"]),
    "direction": st.sampled_from([None, "in", "out"]),
    "tags": tags,
    "flags": flags,
    "currency": currencies,
    "segments": st.lists(segments(), max_size=2),
    "formula": st.one_of(st.none(), tricky_text),
    "settlement": st.one_of(st.none(), settlements),
    "vat": st.one_of(st.none(), vat_specs),
    "agg_rule": st.sampled_from(["sum", "last", "mean"]),
}


@st.composite
def item_overlays(draw: st.DrawFn) -> ItemOverlay:
    recorded = draw(
        st.lists(st.sampled_from(sorted(_ITEM_OVERLAY_FIELDS)), unique=True, max_size=6)
    )
    return ItemOverlay(
        **{name: draw(_ITEM_OVERLAY_FIELDS[name]) for name in recorded}
    )


_EVENT_OVERLAY_FIELDS: dict[str, st.SearchStrategy[object]] = {
    "date": dates,
    "amount": money,
    "status": st.sampled_from(["committed", "forecast"]),
    "item": st.one_of(st.none(), idents),
    "tags": tags,
    "vat": st.one_of(st.none(), vat_specs),
    "settlement": st.one_of(st.none(), settlements),
    "currency": currencies,
    "note": st.one_of(st.none(), tricky_text),
}


@st.composite
def event_overlays(draw: st.DrawFn) -> EventOverlay:
    recorded = draw(
        st.lists(st.sampled_from(sorted(_EVENT_OVERLAY_FIELDS)), unique=True, max_size=5)
    )
    return EventOverlay(
        **{name: draw(_EVENT_OVERLAY_FIELDS[name]) for name in recorded}
    )


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    scenario_id = draw(file_idents)
    overlay_ids = draw(st.lists(idents, max_size=3, unique=True))
    added_ids = draw(
        st.lists(idents, max_size=2, unique=True).filter(
            lambda ids: not set(ids) & set(overlay_ids)
        )
    )
    return Scenario(
        id=scenario_id,
        parent=draw(
            st.one_of(st.none(), file_idents.filter(lambda s: s != scenario_id))
        ),
        note=draw(tricky_text),
        params=draw(st.dictionaries(idents, param_values, max_size=3)),
        items={item_id: draw(item_overlays()) for item_id in overlay_ids},
        added={item_id: draw(items(item_id=item_id)) for item_id in added_ids},
        removed=draw(st.sets(idents, max_size=3)),
        event_overrides=draw(
            st.dictionaries(event_ids, event_overlays(), max_size=2)
        ),
    )
