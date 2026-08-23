"""The structured request log — SPEC §11's third observability layer.

One JSON line per request: ``request_id``, ``route``, ``method``, ``status``,
``duration_ms``. The correlation chain SPEC §11 asks for starts here, and the
turn journal (`agent/journal.py`) carries the same ``request_id`` into
``turns`` and ``llm_calls``, so a single user report walks
``request_id → turn_id → llm_calls.seq → proposal_id`` end to end.

**Two rules this module exists to keep, both mechanical rather than careful.**

*No user identifier, ever.* SPEC §11's hard rule is written about metric
labels; it is worth no less here, because a log line is the easier place to
leak one. So the line is built from a fixed field list, and the route is the
**matched template** — ``/proposals/{proposal_id}`` — never the requested
path, which carries the id. No email, no user id, no session token, no query
string, no body. :func:`assert_content_free` is the test hook that says so.

*The line is emitted for a failed request too.* A 500 that produced no log
line is the one request an operator most wants to see, so the timer is in a
``finally``.

In deployment the ``cashkit.request`` logger writes to a daily-rotating file
under ``CASHKIT_REQUEST_LOG_DIR`` and the rotation is the first half of the
90-day retention of SPEC §9; :func:`cashkit_service.retention.purge_request_logs`
is the second.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from .middleware import REQUEST_ID_HEADER

LOGGER_NAME = "cashkit.request"
log = logging.getLogger(LOGGER_NAME)

#: The whole vocabulary of a request-log line. Nothing else is ever written,
#: which is what makes "no user identifier" a property of the code rather than
#: a promise about the call sites.
FIELDS = ("ts", "request_id", "method", "route", "status", "duration_ms")

#: Substrings that must never appear in a log line. The route template is the
#: only place a path could smuggle one in.
FORBIDDEN_KEYS = ("email", "user_id", "token", "password", "authorization", "input_text")


def route_template(request: Request) -> str:
    """The matched route's template, or ``"<unmatched>"``.

    ``/proposals/{proposal_id}``, not ``/proposals/6f1c…``. A raw path is an
    identifier with a slash in front of it, and it would also make every
    per-endpoint latency series unaggregatable, which is the other half of why
    SPEC §11 wants templates.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "<unmatched>"


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Emit one JSON line per request, and one metric observation, whatever
    the request did.

    The two travel together because they carry the same two facts — the route
    template and the duration — and because a metric recorded somewhere else
    would eventually disagree with the log about which route a request was on.
    """

    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            line = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "request_id": getattr(request.state, "request_id", None)
                or request.headers.get(REQUEST_ID_HEADER)
                or "",
                "method": request.method,
                "route": route_template(request),
                "status": status,
                "duration_ms": duration_ms,
            }
            log.info(json.dumps(line, separators=(",", ":"), sort_keys=True))
            registry = getattr(request.app.state, "metrics", None)
            if registry is not None:
                from .metrics import status_class

                route, method = line["route"], request.method
                registry.inc(
                    "cashkit_http_requests_total",
                    route=route, method=method, status=status_class(status),
                )
                registry.observe(
                    "cashkit_http_request_duration_seconds",
                    duration_ms / 1000.0, route=route, method=method,
                )


def assert_content_free(line: dict[str, Any]) -> None:
    """Raise unless the line is exactly the SPEC §11 vocabulary.

    Used by the test suite, and by anyone adding a field who should have to
    argue for it. A new key fails; a value carrying a forbidden word fails.
    """
    extra = set(line) - set(FIELDS)
    if extra:
        raise AssertionError(f"request log line carries fields outside §11: {sorted(extra)}")
    blob = json.dumps(line).lower()
    for word in FORBIDDEN_KEYS:
        if word in blob:
            raise AssertionError(f"request log line contains {word!r}: {line}")


def install_file_handler(
    directory: Path, *, backup_count: int, level: int = logging.INFO
) -> logging.Handler:
    """Send ``cashkit.request`` to a daily-rotating file under ``directory``.

    ``backup_count`` is the retention in days and is passed from
    ``CASHKIT_REQUEST_LOG_RETENTION_DAYS``, so widening the policy and widening
    the rotation are the same edit. The handler writes the line and nothing
    else — no level, no logger name — because the line is already the record.
    """
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        directory / "request.log", when="midnight", utc=True, backupCount=backup_count
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(level)
    log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False
    return handler


__all__ = [
    "FIELDS",
    "LOGGER_NAME",
    "RequestLogMiddleware",
    "assert_content_free",
    "install_file_handler",
    "route_template",
]
