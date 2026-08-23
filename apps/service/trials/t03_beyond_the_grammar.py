"""T03 — the construct the book cannot express (proto T03).

    Tried: one instruction describing the bank's overdraft rule — a fee on
    the previous month's negative closing balance, 7.95% a year over twelve,
    with a 2 EUR floor, feeding back into the balance.
    Result on lite: FAIL twice, with *valid but semantically wrong* formulas
    and zero diagnostics. Plausible numbers, no error signal.

That is the one failure class money cannot tolerate, and the MLP's answer to it
is not a better prompt. **Formula authoring is out of the MLP's scope** (SPEC
§1), so the intent grammar has no formula slot at all: the construct is
unreachable rather than badly reachable. The remaining risk is the model
*approximating* it — authoring a flat 2 EUR line and calling it done — which is
exactly the plausible-wrong-number failure wearing different clothes.

So this trial checks two things. The grammar cannot carry a formula, which is
structural. And asked for the construct anyway, the model says the book cannot
do it instead of inventing a line, which is behaviour and is therefore a gate
that reruns on every prompt or model change (ADR-0028).
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from cashkit_service.ops.schema import MutationOp
from trials.live import author, item_ids, make_book, state, turn

pytestmark = pytest.mark.live_model

OPENING, SALARY, RENT = Decimal("150.00"), Decimal("700"), Decimal("900")

BUILD = "My rent is 900 a month from January and I earn 700 a month from January."
AGIOS = (
    "My bank charges overdraft interest: in any month where the previous month "
    "closed negative, they take 7.95% a year on that negative balance, divided "
    "by twelve, with a minimum of 2 EUR. The fee comes out of my balance. Set "
    "that up."
)


def test_the_grammar_has_no_formula_slot():
    """Structural, and it needs no model: the construct is unreachable.

    Every change operation the model can emit is listed in the typed union, and
    none of them accepts an expression. A model cannot author a wrong formula
    through a surface with no formula on it.
    """
    from pydantic import TypeAdapter

    schema = TypeAdapter(MutationOp).json_schema()
    fields = {
        name
        for definition in schema.get("$defs", {}).values()
        for name in definition.get("properties", {})
    }
    assert not {"formula", "expression", "rule", "condition"} & fields, sorted(fields)


async def test_the_model_says_it_cannot_rather_than_inventing_a_line(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, BUILD)

    before = await state(live_session)
    result = await turn(live_session, AGIOS)

    # Nothing was authored: no card, and therefore nothing to confirm.
    assert result["kind"] != "proposal", result
    assert result["proposal"] is None

    after = await state(live_session)
    assert after["items"] == before["items"]
    assert after["closing"] == before["closing"]
    assert await item_ids(live_session) == set(before_ids(before))


def before_ids(body: dict) -> list[str]:
    return [item["id"] for item in body["items"]]


async def test_the_refusal_follows_the_voice_rule(live_session):
    """SPEC §5-F1, D-MLP-05(c): explain what happened and what is needed."""
    await make_book(live_session, str(OPENING))
    await author(live_session, BUILD)
    reply = (await turn(live_session, AGIOS))["reply"]

    assert reply
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if s]
    assert len(sentences) <= 2, reply
    lowered = reply.lower()
    assert not any(word in lowered for word in ("sorry", "apolog", "i'm afraid")), reply
