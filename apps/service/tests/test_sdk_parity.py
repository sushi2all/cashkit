"""SDK parity — the S1 gate's central claim.

    every money figure and diagnostic in an endpoint payload string-equal to
    the canonically serialized value from the direct SDK call on the same
    book/revision/as_of (envelope fields excluded)

"Direct SDK call" means exactly that: these tests open their **own** kit on the
same book directory and ask the SDK themselves, then compare against what the
endpoint returned. Nothing here re-reads a number the service computed and
compares it to itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from cashkit.sdk import CashKit, balance_series

from cashkit_service.middleware import find_money_paths
from cashkit_service.money import display_str, exact_str, from_minor_units, money

AS_OF = date(2026, 3, 17)  # the frozen clock; the same as_of the service used


@pytest.fixture
def sdk(book_dir):
    """A private kit on the same book, closed afterwards."""
    kit, diagnostics = CashKit.open(book_dir)
    assert kit is not None, diagnostics
    try:
        yield kit
    finally:
        if kit.ledger is not None:
            kit.ledger.close()


def m(value) -> dict:
    """The canonical serialization, as a plain dict for comparison."""
    return money(value).model_dump()


# --- the serializer's own contract ---------------------------------------- #


def test_the_canonical_form_is_exact_and_two_dp():
    assert m(Decimal("-912.50")) == {"exact": "-912.5000", "display": "-912.50"}
    assert m(Decimal("2617.3300")) == {"exact": "2617.3300", "display": "2617.33"}
    # Banker's rounding, the way the engine rounds (proto findings §3).
    assert display_str(Decimal("4.0550")) == "4.06"
    assert display_str(Decimal("4.0450")) == "4.04"


def test_the_serializer_refuses_a_float_outright():
    with pytest.raises(TypeError):
        money(912.5)


def test_the_serializer_refuses_to_round_away_engine_precision():
    with pytest.raises(ValueError):
        exact_str(Decimal("1.00005"))


# --- per-endpoint parity --------------------------------------------------- #


async def test_state_parity(seeded_client, sdk):
    body = (await seeded_client.get("/book/state")).json()
    run = sdk.run("base")
    summary = run.summary()

    assert body["revision"] == sdk.status().revision
    assert body["as_of"] == AS_OF.isoformat()
    assert body["book"]["opening_balance"] == m(run.book.opening_balance)

    for field in (
        "opening_balance", "closing_balance", "min_cash",
        "total_inflow", "total_outflow", "net_cash", "total_accrual",
    ):
        assert body["summary"][field] == m(getattr(summary, field)), field
    assert body["summary"]["min_cash_period"] == (
        summary.min_cash_period.isoformat() if summary.min_cash_period else None
    )
    assert body["summary"]["runway_periods"] == summary.runway_periods

    series, _ = balance_series(run.result, run.book)
    assert body["closing"] == [m(from_minor_units(int(v))) for v in series]

    by_id = {row["id"]: row for row in body["items"]}
    assert set(by_id) == {"salary", "rent", "insurance"}
    for item_id, row in by_id.items():
        assert row["cash"] == [m(from_minor_units(int(v))) for v in run.result.cash[item_id]]
        assert row["accrual"] == [m(from_minor_units(int(v))) for v in run.result.accrual[item_id]]


async def test_warnings_parity(seeded_client, sdk):
    body = (await seeded_client.get("/book/state")).json()
    run = sdk.run("base")
    summary = run.summary()
    series, _ = balance_series(run.result, run.book)
    starts = run.result.periods.starts

    expected = [
        {"period": period.isoformat(), "depth": m(from_minor_units(int(value)))}
        for period, value in zip(starts, series, strict=True)
        if from_minor_units(int(value)) < 0
    ]
    assert expected, "the seeded book is meant to go negative"
    assert body["warnings"]["negative_months"] == expected
    assert body["warnings"]["min_cash"] == m(summary.min_cash)


async def test_forecast_parity(seeded_client, sdk):
    body = (await seeded_client.get("/book/forecast")).json()
    run = sdk.run("base")
    starts = run.result.periods.starts
    series, _ = balance_series(run.result, run.book)

    inflow = [0] * len(starts)
    outflow = [0] * len(starts)
    for item in run.book.items.values():
        if item.kind == "stock":
            continue
        column = run.result.cash.get(item.id)
        if column is None:
            continue
        for index, raw in enumerate(column):
            value = int(raw)
            if value >= 0:
                inflow[index] += value
            else:
                outflow[index] += value

    assert len(body["rows"]) == len(starts)
    for index, row in enumerate(body["rows"]):
        assert row["period"] == starts[index].isoformat()
        assert row["inflow"] == m(from_minor_units(inflow[index]))
        assert row["outflow"] == m(from_minor_units(outflow[index]))
        assert row["net"] == m(from_minor_units(inflow[index] + outflow[index]))
        assert row["closing"] == m(from_minor_units(int(series[index])))


async def test_trace_parity(seeded_client, sdk):
    response = await seeded_client.get(
        "/book/trace", params={"item": "rent", "period": "2027-01-01" if False else "2026-04-01"}
    )
    body = response.json()
    trace = sdk.run("base").trace("rent", date(2026, 4, 1), measure="accrual", depth=3)

    assert body["trace"]["value"] == m(trace.value)
    assert body["trace"]["kind"] == trace.kind
    assert body["trace"]["formula"] == trace.formula
    assert [b["value"] for b in body["trace"]["bindings"]] == [m(b.value) for b in trace.bindings]
    assert [b["symbol"] for b in body["trace"]["bindings"]] == [b.symbol for b in trace.bindings]
    assert [s["value"] for s in body["trace"]["steps"]] == [m(s.value) for s in trace.steps]
    assert [s["expression"] for s in body["trace"]["steps"]] == [s.expression for s in trace.steps]


async def test_why_zero_parity(seeded_client, sdk):
    params = {"item": "insurance", "period": "2026-05-01", "measure": "cash"}
    body = (await seeded_client.get("/book/why_zero", params=params)).json()
    explanation = sdk.run("base").why_zero("insurance", date(2026, 5, 1), measure="cash")

    assert body["explanation"]["value"] == m(explanation.value)
    assert body["explanation"]["cause"] == explanation.cause
    # Verbatim: the message and the fix are the engine's words, not the app's.
    assert body["explanation"]["message"] == explanation.message
    assert body["explanation"]["suggested_fix"] == explanation.suggested_fix


async def test_events_parity(seeded_client, sdk):
    body = (await seeded_client.get("/book/events")).json()
    rows = sdk.query_events().to_dicts()

    assert [e["id"] for e in body["events"]] == [r["id"] for r in rows]
    assert [e["amount"] for e in body["events"]] == [m(r["amount"]) for r in rows]
    assert [e["status"] for e in body["events"]] == [r["status"] for r in rows]
    assert [e["note"] for e in body["events"]] == [r["note"] for r in rows]


async def test_reconcile_parity(seeded_client, sdk):
    body = (await seeded_client.get("/book/reconcile")).json()
    # `until` defaults to as_of, which is what the host filled.
    report = sdk.reconcile(AS_OF, scenario_id="base")

    payload = body["reconciliation"]
    assert payload["until"] == report.until.isoformat()
    assert payload["suggested_cutover"] == report.suggested_cutover.isoformat()
    assert payload["forecast_total"] == m(report.forecast_total)
    assert payload["actual_total"] == m(report.actual_total)
    assert payload["drift_total"] == m(report.drift_total)
    assert payload["actual_events"] == report.actual_events
    assert [line["item_id"] for line in payload["lines"]] == [l.item_id for l in report.lines]
    for line, expected in zip(payload["lines"], report.lines, strict=True):
        assert line["forecast"] == m(expected.forecast)
        assert line["actual"] == m(expected.actual)
        assert line["drift"] == m(expected.drift)


async def test_validate_diagnostics_are_verbatim(seeded_client, sdk):
    body = (await seeded_client.get("/book/validate")).json()
    expected = sdk.validate("base")
    assert [d["code"] for d in body["diagnostics"]] == [d.code for d in expected]
    for got, want in zip(body["diagnostics"], expected, strict=True):
        assert got["severity"] == want.severity
        assert got["message"] == want.message
        assert got["suggested_fix"] == want.suggested_fix
        assert got["item_id"] == want.item_id
        assert got["field"] == want.field


async def test_history_parity(seeded_client, sdk):
    body = (await seeded_client.get("/book/history")).json()
    expected = sdk.history()
    assert [r["id"] for r in body["revisions"]] == [r.id for r in expected]
    assert [r["message"] for r in body["revisions"]] == [r.message for r in expected]
    assert body["revisions"][0]["engine_version"] == "1"


# --- the invariant, over every payload ------------------------------------ #


#: Endpoints whose payload carries money figures.
MONEY_ENDPOINTS = [
    ("/book/state", {}),
    ("/book/forecast", {}),
    ("/book/trace", {"item": "rent", "period": "2026-04-01"}),
    ("/book/why_zero", {"item": "insurance", "period": "2026-05-01"}),
    ("/book/events", {}),
    ("/book/reconcile", {}),
]
#: Endpoints that answer with diagnostics or metadata and no figure. They still
#: carry the envelope: a client must be able to stamp what it renders either way.
ENDPOINTS = MONEY_ENDPOINTS + [("/book/validate", {}), ("/book/history", {})]


@pytest.mark.parametrize(("path", "params"), MONEY_ENDPOINTS)
async def test_every_money_figure_is_canonically_formed(seeded_client, path, params):
    body = (await seeded_client.get(path, params=params)).json()
    paths = find_money_paths(body)
    assert paths, f"{path} returned no money figure to check"
    for where in paths:
        value = _at(body, where)
        assert value["exact"] == exact_str(Decimal(value["exact"])), where
        assert value["display"] == display_str(Decimal(value["exact"])), where
        assert "." in value["exact"] and len(value["exact"].split(".")[1]) == 4, where
        assert len(value["display"].split(".")[1]) == 2, where


@pytest.mark.parametrize(("path", "params"), ENDPOINTS)
async def test_every_payload_with_a_number_carries_its_provenance(seeded_client, path, params):
    """SPEC §3 response invariants — also enforced by middleware in test mode."""
    body = (await seeded_client.get(path, params=params)).json()
    for key in ("as_of", "scenario", "revision", "engine_version", "what_if"):
        assert key in body, f"{path} is missing {key}"
    assert body["engine_version"] == "1"
    assert body["revision"]


def _at(payload, path: str):
    node = payload
    for part in path.removeprefix("$.").split("."):
        while "[" in part:
            name, _, rest = part.partition("[")
            if name:
                node = node[name]
            index, _, part = rest.partition("]")
            node = node[int(index)]
        if part:
            node = node[part]
    return node


async def test_compare_parity_and_absent_is_not_zero(seeded_client, sdk):
    body = (await seeded_client.get(
        "/book/compare", params={"scenarios": "base,downside", "metric": "cash"}
    )).json()
    runs = [sdk.run("base"), sdk.run("downside")]
    table = sdk.compare(runs, metric="cash")
    keys = [c for c in table.columns if c != "period_start"]
    rows = table.to_dicts()

    assert len(body["periods"]) == len(rows)
    for period, row in zip(body["periods"], rows, strict=True):
        assert period["period_start"] == row["period_start"].isoformat()
        for scenario_id, key in zip(["base", "downside"], keys, strict=True):
            raw = row[key]
            # None must survive as null; the engine keeps absent and zero apart.
            assert period["values"][scenario_id] == (None if raw is None else m(raw))
        if row[keys[0]] is not None and row[keys[1]] is not None:
            assert period["delta"] == m(Decimal(str(row[keys[1]])) - Decimal(str(row[keys[0]])))


async def test_a_fork_is_stamped_what_if(seeded_client):
    body = (await seeded_client.get("/book/state", params={"scenario": "downside"})).json()
    assert body["what_if"] == {"stamped": True, "reason": "scenario", "scenario": "downside"}


async def test_base_committed_state_is_not_stamped(seeded_client):
    body = (await seeded_client.get("/book/state")).json()
    assert body["what_if"]["stamped"] is False


async def test_compare_is_always_stamped(seeded_client):
    body = (await seeded_client.get(
        "/book/compare", params={"scenarios": "base,downside"}
    )).json()
    assert body["what_if"]["stamped"] is True
