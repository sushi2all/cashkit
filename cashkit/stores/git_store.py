"""The git implementation of :class:`~cashkit.stores.revisions.RevisionStore`.

**``pygit2`` is imported here and nowhere else in the package** (ADR-0018,
asserted structurally by ``tests/test_revision_store.py``). Everything above
this module speaks the interface in ``revisions.py``, so replacing git with an
append-only SQLite revision table changes this file and no other.

Three rules, from non-negotiable constraint 7:

* **No worktree, no index, no checkout.** Trees are built in the object
  database from the state's text and committed by writing a ref. ``repo.index``
  and ``repo.checkout`` appear nowhere below — an agent can never leave the
  repository in a weird state, because nothing here has a state to leave it in.
* **No shelling out.** libgit2 through ``pygit2``, never a ``git`` subprocess.
* **No git noun escapes.** Oids, refs and trees stay inside this module;
  callers see :class:`~cashkit.stores.revisions.Revision` ids, which are opaque
  strings they may print and pass back but never parse.

The repository lives at the book root so a human can still ``git log`` it —
ADR-0018 counts that escape hatch as one of the things git actually buys — but
CashKit itself never reads the working tree through git.

Revision metadata (engine version, ledger watermark, config schema version)
rides in the commit message as ``cashkit-<key>: <value>`` trailers, separated
from the human message by a blank line and stripped back off on read. Git has no
other place for structured per-commit data that survives a clone, and a trailer
is legible to a human reading ``git log`` — which is the point of keeping git
underneath at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pygit2

from .clock import Timestamp, wall_clock
from .revisions import Revision, RevisionState, StateDiff, diff_states, parse_ref

__all__ = ["GitRevisionStore", "METADATA_PREFIX"]

#: Commit-message trailer prefix carrying :attr:`Revision.metadata`.
METADATA_PREFIX = "cashkit-"

_DEFAULT_BRANCH = "main"
_HEAD_REF = f"refs/heads/{_DEFAULT_BRANCH}"
_BLOB_MODE = pygit2.enums.FileMode.BLOB
_TREE_MODE = pygit2.enums.FileMode.TREE


class GitRevisionStore:
    """A :class:`RevisionStore` over a git object database.

    Construct it on a book root; the repository is created on first use. The
    history is linear by construction — every commit has exactly one parent and
    nothing here creates a branch or a merge — which is what keeps the
    interface honest about being implementable without a commit graph.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._repo = self._open_or_init()

    def _open_or_init(self) -> pygit2.Repository:
        git_dir = self.root / ".git"
        if git_dir.exists():
            return pygit2.Repository(str(git_dir))
        return pygit2.init_repository(
            str(self.root), bare=False, initial_head=_DEFAULT_BRANCH
        )

    # -- reading ------------------------------------------------------------ #

    def _head_commit(self) -> "pygit2.Commit | None":
        try:
            reference = self._repo.references[_HEAD_REF]
        except KeyError:
            return None
        return self._repo[reference.target]

    def _first_parent(self, commit: "pygit2.Commit") -> "pygit2.Commit | None":
        parents = commit.parent_ids
        if not parents:
            return None
        return self._repo[parents[0]]

    def _walk(self, commit: "pygit2.Commit | None"):
        """Yield ``commit`` and its first-parent ancestors, newest first."""
        current = commit
        while current is not None:
            yield current
            current = self._first_parent(current)

    def _depth_of(self, commit: "pygit2.Commit") -> int:
        depth = 0
        current = self._first_parent(commit)
        while current is not None:
            depth += 1
            current = self._first_parent(current)
        return depth

    def _revision_of(self, commit: "pygit2.Commit") -> Revision:
        message, metadata = _split_message(commit.message)
        parents = commit.parent_ids
        return Revision(
            id=str(commit.id),
            parent=str(parents[0]) if parents else None,
            message=message,
            author=commit.author.name,
            timestamp=_iso_of(commit.author.time, commit.author.offset),
            depth=self._depth_of(commit),
            metadata=metadata,
        )

    def head(self) -> Revision | None:
        """The tip revision, or ``None`` for a history with no commits.

        Produces no diagnostics.
        """
        commit = self._head_commit()
        return None if commit is None else self._revision_of(commit)

    def resolve(self, ref: str) -> tuple[Revision | None, str | None]:
        """Resolve an opaque ref to a :class:`Revision`.

        Accepts ``"HEAD"``, ``"HEAD~<n>"`` and a revision id (full or a unique
        prefix). Returns ``(revision, None)`` or ``(None, reason)``; produces no
        diagnostics — the SDK turns ``reason`` into a catalogue code.
        """
        commit, reason = self._resolve_commit(ref)
        if commit is None:
            return None, reason
        return self._revision_of(commit), None

    def _resolve_commit(self, ref: str) -> tuple["pygit2.Commit | None", str | None]:
        parsed, reason = parse_ref(ref)
        if parsed is None:
            return None, reason
        kind, value = parsed
        head = self._head_commit()
        if kind == "head":
            if head is None:
                return None, "the history has no revisions yet"
            current = head
            for step in range(int(value)):  # type: ignore[arg-type]
                parent = self._first_parent(current)
                if parent is None:
                    return None, (
                        f"HEAD~{value} reaches past the first revision; the history "
                        f"is {step + 1} revision(s) deep"
                    )
                current = parent
            return current, None

        text = str(value)
        if not _is_hex(text):
            return None, f"revision id {text!r} is not a valid identifier"
        matches = [
            commit for commit in self._walk(head) if str(commit.id).startswith(text)
        ]
        if not matches:
            return None, f"no revision with id {text!r} exists in this history"
        if len(matches) > 1:
            return None, (
                f"revision id {text!r} is ambiguous; it matches "
                f"{len(matches)} revisions"
            )
        return matches[0], None

    def list_revisions(
        self, *, limit: int = 50, path: str | None = None
    ) -> list[Revision]:
        """Revisions newest first, at most ``limit`` of them.

        ``path`` filters to revisions in which that file's content changed —
        appearing, disappearing or differing from the first parent — which is
        what ``history(item=...)`` and ``blame()`` are built on. Produces no
        diagnostics.
        """
        out: list[Revision] = []
        for commit in self._walk(self._head_commit()):
            if path is not None and not self._touches(commit, path):
                continue
            out.append(self._revision_of(commit))
            if len(out) >= limit:
                break
        return out

    def _touches(self, commit: "pygit2.Commit", path: str) -> bool:
        here = self._blob_id(commit, path)
        parent = self._first_parent(commit)
        there = None if parent is None else self._blob_id(parent, path)
        return here != there

    def _blob_id(self, commit: "pygit2.Commit", path: str) -> str | None:
        node: object = commit.tree
        for part in path.split("/"):
            if not isinstance(node, pygit2.Tree):
                return None
            try:
                node = node[part]
            except KeyError:
                return None
            if isinstance(node, pygit2.Object) and node.type_str == "tree":
                node = self._repo[node.id]
        if isinstance(node, pygit2.Tree):
            return None
        return str(node.id)  # type: ignore[union-attr]

    def read_state(self, ref: str) -> tuple[RevisionState | None, str | None]:
        """The whole book state at ``ref``, as path -> text.

        Returns ``(state, None)`` or ``(None, reason)``. Blobs are decoded as
        UTF-8, which is what the canonical emitter writes; a blob that is not
        valid UTF-8 could not have come from CashKit and reports a reason rather
        than raising. Produces no diagnostics.
        """
        commit, reason = self._resolve_commit(ref)
        if commit is None:
            return None, reason
        files: dict[str, str] = {}
        problem = self._collect(commit.tree, "", files)
        if problem is not None:
            return None, problem
        return RevisionState(files=files), None

    def _collect(self, tree: "pygit2.Tree", prefix: str, out: dict[str, str]) -> str | None:
        for entry in tree:
            path = f"{prefix}{entry.name}"
            obj = self._repo[entry.id]
            if isinstance(obj, pygit2.Tree):
                problem = self._collect(obj, f"{path}/", out)
                if problem is not None:
                    return problem
                continue
            try:
                out[path] = obj.data.decode("utf-8")
            except UnicodeDecodeError:
                return f"file {path!r} at this revision is not UTF-8 text"
        return None

    def diff_revisions(
        self, left: str, right: str
    ) -> tuple[StateDiff | None, str | None]:
        """Compare two revisions path by path.

        Returns ``(diff, None)`` or ``(None, reason)`` when either ref fails to
        resolve. A pure reformat produces an empty diff, because every state
        CashKit writes goes through the canonical emitter — see
        :meth:`~cashkit.sdk.kit.CashKit.diff_revisions` for the semantic
        comparison that covers states written by anything else. Produces no
        diagnostics.
        """
        left_state, reason = self.read_state(left)
        if left_state is None:
            return None, reason
        right_state, reason = self.read_state(right)
        if right_state is None:
            return None, reason
        return diff_states(left_state, right_state, left_ref=left, right_ref=right), None

    # -- writing ------------------------------------------------------------ #

    def write_revision(
        self,
        state: RevisionState,
        *,
        message: str,
        author: str = "agent",
        metadata: Mapping[str, str] | None = None,
        timestamp: Timestamp | None = None,
    ) -> Revision | None:
        """Record ``state`` as a new revision, or return ``None`` if unchanged.

        The tree is built in the object database from the state's text; nothing
        is staged, nothing is checked out. When the resulting tree is identical
        to the tip's, no commit is made and ``None`` comes back — an unchanged
        tree is not a revision (PRD §6.6).

        ``timestamp`` is injectable so a fixture repository is byte-reproducible;
        it defaults to the wall clock, which a commit is allowed to read because
        a commit is an authored artifact, not an evaluation. Produces no
        diagnostics; raises ``pygit2.GitError`` only on a genuinely broken
        object database (programmer error / corrupt store).
        """
        tree_id = self._build_tree(state)
        head = self._head_commit()
        if head is not None and head.tree_id == tree_id:
            return None

        stamp = timestamp or wall_clock()
        signature = pygit2.Signature(
            name=author or "agent",
            email=_email_for(author),
            time=int(stamp.timestamp()),
            offset=0,
        )
        full_message = _join_message(message, metadata or {})
        commit_id = self._repo.create_commit(
            _HEAD_REF,
            signature,
            signature,
            full_message,
            tree_id,
            [head.id] if head is not None else [],
        )
        # Point the symbolic HEAD at the branch the first time, so a human
        # running `git log` in the book root sees the history without arguments.
        self._repo.set_head(_HEAD_REF)
        return self._revision_of(self._repo[commit_id])

    def _build_tree(self, state: RevisionState) -> "pygit2.Oid":
        """Assemble a tree from path -> text, deepest directories first."""
        nested: dict[str, object] = {}
        for path in state.paths():
            parts = path.split("/")
            cursor = nested
            for part in parts[:-1]:
                child = cursor.get(part)
                if not isinstance(child, dict):
                    child = {}
                    cursor[part] = child
                cursor = child
            cursor[parts[-1]] = state.files[path]
        return self._write_dir(nested)

    def _write_dir(self, node: Mapping[str, object]) -> "pygit2.Oid":
        builder = self._repo.TreeBuilder()
        for name in sorted(node):
            value = node[name]
            if isinstance(value, dict):
                builder.insert(name, self._write_dir(value), _TREE_MODE)
            else:
                blob_id = self._repo.create_blob(str(value).encode("utf-8"))
                builder.insert(name, blob_id, _BLOB_MODE)
        return builder.write()


# --------------------------------------------------------------------------- #
# Message trailers
# --------------------------------------------------------------------------- #


def _join_message(message: str, metadata: Mapping[str, str]) -> str:
    body = message.rstrip("\n")
    if not metadata:
        return body + "\n"
    trailers = "\n".join(
        f"{METADATA_PREFIX}{key}: {metadata[key]}" for key in sorted(metadata)
    )
    return f"{body}\n\n{trailers}\n"


def _split_message(raw: str) -> tuple[str, dict[str, str]]:
    """Split a stored message back into the human text and the metadata."""
    lines = raw.rstrip("\n").split("\n")
    metadata: dict[str, str] = {}
    end = len(lines)
    while end > 0:
        line = lines[end - 1]
        if not line.startswith(METADATA_PREFIX) or ": " not in line:
            break
        key, _, value = line[len(METADATA_PREFIX) :].partition(": ")
        metadata[key] = value
        end -= 1
    body = "\n".join(lines[:end]).rstrip("\n")
    return body, metadata


def _email_for(author: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in (author or "agent")).strip("-")
    return f"{slug or 'agent'}@cashkit.local"


def _iso_of(seconds: int, offset_minutes: int) -> str:
    """Format a commit time as ISO 8601 without reading the clock."""
    import datetime

    zone = datetime.timezone(datetime.timedelta(minutes=offset_minutes))
    return datetime.datetime.fromtimestamp(seconds, tz=zone).isoformat()


def _is_hex(text: str) -> bool:
    return bool(text) and len(text) <= 40 and all(ch in "0123456789abcdef" for ch in text.lower())
