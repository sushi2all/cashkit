"""Book lifecycle (SPEC §3 ``POST /books``, §6-S13 onboarding step a)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field, field_validator

from cashkit.model import Grain, PeriodRange

from ..db import books
from ..deps import ClockDep, ConnDep, PrincipalDep, get_books, load_book_row
from ..errors import bad_request, book_exists
from ..serialize import diagnostics_out, DiagnosticOut

router = APIRouter(tags=["book"])


class CreateBook(BaseModel):
    """Onboarding step (a): horizon and opening balance, nothing else.

    Currency is EUR and grain is month for the whole MLP (SPEC §1, §3); they
    are fixed here rather than offered, because an option nothing supports is a
    way to build an unusable book.
    """

    horizon_start: date
    horizon_end: date
    # A money value arrives as a string. A JSON number would be a float by the
    # time Pydantic saw it, and no float ever enters the money path.
    opening_balance: str = Field(examples=["2500.00"])
    cutover: date | None = None
    calendar: str | None = Field(default=None, examples=["IT"])
    currency: Literal["EUR"] = "EUR"
    grain: Literal["month"] = "month"

    @field_validator("opening_balance")
    @classmethod
    def _decimal_string(cls, value: str) -> str:
        try:
            Decimal(value)
        except Exception as exc:  # noqa: BLE001 — the message is the useful part
            raise ValueError(f"{value!r} is not a decimal amount") from exc
        return value


class BookCreated(BaseModel):
    book_id: str
    active_scenario: str
    revision: str | None
    diagnostics: list[DiagnosticOut]


@router.post("/books", status_code=status.HTTP_201_CREATED)
async def create_book_endpoint(
    body: CreateBook, request: Request, conn: ConnDep, principal: PrincipalDep, clock: ClockDep
) -> BookCreated:
    """Create the account's single book.

    One book per user is structural: ``books.user_id`` is UNIQUE (SPEC §4), so
    a second attempt is refused by the database, not by a check that could be
    raced.
    """
    if body.horizon_end <= body.horizon_start:
        raise bad_request("BAD_HORIZON", "The horizon must end after it starts.")
    if await load_book_row(conn, principal.user_id) is not None:
        raise book_exists()

    runtime = get_books(request)
    book_id = uuid.uuid4()
    path, diagnostics = await runtime.create(
        book_id=book_id,
        horizon=PeriodRange(start=body.horizon_start, end=body.horizon_end),
        opening_balance=Decimal(body.opening_balance),
        grain=Grain.MONTH,
        cutover=body.cutover,
        params=None,
        calendar=body.calendar,
    )
    errors = [d for d in diagnostics if d.severity == "error"]
    if errors:
        await runtime.forget(book_id, delete_storage=True)
        raise bad_request(
            "BOOK_NOT_CREATED",
            "; ".join(d.message for d in errors),
            diagnostics=[d.model_dump() for d in diagnostics],
        )

    await conn.execute(
        books.insert().values(
            id=book_id,
            user_id=principal.user_id,
            storage_path=str(path),
            active_scenario="base",
            created_at=clock.now(),
        )
    )
    async with runtime.acquire(book_id, path) as kit:
        revision = kit.status().revision
    return BookCreated(
        book_id=str(book_id),
        active_scenario="base",
        revision=revision,
        diagnostics=diagnostics_out(diagnostics),
    )
