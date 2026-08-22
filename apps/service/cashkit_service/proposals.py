"""Proposal storage, staleness and supersession (SPEC §2.5, ADR-0029).

The invariant this module exists to hold: **no path mutates a book without a
stored, user-accepted proposal.** There is no debug flag, admin route or test
shortcut around it; T13 is the test that says so.

Gate 3 needs only supersession, because scenario activation invalidates pending
cards. The applier, the dry-run and accept/discard arrive in gate 4.
"""

from __future__ import annotations

import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from .clock import Clock
from .db import proposals

ProposalStatus = Literal["pending", "accepted", "discarded", "expired", "superseded"]

#: SPEC §2.5 proposal origins.
Origin = Literal["turn", "cell_edit", "onboarding", "import", "settings", "button"]


async def supersede_pending(
    conn: AsyncConnection,
    *,
    book_id: uuid.UUID,
    clock: Clock,
    keep: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Mark every pending proposal of a book ``superseded``.

    SPEC §2.5: *Save, Discard, scenario activation, and any accept mark all
    other pending proposals* ``superseded``. The card the user confirms is
    always the card that applies — so a card whose ground has moved stops being
    confirmable at all, rather than being silently re-interpreted.
    """
    statement = (
        proposals.update()
        .where(proposals.c.book_id == book_id, proposals.c.status == "pending")
        .values(status="superseded", resolved_at=clock.now())
        .returning(proposals.c.id)
    )
    if keep is not None:
        statement = statement.where(proposals.c.id != keep)
    result = await conn.execute(statement)
    return [row.id for row in result]


async def expire_overdue(
    conn: AsyncConnection, *, book_id: uuid.UUID, clock: Clock
) -> list[uuid.UUID]:
    """Retire pending proposals past their 15-minute window (SPEC §2.5)."""
    result = await conn.execute(
        proposals.update()
        .where(
            proposals.c.book_id == book_id,
            proposals.c.status == "pending",
            proposals.c.expires_at <= clock.now(),
        )
        .values(status="expired", resolved_at=clock.now())
        .returning(proposals.c.id)
    )
    return [row.id for row in result]


async def load(conn: AsyncConnection, proposal_id: uuid.UUID):
    return (
        await conn.execute(sa.select(proposals).where(proposals.c.id == proposal_id))
    ).first()
