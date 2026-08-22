"""Spreadsheet export (SPEC §3 ``GET /export``)."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request, Response
from openpyxl import Workbook

from ..deps import BookDep, ClockDep
from ..reads import read_context
from ..serialize import closing_series, item_series, period_starts

router = APIRouter(tags=["export"])


@router.get(
    "/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
            "description": "The book as a workbook.",
        }
    },
)
async def export_workbook(
    request: Request,
    book: BookDep,
    clock: ClockDep,
    mode: Literal["ledger", "budget"] = "budget",
    months: int = 12,
    start: date | None = None,
    scenario: str | None = None,
) -> Response:
    """Export the book as xlsx.

    A spreadsheet cell is a float — that is Excel's type, not a choice this
    service makes. The conversion happens exactly once, at the cell boundary,
    on a Decimal the engine produced; no arithmetic is ever done on the result
    (D-MLP-13). Anyone who needs the exact figure has the API, whose money is
    always a Decimal string.
    """
    async with read_context(request, book, clock, scenario) as ctx:
        workbook = Workbook()
        sheet = workbook.active
        if mode == "ledger":
            sheet.title = "Ledger"
            table = ctx.kit.query_events()
            sheet.append(list(table.columns))
            for row in table.rows:
                sheet.append([_cell(v) for v in row])
        else:
            run = ctx.run()
            starts = period_starts(run)
            closing = closing_series(run)
            lo = 0 if start is None else next(
                (i for i, period in enumerate(starts) if period >= start), 0
            )
            hi = min(lo + max(int(months), 1), len(starts))

            sheet.title = "Budget"
            sheet.append(["Item", "Kind"] + [p.isoformat()[:7] for p in starts[lo:hi]])
            sheet.append(["Opening balance", "meta", float(run.book.opening_balance)])
            for item in item_series(run):
                values = item.accrual if item.kind == "stock" else item.cash
                sheet.append(
                    [item.name, item.kind]
                    + [float(Decimal(m.exact)) for m in values[lo:hi]]
                )
            sheet.append([])
            sheet.append(["Closing balance", ""] + [float(v) for v in closing[lo:hi]])

        buffer = io.BytesIO()
        workbook.save(buffer)
        return Response(
            content=buffer.getvalue(),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": f"attachment; filename=cashkit-{mode}.xlsx"},
        )


def _cell(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if value is None or isinstance(value, (int, float, str, date)):
        return value
    return str(value)
