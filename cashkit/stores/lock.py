"""The single-writer lock (PRD §6.6, ADR-0010).

Write operations take an exclusive lockfile at ``.cashkit/lock``, created with
``O_EXCL`` so acquisition is atomic on every platform CashKit runs on. A second
concurrent writer receives ``CK-E013`` naming the holder; a lock whose holder is
dead is reclaimed with ``CK-W010``.

This is the "fails loudly, never merges silently" mechanism, and it deliberately
covers **all three stores** with one file: the config store, the ledger and the
frame store are one consistency domain (a commit stamps a watermark over the
ledger while serializing config), so a per-store lock would let a second writer
interleave between them.

Nothing here merges. There is no lock-stealing path, no timeout that silently
proceeds, and no branch that resolves a conflict: the only outcomes are
*acquired* and *refused with a diagnostic*.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from cashkit.model import Diagnostic
from cashkit.model.diagnostics import make_diagnostic

from .clock import Timestamp, wall_clock

__all__ = ["LOCK_FILENAME", "LockHolder", "WriterLock", "read_holder"]

#: Lock path relative to the book root.
LOCK_FILENAME = ".cashkit/lock"

_SEPARATOR = "\n"


@dataclass(frozen=True)
class LockHolder:
    """Who holds a lock: the writer's pid and when it took the lock."""

    pid: int
    since: str

    def render(self) -> str:
        """The lockfile's exact contents. No diagnostics."""
        return f"{self.pid}{_SEPARATOR}{self.since}{_SEPARATOR}"

    @classmethod
    def parse(cls, text: str) -> "LockHolder | None":
        """Read a lockfile body, or ``None`` when it is unreadable.

        A truncated or garbled lockfile is treated as *held by an unknown
        writer*, never as free: the caller refuses rather than guessing, which
        is the whole point of the lock. Produces no diagnostics.
        """
        parts = text.split(_SEPARATOR)
        if len(parts) < 2:
            return None
        try:
            pid = int(parts[0].strip())
        except ValueError:
            return None
        return cls(pid=pid, since=parts[1].strip())


def _process_alive(pid: int) -> bool:
    """True when a process with this pid exists.

    ``os.kill(pid, 0)`` is the portable liveness probe on POSIX; a
    ``PermissionError`` means the process exists but belongs to another user,
    which counts as alive. On a pid we cannot judge we answer *alive*, so an
    ambiguous lock is refused rather than stolen.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:  # pragma: no cover - platform-specific
        return exc.errno != errno.ESRCH
    return True


def read_holder(root: Path) -> LockHolder | None:
    """Return the current lock holder, or ``None`` when the lock is free.

    A lockfile that exists but cannot be parsed reports a holder with pid ``0``,
    which never looks alive and is therefore reclaimable — the file is corrupt,
    not a live writer. Produces no diagnostics.
    """
    path = root / LOCK_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    holder = LockHolder.parse(text)
    if holder is None:
        return LockHolder(pid=0, since="unreadable")
    return holder


class WriterLock:
    """An exclusive writer lock over one book root.

    Use it as a context manager::

        with WriterLock(root) as lock:
            if not lock.acquired:
                return ChangeReport(diagnostics=lock.diagnostics)
            ...

    :attr:`acquired` says whether the caller may write, and
    :attr:`diagnostics` carries ``CK-E013`` when it may not and ``CK-W010``
    when a dead holder's lock was reclaimed. Acquisition never raises on a
    contended lock — refusal is data, like every other failure a caller can
    provoke.
    """

    def __init__(self, root: str | Path, *, timestamp: Timestamp | None = None) -> None:
        self.root = Path(root)
        self.path = self.root / LOCK_FILENAME
        self._timestamp = timestamp
        self.acquired = False
        self.diagnostics: tuple[Diagnostic, ...] = ()

    # -- acquisition -------------------------------------------------------- #

    def acquire(self) -> bool:
        """Try to take the lock. Returns whether the caller may write.

        Diagnostics: ``CK-E013`` when another live writer holds it (naming the
        pid and when it was taken), ``CK-W010`` when a dead holder's lock was
        reclaimed (the caller then holds it). Never raises on contention.
        """
        notes: list[Diagnostic] = []
        if self._try_create(notes):
            self.acquired = True
            self.diagnostics = tuple(notes)
            return True

        holder = read_holder(self.root)
        if holder is not None and _process_alive(holder.pid):
            self.diagnostics = (
                make_diagnostic("CK-E013", pid=holder.pid, since=holder.since),
            )
            return False

        # The holder is gone. Reclaim by removing the stale file and retrying
        # the same atomic create — never by writing over it, so two reclaimers
        # racing still produce exactly one winner.
        notes.append(
            make_diagnostic(
                "CK-W010", pid=(holder.pid if holder is not None else 0)
            )
        )
        try:
            self.path.unlink()
        except FileNotFoundError:  # pragma: no cover - lost the race benignly
            pass
        if self._try_create(notes):
            self.acquired = True
            self.diagnostics = tuple(notes)
            return True

        winner = read_holder(self.root)
        self.diagnostics = (
            make_diagnostic(
                "CK-E013",
                pid=(winner.pid if winner is not None else 0),
                since=(winner.since if winner is not None else "unknown"),
            ),
        )
        return False

    def _try_create(self, notes: list[Diagnostic]) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = self._timestamp or wall_clock()
        body = LockHolder(pid=os.getpid(), since=stamp.isoformat()).render()
        try:
            handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
        return True

    def release(self) -> None:
        """Release the lock if this instance holds it. No diagnostics."""
        if not self.acquired:
            return
        self.acquired = False
        try:
            self.path.unlink()
        except FileNotFoundError:  # pragma: no cover - already reclaimed
            pass

    def __enter__(self) -> "WriterLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
