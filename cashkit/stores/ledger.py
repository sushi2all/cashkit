"""The ledger store: events, tombstones and corrections in SQLite (PRD §6.2).

The ledger is **physically append-only**. There is one table, ``ledger_entries``,
and every operation adds rows to it: an event is an ``event`` entry, voiding one
is a ``void`` entry naming it, correcting one is a ``void`` entry plus a new
``event`` entry carrying ``corrects``. No ``DELETE`` and no ``UPDATE`` of an
entry exists anywhere in this module — the ADR-0006 watermark is a sequence
number over this log, and ``at(ref)`` truncation is ``seq <= max_rowid``. A
destructive edit would silently rewrite history and break the §1 reproducibility
guarantee.

``UNIQUE(source, ext_id)`` is the only thing preventing double-counted actuals on
re-import (PRD §4.3), so it is a database constraint, not a convention.

Storage stays swappable: :class:`LedgerStore` is the protocol the rest of the
system codes against, and ``sqlite3`` is imported here and nowhere else.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from cashkit.model import (
    ChangeReport,
    Diagnostic,
    Event,
    EventId,
    EventRef,
    ImportReport,
    Watermark,
    make_diagnostic,
    to_canonical_yaml,
)

__all__ = ["LedgerStore", "SqliteLedger", "SCHEMA_VERSION"]

#: Ledger schema generation. Bumped only by a migration (PRD §8.5).
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL CHECK (kind IN ('event', 'void')),
    event_id  TEXT NOT NULL,
    source    TEXT,
    ext_id    TEXT,
    event_date TEXT,
    amount    TEXT,
    status    TEXT,
    item      TEXT,
    currency  TEXT,
    corrects  TEXT,
    note      TEXT,
    digest    TEXT,
    payload   TEXT
);

-- The idempotency key. NULLs compare distinct in SQLite, so keyless rows
-- (add_event) never collide; import_events refuses keyless rows outright.
CREATE UNIQUE INDEX IF NOT EXISTS ledger_source_ext
    ON ledger_entries (source, ext_id)
    WHERE kind = 'event' AND source IS NOT NULL AND ext_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ledger_event_id
    ON ledger_entries (event_id) WHERE kind = 'event';

CREATE INDEX IF NOT EXISTS ledger_void_target
    ON ledger_entries (event_id) WHERE kind = 'void';

CREATE TABLE IF NOT EXISTS import_log (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    at_seq    INTEGER NOT NULL,
    source    TEXT NOT NULL,
    considered INTEGER NOT NULL,
    inserted  INTEGER NOT NULL,
    skipped   INTEGER NOT NULL,
    conflicted INTEGER NOT NULL,
    aborted   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@runtime_checkable
class LedgerStore(Protocol):
    """What the rest of CashKit needs from a ledger, and nothing more.

    Every method returns data — :class:`Event` models, reports, diagnostics —
    and none exposes the backend. A second implementation (Postgres, in-memory)
    satisfies this protocol without any caller changing.
    """

    def add_event(self, event: Event) -> ChangeReport:
        """Append one event. Returns a :class:`ChangeReport`."""

    def import_events(self, rows: Iterable[Event], source: str) -> ImportReport:
        """Idempotent batch import keyed on ``(source, ext_id)``."""

    def void_event(self, event_id: EventId, note: str) -> ChangeReport:
        """Tombstone a committed/forecast event."""

    def correct_event(
        self, event_id: EventId, corrected: Event, note: str
    ) -> ChangeReport:
        """Tombstone an event and append its correction, atomically."""

    def query_events(
        self,
        *,
        where: str | None = None,
        since: date | None = None,
        until: date | None = None,
        watermark: Watermark | None = None,
        include_voided: bool = False,
    ) -> list[Event]:
        """Return live events, optionally truncated to a watermark."""

    def facts(self, watermark: Watermark | None = None) -> list[Event]:
        """Every live event, in ledger order — the event side of the fact union."""

    def watermark(self) -> Watermark:
        """The current ADR-0006 watermark."""


# --------------------------------------------------------------------------- #
# Payload identity
# --------------------------------------------------------------------------- #

#: Placeholder substituted for the row's ``id`` before fingerprinting. Identity
#: is ``(source, ext_id)`` — the PRD says so explicitly — so the row's ``id`` is
#: the ledger's business, not the source system's. A source that regenerates
#: surrogate ids on every export would otherwise turn every re-import into a
#: conflict, which is exactly the idempotency the Phase 5 gate demands.
_ID_PLACEHOLDER = "-"


def payload_digest(event: Event) -> str:
    """Canonical fingerprint of an event's payload, excluding its ``id``.

    Uses the canonical YAML emitter, so the fingerprint inherits its
    determinism: Decimals keep their exact spelling, ``None`` is distinguishable
    from absent, and no float ever enters the comparison. Returns a hex digest;
    produces no diagnostics.
    """
    text = to_canonical_yaml(event.model_copy(update={"id": _ID_PLACEHOLDER}))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _selector_matches(event: Event, where: str) -> bool:
    """Match an event against the §5.4 selector grammar (space-separated ANDs)."""
    for term in where.split():
        if term.startswith("flag:"):
            return False  # events carry no flags
        if ":" not in term:
            return False
        key, _, value = term.partition(":")
        if event.tags.get(key) != value:
            return False
    return True


# --------------------------------------------------------------------------- #
# SQLite implementation
# --------------------------------------------------------------------------- #


class SqliteLedger:
    """The SQLite ledger. The only module in CashKit that imports ``sqlite3``.

    Open with a filesystem path or ``":memory:"``. Nothing here reads the wall
    clock: a ledger entry is ordered by its sequence number, never by a
    timestamp, so a ledger replays identically on any machine.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the connection. No diagnostics."""
        self._conn.close()

    def __enter__(self) -> "SqliteLedger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- reads ------------------------------------------------------------- #

    def _voided_ids(self, upto: int | None = None) -> set[str]:
        sql = "SELECT event_id FROM ledger_entries WHERE kind = 'void'"
        params: tuple[object, ...] = ()
        if upto is not None:
            sql += " AND seq <= ?"
            params = (upto,)
        return {row["event_id"] for row in self._conn.execute(sql, params)}

    def _event_rows(self, upto: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM ledger_entries WHERE kind = 'event'"
        params: tuple[object, ...] = ()
        if upto is not None:
            sql += " AND seq <= ?"
            params = (upto,)
        return list(self._conn.execute(sql + " ORDER BY seq", params))

    def _row_by_event_id(self, event_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM ledger_entries WHERE kind = 'event' AND event_id = ?",
            (event_id,),
        ).fetchone()

    def _corrector_of(self, event_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT event_id FROM ledger_entries "
            "WHERE kind = 'event' AND corrects = ? ORDER BY seq LIMIT 1",
            (event_id,),
        ).fetchone()
        return None if row is None else row["event_id"]

    def _max_seq(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM ledger_entries")
        return int(row.fetchone()["m"])

    def facts(self, watermark: Watermark | None = None) -> list[Event]:
        """Every live event, in ledger order (ADR-0012 decision 6).

        Tombstoned rows are excluded and correcting rows included — the same
        filter voids already required. ``watermark`` truncates the ledger to the
        state at a past revision: a run through ``at(ref)`` sees the ledger as it
        stood then, corrections included only if they predate it. Returns
        ``Event`` models; produces no diagnostics.
        """
        upto = None if watermark is None else watermark.max_rowid
        voided = self._voided_ids(upto)
        return [
            _row_to_event(row)
            for row in self._event_rows(upto)
            if row["event_id"] not in voided
        ]

    def query_events(
        self,
        *,
        where: str | None = None,
        since: date | None = None,
        until: date | None = None,
        watermark: Watermark | None = None,
        include_voided: bool = False,
    ) -> list[Event]:
        """Filter the ledger by selector and date window (PRD §6.2).

        ``where`` uses the §5.4 selector grammar (space-separated ``key:value``
        terms, ANDed). ``since``/``until`` bound the event date inclusively.
        ``include_voided`` returns tombstoned rows too, for audit. Returns
        ``Event`` models in ledger order; produces no diagnostics.
        """
        upto = None if watermark is None else watermark.max_rowid
        voided = self._voided_ids(upto)
        out: list[Event] = []
        for row in self._event_rows(upto):
            if not include_voided and row["event_id"] in voided:
                continue
            event = _row_to_event(row)
            if since is not None and event.date < since:
                continue
            if until is not None and event.date > until:
                continue
            if where and not _selector_matches(event, where):
                continue
            out.append(event)
        return out

    def is_voided(self, event_id: EventId) -> bool:
        """True when a tombstone entry names ``event_id``. No diagnostics."""
        return event_id in self._voided_ids()

    def watermark(self) -> Watermark:
        """The ADR-0006 watermark over the whole log.

        ``max_rowid`` is the last entry's sequence number, ``row_count`` the
        number of entries, and ``content_hash`` a digest over
        ``(kind, event_id, source, ext_id, date, amount)`` per entry in sequence
        order. Voids are included in the hash deliberately: a watermark blind to
        tombstones would call two materially different ledgers identical.
        Stamped by ``commit()``, never by an import. No diagnostics.
        """
        digest = hashlib.sha256()
        count = 0
        for row in self._conn.execute(
            "SELECT kind, event_id, source, ext_id, event_date, amount "
            "FROM ledger_entries ORDER BY seq"
        ):
            count += 1
            digest.update(
                "\x1f".join(
                    "" if row[name] is None else str(row[name])
                    for name in (
                        "kind",
                        "event_id",
                        "source",
                        "ext_id",
                        "event_date",
                        "amount",
                    )
                ).encode("utf-8")
            )
            digest.update(b"\x1e")
        return Watermark(
            max_rowid=self._max_seq(), row_count=count, content_hash=digest.hexdigest()
        )

    def state_digest(self) -> str:
        """A fingerprint of the *entire* ledger, payloads included.

        Stronger than the watermark: two ledgers with the same digest are
        byte-identical row for row. Used by the Phase 5 gate to prove that three
        re-imports of the same file leave the store untouched. No diagnostics.
        """
        digest = hashlib.sha256()
        for row in self._conn.execute(
            "SELECT seq, kind, event_id, source, ext_id, event_date, amount, "
            "status, item, currency, corrects, note, digest, payload "
            "FROM ledger_entries ORDER BY seq"
        ):
            digest.update(json.dumps(list(row), sort_keys=True).encode("utf-8"))
            digest.update(b"\x1e")
        return digest.hexdigest()

    def import_history(self) -> list[dict[str, object]]:
        """The import log, oldest first. No diagnostics."""
        return [dict(row) for row in self._conn.execute("SELECT * FROM import_log ORDER BY seq")]

    # -- writes ------------------------------------------------------------ #

    def _insert_event(self, event: Event) -> int:
        cursor = self._conn.execute(
            "INSERT INTO ledger_entries (kind, event_id, source, ext_id, event_date, "
            "amount, status, item, currency, corrects, note, digest, payload) "
            "VALUES ('event', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.source,
                event.ext_id,
                event.date.isoformat(),
                str(event.amount),
                event.status,
                event.item,
                event.currency,
                event.corrects,
                event.note,
                payload_digest(event),
                event.model_dump_json(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def _insert_void(self, event_id: str, note: str) -> int:
        cursor = self._conn.execute(
            "INSERT INTO ledger_entries (kind, event_id, note) VALUES ('void', ?, ?)",
            (event_id, note),
        )
        return int(cursor.lastrowid or 0)

    def add_event(self, event: Event) -> ChangeReport:
        """Append one event (PRD §6.2).

        Returns a :class:`ChangeReport` naming the created row. Produces
        ``CK-E010`` if ``(source, ext_id)`` is already taken by a different
        payload, and reports an identical re-add as an empty change
        (``CK-I002``) rather than double-counting it. Never raises on content.
        """
        if event.source is not None and event.ext_id is not None:
            existing = self._conn.execute(
                "SELECT * FROM ledger_entries WHERE kind = 'event' AND source = ? "
                "AND ext_id = ?",
                (event.source, event.ext_id),
            ).fetchone()
            if existing is not None:
                if existing["digest"] == payload_digest(event):
                    return ChangeReport(
                        target=existing["event_id"],
                        diagnostics=(make_diagnostic("CK-I002"),),
                    )
                return ChangeReport(
                    target=event.id,
                    diagnostics=(
                        make_diagnostic(
                            "CK-E010", source=event.source, ext_id=event.ext_id
                        ),
                    ),
                )
        if self._row_by_event_id(event.id) is not None:
            return ChangeReport(
                target=event.id,
                diagnostics=(
                    make_diagnostic(
                        "CK-E015",
                        event_id=event.id,
                        operation="appended",
                        reason="an event with this id already exists",
                    ),
                ),
            )
        with self._conn:
            self._insert_event(event)
        return ChangeReport(target=event.id, created=(event.id,))

    def import_events(self, rows: Iterable[Event], source: str) -> ImportReport:
        """Idempotent batch import keyed on ``(source, ext_id)`` (PRD §6.2).

        A row whose key exists with an **identical payload** is skipped; a row
        whose key exists with a **different payload** is a conflict, and any
        conflict aborts the whole batch (ADR-0008) leaving the ledger untouched.
        Payload identity ignores the row's ``id``: identity is
        ``(source, ext_id)``.

        Returns an :class:`ImportReport` with inserted / skipped / conflicted
        counts. Diagnostics: ``CK-E010`` per conflicting row (batch aborted, and
        the fix names ``correct_event``), ``CK-E017`` for a row with no
        ``ext_id`` — importing without an idempotency key would double-count on
        the next re-import, which is the one failure ``UNIQUE(source, ext_id)``
        exists to prevent. Never raises on row content.
        """
        staged: list[Event] = []
        diagnostics: list[Diagnostic] = []
        inserted = skipped = conflicted = considered = 0
        seen: dict[str, str] = {}

        for position, row in enumerate(rows):
            considered += 1
            event = row if row.source == source else row.model_copy(update={"source": source})
            if event.ext_id is None:
                diagnostics.append(
                    make_diagnostic("CK-E017", position=position, source=source)
                )
                conflicted += 1
                continue
            digest = payload_digest(event)
            existing = self._conn.execute(
                "SELECT * FROM ledger_entries WHERE kind = 'event' AND source = ? "
                "AND ext_id = ?",
                (source, event.ext_id),
            ).fetchone()
            prior = seen.get(event.ext_id)
            if existing is not None:
                if existing["digest"] == digest:
                    skipped += 1
                    continue
                diagnostics.append(
                    make_diagnostic("CK-E010", source=source, ext_id=event.ext_id)
                )
                conflicted += 1
                continue
            if prior is not None:
                if prior == digest:
                    skipped += 1
                    continue
                diagnostics.append(
                    make_diagnostic("CK-E010", source=source, ext_id=event.ext_id)
                )
                conflicted += 1
                continue
            seen[event.ext_id] = digest
            staged.append(event)
            inserted += 1

        aborted = conflicted > 0
        created: tuple[str, ...] = ()
        if not aborted:
            with self._conn:
                for event in staged:
                    self._insert_event(event)
            created = tuple(event.id for event in staged)
        if not aborted and inserted == 0 and skipped:
            diagnostics.append(make_diagnostic("CK-I002"))
        with self._conn:
            self._conn.execute(
                "INSERT INTO import_log (at_seq, source, considered, inserted, skipped, "
                "conflicted, aborted) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self._max_seq(),
                    source,
                    considered,
                    0 if aborted else inserted,
                    skipped,
                    conflicted,
                    int(aborted),
                ),
            )
        return ImportReport(
            target=source,
            source=source,
            considered=considered,
            inserted=0 if aborted else inserted,
            skipped=skipped,
            conflicted=conflicted,
            aborted=aborted,
            created=created,
            diagnostics=tuple(diagnostics),
        )

    def _referential_problem(
        self, event_id: EventId, operation: str
    ) -> Diagnostic | None:
        """The ADR-0012 referential rules: target exists, is not a tombstone,
        is not already corrected."""
        row = self._row_by_event_id(event_id)
        if row is None:
            return make_diagnostic("CK-E014", event_id=event_id)
        # The corrector check comes first: a corrected row is also a tombstone,
        # and "already corrected by X" tells the caller where to go next, while
        # "already void" leaves them looking.
        corrector = self._corrector_of(event_id)
        if corrector is not None:
            return make_diagnostic(
                "CK-E015",
                event_id=event_id,
                operation=operation,
                reason=f"it has already been corrected by {corrector}",
            )
        if self.is_voided(event_id):
            return make_diagnostic(
                "CK-E015",
                event_id=event_id,
                operation=operation,
                reason="the row is already void",
            )
        return None

    def void_event(self, event_id: EventId, note: str) -> ChangeReport:
        """Tombstone a committed/forecast event (PRD §6.2, ADR-0008).

        Append-only: a ``void`` entry names the row, which is never deleted, so
        watermarks stay valid. Returns a :class:`ChangeReport`. Diagnostics:
        ``CK-E014`` (unknown id), ``CK-E015`` (already void or already
        corrected), ``CK-E016`` when the target is an ``actual`` — voiding an
        actual destroys the fact rather than correcting the record, so the fix
        names ``correct_event`` (ADR-0012). Never raises on content.
        """
        problem = self._referential_problem(event_id, "voided")
        if problem is not None:
            return ChangeReport(target=event_id, diagnostics=(problem,))
        row = self._row_by_event_id(event_id)
        assert row is not None
        if row["status"] == "actual":
            return ChangeReport(
                target=event_id,
                diagnostics=(make_diagnostic("CK-E016", event_id=event_id),),
            )
        if not note or not note.strip():
            return ChangeReport(
                target=event_id,
                diagnostics=(
                    make_diagnostic(
                        "CK-E015",
                        event_id=event_id,
                        operation="voided",
                        reason="a void requires a non-empty note",
                    ),
                ),
            )
        with self._conn:
            self._insert_void(event_id, note)
        return ChangeReport(target=event_id, changed=("void",))

    def correct_event(
        self, event_id: EventId, corrected: Event, note: str
    ) -> ChangeReport:
        """Tombstone an event and append its correction, atomically (ADR-0012).

        The fact is immutable; the *record* of it can be wrong, and correcting a
        record is itself an event: dated, attributed, auditable. The correction
        inherits the original's ``status`` and carries ``corrects=<original>``
        plus a mandatory ``note``. No code path performs an in-place ``UPDATE``.

        The correcting row does **not** inherit ``ext_id``: the original keeps
        the idempotency key, so re-importing the erroneous upstream row is
        still a no-op skip and the correction stands.

        Returns a :class:`ChangeReport` naming the created row. Diagnostics:
        ``CK-E014`` (unknown target), ``CK-E015`` (target already void or
        already corrected, or an empty note — a correction without a stated
        reason is not auditable). Never raises on content.
        """
        problem = self._referential_problem(event_id, "corrected")
        if problem is not None:
            return ChangeReport(target=event_id, diagnostics=(problem,))
        if not note or not note.strip():
            return ChangeReport(
                target=event_id,
                diagnostics=(
                    make_diagnostic(
                        "CK-E015",
                        event_id=event_id,
                        operation="corrected",
                        reason="a correction requires a non-empty note (ADR-0012)",
                    ),
                ),
            )
        row = self._row_by_event_id(event_id)
        assert row is not None
        original = _row_to_event(row)
        new_id = self._free_correction_id(event_id)
        replacement = corrected.model_copy(
            update={
                "id": new_id,
                "status": original.status,
                "corrects": event_id,
                "note": note,
                "source": original.source,
                "ext_id": None,
            }
        )
        # Re-validate: model_copy does not, and the structural rules of
        # DECISIONS D-P1-15 (corrects != id, non-empty note) must still hold.
        replacement = Event.model_validate(replacement.model_dump())
        with self._conn:
            self._insert_void(event_id, note)
            self._insert_event(replacement)
        return ChangeReport(
            target=event_id, changed=("void",), created=(new_id,)
        )

    def _free_correction_id(self, event_id: str) -> str:
        base = f"{event_id}~c"
        index = 1
        while self._row_by_event_id(f"{base}{index}") is not None:
            index += 1
        return f"{base}{index}"


def _row_to_event(row: sqlite3.Row) -> Event:
    """Rebuild an ``Event`` from its stored payload.

    The payload is the model's own JSON form, in which a ``Decimal`` is a
    string: amounts round-trip through their exact decimal spelling, never a
    float. Identity is a separate concern — the ``digest`` column holds the
    canonical-YAML fingerprint, so conflict detection never rehydrates a row.
    """
    return Event.model_validate_json(row["payload"])
