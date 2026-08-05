"""The revision store **interface** (ADR-0018, PRD §6.6).

PRD D10 says git is an implementation detail of persistence, never part of the
agent tool surface. That guarantees agents never run git commands; it does not
by itself guarantee the engine can run without git. This module is the seam that
does: everything above it — ``commit()``, ``history()``, ``at()``,
``diff_revisions()``, ``blame()`` — is written against
:class:`RevisionStore`, and **no git type appears in any signature here**. No
refs, no trees, no oids, no ``pygit2`` import.

Four operations, which is all §6.6 actually needs:

============================  =============================================
``write_revision``            record a state as a new revision
``list_revisions``            walk the history, optionally filtered to a path
``read_state``                read the book state at a revision
``diff_revisions``            compare two revisions
============================  =============================================

**History is linear.** There is no branch, no merge, no fork: v1 has no
branch-based propose-and-review workflow (PRD §7.3 defers it) and the single
writer lock (ADR-0010) makes concurrent divergence an error rather than a state
to reconcile. Keeping merge semantics out of the interface is what makes an
append-only SQLite revision table a plausible second implementation, which is
the test ADR-0018 sets for whether this is an interface or a git wrapper with
different nouns.

**A state is text, keyed by path.** :class:`RevisionState` is a mapping from a
book-root-relative path to the file's canonical text. That is deliberately the
least structured thing that still round-trips the §3.3 layout: the revision
store never learns what a Book is, and the config store never learns what a
revision is.

**Refs are opaque strings with a linear grammar.** ``"HEAD"``, ``"HEAD~<n>"``
(``n`` revisions back along the first-parent line) or a revision id.
:func:`parse_ref` is shared by every implementation so none of them invents a
dialect, and nothing in the grammar requires a git object database to answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

__all__ = [
    "HEAD",
    "RefKind",
    "Revision",
    "RevisionState",
    "RevisionStore",
    "StateDiff",
    "diff_states",
    "parse_ref",
]

#: The one well-known ref name. Everything else is a revision id or ``HEAD~n``.
HEAD = "HEAD"

_HEAD_BACK = re.compile(r"^HEAD(?:~(\d+))?$")

#: ``("head", n)`` — ``n`` revisions back from the tip — or ``("id", value)``.
RefKind = tuple[str, object]


def parse_ref(ref: str) -> tuple[RefKind | None, str | None]:
    """Parse an opaque revision ref.

    Returns ``(("head", n), None)`` for ``HEAD`` / ``HEAD~n``,
    ``(("id", ref), None)`` for anything else that could be a revision id, or
    ``(None, reason)`` when the string cannot address a revision at all.

    The grammar is deliberately linear-history-only: ``HEAD~n`` counts back
    along a single line of revisions, which an append-only revision table
    answers as cheaply as a commit graph does. Produces no diagnostics — the
    caller decides which catalogue code carries ``reason``.
    """
    text = ref.strip()
    if not text:
        return None, "revision ref is empty"
    match = _HEAD_BACK.match(text)
    if match is not None:
        back = int(match.group(1) or 0)
        return ("head", back), None
    if text.startswith(HEAD):
        return None, (
            f"ref {ref!r} is not a supported form; use 'HEAD', 'HEAD~<n>' or a "
            "revision id"
        )
    return ("id", text), None


@dataclass(frozen=True)
class Revision:
    """One recorded revision — the store's own vocabulary, not git's.

    ``id`` is an opaque, stable, content-derived identifier. Callers may print
    it, compare it and pass it back as a ref; they may never parse it. ``depth``
    is the number of revisions between this one and the root, which is what
    makes ``HEAD~n`` answerable without a commit graph.

    ``metadata`` carries what the SDK needs to judge reproducibility —
    ``engine_version``, the ledger watermark, the config schema version — as
    plain strings, so the store never has to understand any of it.
    """

    id: str
    parent: str | None
    message: str
    author: str
    timestamp: str
    depth: int
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        """A display-length prefix of the id. No diagnostics."""
        return self.id[:12]


@dataclass(frozen=True)
class RevisionState:
    """A whole book state as text, keyed by book-root-relative path.

    Paths use ``/`` separators on every platform, so a state written on one
    machine reads identically on another. Empty is a legitimate state (a book
    root with nothing in it yet).
    """

    files: Mapping[str, str] = field(default_factory=dict)

    def paths(self) -> tuple[str, ...]:
        """Every path in the state, sorted. No diagnostics."""
        return tuple(sorted(self.files))

    def get(self, path: str) -> str | None:
        """The text at ``path``, or ``None`` when absent. No diagnostics."""
        return self.files.get(path)

    def digest(self) -> str:
        """A content fingerprint over the whole state.

        Two states with the same digest are byte-identical file for file.
        Produced without any store involvement, so implementations can be
        compared against each other. No diagnostics.
        """
        import hashlib

        accumulator = hashlib.sha256()
        for path in self.paths():
            accumulator.update(path.encode("utf-8"))
            accumulator.update(b"\x1f")
            accumulator.update(self.files[path].encode("utf-8"))
            accumulator.update(b"\x1e")
        return accumulator.hexdigest()


@dataclass(frozen=True)
class StateDiff:
    """What changed between two revisions, path by path.

    This is the *textual* difference the store can see. A pure reformat never
    reaches here: state is written through the canonical emitter, so two
    semantically identical states are byte-identical and produce an empty diff
    (PRD §10, "diff_revisions() shows nothing for a pure reformat"). The SDK
    layers the semantic comparison on top for states that were written by
    something other than the emitter.
    """

    left: str
    right: str
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """True when the two revisions hold identical state. No diagnostics."""
        return not (self.added or self.removed or self.changed)


def diff_states(
    left: RevisionState,
    right: RevisionState,
    *,
    left_ref: str = "",
    right_ref: str = "",
) -> StateDiff:
    """Compare two states path by path.

    Shared by every implementation so "what changed" cannot mean two things.
    Returns a :class:`StateDiff`; produces no diagnostics.
    """
    left_paths = set(left.files)
    right_paths = set(right.files)
    return StateDiff(
        left=left_ref,
        right=right_ref,
        added=tuple(sorted(right_paths - left_paths)),
        removed=tuple(sorted(left_paths - right_paths)),
        changed=tuple(
            sorted(
                path
                for path in left_paths & right_paths
                if left.files[path] != right.files[path]
            )
        ),
    )


@runtime_checkable
class RevisionStore(Protocol):
    """What the SDK needs from a revision history, and nothing more.

    Every method takes and returns the types defined above. A second
    implementation — the obvious candidate being an append-only SQLite revision
    table with one row per revision and one blob row per path — satisfies this
    protocol without any caller changing, which is the test ADR-0018 sets.
    """

    def write_revision(
        self,
        state: RevisionState,
        *,
        message: str,
        author: str,
        metadata: Mapping[str, str] | None = None,
        timestamp: object | None = None,
    ) -> Revision | None:
        """Record ``state`` as a new revision.

        Returns the new :class:`Revision`, or ``None`` when the state is
        identical to the current tip — an unchanged tree is not a revision
        (PRD §6.6: ``commit()`` returns ``None`` if the tree is unchanged).
        """
        ...  # pragma: no cover - protocol

    def head(self) -> Revision | None:
        """The tip revision, or ``None`` for an empty history."""
        ...  # pragma: no cover - protocol

    def resolve(self, ref: str) -> tuple[Revision | None, str | None]:
        """Resolve an opaque ref to a revision, or report why it does not."""
        ...  # pragma: no cover - protocol

    def list_revisions(
        self, *, limit: int = 50, path: str | None = None
    ) -> list[Revision]:
        """Revisions newest first, optionally only those changing ``path``."""
        ...  # pragma: no cover - protocol

    def read_state(self, ref: str) -> tuple[RevisionState | None, str | None]:
        """The book state at ``ref``, or ``(None, reason)``."""
        ...  # pragma: no cover - protocol

    def diff_revisions(
        self, left: str, right: str
    ) -> tuple[StateDiff | None, str | None]:
        """Compare two revisions, or report why one of them does not resolve."""
        ...  # pragma: no cover - protocol
