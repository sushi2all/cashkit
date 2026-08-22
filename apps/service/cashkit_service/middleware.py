"""Request correlation, and the test-mode response-invariant check.

SPEC §11 wants one correlation chain — ``request_id → turn_id →
llm_calls.seq → proposal_id`` — in every log line and payload envelope. S1 owns
the first link: every request gets an id, and every payload carries it.

SPEC §10 wants the §3 response invariants "checked by middleware in test mode".
That is what :class:`ResponseInvariantMiddleware` does, and only in test mode:
turning a served payload into a 500 because an assertion tripped is not a
production behaviour.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from .envelope import ENVELOPE_KEYS

REQUEST_ID_HEADER = "x-request-id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Give every request an id, honouring one the caller supplied."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def _is_money(value: Any) -> bool:
    """Recognise the canonical money object of :mod:`cashkit_service.money`."""
    return (
        isinstance(value, dict)
        and set(value) == {"exact", "display"}
        and isinstance(value.get("exact"), str)
        and isinstance(value.get("display"), str)
    )


def find_money_paths(payload: Any, prefix: str = "$") -> list[str]:
    """Every path in a payload at which a money figure sits."""
    found: list[str] = []
    if _is_money(payload):
        return [prefix]
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.extend(find_money_paths(value, f"{prefix}.{key}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(find_money_paths(value, f"{prefix}[{index}]"))
    return found


class ResponseInvariantViolation(AssertionError):
    """A payload carried a money figure without its provenance."""


class ResponseInvariantMiddleware(BaseHTTPMiddleware):
    """Test-mode enforcement of the SPEC §3 response invariants.

    The check is mechanical rather than per-endpoint: it looks for the money
    shape anywhere in the response body, and if it finds one it requires the
    envelope at the top level. A new endpoint therefore cannot forget the
    stamps — it fails the moment it returns a number.
    """

    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not self.enabled or response.status_code >= 400:
            return response
        if response.headers.get("content-type", "").split(";")[0] != "application/json":
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            payload = None

        if payload is not None:
            money_paths = find_money_paths(payload)
            if money_paths and not (
                isinstance(payload, dict) and ENVELOPE_KEYS <= set(payload)
            ):
                missing = sorted(ENVELOPE_KEYS - set(payload if isinstance(payload, dict) else {}))
                raise ResponseInvariantViolation(
                    f"{request.method} {request.url.path} returned money at "
                    f"{money_paths[:3]} without the SPEC §3 envelope; missing {missing}"
                )

        return _rebuild(response, body)


def _rebuild(response, body: bytes):
    from starlette.responses import Response

    return Response(
        content=body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIdMiddleware",
    "ResponseInvariantMiddleware",
    "ResponseInvariantViolation",
    "find_money_paths",
]
