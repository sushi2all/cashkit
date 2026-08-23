"""The import pipeline, against a model that does exactly what the test says.

The scripted provider replaces the **provider**, never the pipeline: every test
here goes through the real endpoint, the real guard, the real dry-run, the real
proposal store and a real book on disk. The live half — can the pinned model
actually read a spreadsheet — is ``trials/t16_import_round_trip.py``.

What is scripted is what a live model cannot be asked to do on cue: author into
the wrong scenario, name an operation an import may not use, supply its own
check figure, or fail to reconcile twenty times in a row.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from cashkit_service.imports import sheets as sheet_reader
from cashkit_service.imports.checks import PARITY_TOLERANCE
from cashkit_service.imports.loop import book_is_empty, decide_target, fork_name_for
from workbooks import export_like




# --- driving an import ---------------------------------------------------- #


async def start(client: AsyncClient, data: bytes, filename: str = "budget.xlsx") -> dict[str, Any]:
    response = await client.post(
        "/import", files={"file": (filename, data, "application/vnd.ms-excel")}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def drain(client: AsyncClient, job_id: str) -> list[dict[str, Any]]:
    """Read the whole SSE stream, as the browser does."""
    events: list[dict[str, Any]] = []
    async with client.stream("GET", f"/imports/{job_id}/stream") as response:
        assert response.status_code == 200, response.status_code
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1].get("stage") in ("done", "failed"):
                    break
    return events


async def run_import(
    client: AsyncClient, data: bytes, filename: str = "budget.xlsx"
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = await start(client, data, filename)
    assert started["status"] == "running", started
    events = await drain(client, started["job_id"])
    return started, events


def terminal(events: list[dict[str, Any]]) -> dict[str, Any]:
    assert events, "the stream carried nothing"
    last = events[-1]
    assert last["stage"] == "done", str(last)[:2000]
    return last


async def items(client: AsyncClient, scenario: str | None = None) -> list[str]:
    params = {"scenario": scenario} if scenario else None
    response = await client.get("/book/state", params=params)
    assert response.status_code == 200, response.text
    return sorted(i["id"] for i in response.json()["items"])


# --- the scripted answers ------------------------------------------------- #

#: Twelve months of 2 000 in, on a 2 500 opening balance: the export shape.
SIMPLE_ROWS = [("Salary", "flow", [Decimal("2000")] * 12)]


def simple_workbook() -> bytes:
    return export_like(opening=Decimal("2500.00"), rows=SIMPLE_ROWS)


def plan(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reply": "A twelve-month budget with one income line.",
        "opening_balance": {"cell": "Budget!C2"},
        "horizon": None,
        "sections": [{"name": "Income", "where": "row 3"}],
        "checks": [
            {"ref": "Budget!C5", "label": "Closing January", "measure": "closing",
             "period": "2026-01-01"},
            {"ref": "Budget!N5", "label": "Closing December", "measure": "closing",
             "period": "2026-12-01"},
        ],
    }
    payload.update(overrides)
    return payload


SALARY_OP = {
    "op": "add_item", "id": "salary", "name": "Salary", "direction": "in",
    "amount": "2000.00", "recurrence": "1m", "start": "2026-01-01",
    "tags": {"cat": "income"},
}


def authored(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "answer", "reply": "Authored the section.", "intents": list(ops)}


# --- the workbook reader -------------------------------------------------- #


def test_a_european_sheet_and_an_english_one_read_the_same():
    read = sheet_reader.as_decimal
    assert read("1.234,56") == Decimal("1234.56")
    assert read("1,234.56") == Decimal("1234.56")
    assert read("(450,00)") == Decimal("-450.00")
    assert read("€ 912.50") == Decimal("912.50")
    assert read("not a number") is None
    assert read(None) is None
    assert read(True) is None


def test_the_candidate_scan_finds_the_sheets_own_arithmetic():
    parsed = sheet_reader.parse(simple_workbook())
    refs = {c["ref"] for c in sheet_reader.total_row_candidates(parsed)}
    assert "Budget!C5" in refs, "the closing-balance row is the whole point"
    assert "Budget!C3" not in refs, "an ordinary item row is not a total"


def test_sheet_text_carries_values_and_formulas():
    from workbooks import messy_family_budget

    text = sheet_reader.sheet_text(sheet_reader.parse(messy_family_budget()))
    assert "[=SUM(B5:B6)]" in text, "a formula is how a reader knows a row is a subtotal"
    assert "B7=2800.0" in text, "and the cached value is what it reconciles against"


# --- the target rule (SPEC §7.3) ------------------------------------------ #


def test_a_fork_is_named_from_the_file_and_never_collides():
    assert fork_name_for("Family Budget 2026.xlsx", set()) == "family-budget-2026"
    assert fork_name_for("budget.xlsx", {"budget"}) == "budget-2"
    assert fork_name_for("budget.xlsx", {"budget", "budget-2"}) == "budget-3"
    assert fork_name_for("base.xlsx", set()) == "base-import"
    assert fork_name_for("!!!.xlsx", set()) == "import"


async def test_an_empty_book_takes_the_import_into_base(book_client, app, books_root):
    book_dir = next(p for p in books_root.iterdir() if p.is_dir())
    async with app.state.books.acquire(__import__("uuid").UUID(book_dir.name), book_dir) as kit:
        assert book_is_empty(kit)
        target = decide_target(kit, "anything.xlsx")
    assert target.scenario == "base"
    assert target.reason == "empty_book"
    assert target.created_fork is False


async def test_a_book_with_a_plan_never_takes_an_import_into_base(seeded_client, app, books_root):
    import uuid as _uuid

    book_dir = next(p for p in books_root.iterdir() if p.is_dir())
    async with app.state.books.acquire(_uuid.UUID(book_dir.name), book_dir) as kit:
        assert not book_is_empty(kit)
        target = decide_target(kit, "Family Budget 2026.xlsx")
    assert target.scenario == "family-budget-2026"
    assert target.reason == "non_empty_book"
    assert target.created_fork is True


async def test_the_answer_says_where_the_file_is_going_before_anything_is_spent(
    seeded_client, transport
):
    started = await start(seeded_client, simple_workbook(), "Family Budget 2026.xlsx")
    assert started["target"]["created_fork"] is True
    assert started["target"]["scenario"] == "family-budget-2026"
    assert "Base is left exactly as it is." in started["target"]["message"]
    assert transport.calls == [], "the target is decided before the first model call"
    await drain(seeded_client, started["job_id"])


# --- the happy path ------------------------------------------------------- #


async def test_an_import_reconciles_and_produces_one_card(book_client, model_script):
    model_script.extend([plan(), authored(SALARY_OP)])
    _started, events = await run_import(book_client, simple_workbook())
    done = terminal(events)

    report = done["report"]
    assert report["target_scenario"] == "base"
    assert report["mismatched"] == 0, report["checks"]
    assert report["matched"] == 2
    assert report["llm_calls"] == 2
    assert report["capped"] is False
    assert done["proposal"] is not None
    assert [op["op"] for op in done["proposal"]["operations"]] == [
        "set_opening_balance",
        "add_item",
    ]


async def test_the_import_applies_nothing_until_the_card_is_applied(book_client, model_script):
    model_script.extend([plan(), authored(SALARY_OP)])
    _started, events = await run_import(book_client, simple_workbook())
    done = terminal(events)

    assert await items(book_client) == [], "an import that applied itself is the whole failure"
    response = await book_client.post(
        f"/proposals/{done['proposal']['id']}", json={"action": "accept"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "applied"
    assert await items(book_client) == ["salary"]


async def test_a_discarded_import_leaves_the_book_alone(book_client, model_script):
    model_script.extend([plan(), authored(SALARY_OP)])
    _started, events = await run_import(book_client, simple_workbook())
    done = terminal(events)
    await book_client.post(f"/proposals/{done['proposal']['id']}", json={"action": "discard"})
    assert await items(book_client) == []


# --- the non-empty-book fork rule, end to end ----------------------------- #


async def test_an_import_into_a_book_with_a_plan_lands_in_a_fork_and_base_does_not_move(
    seeded_client, model_script
):
    before = await items(seeded_client, "base")
    assert before, "the fixture book has to have something to protect"

    model_script.extend(
        [
            plan(opening_balance=None, checks=[]),
            authored(
                {
                    "op": "add_item", "id": "imported_salary", "name": "Imported salary",
                    "direction": "in", "amount": "2000.00", "recurrence": "1m",
                    "start": "2026-01-01",
                }
            ),
        ]
    )
    _started, events = await run_import(seeded_client, simple_workbook(), "Family Budget.xlsx")
    done = terminal(events)
    assert done["report"]["target_scenario"] == "family-budget"
    assert done["report"]["created_fork"] is True

    response = await seeded_client.post(
        f"/proposals/{done['proposal']['id']}", json={"action": "accept"}
    )
    assert response.status_code == 200, response.text

    assert await items(seeded_client, "base") == before, "base was changed by an import"
    assert "imported_salary" in await items(seeded_client, "family-budget")
    scenarios = (await seeded_client.get("/book/scenarios")).json()
    assert "family-budget" in [s["id"] for s in scenarios["scenarios"]]


async def test_an_import_into_a_fork_never_writes_to_the_shared_ledger(
    seeded_client, model_script
):
    """The ledger is shared by every scenario, so an event would reach base."""
    events_before = (await seeded_client.get("/book/events")).json()["events"]
    model_script.extend(
        [
            plan(opening_balance=None, checks=[]),
            authored(
                {"op": "add_event", "date": "2026-06-01", "amount": "-500.00",
                 "direction": "out", "note": "premium"},
                {"op": "add_item", "id": "kept", "direction": "out", "amount": "-10.00",
                 "recurrence": "1m", "start": "2026-01-01"},
            ),
        ]
    )
    _started, stream = await run_import(seeded_client, simple_workbook(), "Family.xlsx")
    done = terminal(stream)

    assert [op["op"] for op in done["proposal"]["operations"]] == ["fork_scenario", "add_item"]
    codes = {d["code"] for d in done["diagnostics"]}
    assert "CK-E901" in codes, done["diagnostics"]
    await seeded_client.post(f"/proposals/{done['proposal']['id']}", json={"action": "accept"})
    assert (await seeded_client.get("/book/events")).json()["events"] == events_before


async def test_an_import_into_a_fork_never_moves_the_book_level_settings(
    seeded_client, model_script
):
    """Horizon and opening balance are book-level; on a fork they stay put."""
    before = (await seeded_client.get("/book/state", params={"scenario": "base"})).json()
    model_script.extend(
        [
            plan(
                opening_balance={"amount": "99999.00"},
                horizon={"start": "2020-01-01", "end": "2021-01-01"},
                checks=[],
            ),
            authored({"op": "add_item", "id": "x", "direction": "in", "amount": "1.00",
                      "recurrence": "1m", "start": "2026-01-01"}),
        ]
    )
    _started, stream = await run_import(seeded_client, simple_workbook(), "Sheet.xlsx")
    done = terminal(stream)
    assert [op["op"] for op in done["proposal"]["operations"]] == ["fork_scenario", "add_item"]

    await seeded_client.post(f"/proposals/{done['proposal']['id']}", json={"action": "accept"})
    after = (await seeded_client.get("/book/state", params={"scenario": "base"})).json()
    assert after["book"]["opening_balance"] == before["book"]["opening_balance"]
    assert after["book"]["horizon_start"] == before["book"]["horizon_start"]
    assert after["months"] == before["months"]


# --- reconciliation is the model's to fail, not to assert ----------------- #


async def test_a_figure_the_model_supplies_for_a_check_is_ignored(book_client, model_script):
    """The value comes from the workbook. A check the model could satisfy by
    asserting would not be a check."""
    model_script.extend(
        [
            plan(
                checks=[
                    {
                        "ref": "Budget!C5", "label": "Closing January", "measure": "closing",
                        "period": "2026-01-01",
                        # All three are the model trying to answer its own exam.
                        "value": "999999.00", "sheet_value": "999999.00",
                        "engine_value": "999999.00",
                    }
                ]
            ),
            authored(SALARY_OP),
        ]
    )
    _started, events = await run_import(book_client, simple_workbook())
    check = terminal(events)["report"]["checks"][0]
    assert check["sheet_value"] == "4500", check
    assert check["status"] == "matched"


async def test_a_mismatch_is_reported_and_the_import_still_produces_a_card(
    book_client, model_script
):
    wrong = {**SALARY_OP, "amount": "1500.00"}
    model_script.extend([plan(), authored(wrong), authored(wrong), authored(wrong)])
    _started, events = await run_import(book_client, simple_workbook())
    report = terminal(events)["report"]

    assert report["mismatched"] == 2, report["checks"]
    january = next(c for c in report["checks"] if c["period"] == "2026-01-01")
    assert january["status"] == "mismatched"
    assert january["sheet_value"] == "4500"
    assert january["engine_value"]["exact"] == "4000.0000"
    assert january["delta"] == "-500"
    assert january["parity"] is False
    assert report["partial"] is True


async def test_a_one_cent_divergence_is_labelled_and_never_absorbed(book_client, model_script):
    """SPEC §7.5: the engine rounds at 4dp with banker's; Excel uses float ROUND."""
    off_by_a_cent = export_like(
        opening=Decimal("2500.00"), rows=[("Salary", "flow", [Decimal("2000")] * 12)]
    )
    # The sheet's own January closing is one cent below what the engine will
    # compute from the same lines.
    from openpyxl import load_workbook
    import io

    book = load_workbook(io.BytesIO(off_by_a_cent))
    book["Budget"]["C5"] = 4499.99
    buffer = io.BytesIO()
    book.save(buffer)

    model_script.extend([plan(), authored(SALARY_OP), authored(SALARY_OP), authored(SALARY_OP)])
    _started, events = await run_import(book_client, buffer.getvalue())
    report = terminal(events)["report"]

    january = next(c for c in report["checks"] if c["period"] == "2026-01-01")
    assert january["status"] == "mismatched", "a parity note is a label, not a pass"
    assert january["parity"] is True
    assert "banker" in january["note"]
    assert Decimal(january["delta"]).copy_abs() <= PARITY_TOLERANCE
    assert report["parity_notes"] == 1
    assert report["parity_tolerance"] == "0.01"


async def test_a_row_outside_the_horizon_is_skipped_with_the_reason(book_client, model_script):
    model_script.extend(
        [
            plan(
                checks=[
                    {"ref": "Budget!C5", "label": "Closing 2030", "measure": "closing",
                     "period": "2030-01-01"}
                ]
            ),
            authored(SALARY_OP),
        ]
    )
    _started, events = await run_import(book_client, simple_workbook())
    report = terminal(events)["report"]
    assert report["skipped"] == 1
    assert "outside the book's horizon" in report["checks"][0]["note"]


# --- the call cap (SPEC §7.2) --------------------------------------------- #


async def test_the_call_cap_stops_the_loop_and_the_partial_result_says_so(
    book_client, model_script, settings
):
    wrong = {**SALARY_OP, "amount": "1.00"}
    model_script.append(
        plan(sections=[{"name": f"section {i}"} for i in range(10)])
    )
    model_script.extend([authored(wrong)] * 40)

    _started, events = await run_import(book_client, simple_workbook())
    done = terminal(events)
    report = done["report"]

    assert report["capped"] is True
    assert report["llm_calls"] == settings.import_max_llm_calls == 20
    assert report["call_cap"] == 20
    assert report["partial"] is True
    assert "limit of 20 assistant calls" in report["incomplete_reason"]
    assert done["status"] == "partial"
    # Honest, not silent: the partial result is still a card the user can read.
    assert done["proposal"] is not None
    assert await items(book_client) == []


async def test_the_import_rate_limit_is_a_sentence_on_a_200(book_client, settings, model_script):
    from cashkit_service.db import import_jobs

    app = book_client._transport.app  # noqa: SLF001 — the test owns this app
    book = (await book_client.get("/book/state")).json()
    del book
    async with app.state.db.connect() as conn:
        import uuid as _uuid

        import sqlalchemy as sa

        book_id = (await conn.execute(sa.text("SELECT id FROM books"))).scalar_one()
        for _ in range(settings.imports_per_day):
            await conn.execute(
                import_jobs.insert().values(
                    id=_uuid.uuid4(), book_id=book_id, status="done", report=None,
                    created_at=app.state.clock.now(),
                )
            )

    response = await book_client.post(
        "/import", files={"file": ("b.xlsx", simple_workbook(), "application/vnd.ms-excel")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "refusal"
    assert body["status"] == "refused"
    assert "5 imports" in body["reply"]
    assert body["retry_after_seconds"] > 0
    assert model_script == [], "a refused import must not reach the model"


# --- the stream ----------------------------------------------------------- #


async def test_the_stream_replays_for_a_listener_that_arrives_late(book_client, model_script):
    model_script.extend([plan(), authored(SALARY_OP)])
    started = await start(book_client, simple_workbook())
    first = await drain(book_client, started["job_id"])
    again = await drain(book_client, started["job_id"])
    assert [e["stage"] for e in again] == [e["stage"] for e in first]
    assert again[-1]["report"] == first[-1]["report"]


async def test_the_stream_carries_progress_before_it_carries_a_report(book_client, model_script):
    model_script.extend([plan(), authored(SALARY_OP)])
    _started, events = await run_import(book_client, simple_workbook())
    stages = [e["stage"] for e in events]
    assert stages[:4] == ["parsing", "parsed", "target", "planning"]
    assert "section" in stages and "authored" in stages
    assert "check" in stages, "the checks pass or fail live (SPEC §6-S14)"
    checks = [e for e in events if e["stage"] == "check"]
    assert {c["status"] for c in checks} == {"matched"}


async def test_the_terminal_event_carries_the_whatif_stamp(book_client, model_script):
    """Every figure in the report is a dry-run figure (SPEC §2.4)."""
    model_script.extend([plan(), authored(SALARY_OP)])
    _started, events = await run_import(book_client, simple_workbook())
    done = terminal(events)
    for key in ("as_of", "scenario", "revision", "engine_version", "what_if"):
        assert key in done, key
    assert done["what_if"]["stamped"] is True
    assert done["what_if"]["reason"] == "pending"


async def test_another_account_cannot_read_an_import(book_client, app, mailer, model_script):
    model_script.extend([plan(), authored(SALARY_OP)])
    started = await start(book_client, simple_workbook())
    await drain(book_client, started["job_id"])

    other = AsyncClient(transport=book_client._transport, base_url="http://test")  # noqa: SLF001
    await other.post("/auth/link", json={"email": "other@example.com", "platform": "web"})
    token = (
        await other.post(
            "/auth/verify",
            json={"token": mailer.last_for("other@example.com").token, "platform": "web"},
        )
    ).json()["token"]
    other.headers["Authorization"] = f"Bearer {token}"
    response = await other.get(f"/imports/{started['job_id']}/stream")
    assert response.status_code == 404
    await other.aclose()


# --- what an import may not do -------------------------------------------- #


async def test_an_import_cannot_author_an_operation_outside_its_set(book_client, model_script):
    model_script.extend(
        [
            plan(checks=[]),
            authored(
                {"op": "scale_items", "selector": "cat:income", "factor": "0.5"},
                {"op": "save", "message": "done"},
                SALARY_OP,
            ),
        ]
    )
    _started, events = await run_import(book_client, simple_workbook())
    done = terminal(events)
    assert [op["op"] for op in done["proposal"]["operations"]] == [
        "set_opening_balance",
        "add_item",
    ]


async def test_a_model_that_names_a_scenario_does_not_get_one(seeded_client, model_script):
    """SPEC §7.3 is structural: the host stamps the target after the guard."""
    model_script.extend(
        [
            plan(opening_balance=None, checks=[]),
            authored({**SALARY_OP, "scenario": "base"}),
        ]
    )
    _started, events = await run_import(seeded_client, simple_workbook(), "Mine.xlsx")
    done = terminal(events)
    authored_op = next(op for op in done["proposal"]["operations"] if op["op"] == "add_item")
    assert authored_op["scenario"] == "mine", "the model asked for base and did not get it"


async def test_an_unreadable_upload_is_refused_before_anything_is_spent(
    book_client, model_script
):
    response = await book_client.post(
        "/import", files={"file": ("notes.txt", b"this is not a workbook", "text/plain")}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "UNREADABLE_WORKBOOK"
    assert model_script == []


async def test_an_empty_upload_is_refused(book_client):
    response = await book_client.post(
        "/import", files={"file": ("empty.xlsx", b"", "application/vnd.ms-excel")}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "EMPTY_UPLOAD"


async def test_a_fork_reconciles_against_what_the_import_added(seeded_client, model_script):
    """A fork carries the book's own plan too, so an absolute comparison would
    be comparing two different things (SPEC §7.3)."""
    model_script.extend(
        [
            plan(
                opening_balance=None,
                checks=[
                    {"ref": "Budget!C5", "label": "Closing January", "measure": "closing",
                     "period": "2026-01-01"},
                    {"ref": "Budget!C3", "label": "Salary January", "measure": "total_in",
                     "period": "2026-01-01"},
                ],
            ),
            authored(SALARY_OP),
        ]
    )
    _started, events = await run_import(seeded_client, simple_workbook(), "Mine.xlsx")
    report = terminal(events)["report"]

    closing = next(c for c in report["checks"] if c["measure"] == "closing")
    assert closing["status"] == "skipped"
    assert closing["basis"] == "added"
    assert "opening balance" in closing["note"]

    total_in = next(c for c in report["checks"] if c["measure"] == "total_in")
    assert total_in["basis"] == "added"
    assert total_in["sheet_value"] == "2000"
    # The fixture book's own salary is in the fork as well; what the import
    # added is the 2 000 the sheet asked for, and that is what is compared.
    assert total_in["engine_value"]["exact"] == "2000.0000"
    assert total_in["status"] == "matched"


async def test_an_imported_line_never_takes_over_a_line_the_book_already_had(
    seeded_client, model_script
):
    """SPEC §7.3: import never merges silently and never destroys existing items.

    ``add_item`` on an existing id replaces that line, so an import that reused
    one would quietly overwrite the user's own — inside the fork, where they
    would not see it happen.
    """
    before = (await seeded_client.get("/book/state", params={"scenario": "base"})).json()
    salary_before = next(i for i in before["items"] if i["id"] == "salary")

    model_script.extend([plan(opening_balance=None, checks=[]), authored(SALARY_OP)])
    _started, events = await run_import(seeded_client, simple_workbook(), "Mine.xlsx")
    done = terminal(events)

    authored_op = next(op for op in done["proposal"]["operations"] if op["op"] == "add_item")
    assert authored_op["id"] == "salary_imported"
    notes = [d for d in done["diagnostics"] if "already has a line" in d["message"]]
    assert notes and notes[0]["severity"] == "info", done["diagnostics"]

    await seeded_client.post(f"/proposals/{done['proposal']['id']}", json={"action": "accept"})
    after = (await seeded_client.get("/book/state", params={"scenario": "mine"})).json()
    fork_ids = {i["id"] for i in after["items"]}
    assert {"salary", "salary_imported"} <= fork_ids
    assert next(i for i in after["items"] if i["id"] == "salary")["cash"] == salary_before["cash"]


async def test_an_import_does_not_edit_a_line_the_book_already_had(seeded_client, model_script):
    model_script.extend(
        [
            plan(opening_balance=None, checks=[]),
            authored(
                {"op": "set_amount", "item": "rent", "amount": "-1.00"},
                {"op": "add_item", "id": "new_line", "direction": "out", "amount": "-5.00",
                 "recurrence": "1m", "start": "2026-01-01"},
            ),
        ]
    )
    _started, events = await run_import(seeded_client, simple_workbook(), "Mine.xlsx")
    done = terminal(events)
    assert [op["op"] for op in done["proposal"]["operations"]] == ["fork_scenario", "add_item"]
    assert any("import does not change one" in d["message"] for d in done["diagnostics"])


async def test_an_import_may_still_change_a_line_it_authored_itself(book_client, model_script):
    """A mid-year price rise is one line plus a change from a date (proto T07)."""
    model_script.extend(
        [
            plan(opening_balance=None, checks=[]),
            authored(
                {"op": "add_item", "id": "nursery", "direction": "out", "amount": "-420.00",
                 "recurrence": "1m", "start": "2026-01-01"},
                {"op": "set_amount", "item": "nursery", "amount": "-480.00",
                 "from_date": "2026-07-01"},
            ),
        ]
    )
    _started, events = await run_import(book_client, simple_workbook())
    done = terminal(events)
    assert [op["op"] for op in done["proposal"]["operations"]] == ["add_item", "set_amount"]

    await book_client.post(f"/proposals/{done['proposal']['id']}", json={"action": "accept"})
    state = (await book_client.get("/book/state")).json()
    nursery = next(i for i in state["items"] if i["id"] == "nursery")
    assert nursery["cash"][0]["display"] == "-420.00"
    assert nursery["cash"][6]["display"] == "-480.00"
