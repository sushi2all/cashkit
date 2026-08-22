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


# --- creation, staleness and accept (SPEC §2.5) --------------------------- #


import datetime as _dt  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

from cashkit.sdk import CashKit  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from .books import head_revision, overlay_fingerprint  # noqa: E402
from .config import Settings  # noqa: E402
from .envelope import Envelope  # noqa: E402
from .ops.dryrun import Deltas, DryRun, dry_run  # noqa: E402
from .serialize import DiagnosticOut  # noqa: E402


class ProposalOut(BaseModel):
    """A proposal card, as the API ships it (SPEC §6-S4)."""

    id: str
    status: str
    origin: str
    scenario: str
    turn_id: str | None
    operations: list[dict[str, Any]]
    deltas: Deltas
    diagnostics: list[DiagnosticOut]
    base_revision: str | None
    expires_at: str
    created_at: str
    #: Set when this card replaced a stale one (SPEC §2.5 refreshed proposal).
    supersedes: str | None = None


class ProposalResponse(Envelope):
    """``kind`` mirrors ``POST /turns`` so a client renders one shape.

    ``proposal`` for a change awaiting confirmation, ``clarification`` when the
    service needs an answer before it can build one — never a guess.
    """

    kind: str
    proposal: ProposalOut | None = None
    clarification: str | None = None


def _row_to_out(row: Any) -> ProposalOut:
    return ProposalOut(
        id=str(row.id),
        status=row.status,
        origin=row.origin,
        scenario=row.scenario,
        turn_id=str(row.turn_id) if row.turn_id else None,
        operations=list(row.ops),
        deltas=Deltas.model_validate(row.deltas["deltas"]),
        diagnostics=[DiagnosticOut.model_validate(d) for d in row.deltas.get("diagnostics", [])],
        base_revision=row.base_revision,
        expires_at=row.expires_at.isoformat(),
        created_at=row.created_at.isoformat(),
        supersedes=str(row.supersedes) if row.supersedes else None,
    )


@dataclass(frozen=True)
class Stamp:
    """What a proposal was dry-run against (SPEC §2.5)."""

    revision: str | None
    fingerprint: str

    @classmethod
    def of(cls, kit: CashKit) -> "Stamp":
        return cls(revision=head_revision(kit), fingerprint=overlay_fingerprint(kit))

    def matches(self, other: "Stamp") -> bool:
        return self.revision == other.revision and self.fingerprint == other.fingerprint


async def create(
    conn: AsyncConnection,
    *,
    kit: CashKit,
    book_id: uuid.UUID,
    origin: str,
    scenario: str,
    operations: list[dict[str, Any]],
    as_of: _dt.date,
    clock: Clock,
    settings: Settings,
    context: str | None = None,
    turn_id: uuid.UUID | None = None,
    supersedes: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, DryRun]:
    """Dry-run the operations and store the resulting proposal.

    Returns ``(None, result)`` when the pipeline needs a clarification: a
    question is not a pending change, so nothing is stored for the user to
    confirm.
    """
    result = dry_run(kit, operations, scenario=scenario, as_of=as_of, context=context)
    if result.clarification is not None:
        return None, result

    stamp = Stamp.of(kit)
    now = clock.now()
    proposal_id = uuid.uuid4()
    await conn.execute(
        proposals.insert().values(
            id=proposal_id,
            book_id=book_id,
            turn_id=turn_id,
            origin=origin,
            context=context,
            scenario=scenario,
            ops=result.operations,
            deltas={
                "deltas": result.deltas.model_dump(mode="json"),
                "diagnostics": [d.model_dump() for d in result.diagnostics],
                "ok": result.ok,
            },
            base_revision=stamp.revision,
            overlay_fingerprint=stamp.fingerprint,
            status="pending",
            supersedes=supersedes,
            expires_at=now + _dt.timedelta(minutes=settings.proposal_ttl_minutes),
            created_at=now,
        )
    )
    return proposal_id, result


async def mark(
    conn: AsyncConnection, proposal_id: uuid.UUID, status: ProposalStatus, *, clock: Clock
) -> None:
    await conn.execute(
        proposals.update()
        .where(proposals.c.id == proposal_id)
        .values(status=status, resolved_at=clock.now())
    )
