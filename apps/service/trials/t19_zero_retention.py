"""T19 — zero-retention model routing, verified end to end (SPEC §9).

SPEC §9 says "zero-retention model routing **verified**", and S2 already
proved the half that is ours: `provider: {"data_collection": "deny"}` is in
the payload of every call (D-MLP-31, `tests/test_transport.py`). This trial is
the other half — does the thing at the far end read it?

**What can honestly be verified from outside, and what cannot.** The exact
scope is written into the assertions rather than into a summary, because the
temptation with a compliance check is to write a test that passes and call the
matter closed.

Verifiable:

1. the flag is on the wire on a **real** call the service itself builds;
2. that call succeeds against the pinned model, and the response names the
   provider that served it;
3. the router genuinely **parses and enforces** the `provider` block — proved
   by a controlled negative, a request the router must refuse;
4. every endpoint serving the pinned model belongs to Google, so no
   third-party reseller is in the path.

Not verifiable from outside, and stated as such in
`compliance/SPEC9-checklist.md` item 9: that `data_collection: "deny"`
**excludes** a particular data-collecting endpoint. OpenRouter's public API no
longer exposes a per-endpoint data policy, and no logging-tier endpoint is
reachable on this key to use as a differential control. The claim rests on
OpenRouter's contract and Google's; **owner: Luca**, in writing, before the
first external user.

    uv run pytest apps/service/trials/t19_zero_retention.py -m live_model -q
"""

from __future__ import annotations

import httpx
import pytest

from trials.live import api_key, make_book, turn

pytestmark = pytest.mark.live_model


async def test_the_flag_is_on_a_real_call_and_the_call_succeeds(live_transport):
    """(1) and (2): the payload the service built, and what answered it."""
    transport = live_transport
    completion = await transport.complete(
        [{"role": "user", "content": 'Reply with exactly {"ok": true}'}]
    )
    try:
        assert completion.request["provider"] == {"data_collection": "deny"}, (
            "the zero-retention instruction is missing from a live request"
        )
        assert completion.ok, completion.error
        # OpenRouter names the provider that served the request.
        provider = completion.response.get("provider")
        assert provider, f"no provider in the response: {list(completion.response)}"
        assert "google" in str(provider).lower(), (
            f"the pinned model was served by {provider!r}, which is not Google — "
            "the subprocessor list names Google as the model provider"
        )
    finally:
        await transport.aclose()


async def test_the_router_enforces_the_provider_block_it_is_sent(settings):
    """(3) The controlled negative, and the point of the whole trial.

    A flag the router ignored as an unknown field would look exactly like a
    flag the router honoured. So this sends a `provider` preference the router
    **must refuse** — a provider that does not serve this model — and requires
    the refusal to name the preference. That is proof the router parses and
    acts on the object the zero-retention instruction travels in.
    """
    key = api_key()
    if not key:
        pytest.skip("no OPENROUTER_API_KEY")
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
                "provider": {
                    "data_collection": "deny",
                    "only": ["Google Vertex"],  # not a provider slug for this model
                },
            },
        )
    body = response.json()
    assert "error" in body, (
        "a provider preference that cannot be satisfied was served anyway; the "
        f"router may not be reading the provider block at all: {body}"
    )
    message = str(body["error"].get("message", "")).lower()
    assert "provider" in message and ("only" in message or "allowed" in message), (
        f"the refusal does not name the provider preference: {body['error']}"
    )


async def test_no_third_party_reseller_serves_the_pinned_model(settings):
    """(4) The subprocessor list says Google. This checks the routing agrees.

    A model available through a reseller would put a company on the path that
    `compliance/subprocessors.md` does not name, which would make the page
    wrong rather than merely incomplete.
    """
    key = api_key()
    if not key:
        pytest.skip("no OPENROUTER_API_KEY")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            f"{settings.llm_base_url}/models/{settings.llm_model}/endpoints",
            headers={"Authorization": f"Bearer {key}"},
        )
    endpoints = response.json()["data"]["endpoints"]
    assert endpoints, "the pinned model has no endpoints at all"
    providers = {e["provider_name"] for e in endpoints}
    assert all("google" in p.lower() for p in providers), (
        f"the pinned model is served by {sorted(providers)}; anything that is not "
        "Google belongs on compliance/subprocessors.md before it serves a turn"
    )


async def test_a_turn_through_the_service_carries_the_flag_into_its_journal(
    live_session, database
):
    """The flag is not only in the transport — it is in what the turn recorded.

    `llm_calls.request` stores the payload verbatim (SPEC §4), so the
    instruction is auditable after the fact rather than only assertable before
    it. That row purges after 30 days like every other, so this is a check on
    a live turn, not on a permanent record.
    """
    import sqlalchemy as sa

    from cashkit_service.db import llm_calls

    await make_book(live_session, "2500.00")
    await turn(live_session, "what is my closing balance in June")

    async with database.connect() as conn:
        rows = (await conn.execute(sa.select(llm_calls.c.request))).scalars().all()
    assert rows, "the turn made no model call"
    for request in rows:
        assert request["provider"] == {"data_collection": "deny"}, (
            "a model call reached the provider without the zero-retention "
            f"instruction: {list(request)}"
        )
