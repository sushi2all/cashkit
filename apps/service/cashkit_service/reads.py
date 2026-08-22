"""Shared plumbing for the engine-wrapper read endpoints.

One place decides which scenario a read runs against, and one place builds the
provenance envelope, so no endpoint can quietly disagree with another about
either.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date

from cashkit.sdk import CashKit, RunRef
from fastapi import Request

from .clock import Clock
from .deps import BookRow, get_books
from .envelope import Envelope, envelope
from .errors import bad_request, not_found


@dataclass
class ReadContext:
    """A kit held under its book lock, with everything a payload envelope needs."""

    kit: CashKit
    scenario: str
    as_of: date
    revision: str | None
    clean: bool
    request_id: str

    def run(self) -> RunRef:
        return self.kit.run(self.scenario)

    def envelope(self, *, pending: bool = False) -> Envelope:
        return envelope(
            as_of=self.as_of,
            scenario=self.scenario,
            revision=self.revision,
            clean=self.clean,
            request_id=self.request_id,
            pending=pending,
        )


def resolve_scenario(kit: CashKit, book: BookRow, requested: str | None) -> str:
    """Which scenario this read runs against.

    ``books.active_scenario`` is app state, effective book-wide; an explicit
    ``?scenario=`` overrides it for this read only (SPEC §2.4).
    """
    scenario = requested or book.active_scenario
    if scenario not in kit.scenarios.scenarios:
        raise not_found("NO_SCENARIO", f"No scenario named {scenario!r} in this book.")
    return scenario


@asynccontextmanager
async def read_context(
    request: Request, book: BookRow, clock: Clock, scenario: str | None = None
) -> AsyncIterator[ReadContext]:
    """Acquire the book lock and yield a context for one read."""
    runtime = get_books(request)
    async with runtime.acquire(book.id, book.storage_path) as kit:
        state = kit.status()
        yield ReadContext(
            kit=kit,
            scenario=resolve_scenario(kit, book, scenario),
            # as_of is host-filled, from the injectable clock, never from the
            # engine and never from a caller-supplied value (ADR-0019 rule 2).
            as_of=clock.today(),
            revision=state.revision,
            clean=state.clean,
            request_id=getattr(request.state, "request_id", ""),
        )


def parse_period(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise bad_request("BAD_PERIOD", f"{value!r} is not an ISO date.") from exc
