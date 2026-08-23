"""T04 — a scenario fork and a lever on it (proto T04).

    Tried: turn 1 builds the book; turn 2 forks a downside and moves one
    lever. Checks: the figure in both scenarios.

Proto T04 used params as the lever. The MLP intent grammar has no param verb —
its lever is the M4 ``scale_items`` macro over a tag selector — so this is the
scenario half of T04 on the surface the MLP actually ships.

The invariant that matters more than the arithmetic: **base does not move.** A
what-if that quietly edits the plan of record is the exact confusion ADR-0024
exists to prevent.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, make_book, state

pytestmark = pytest.mark.live_model

OPENING = Decimal("1000.00")
REVENUE, COSTS = Decimal("5000"), Decimal("3000")

BUILD = (
    "I invoice 5000 a month from January, tagged as revenue, and I pay 3000 a "
    "month of fixed costs from January."
)
FORK = "Make a downside scenario called downside where the revenue is 20% lower."


async def test_the_fork_moves_and_base_does_not(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, BUILD)

    base_december = OPENING + 12 * (REVENUE - COSTS)      # 25000.00
    assert await closing(live_session, "2026-12") == base_december

    await author(live_session, FORK)

    assert "downside" in (await state(live_session))["scenarios"]
    assert await closing(live_session, "2026-12") == base_december

    downside = OPENING + 12 * (REVENUE * Decimal("0.8") - COSTS)   # 13000.00
    assert await closing(live_session, "2026-12", scenario="downside") == downside


async def test_the_active_scenario_is_still_base_after_a_fork(live_session):
    """Creating a fork is not switching to it (SPEC §2.4: activation is app state)."""
    await make_book(live_session, str(OPENING))
    await author(live_session, BUILD)
    await author(live_session, FORK)

    body = await state(live_session)
    assert body["active_scenario"] == "base"
    # The book is stamped because it is uncommitted, not because a fork exists:
    # SPEC §2.4 stamps a working overlay, and a fork nobody activated is not it.
    assert body["what_if"]["reason"] == "overlay"

    await live_session.post("/book/save", json={"message": "with a downside"})
    assert (await state(live_session))["what_if"]["stamped"] is False
    assert (await state(live_session, "downside"))["what_if"]["reason"] == "scenario"
