"""Cost and rate guardrails (SPEC §8), enforced server-side.

Three limits, all per user, all checked before a turn can reach the model:

* a **daily model budget**, default $0.50/day;
* **30 turns per hour**;
* **5 imports per day** (S5 owns the import endpoint; the counter lives here so
  both limits are read from one place).

They are enforced, not configured: a limit that only exists in a settings file
is a comment. The check runs before the first model call, so an over-budget
turn costs nothing, and its refusal is a normal turn outcome with a normal
response — the user reads a sentence, not an error code.

Refusal copy follows the SPEC §5-F1 voice rule (D-MLP-05(c)): what happened and
what is needed, at most two short sentences, no apology boilerplate.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ..clock import Clock
from ..config import Settings
from ..db import import_jobs, turns


@dataclass(frozen=True)
class Refusal:
    """Why a turn stopped before the model, and what the user is told."""

    outcome: str
    reply: str
    retry_after_seconds: int


@dataclass(frozen=True)
class Allowance:
    """What the user has left today."""

    spent_today: Decimal
    budget: Decimal
    turns_this_hour: int
    turns_per_hour: int

    @property
    def remaining(self) -> Decimal:
        return self.budget - self.spent_today


async def allowance(
    conn: AsyncConnection, *, user_id: uuid.UUID, clock: Clock, settings: Settings
) -> Allowance:
    """Read the user's spend today and turn count this hour."""
    now = clock.now()
    spent = (
        await conn.execute(
            sa.select(sa.func.coalesce(sa.func.sum(turns.c.cost), 0)).where(
                turns.c.user_id == user_id, turns.c.created_at >= _day_start(now)
            )
        )
    ).scalar_one()
    recent = (
        await conn.execute(
            sa.select(sa.func.count()).select_from(turns).where(
                turns.c.user_id == user_id,
                turns.c.created_at > now - _dt.timedelta(hours=1),
            )
        )
    ).scalar_one()
    return Allowance(
        spent_today=Decimal(str(spent)),
        budget=Decimal(str(settings.daily_model_budget_usd)),
        turns_this_hour=int(recent),
        turns_per_hour=settings.turns_per_hour,
    )


async def check_turn(
    conn: AsyncConnection, *, user_id: uuid.UUID, clock: Clock, settings: Settings
) -> Refusal | None:
    """Decide whether this turn may reach the model. ``None`` means yes."""
    state = await allowance(conn, user_id=user_id, clock=clock, settings=settings)
    now = clock.now()

    if state.turns_this_hour >= state.turns_per_hour:
        return Refusal(
            outcome="rate_limited",
            reply=(
                f"You have used this hour's {state.turns_per_hour} turns. "
                "Try again in a little while."
            ),
            retry_after_seconds=_seconds_to(now, _next_hour(now)),
        )

    if state.spent_today >= state.budget:
        return Refusal(
            outcome="over_budget",
            reply=(
                "Today's model budget is used up. "
                "Ask again tomorrow, or read the book directly in the meantime."
            ),
            retry_after_seconds=_seconds_to(now, _day_start(now) + _dt.timedelta(days=1)),
        )
    return None


async def check_import(
    conn: AsyncConnection, *, book_id: uuid.UUID, clock: Clock, settings: Settings
) -> Refusal | None:
    """SPEC §8: five imports per day. S5 wires this to ``POST /import``."""
    now = clock.now()
    count = (
        await conn.execute(
            sa.select(sa.func.count()).select_from(import_jobs).where(
                import_jobs.c.book_id == book_id,
                import_jobs.c.created_at >= _day_start(now),
            )
        )
    ).scalar_one()
    if int(count) >= settings.imports_per_day:
        return Refusal(
            outcome="import_rate_limited",
            reply=(
                f"You have run today's {settings.imports_per_day} imports. "
                "Try again tomorrow."
            ),
            retry_after_seconds=_seconds_to(now, _day_start(now) + _dt.timedelta(days=1)),
        )
    return None


def _day_start(now: _dt.datetime) -> _dt.datetime:
    """UTC midnight. The budget is a day's budget, so the day needs a start."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _next_hour(now: _dt.datetime) -> _dt.datetime:
    return now.replace(minute=0, second=0, microsecond=0) + _dt.timedelta(hours=1)


def _seconds_to(now: _dt.datetime, when: _dt.datetime) -> int:
    return max(1, int((when - now).total_seconds()))


__all__ = ["Allowance", "Refusal", "allowance", "check_import", "check_turn"]
