"""T16 — a spreadsheet import round-trips through the API (SPEC §7, §10).

Two workbooks, both from `proto/TESTLOG.md`, both on the pinned model:

* **T06** is a round-trip of our own export. A book is authored deterministically,
  exported, and imported into a second, empty account; the twelve closing
  balances have to come back. Proto T06's finding was that the round trip is
  format-limited before it is model-limited — it only works because the export
  writes an ``Opening balance | meta`` row — so this trial is as much a test of
  the export as of the import.
* **T07** is the messy human sheet: month-name headers, POSITIVE expenses,
  section and SUM rows, a starting-balance corner cell, a 13th-month salary,
  bimonthly utilities, one annual premium and a mid-year price rise.

**The assertions are on the closing balance of every month**, not on the items.
The construct is the model's to choose — a December bonus is equally correct as
a one-off or as a windowed line, and a trial that insisted on one of them would
fail a right answer (S2's T07 lesson). The month-by-month balance is what the
user sees, and it is wrong the moment any construct is.

The third test is SPEC §7.3, which is a data-safety rule and not a convenience:
a book that already has a plan gets a **fresh fork named from the file, never
base**. It runs live because the interesting question is whether the rule holds
while a real model is authoring into it.

Marked ``live_model``: excluded per commit, run nightly and before a release.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from trials.live import HORIZON, closings, item_ids, make_book, new_session, state
from workbooks import expected_closings, messy_family_budget

pytestmark = pytest.mark.live_model

CALL_CAP = 20


# --- driving an import through the real endpoints ------------------------- #


async def import_file(
    client: AsyncClient, data: bytes, filename: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Upload, read the stream to the end, and return (started, done)."""
    response = await client.post(
        "/import",
        files={
            "file": (
                filename,
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200, response.text
    started = response.json()
    assert started["status"] == "running", started

    events: list[dict[str, Any]] = []
    async with client.stream("GET", started["stream"]) as stream:
        assert stream.status_code == 200, stream.status_code
        async for line in stream.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1].get("stage") in ("done", "failed"):
                    break
    assert events, "the import stream carried nothing"
    assert events[-1]["stage"] == "done", str(events[-1])[:1500]
    return started, events[-1]


async def edits(client: AsyncClient, ops: list[dict[str, Any]]) -> None:
    """Author deterministically, through the real proposal pipeline, no model."""
    response = await client.post("/book/edits", json={"ops": ops, "origin": "settings"})
    assert response.status_code == 201, response.text
    card = response.json()["proposal"]
    assert not [d for d in card["diagnostics"] if d["severity"] == "error"], card["diagnostics"]
    applied = await client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["kind"] == "applied"


async def apply_import(client: AsyncClient, done: dict[str, Any]) -> None:
    assert done["proposal"] is not None, done["report"]
    errors = [d for d in done["proposal"]["diagnostics"] if d["severity"] == "error"]
    assert not errors, errors
    response = await client.post(
        f"/proposals/{done['proposal']['id']}", json={"action": "accept"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "applied"


def explain(done: dict[str, Any]) -> str:
    report = done["report"]
    rows = [
        f"  {c['label']} [{c['measure']}/{c['basis']}] {c['status']}"
        f" sheet={c['sheet_value']} engine="
        f"{None if c['engine_value'] is None else c['engine_value']['exact']}"
        f" delta={c['delta']}{' PARITY' if c['parity'] else ''}"
        f"{'  — ' + c['note'] if c['note'] else ''}"
        for c in report["checks"]
    ]
    return (
        f"target={report['target_scenario']} ({report['target_reason']}) "
        f"matched={report['matched']} mismatched={report['mismatched']} "
        f"skipped={report['skipped']} parity={report['parity_notes']} "
        f"calls={report['llm_calls']}/{report['call_cap']} capped={report['capped']}\n"
        + "\n".join(rows)
        + "\ndiagnostics: "
        + json.dumps([d["message"] for d in done["diagnostics"]], indent=None)
    )


# --- T06: our own export, round-tripped ----------------------------------- #

#: The source book, authored without a model so the expected figures are ours.
SOURCE_OPS = [
    {
        "op": "add_item", "id": "salary", "name": "Salary", "direction": "in",
        "amount": "2450.00", "recurrence": "1m", "start": "2026-01-01",
        "tags": {"cat": "income"},
    },
    {
        "op": "add_item", "id": "rent", "name": "Rent", "direction": "out",
        "amount": "-980.00", "recurrence": "1m", "start": "2026-01-01",
        "tags": {"cat": "housing"},
    },
    {
        "op": "add_item", "id": "gym", "name": "Gym", "direction": "out",
        "amount": "-45.00", "recurrence": "1m", "start": "2026-03-01",
        "end": "2026-10-01", "tags": {"cat": "living"},
    },
]

SOURCE_OPENING = Decimal("2500.00")


def source_closings() -> list[Decimal]:
    """The twelve balances the source book must have, computed here."""
    balance = SOURCE_OPENING
    series: list[Decimal] = []
    for month in range(1, 13):
        balance += Decimal("2450.00") - Decimal("980.00")
        if 3 <= month <= 9:  # the gym line ends 2026-10-01, exclusive
            balance -= Decimal("45.00")
        series.append(balance)
    return series


async def test_our_own_export_round_trips_through_the_import(live_app, live_session, mailer):
    """proto T06, through the real xlsx pipeline this time."""
    await make_book(live_session, str(SOURCE_OPENING))
    await edits(live_session, SOURCE_OPS)
    assert await closings(live_session) == source_closings(), "the source book is wrong"

    export = await live_session.get("/export", params={"mode": "budget", "months": 12})
    assert export.status_code == 200, export.text

    target = await new_session(live_app, mailer, "t16-roundtrip@example.com")
    try:
        # An empty book with a deliberately wrong opening balance: the import
        # has to recover it from the sheet's own `Opening balance | meta` row,
        # which is the thing proto T06 found the round trip stands or falls on.
        await make_book(target, "0.00")
        _started, done = await import_file(target, export.content, "cashkit-budget.xlsx")
        assert done["report"]["target_scenario"] == "base", explain(done)
        assert done["report"]["llm_calls"] <= CALL_CAP, explain(done)
        await apply_import(target, done)

        assert await closings(target) == source_closings(), explain(done)
        assert done["report"]["mismatched"] == 0, explain(done)
        assert done["report"]["matched"] > 0, explain(done)
    finally:
        await target.aclose()


# --- T07: the messy human sheet ------------------------------------------- #


async def test_the_messy_family_budget_imports_month_for_month(live_session):
    """proto T07: the actual "initialize from an existing budget" case."""
    await make_book(live_session, "0.00")
    _started, done = await import_file(
        live_session, messy_family_budget(), "Family Budget 2026.xlsx"
    )

    report = done["report"]
    assert report["target_scenario"] == "base", explain(done)
    assert report["target_reason"] == "empty_book"
    assert report["llm_calls"] <= CALL_CAP, explain(done)
    assert not report["capped"], explain(done)

    await apply_import(live_session, done)

    # The whole assertion: every month's closing balance, against the figures
    # the sheet itself implies. Any wrong construct — a one-off charged twice,
    # an inclusive end date, an inverted sign — moves one of these.
    assert await closings(live_session) == expected_closings(), explain(done)
    assert report["mismatched"] == 0, explain(done)
    assert report["matched"] >= 12, explain(done)


async def test_the_report_says_what_it_checked_and_against_what(live_session):
    """The report is per sheet row, and every row names its own cell."""
    await make_book(live_session, "0.00")
    _started, done = await import_file(live_session, messy_family_budget(), "Family.xlsx")
    report = done["report"]

    assert report["checks"], explain(done)
    for check in report["checks"]:
        assert check["ref"], check
        assert check["status"] in ("matched", "mismatched", "skipped")
        if check["status"] != "skipped":
            assert check["sheet_value"] is not None
            assert check["engine_value"] is not None
            assert check["delta"] is not None
        if check["parity"]:
            assert check["status"] == "mismatched", "a parity note is a label, not a pass"
            assert Decimal(check["delta"]).copy_abs() <= Decimal(report["parity_tolerance"])
    assert report["matched"] + report["mismatched"] + report["skipped"] == len(report["checks"])


# --- SPEC §7.3: the non-empty book, with a live model authoring ----------- #


async def test_an_import_into_a_book_that_has_a_plan_never_touches_base(live_session):
    await make_book(live_session, str(SOURCE_OPENING))
    await edits(live_session, SOURCE_OPS)
    base_before = await closings(live_session, "base")
    ids_before = await item_ids(live_session, "base")

    started, done = await import_file(
        live_session, messy_family_budget(), "Family Budget 2026.xlsx"
    )
    assert started["target"]["created_fork"] is True
    assert started["target"]["scenario"] == "family-budget-2026"
    report = done["report"]
    assert report["target_reason"] == "non_empty_book", explain(done)
    assert report["llm_calls"] <= CALL_CAP, explain(done)

    await apply_import(live_session, done)

    # Base is the plan of record and the import did not move it.
    assert await closings(live_session, "base") == base_before, explain(done)
    assert await item_ids(live_session, "base") == ids_before, explain(done)

    # The fork exists, is named from the file, and holds the imported budget.
    scenarios = {s["id"] for s in (await live_session.get("/book/scenarios")).json()["scenarios"]}
    assert "family-budget-2026" in scenarios
    fork_ids = await item_ids(live_session, "family-budget-2026")
    assert ids_before <= fork_ids, "the fork must keep the lines the book already had"
    assert len(fork_ids) > len(ids_before), explain(done)

    # And it is a different plan: the imported lines actually changed the fork.
    assert await closings(live_session, "family-budget-2026") != base_before

    # The ledger is shared by every scenario, so an import into a fork writes
    # none: a one-off there is a one-month line (SPEC §7.3).
    assert (await live_session.get("/book/events")).json()["events"] == []


async def test_the_import_applies_nothing_of_its_own(live_session):
    """ADR-0029 with a live model in the loop: the card is the change."""
    await make_book(live_session, "0.00")
    before = await state(live_session)
    _started, done = await import_file(live_session, messy_family_budget(), "Family.xlsx")

    after = await state(live_session)
    assert after["items"] == before["items"], "an import that applied itself"
    assert after["revision"] == before["revision"]
    assert after["dirty"] == before["dirty"]

    await apply_import(live_session, done)
    assert (await state(live_session))["items"] != before["items"]


async def test_the_import_endpoints_stay_off_the_horizon_of_the_other_gates(live_session):
    """A guard on the two numbers the whole gate rests on."""
    await make_book(live_session, "0.00", **HORIZON)
    _started, done = await import_file(live_session, messy_family_budget(), "Family.xlsx")
    assert done["report"]["call_cap"] == CALL_CAP
    assert done["what_if"]["stamped"] is True
    assert done["what_if"]["reason"] == "pending"
