"""Thread confinement and the per-book lock (SPEC §2.2).

The failure this suite exists to catch is the proto's finding §4: the SQLite
ledger connection binds to the thread that created it, and a threadpooled
handler produces

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

The gate is "a concurrency test proving thread confinement (no sqlite
cross-thread error under parallel requests)". These tests assert both halves:
parallel requests all succeed, and the code path that would break confinement
raises a named error rather than a mystery.
"""

from __future__ import annotations

import asyncio
import inspect
import threading

import pytest

from cashkit_service.app import create_app
from cashkit_service.books import BookRuntime, ThreadConfinementError


async def test_parallel_reads_never_hit_a_cross_thread_error(book_client):
    responses = await asyncio.gather(
        *(book_client.get("/book/state") for _ in range(24))
    )
    assert [r.status_code for r in responses] == [200] * 24
    bodies = [r.json() for r in responses]
    # Every reader saw the same book at the same revision: the lock serialized
    # them, and nothing was half-written.
    assert len({b["revision"] for b in bodies}) == 1
    assert len({b["summary"]["closing_balance"]["exact"] for b in bodies}) == 1


async def test_parallel_reads_and_writes_serialize_per_book(seeded_client):
    """Reads and a mutating flow interleave without corrupting either."""
    async def edit(index: int):
        return await seeded_client.post(
            "/book/edits",
            json={
                "origin": "cell_edit",
                "ops": [
                    {
                        "op": "add_event",
                        "date": "2026-05-1{}".format(index % 10),
                        "amount": "-10.00",
                        "direction": "out",
                        "note": f"parallel {index}",
                    }
                ],
            },
        )

    reads = [seeded_client.get("/book/state") for _ in range(8)]
    writes = [edit(i) for i in range(8)]
    results = await asyncio.gather(*reads, *writes)
    assert all(r.status_code in (200, 201) for r in results), [r.status_code for r in results]


async def test_every_route_is_async(app):
    """A ``def`` handler would be run in a threadpool and break confinement."""
    offenders = [
        f"{route.methods} {route.path} -> {route.endpoint.__name__}"
        for route in app.routes
        if getattr(route, "endpoint", None) is not None
        and not inspect.iscoroutinefunction(route.endpoint)
        and not route.path.startswith(("/openapi", "/docs", "/redoc"))
    ]
    assert offenders == [], offenders


async def test_a_kit_reached_from_another_thread_raises_by_name(books_root, book_client):
    """The guard, exercised directly.

    Without it, using a kit off-thread surfaces as a SQLite error deep inside a
    query. With it, the service names the invariant that was broken.
    """
    runtime = BookRuntime(books_root)
    book_dir = next(p for p in books_root.iterdir() if p.is_dir())
    import uuid as _uuid

    book_id = _uuid.UUID(book_dir.name)
    async with runtime.acquire(book_id, book_dir) as kit:
        assert kit is not None

    handle = runtime._handles[book_id]
    failure: list[BaseException] = []

    def use_from_another_thread() -> None:
        try:
            handle.kit_for_this_thread()
        except BaseException as exc:  # noqa: BLE001 — the point is to capture it
            failure.append(exc)

    thread = threading.Thread(target=use_from_another_thread)
    thread.start()
    thread.join()
    runtime.close_all()

    assert failure and isinstance(failure[0], ThreadConfinementError)
    assert "never crosses threads" in str(failure[0])


async def test_the_lock_is_per_book_not_global(books_root):
    """Two different books do not block each other."""
    runtime = BookRuntime(books_root)
    import uuid as _uuid

    a, b = _uuid.uuid4(), _uuid.uuid4()
    handle_a = runtime._handle(a, books_root / "a")
    handle_b = runtime._handle(b, books_root / "b")
    assert handle_a.lock is not handle_b.lock

    await handle_a.lock.acquire()
    try:
        await asyncio.wait_for(handle_b.lock.acquire(), timeout=0.5)
        handle_b.lock.release()
    finally:
        handle_a.lock.release()


async def test_a_held_lock_times_out_as_busy_rather_than_hanging(books_root):
    runtime = BookRuntime(books_root, lock_timeout=0.05)
    import uuid as _uuid

    book_id = _uuid.uuid4()
    handle = runtime._handle(book_id, books_root / "held")
    await handle.lock.acquire()
    try:
        with pytest.raises(Exception) as excinfo:
            async with runtime.acquire(book_id, books_root / "held"):
                pass
        assert excinfo.value.detail["code"] == "BOOK_BUSY"
    finally:
        handle.lock.release()
