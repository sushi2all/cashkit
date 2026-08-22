"""The twelve read intents, executed with no model call (ADR-0019 rule 1)."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest
from cashkit.sdk import CashKit

from cashkit_service.intents.read import READ_INTENTS, UnknownIntent, execute
from cashkit_service.money import money
from cashkit_service.ops.schema import READ_INTENTS as SCHEMA_READ_INTENTS

AS_OF = _dt.date(2026, 3, 17)


@pytest.fixture
def kit(seeded_client, books_root):
    # `seeded_client` first: the directory does not exist until the book does.
    book_dir = next(p for p in books_root.iterdir() if p.is_dir())
    kit, diagnostics = CashKit.open(book_dir)
    assert kit is not None, diagnostics
    try:
        yield kit
    finally:
        if kit.ledger is not None:
            kit.ledger.close()


def run(kit, intent):
    return execute(kit, intent, scenario="base", as_of=AS_OF)


def test_all_twelve_read_intents_are_executable():
    assert len(READ_INTENTS) == 12
    assert set(READ_INTENTS) == set(SCHEMA_READ_INTENTS)


def test_an_unknown_intent_is_a_programmer_error(kit):
    with pytest.raises(UnknownIntent):
        run(kit, {"intent": "delete_the_book"})


def test_r1_project_balance_without_a_delta(kit):
    payload = run(kit, {"intent": "project_balance"})
    assert payload["hypothetical"] is False
    assert payload["summary"]["closing_balance"] == money(kit.run("base").summary().closing_balance).model_dump()


def test_r1_a_hypothetical_delta_never_touches_the_book(kit):
    before = kit.run("base").summary().closing_balance
    payload = run(kit, {"intent": "project_balance", "delta": "-1500.00", "delta_date": "2026-09-01"})
    assert payload["hypothetical"] is True
    after_hypothetical = Decimal(payload["summary"]["closing_balance"]["exact"])
    assert after_hypothetical == before - Decimal("1500.00")
    # The real book is unchanged: a question never writes (ADR-0029).
    assert kit.run("base").summary().closing_balance == before


def test_r2_runway_reports_absent_as_none_not_zero(kit):
    payload = run(kit, {"intent": "runway"})
    summary = kit.run("base").summary()
    assert payload["runway_periods"] == summary.runway_periods
    assert payload["runway_end"] == (summary.runway_end.isoformat() if summary.runway_end else None)


def test_r3_min_cash_matches_the_engine(kit):
    payload = run(kit, {"intent": "min_cash"})
    summary = kit.run("base").summary()
    assert payload["min_cash"] == money(summary.min_cash).model_dump()
    assert payload["min_cash_period"] == summary.min_cash_period.isoformat()


def test_r3_min_cash_can_be_bounded_by_a_horizon(kit):
    early = run(kit, {"intent": "min_cash", "horizon": "2026-05-01"})
    whole = run(kit, {"intent": "min_cash"})
    assert Decimal(early["min_cash"]["exact"]) > Decimal(whole["min_cash"]["exact"])


def test_r4_breakeven(kit):
    payload = run(kit, {"intent": "breakeven"})
    assert "breakeven_period" in payload


def test_r5_top_categories_is_host_composed_from_engine_columns(kit):
    payload = run(kit, {"intent": "top_categories", "direction": "out", "n": 5})
    categories = {c["category"]: Decimal(c["total"]["exact"]) for c in payload["categories"]}
    assert "housing" in categories

    run_ref = kit.run("base")
    expected = sum(
        int(v)
        for item in run_ref.book.items.values()
        if item.tags.get("cat") == "housing"
        for v in run_ref.result.cash[item.id]
        if int(v) < 0
    )
    assert categories["housing"] == Decimal(expected) / 10000


def test_r5_ranks_by_size(kit):
    payload = run(kit, {"intent": "top_categories", "direction": "out"})
    totals = [abs(Decimal(c["total"]["exact"])) for c in payload["categories"]]
    assert totals == sorted(totals, reverse=True)


def test_r6_item_total_for_one_item(kit):
    payload = run(kit, {"intent": "item_total", "item": "rent", "measure": "cash"})
    expected = sum(int(v) for v in kit.run("base").result.cash["rent"])
    assert payload["total"] == money(Decimal(expected) / 10000).model_dump()
    assert payload["items"] == ["rent"]


def test_r6_item_total_over_a_tag_selector(kit):
    payload = run(kit, {"intent": "item_total", "item": "cat:housing"})
    assert set(payload["items"]) == {"rent", "insurance"}


def test_r6_item_total_over_a_window(kit):
    whole = run(kit, {"intent": "item_total", "item": "rent"})
    part = run(kit, {"intent": "item_total", "item": "rent",
                     "period": {"since": "2026-01-01", "until": "2026-03-01"}})
    assert abs(Decimal(part["total"]["exact"])) < abs(Decimal(whole["total"]["exact"]))


def test_r7_explain_cell(kit):
    payload = run(kit, {"intent": "explain_cell", "item": "rent", "period": "2026-04-01"})
    expected = kit.run("base").trace("rent", _dt.date(2026, 4, 1))
    assert payload["trace"]["value"] == money(expected.value).model_dump()


def test_r8_explain_zero(kit):
    payload = run(kit, {"intent": "explain_zero", "item": "insurance", "period": "2026-05-01"})
    expected = kit.run("base").why_zero("insurance", _dt.date(2026, 5, 1))
    assert payload["explanation"]["cause"] == expected.cause
    assert payload["explanation"]["message"] == expected.message


def test_r9_compare_scenarios_keeps_absent_apart_from_zero(kit):
    payload = run(kit, {"intent": "compare_scenarios", "scenarios": ["base", "downside"]})
    assert payload["scenarios"] == ["base", "downside"]
    assert payload["periods"]
    for period in payload["periods"]:
        for value in period["values"].values():
            assert value is None or set(value) == {"exact", "display"}


def test_r10_coverage_renders_validate_verbatim(kit):
    payload = run(kit, {"intent": "coverage"})
    expected = kit.validate("base")
    assert [d["code"] for d in payload["diagnostics"]] == [d.code for d in expected]
    for got, want in zip(payload["diagnostics"], expected, strict=True):
        assert got["message"] == want.message
        assert got["suggested_fix"] == want.suggested_fix


def test_r11_list_items(kit):
    payload = run(kit, {"intent": "list_items"})
    assert [i["id"] for i in payload["items"]] == ["insurance", "rent", "salary"]


def test_r11_list_items_by_tag(kit):
    payload = run(kit, {"intent": "list_items", "tag": "cat:housing"})
    assert [i["id"] for i in payload["items"]] == ["insurance", "rent"]


def test_r12_history(kit):
    payload = run(kit, {"intent": "history", "n": 10})
    assert [r["id"] for r in payload["revisions"]] == [r.id for r in kit.history(limit=10)]


@pytest.mark.parametrize("name", READ_INTENTS)
def test_no_read_intent_changes_the_book(kit, name):
    """ADR-0029, structurally: the read set cannot write."""
    before = kit.status().model_dump()
    arguments = {
        "explain_cell": {"item": "rent", "period": "2026-04-01"},
        "explain_zero": {"item": "insurance", "period": "2026-05-01"},
        "compare_scenarios": {"scenarios": ["base", "downside"]},
        "item_total": {"item": "rent"},
        "project_balance": {"delta": "-500.00"},
    }.get(name, {})
    execute(kit, {"intent": name, **arguments}, scenario="base", as_of=AS_OF)
    assert kit.status().model_dump() == before
