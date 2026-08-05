"""Phase 5 gate, first half: the ledger is idempotent and append-only.

The gate: re-importing the same 5,000-row CSV three times yields identical
ledger state and an ``ImportReport`` reporting the skips. Around it, the
lifecycle ADR-0008 and ADR-0012 define — conflicts abort the batch, voids
tombstone, actuals are correctable but never voidable — and the append-only
property the ADR-0006 watermark depends on.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from cashkit.model import Event
from cashkit.stores.ledger import SqliteLedger, payload_digest

ROWS = 5000
SOURCE = "erp:2026"

#: The CSV is a deterministic function of ROWS — no randomness that could drift
#: between runs — so "the same file three times" is provable rather than assumed.


def _csv_rows(count: int = ROWS) -> list[dict[str, str]]:
    """Build the import file's rows, deterministically."""
    customers = ("acme", "globex", "initech", "umbrella")
    out: list[dict[str, str]] = []
    for index in range(count):
        # Amounts stay 4 dp and mixed-sign: a credit note every seventeenth row.
        cents = (index * 7919) % 1_000_000
        amount = Decimal(cents).scaleb(-2)
        if index % 17 == 0:
            amount = -amount
        out.append(
            {
                "ext_id": f"INV-{index:06d}",
                "date": (date(2026, 1, 1) + timedelta(days=index % 180)).isoformat(),
                "amount": str(amount),
                "status": "actual" if index % 3 else "committed",
                "customer": customers[index % len(customers)],
            }
        )
    return out


def _write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["ext_id", "date", "amount", "status", "customer"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def _read_csv(path: Path) -> list[Event]:
    """Parse the CSV into Events — Decimal from the text, never float."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Event(
                id=f"{SOURCE}:{row['ext_id']}",
                date=date.fromisoformat(row["date"]),
                amount=Decimal(row["amount"]),
                status=row["status"],
                tags={"cat": "revenue", "customer": row["customer"]},
                source=SOURCE,
                ext_id=row["ext_id"],
            )
            for row in csv.DictReader(handle)
        ]


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    return _write_csv(tmp_path / "actuals.csv", _csv_rows())


@pytest.fixture
def ledger(tmp_path: Path) -> SqliteLedger:
    store = SqliteLedger(tmp_path / "ledger.sqlite")
    yield store
    store.close()


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_reimporting_the_same_file_three_times_is_idempotent(
    ledger: SqliteLedger, csv_path: Path
) -> None:
    """The Phase 5 gate: identical state, and the report says why."""
    events = _read_csv(csv_path)
    assert len(events) == ROWS

    first = ledger.import_events(events, SOURCE)
    assert (first.inserted, first.skipped, first.conflicted) == (ROWS, 0, 0)
    assert not first.aborted
    after_first = ledger.state_digest()
    watermark_first = ledger.watermark()

    for attempt in (2, 3):
        report = ledger.import_events(_read_csv(csv_path), SOURCE)
        assert report.inserted == 0, f"import {attempt} inserted rows"
        assert report.skipped == ROWS, f"import {attempt} did not report the skips"
        assert report.conflicted == 0
        assert not report.aborted
        assert report.considered == ROWS
        assert ledger.state_digest() == after_first, (
            f"import {attempt} changed the ledger"
        )
        assert ledger.watermark() == watermark_first

    assert len(ledger.facts()) == ROWS


def test_reimport_is_idempotent_even_when_the_source_regenerates_ids(
    ledger: SqliteLedger, csv_path: Path
) -> None:
    """Identity is ``(source, ext_id)``; the row's own id is the ledger's business.

    A source that mints a fresh surrogate id on every export must still import
    idempotently, or the gate would pass only for sources that happen to be
    stable in a field the PRD never made part of the key.
    """
    ledger.import_events(_read_csv(csv_path), SOURCE)
    before = ledger.state_digest()
    renamed = [
        event.model_copy(update={"id": f"regenerated-{index}"})
        for index, event in enumerate(_read_csv(csv_path))
    ]
    report = ledger.import_events(renamed, SOURCE)
    assert (report.inserted, report.skipped, report.conflicted) == (0, ROWS, 0)
    assert ledger.state_digest() == before


def test_a_single_conflicting_row_aborts_the_whole_batch(
    ledger: SqliteLedger, csv_path: Path
) -> None:
    """All-or-nothing (ADR-0008): a rewritten upstream row demands a human."""
    ledger.import_events(_read_csv(csv_path), SOURCE)
    before = ledger.state_digest()

    rows = _read_csv(csv_path)
    rows[4200] = rows[4200].model_copy(update={"amount": Decimal("1.0000")})
    fresh = Event(
        id="brand-new",
        date=date(2026, 5, 5),
        amount=Decimal("10"),
        status="forecast",
        source=SOURCE,
        ext_id="INV-999999",
    )
    report = ledger.import_events([*rows, fresh], SOURCE)

    assert report.aborted
    assert report.conflicted == 1
    assert report.inserted == 0, "an aborted batch must write nothing, not most of it"
    assert [d.code for d in report.diagnostics] == ["CK-E010"]
    assert "correct_event" in report.diagnostics[0].suggested_fix
    assert ledger.state_digest() == before
    assert ledger.query_events(where="customer:acme") and not ledger.query_events(
        since=date(2026, 5, 5), until=date(2026, 5, 5), where="cat:nothing"
    )


def test_import_refuses_a_row_without_an_idempotency_key(ledger: SqliteLedger) -> None:
    keyless = Event(
        id="e1", date=date(2026, 2, 1), amount=Decimal("10"), status="actual"
    )
    report = ledger.import_events([keyless], SOURCE)
    assert report.aborted
    assert [d.code for d in report.diagnostics] == ["CK-E017"]
    assert ledger.facts() == []


def test_duplicate_keys_inside_one_batch(ledger: SqliteLedger) -> None:
    row = Event(
        id="a",
        date=date(2026, 2, 1),
        amount=Decimal("10"),
        status="actual",
        source=SOURCE,
        ext_id="k1",
    )
    identical = row.model_copy(update={"id": "b"})
    report = ledger.import_events([row, identical], SOURCE)
    assert (report.inserted, report.skipped, report.conflicted) == (1, 1, 0)

    conflicting = row.model_copy(update={"id": "c", "amount": Decimal("11"), "ext_id": "k2"})
    other = conflicting.model_copy(update={"id": "d", "amount": Decimal("12")})
    report = ledger.import_events([conflicting, other], SOURCE)
    assert report.aborted and report.conflicted == 1


def test_import_log_records_every_batch(ledger: SqliteLedger) -> None:
    row = Event(
        id="a", date=date(2026, 2, 1), amount=Decimal("10"), status="actual",
        source=SOURCE, ext_id="k1",
    )
    ledger.import_events([row], SOURCE)
    ledger.import_events([row], SOURCE)
    history = ledger.import_history()
    assert [(h["inserted"], h["skipped"]) for h in history] == [(1, 0), (0, 1)]


# --------------------------------------------------------------------------- #
# Append-only, voids and corrections (ADR-0008, ADR-0012)
# --------------------------------------------------------------------------- #


def _event(event_id: str, amount: str = "100", status: str = "actual") -> Event:
    return Event(
        id=event_id,
        date=date(2026, 3, 10),
        amount=Decimal(amount),
        status=status,
        source=SOURCE,
        ext_id=event_id,
    )


def test_void_tombstones_a_forecast_without_deleting_it(ledger: SqliteLedger) -> None:
    ledger.add_event(_event("f1", status="forecast"))
    report = ledger.void_event("f1", "order cancelled")
    assert report.ok and report.changed == ("void",)
    assert ledger.facts() == []
    assert [e.id for e in ledger.query_events(include_voided=True)] == ["f1"]


def test_void_refuses_a_bare_actual_and_names_the_alternative(
    ledger: SqliteLedger,
) -> None:
    ledger.add_event(_event("a1"))
    report = ledger.void_event("a1", "typo")
    assert not report.ok
    assert report.diagnostics[0].code == "CK-E016"
    assert "correct_event" in report.diagnostics[0].suggested_fix
    assert [e.id for e in ledger.facts()] == ["a1"]


def test_correct_event_is_append_only_and_leaves_a_scar(ledger: SqliteLedger) -> None:
    ledger.add_event(_event("a1", "100"))
    report = ledger.correct_event(
        "a1", _event("ignored", "137.5000"), "bank feed transposed the digits"
    )
    assert report.ok
    assert report.created == ("a1~c1",)

    live = ledger.facts()
    assert [e.id for e in live] == ["a1~c1"]
    correction = live[0]
    assert correction.amount == Decimal("137.5000")
    assert correction.corrects == "a1"
    assert correction.status == "actual", "a correction inherits the original's status"
    assert correction.note == "bank feed transposed the digits"
    # The scar: both rows survive, and the original keeps the idempotency key.
    audit = {e.id: e for e in ledger.query_events(include_voided=True)}
    assert set(audit) == {"a1", "a1~c1"}
    assert audit["a1"].ext_id == "a1"
    assert audit["a1~c1"].ext_id is None


def test_correcting_does_not_break_reimport_of_the_erroneous_row(
    ledger: SqliteLedger,
) -> None:
    """The original keeps ``(source, ext_id)``, so re-importing it is a no-op skip
    rather than a conflict or a double count."""
    original = _event("a1", "100")
    ledger.import_events([original], SOURCE)
    ledger.correct_event("a1", _event("x", "137.5"), "bank feed error")
    before = ledger.state_digest()

    report = ledger.import_events([original], SOURCE)
    assert (report.inserted, report.skipped, report.conflicted) == (0, 1, 0)
    assert ledger.state_digest() == before
    assert [e.amount for e in ledger.facts()] == [Decimal("137.5")]


def test_correction_requires_a_note(ledger: SqliteLedger) -> None:
    ledger.add_event(_event("a1"))
    report = ledger.correct_event("a1", _event("x", "1"), "   ")
    assert not report.ok and report.diagnostics[0].code == "CK-E015"
    assert [e.id for e in ledger.facts()] == ["a1"]


def test_referential_rules_are_ledger_diagnostics(ledger: SqliteLedger) -> None:
    """ADR-0012 §5: target exists, not already corrected, not a tombstone."""
    assert ledger.void_event("nope", "x").diagnostics[0].code == "CK-E014"
    assert ledger.correct_event("nope", _event("y"), "x").diagnostics[0].code == "CK-E014"

    ledger.add_event(_event("a1"))
    ledger.correct_event("a1", _event("y", "5"), "first correction")
    again = ledger.correct_event("a1", _event("z", "6"), "second correction")
    assert again.diagnostics[0].code == "CK-E015"
    assert "already been corrected by a1~c1" in again.diagnostics[0].message

    ledger.add_event(_event("f1", status="forecast"))
    ledger.void_event("f1", "cancelled")
    assert ledger.void_event("f1", "again").diagnostics[0].code == "CK-E015"


def test_a_correction_can_itself_be_corrected(ledger: SqliteLedger) -> None:
    ledger.add_event(_event("a1", "100"))
    ledger.correct_event("a1", _event("x", "110"), "first")
    second = ledger.correct_event("a1~c1", _event("x", "120"), "second")
    assert second.ok and second.created == ("a1~c1~c1",)
    assert [(e.id, e.amount) for e in ledger.facts()] == [
        ("a1~c1~c1", Decimal("120"))
    ]


def test_add_event_is_idempotent_on_an_identical_payload(ledger: SqliteLedger) -> None:
    ledger.add_event(_event("a1"))
    again = ledger.add_event(_event("a1"))
    assert again.empty and again.diagnostics[0].code == "CK-I002"
    assert len(ledger.facts()) == 1

    clash = ledger.add_event(_event("a1", "999"))
    assert clash.diagnostics[0].code == "CK-E010"


# --------------------------------------------------------------------------- #
# Watermarks and physical append-only-ness (ADR-0006)
# --------------------------------------------------------------------------- #


def test_watermark_truncation_reproduces_the_pre_correction_ledger(
    ledger: SqliteLedger,
) -> None:
    """``at(ref)`` reproduces what was believed at that revision, errors included."""
    ledger.add_event(_event("a1", "100"))
    before = ledger.watermark()

    ledger.correct_event("a1", _event("x", "137.5"), "bank feed error")
    after = ledger.watermark()

    assert [(e.id, e.amount) for e in ledger.facts(before)] == [("a1", Decimal("100"))]
    assert [(e.id, e.amount) for e in ledger.facts(after)] == [
        ("a1~c1", Decimal("137.5"))
    ]
    assert after.max_rowid > before.max_rowid
    assert after.content_hash != before.content_hash


def test_watermark_sees_a_void(ledger: SqliteLedger) -> None:
    """A watermark blind to tombstones would call two different ledgers equal."""
    ledger.add_event(_event("f1", status="forecast"))
    before = ledger.watermark()
    ledger.void_event("f1", "cancelled")
    after = ledger.watermark()
    assert after.content_hash != before.content_hash
    assert ledger.facts(before) and not ledger.facts(after)


def test_the_ledger_never_deletes_or_updates(ledger: SqliteLedger, csv_path: Path) -> None:
    """Physically append-only: the row count of the log only ever grows."""
    counts: list[int] = []

    def total() -> int:
        connection = sqlite3.connect(ledger.path)
        try:
            return int(
                connection.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
            )
        finally:
            connection.close()

    ledger.import_events(_read_csv(csv_path)[:50], SOURCE)
    counts.append(total())
    ledger.void_event("erp:2026:INV-000000", "cancelled")  # committed row
    counts.append(total())
    ledger.correct_event("erp:2026:INV-000001", _event("x", "1"), "fix")  # an actual
    counts.append(total())
    ledger.import_events(_read_csv(csv_path)[:50], SOURCE)
    counts.append(total())

    assert counts == sorted(counts)
    assert counts[1] == counts[0] + 1
    assert counts[2] == counts[1] + 2, "a correction is a void plus an append"
    assert counts[3] == counts[2], "an idempotent re-import appends nothing"


def test_no_delete_or_update_statement_exists_in_the_ledger_source() -> None:
    """Structural proof, not a promise: the watermark scheme depends on it."""
    source = Path("cashkit/stores/ledger.py").read_text(encoding="utf-8").upper()
    for statement in ("DELETE FROM", "UPDATE LEDGER_ENTRIES", "DROP TABLE"):
        assert statement not in source, f"{statement} would break the ADR-0006 watermark"


def test_sqlite3_is_imported_nowhere_outside_the_store() -> None:
    """Storage stays swappable: the backend is confined to ``stores/``."""
    offenders = [
        path
        for path in Path("cashkit").rglob("*.py")
        if "sqlite3" in path.read_text(encoding="utf-8")
        and path != Path("cashkit/stores/ledger.py")
    ]
    assert offenders == []


def test_unique_source_ext_id_is_a_database_constraint(ledger: SqliteLedger) -> None:
    """Not a convention — the one thing preventing double-counted actuals."""
    ledger.add_event(_event("a1"))
    connection = sqlite3.connect(ledger.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO ledger_entries (kind, event_id, source, ext_id) "
                "VALUES ('event', 'sneaky', ?, 'a1')",
                (SOURCE,),
            )
    finally:
        connection.close()


def test_payload_digest_ignores_only_the_id() -> None:
    base = _event("a1")
    assert payload_digest(base) == payload_digest(base.model_copy(update={"id": "z"}))
    for update in (
        {"amount": Decimal("100.0001")},
        {"date": date(2026, 3, 11)},
        {"status": "committed"},
        {"tags": {"cat": "revenue"}},
        {"note": "hello"},
    ):
        assert payload_digest(base) != payload_digest(base.model_copy(update=update))
