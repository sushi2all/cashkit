"""The in-process import-job registry and its SSE fan-out.

An import takes tens of seconds and streams its progress, so the work outlives
the request that started it. This module is the small amount of machinery that
needs: one background task per job, a replayable buffer of everything it has
emitted, and a queue per listener.

Three properties it is built for:

* **A late listener misses nothing.** The stream replays the buffer before it
  waits for anything new, so opening the stream after the job finished still
  yields the whole run and then closes. That is also the reconnection story:
  there is no separate polling route to keep in step with this one.
* **The task owns its own database connection.** The request's connection is
  closed the moment `POST /import` answers; a job that wrote through it would
  fail at its first row.
* **The event loop is the only thread.** The task runs on it, like every
  route, so the kit stays thread-confined (SPEC §2.2). The queues are
  ``asyncio`` queues and need no locking beyond that.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .checks import ReconciliationReport

#: Emitted every this many seconds while nothing else is, so an idle proxy does
#: not decide the connection is dead during a long model call.
HEARTBEAT_SECONDS = 15.0


@dataclass
class ImportJob:
    """One running (or finished) import."""

    id: uuid.UUID
    book_id: uuid.UUID
    user_id: uuid.UUID
    filename: str
    events: list[dict[str, Any]] = field(default_factory=list)
    listeners: set[asyncio.Queue] = field(default_factory=set)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    report: ReconciliationReport | None = None
    proposal_id: uuid.UUID | None = None
    status: str = "running"

    def emit(self, event: dict[str, Any]) -> None:
        """Record one progress event and hand it to every listener."""
        self.events.append(event)
        for queue in list(self.listeners):
            queue.put_nowait(event)

    def finish(self, status: str) -> None:
        self.status = status
        self.finished.set()
        for queue in list(self.listeners):
            queue.put_nowait(None)


class ImportRegistry:
    """Every import this process is running, keyed by job id."""

    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, ImportJob] = {}

    def create(self, *, book_id: uuid.UUID, user_id: uuid.UUID, filename: str) -> ImportJob:
        job = ImportJob(id=uuid.uuid4(), book_id=book_id, user_id=user_id, filename=filename)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: uuid.UUID) -> ImportJob | None:
        return self._jobs.get(job_id)

    async def stream(self, job: ImportJob):
        """Yield SSE frames: the buffer first, then whatever arrives next.

        The buffer is copied and the listener registered with no ``await``
        between the two, so on a single-threaded event loop no event can slip
        through the gap.
        """
        queue: asyncio.Queue = asyncio.Queue()
        backlog = list(job.events)
        done = job.finished.is_set()
        job.listeners.add(queue)
        try:
            for event in backlog:
                yield frame(event)
            if done:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    return
                yield frame(event)
        finally:
            job.listeners.discard(queue)

    async def close_all(self) -> None:
        """Cancel every running import (application shutdown)."""
        for job in list(self._jobs.values()):
            if job.task is not None and not job.task.done():
                job.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await job.task
        self._jobs.clear()


def frame(event: dict[str, Any]) -> str:
    """One SSE frame. The stage is the event name, so a client may listen by name."""
    stage = str(event.get("stage", "progress"))
    return f"event: {stage}\ndata: {json.dumps(event, default=str)}\n\n"


__all__ = ["HEARTBEAT_SECONDS", "ImportJob", "ImportRegistry", "frame"]
