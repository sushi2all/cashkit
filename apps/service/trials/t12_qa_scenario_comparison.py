"""T12 — comparing two scenarios in words (proto T12).

    Tried: base against a downside fork; ask which ends higher and by how
    much. Both models passed: one subtraction over two provided numbers.

The proto let the model do that subtraction. The MLP does not: R9 returns the
two figures **and their delta** per period (SPEC §5-F4's delta column), so the
difference is an engine number the model quotes. One arithmetic operation is
not much to get wrong, but "not much" is not a property — the model either does
arithmetic on money or it does not, and this trial pins which.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, digits, make_book, state, turn

pytestmark = pytest.mark.live_model

OPENING = Decimal("1000.00")
REVENUE, COSTS = Decimal("5000"), Decimal("3000")

BUILD = (
    "I invoice 5000 a month from January, tagged as revenue, and I pay 3000 a "
    "month of fixed costs from January."
)
FORK = "Make a scenario called downside where the revenue is 20% lower."

BASE_END = OPENING + 12 * (REVENUE - COSTS)                       # 25000
DOWNSIDE_END = OPENING + 12 * (REVENUE * Decimal("0.8") - COSTS)  # 13000

#: R9 compares the metric **per period**, not the running balance: base nets
#: 2000 a month, the downside nets 1000, and the delta column carries the
#: difference the caller would otherwise have to work out.
BASE_MONTHLY = REVENUE - COSTS                                    # 2000
DOWNSIDE_MONTHLY = REVENUE * Decimal("0.8") - COSTS               # 1000


async def _two_scenarios(client):
    await make_book(client, str(OPENING))
    await author(client, BUILD)
    await author(client, FORK)
    assert await closing(client, "2026-12") == BASE_END
    assert await closing(client, "2026-12", scenario="downside") == DOWNSIDE_END


async def test_the_comparison_quotes_the_engines_figures(live_session):
    """Both year-end figures, character for character, and no third number."""
    await _two_scenarios(live_session)
    result = await turn(live_session, "which scenario ends the year higher?")

    assert result["kind"] == "answer", result
    reply = digits(result["reply"])
    assert "25000" in reply, result["reply"]
    assert "13000" in reply, result["reply"]
    assert "downside" in result["reply"].lower()


async def test_the_delta_is_the_engines_own_number(live_session):
    """The model is handed the difference; it does not compute one."""
    await _two_scenarios(live_session)
    result = await turn(live_session, "compare base and downside for me")

    receipt = next(
        (r for r in result["receipts"] if r["op"] == "compare_scenarios"), None
    )
    assert receipt is not None, result["receipts"]
    december = receipt["payload"]["periods"][-1]["values"]
    assert Decimal(december["base"]["exact"]) == BASE_MONTHLY
    assert Decimal(december["downside"]["exact"]) == DOWNSIDE_MONTHLY
    # SPEC §5-F4's delta column, computed exactly by the host so the caller
    # never subtracts two money figures of its own accord.
    assert Decimal(december["delta"]["exact"]) == DOWNSIDE_MONTHLY - BASE_MONTHLY


async def test_comparing_changes_nothing(live_session):
    await _two_scenarios(live_session)
    before = await state(live_session)
    await turn(live_session, "which scenario ends the year higher, and by how much?")
    assert (await state(live_session))["revision"] == before["revision"]
