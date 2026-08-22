"""Book runtime: one kit, one thread, one lock per book (SPEC §2.2).

The proto found it the hard way (`km/notes/2026-08-22-proto-webapp-findings.md`
§4): the SQLite ledger connection binds to the thread that created it, and
FastAPI's default threadpool broke it immediately. The rule that follows is
structural, not stylistic:

* every endpoint is ``async def``, so it runs on the event-loop thread;
* a kit is opened, used and closed on that same thread, and
  :meth:`BookRuntime.acquire` asserts it — a kit that ever crossed a thread
  raises here instead of producing a confusing SQLite error later;
* one :class:`asyncio.Lock` per book id serializes writers, which is what
  ADR-0027's single-writer-per-book assumption needs on a hosted service.

A model call must never happen while the lock is held (SPEC §2.2). Nothing in
this package calls a model at all — S1 is model-free — and S2 inherits the rule.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from cashkit.model import Grain, PeriodRange
from cashkit.sdk import BASE_SCENARIO, CashKit, create_book

from .errors import busy, not_found

#: Files that are not part of the book's logical state. `.git` is the revision
#: store's own plumbing and churns on every commit; the lockfile is transient.
_FINGERPRINT_SKIP = (".git/", ".cashkit/lock")
#: The ledger is a SQLite file whose bytes move without its rows moving, so it
#: is fingerprinted through its rows instead of its pages.
_LEDGER_FILES = ("ledger.sqlite", "ledger.sqlite-journal", "ledger.sqlite-wal")


class ThreadConfinementError(RuntimeError):
    """A kit was reached from a thread other than the one that opened it."""


@dataclass
class BookHandle:
    """One book's kit, its lock, and the thread they belong to."""

    book_id: uuid.UUID
    root: Path
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kit: CashKit | None = None
    owner_thread: int | None = None

    def _open(self) -> CashKit:
        kit, diagnostics = CashKit.open(self.root)
        if kit is None:
            raise not_found(
                "BOOK_UNREADABLE",
                "; ".join(d.message for d in diagnostics) or "The book could not be opened.",
            )
        self.kit = kit
        self.owner_thread = threading.get_ident()
        return kit

    def kit_for_this_thread(self) -> CashKit:
        here = threading.get_ident()
        if self.kit is None:
            return self._open()
        if self.owner_thread != here:
            raise ThreadConfinementError(
                f"book {self.book_id} was opened on thread {self.owner_thread} and is "
                f"being used from thread {here}; a kit instance never crosses threads "
                "(SPEC §2.2)"
            )
        return self.kit

    def close(self) -> None:
        kit, self.kit, self.owner_thread = self.kit, None, None
        if kit is None:
            return
        ledger = getattr(kit, "ledger", None)
        if ledger is not None and hasattr(ledger, "close"):
            ledger.close()
        frames = getattr(kit, "frames", None)
        if frames is not None and hasattr(frames, "close"):
            frames.close()


class BookRuntime:
    """The per-process registry of open books."""

    def __init__(self, root: Path, *, lock_timeout: float = 30.0) -> None:
        self.root = Path(root)
        self.lock_timeout = lock_timeout
        self._handles: dict[uuid.UUID, BookHandle] = {}

    def storage_path(self, book_id: uuid.UUID) -> Path:
        return self.root / str(book_id)

    def _handle(self, book_id: uuid.UUID, storage_path: str | Path) -> BookHandle:
        handle = self._handles.get(book_id)
        if handle is None:
            handle = BookHandle(book_id=book_id, root=Path(storage_path))
            self._handles[book_id] = handle
        return handle

    @asynccontextmanager
    async def acquire(self, book_id: uuid.UUID, storage_path: str | Path) -> AsyncIterator[CashKit]:
        """Hold the book's lock and yield its kit, on this thread."""
        handle = self._handle(book_id, storage_path)
        try:
            await asyncio.wait_for(handle.lock.acquire(), timeout=self.lock_timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:  # noqa: UP041
            raise busy() from exc
        try:
            yield handle.kit_for_this_thread()
        finally:
            handle.lock.release()

    async def create(
        self,
        *,
        book_id: uuid.UUID,
        horizon: PeriodRange,
        opening_balance: Decimal,
        grain: Grain,
        cutover: date | None,
        params: dict[str, Decimal] | None,
        calendar: str | None,
    ) -> tuple[Path, list]:
        """Create the book directory and give it its first revision.

        ``create_book`` writes the layout and commits nothing, which would leave
        every payload with a null ``revision``. An empty book is a real state
        worth naming, so the service commits it immediately (D-MLP-11).
        """
        path = self.storage_path(book_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        ref = create_book(
            path,
            id=f"book_{str(book_id).replace('-', '')[:16]}",
            horizon=horizon,
            opening_balance=opening_balance,
            grain=grain,
            cutover=cutover,
            params=params or {},
            calendar=calendar,
        )
        diagnostics = list(ref.diagnostics)
        if ref.kit is None:
            return path, diagnostics
        report = ref.kit.commit("book created")
        diagnostics.extend(report.diagnostics)
        handle = self._handle(book_id, path)
        handle.kit = ref.kit
        handle.owner_thread = threading.get_ident()
        return path, diagnostics

    async def forget(self, book_id: uuid.UUID, *, delete_storage: bool = False) -> None:
        """Drop the cached kit, optionally erasing the directory (``DELETE /me``)."""
        handle = self._handles.pop(book_id, None)
        path = handle.root if handle is not None else self.storage_path(book_id)
        if handle is not None:
            handle.close()
        if delete_storage:
            shutil.rmtree(path, ignore_errors=True)

    def close_all(self) -> None:
        for handle in list(self._handles.values()):
            handle.close()
        self._handles.clear()


def ledger_digest(kit: CashKit) -> str:
    """A content digest of every ledger row, tombstones included.

    Voided and correcting rows must move the fingerprint: a correction changes
    what the book computes, and a proposal dry-run against the pre-correction
    ledger is stale (SPEC §2.5).
    """
    table = kit.query_events(include_voided=True)
    hasher = hashlib.sha256()
    for row in table.rows:
        hasher.update(repr(row).encode("utf-8"))
        hasher.update(b"\x1e")
    return hasher.hexdigest()


def overlay_fingerprint(kit: CashKit) -> str:
    """The working-overlay fingerprint a proposal is dry-run against (SPEC §2.5).

    Two halves, because a book has two stores. The configuration half is the
    revision store's own canonical digest — the same bytes a commit would
    record, so a reformat that changes nothing semantic does not invalidate a
    proposal. The ledger half is the row digest above.
    """
    from cashkit.stores.revisions import build_state

    config = build_state(kit.config_state()).digest()
    return hashlib.sha256(f"{config}:{ledger_digest(kit)}".encode("utf-8")).hexdigest()


def head_revision(kit: CashKit) -> str | None:
    """The revision every payload and every proposal is anchored to."""
    return kit.status().revision


@contextmanager
def scratch_copy(kit: CashKit, root: Path) -> Iterator[CashKit]:
    """Open a throwaway copy of the book, for a dry-run (SPEC §2.3 step 2).

    A dry-run must be able to contain *any* operation, and some of them are not
    scenario-scoped: the ledger is shared by every scenario and is append-only,
    and horizon and opening balance are book-level. A scenario overlay cannot
    isolate those, so the isolation is a copy of the whole directory. The
    original book is never touched, which is what makes "the card the user
    confirms is always the card that applies" checkable rather than hoped for.
    """
    kit.save()  # the copy must see the working overlay, not the last save
    tmp = Path(tempfile.mkdtemp(prefix="cashkit-dryrun-"))
    target = tmp / "book"
    try:
        shutil.copytree(kit.root, target, symlinks=False)
        scratch, diagnostics = CashKit.open(target)
        if scratch is None:
            raise not_found(
                "BOOK_UNREADABLE",
                "; ".join(d.message for d in diagnostics) or "The book copy could not be opened.",
            )
        try:
            yield scratch
        finally:
            ledger = getattr(scratch, "ledger", None)
            if ledger is not None and hasattr(ledger, "close"):
                ledger.close()
            frames = getattr(scratch, "frames", None)
            if frames is not None and hasattr(frames, "close"):
                frames.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


__all__ = [
    "BASE_SCENARIO",
    "BookHandle",
    "BookRuntime",
    "ThreadConfinementError",
    "head_revision",
    "ledger_digest",
    "overlay_fingerprint",
    "scratch_copy",
]
