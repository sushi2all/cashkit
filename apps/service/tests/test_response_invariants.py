"""The §3 response invariants, and proof that the middleware enforces them.

SPEC §10 contract tests: *response invariants (§3) checked by middleware in
test mode*. A check nobody has seen fail is not a check, so this suite makes it
fail on purpose.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cashkit_service.middleware import (
    ResponseInvariantMiddleware,
    ResponseInvariantViolation,
    find_money_paths,
)


def _rogue_app(*, enabled: bool, payload: dict) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ResponseInvariantMiddleware, enabled=enabled)

    @app.get("/rogue")
    async def rogue() -> dict:
        return payload

    return app


NAKED_MONEY = {"closing_balance": {"exact": "10.0000", "display": "10.00"}}
STAMPED_MONEY = {
    "as_of": "2026-03-17",
    "scenario": "base",
    "revision": "abc",
    "engine_version": "1",
    "what_if": {"stamped": False},
    "closing_balance": {"exact": "10.0000", "display": "10.00"},
}


async def test_the_middleware_catches_money_without_provenance():
    app = _rogue_app(enabled=True, payload=NAKED_MONEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        with pytest.raises(ResponseInvariantViolation) as excinfo:
            await client.get("/rogue")
    assert "without the SPEC §3 envelope" in str(excinfo.value)


async def test_the_middleware_passes_a_stamped_payload():
    app = _rogue_app(enabled=True, payload=STAMPED_MONEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/rogue")
    assert response.status_code == 200
    assert response.json() == STAMPED_MONEY


async def test_the_check_is_off_outside_test_mode():
    """An assertion must never turn a served payload into a 500 in production."""
    app = _rogue_app(enabled=False, payload=NAKED_MONEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/rogue")
    assert response.status_code == 200


def test_money_is_found_however_deeply_it_is_nested():
    payload = {"a": [{"b": {"c": {"exact": "1.0000", "display": "1.00"}}}]}
    assert find_money_paths(payload) == ["$.a[0].b.c"]


def test_a_lookalike_is_not_mistaken_for_money():
    assert find_money_paths({"exact": "1.0000"}) == []
    assert find_money_paths({"exact": 1, "display": "1.00"}) == []


async def test_the_middleware_is_enabled_across_this_suite(app):
    """Every other test in this package runs behind the check."""
    assert app.state.settings.check_response_invariants is True
