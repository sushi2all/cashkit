"""Sentry, and the one thing that must never reach it (SPEC §9, §11).

SPEC §11: *unhandled exceptions to Sentry (or equivalent) with the request_id
attached.* The request_id is what makes a Sentry event joinable to the turn
journal and to the access log — one report, one chain, `request_id → turn_id →
llm_calls.seq → proposal_id`.

**What is scrubbed, and why it is a `before_send` rather than a convention.**
An exception raised inside a turn has the user's own sentence in scope: the
instruction they typed, the compact book state, the model's request payload.
Sentry's default is to capture local variables and request bodies, and those
are the two places that data lives. SPEC §9 lists Sentry as a subprocessor for
*error tracking*; it is not a second copy of the financial data, and the
30-day payload retention of §4 would mean nothing if a stack frame carried the
same bytes to a different vendor on a different schedule.

So the client is configured to send **no** request body, **no** local
variables and **no** default PII, and `before_send` drops anything that got
through anyway. It is defence in depth on purpose: each of those is a setting,
and a setting is one upgrade away from a new default.

With no DSN the module does nothing at all — no import of the SDK, no client,
no network call. That is the state every test runs in.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import Settings

log = logging.getLogger("cashkit.observability")

#: Keys whose values never leave this process, wherever Sentry found them.
SCRUB_KEYS = frozenset(
    {
        "input_text", "text", "reply", "messages", "request", "response",
        "prompt", "snapshot", "email", "token", "authorization", "api_key",
        "llm_api_key", "password", "ops", "operations", "intents",
    }
)
_REDACTED = "[scrubbed by cashkit_service.observability]"


def scrub(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Sentry's ``before_send``: strip the payloads, keep the shape.

    It walks the whole event rather than a list of known locations, because
    the interesting fields move between SDK versions and a location list is a
    list that goes stale silently.
    """
    event.pop("request", None)
    for entry in event.get("exception", {}).get("values", []) or []:
        for frame in entry.get("stacktrace", {}).get("frames", []) or []:
            frame.pop("vars", None)
    return _walk(event)


def _walk(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: (_REDACTED if str(key).lower() in SCRUB_KEYS else _walk(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_walk(item) for item in node]
    return node


def install_sentry(settings: Settings) -> bool:
    """Install the Sentry client, if there is a DSN. Returns whether it did.

    A missing SDK is a warning and not a failure: the service's job is to be
    right about money, and it does not stop because an error tracker is absent.
    """
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - the extra is optional
        log.warning("CASHKIT_SENTRY_DSN is set but sentry-sdk is not installed")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        # No sampling of traces: a performance trace carries route parameters
        # and timings for every request, and SPEC §11's latency numbers already
        # come from metrics that are content-free by construction.
        traces_sample_rate=0.0,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        before_send=scrub,
    )
    return True


def attach_request_id(request_id: str) -> None:
    """Tag the current Sentry scope with the SPEC §11 chain's first link."""
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover
        return
    if sentry_sdk.get_client().dsn is None:
        return
    sentry_sdk.set_tag("request_id", request_id)


__all__ = ["SCRUB_KEYS", "attach_request_id", "install_sentry", "scrub"]
