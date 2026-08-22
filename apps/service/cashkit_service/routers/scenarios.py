"""Scenarios and comparison (SPEC §3, F4)."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..db import books
from ..deps import BookDep, ClockDep, ConnDep, SettingsDep
from ..envelope import BASE_SCENARIO, Envelope, envelope
from ..errors import bad_request, not_found
from ..money import Money, money_or_none, to_decimal
from ..reads import read_context
from ..serialize import ComparePeriod, ScenarioOut, scenarios_out

router = APIRouter(tags=["scenarios"])


class ScenariosResponse(BaseModel):
    active: str
    scenarios: list[ScenarioOut]


@router.get("/book/scenarios")
async def list_scenarios(request: Request, book: BookDep, clock: ClockDep) -> ScenariosResponse:
    async with read_context(request, book, clock) as ctx:
        return ScenariosResponse(
            active=book.active_scenario, scenarios=scenarios_out(ctx.kit, book.active_scenario)
        )


class ActivateResponse(BaseModel):
    active: str
    superseded_proposals: list[str]


@router.post("/book/scenarios/{scenario_id}/activate")
async def activate_scenario(
    scenario_id: str, request: Request, book: BookDep, clock: ClockDep, conn: ConnDep
) -> ActivateResponse:
    """Switch the working context, book-wide, from the next request on.

    Activation invalidates every pending proposal: a card dry-run against one
    scenario must never be applied to another (SPEC §2.5).
    """
    from ..proposals import supersede_pending

    async with read_context(request, book, clock) as ctx:
        if scenario_id not in ctx.kit.scenarios.scenarios:
            raise not_found("NO_SCENARIO", f"No scenario named {scenario_id!r} in this book.")
    await conn.execute(
        books.update().where(books.c.id == book.id).values(active_scenario=scenario_id)
    )
    superseded = await supersede_pending(conn, book_id=book.id, clock=clock)
    return ActivateResponse(active=scenario_id, superseded_proposals=[str(p) for p in superseded])


class CompareResponse(Envelope):
    """``GET /book/compare`` — the R9 payload.

    A scenario absent from a period is ``null``, never ``0``; the engine keeps
    absent and zero apart and so does this payload (SPEC §5-F4).
    """

    metric: str
    scenarios: list[str]
    periods: list[ComparePeriod]
    diagnostics: list


@router.get("/book/compare")
async def compare_scenarios(
    request: Request,
    book: BookDep,
    clock: ClockDep,
    scenarios: str = Query(examples=["base,downside"]),
    metric: str = "cash",
) -> CompareResponse:
    from ..serialize import diagnostics_out

    ids = [s.strip() for s in scenarios.split(",") if s.strip()]
    if len(ids) < 2:
        raise bad_request("BAD_COMPARE", "Compare needs at least two scenarios.")

    request_id = getattr(request.state, "request_id", "")
    runtime = request.app.state.books
    async with runtime.acquire(book.id, book.storage_path) as kit:
        for scenario_id in ids:
            if scenario_id not in kit.scenarios.scenarios:
                raise not_found("NO_SCENARIO", f"No scenario named {scenario_id!r} in this book.")
        state = kit.status()
        runs = [kit.run(scenario_id) for scenario_id in ids]
        table = kit.compare(runs, metric=metric)
        rows = table.to_dicts()
        # compare() names its columns by run key, in the order the runs were
        # given; the payload names them by scenario so the client never parses
        # an engine-internal key.
        keys = [c for c in table.columns if c != "period_start"]
        periods: list[ComparePeriod] = []
        for row in rows:
            values: dict[str, Money | None] = {}
            for scenario_id, key in zip(ids, keys, strict=False):
                raw = row.get(key)
                values[scenario_id] = money_or_none(raw)
            delta = None
            if len(ids) == 2:
                left, right = (row.get(keys[0]), row.get(keys[1])) if len(keys) == 2 else (None, None)
                if left is not None and right is not None:
                    delta = money_or_none(to_decimal(right) - to_decimal(left))
            periods.append(
                ComparePeriod(period_start=row["period_start"], values=values, delta=delta)
            )

        # A comparison is never base committed state: it exists to show a fork
        # against the plan of record (SPEC §2.4).
        stamped_scenario = next((s for s in ids if s != BASE_SCENARIO), ids[0])
        env = envelope(
            as_of=clock.today(),
            scenario=stamped_scenario,
            revision=state.revision,
            clean=state.clean,
            request_id=request_id,
        )
        return CompareResponse(
            **env.model_dump(),
            metric=metric,
            scenarios=ids,
            periods=periods,
            diagnostics=diagnostics_out(table.diagnostics),
        )


class CreateScenario(BaseModel):
    name: str = Field(min_length=1)
    parent: str | None = None
    note: str = ""


@router.post("/book/scenarios", status_code=201)
async def create_scenario(
    body: CreateScenario,
    request: Request,
    book: BookDep,
    clock: ClockDep,
    conn: ConnDep,
    settings: SettingsDep,
):
    """Create a fork — as a proposal, like every other write.

    SPEC §5-F4 has fork creation as "M7 via turn or button". The button path is
    this endpoint, and it produces a confirmation card rather than a scenario:
    ADR-0029 admits no exception for a change that merely looks harmless
    (D-MLP-14).
    """
    from ..routers.book_edits import EditsRequest, create_edit_proposal

    return await create_edit_proposal(
        EditsRequest(
            ops=[{"op": "fork_scenario", "name": body.name, "parent": body.parent, "note": body.note}],
            origin="button",
        ),
        request, book, clock, conn, settings,
    )
