"""Diagnostic catalogue (PRD §10.1) as data.

Codes are stable identifiers: the set grows, codes never change meaning.
Every fallible SDK operation returns ``Diagnostic`` objects built from this
catalogue via :func:`make_diagnostic`; exceptions are reserved for programmer
error.
"""

from __future__ import annotations

import string
from typing import Literal, Mapping

from .primitives import CashKitModel, Diagnostic, ItemId

__all__ = ["CATALOGUE", "DiagnosticSpec", "make_diagnostic"]


class DiagnosticSpec(CashKitModel):
    """Catalogue entry: a stable code with message and fix templates.

    Templates use ``str.format`` placeholders; :func:`make_diagnostic` fills
    them from keyword arguments.
    """

    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    suggested_fix: str

    def placeholders(self) -> frozenset[str]:
        """Return the set of template placeholder names used by this spec.

        Returns a frozenset of field names; produces no diagnostics.
        """
        names: set[str] = set()
        for template in (self.message, self.suggested_fix):
            for _, field_name, _, _ in string.Formatter().parse(template):
                if field_name:
                    names.add(field_name)
        return frozenset(names)


def _spec(
    code: str,
    severity: Literal["error", "warning", "info"],
    message: str,
    suggested_fix: str,
) -> DiagnosticSpec:
    return DiagnosticSpec(
        code=code, severity=severity, message=message, suggested_fix=suggested_fix
    )


#: The initial catalogue, verbatim from PRD §10.1. Append-only.
CATALOGUE: Mapping[str, DiagnosticSpec] = {
    spec.code: spec
    for spec in [
        _spec(
            "CK-E001",
            "error",
            "Unknown reference at graph build: {reference}",
            "Reference an existing item id in it(), or use an agg() selector that "
            "matches at least one item; run describe_book() to list valid ids and tags.",
        ),
        _spec(
            "CK-E002",
            "error",
            "Circular dependency without prev(): {cycle}",
            "Break the cycle by routing one edge through prev(), or restructure "
            "the formulas so the dependency is acyclic.",
        ),
        _spec(
            "CK-E003",
            "error",
            "Formula rejected: {reason}",
            "Use only the documented formula surface: it(), prev() with a literal "
            "n, p.<param>, agg(), cum(), t.<field>, where(), and the safe builtins.",
        ),
        _spec(
            "CK-E004",
            "error",
            "Settlement shares sum to {total}, not exactly 1",
            "Adjust DueTerm shares so they sum to exactly 1 as Decimals "
            "(no float tolerance), or switch to fixed amounts with one remainder.",
        ),
        _spec(
            "CK-E005",
            "error",
            "Invalid settlement structure: {reason}",
            "Use either all-share terms summing to 1, or fixed-amount terms with "
            "exactly one remainder=True term; never mix share and amount.",
        ),
        _spec(
            "CK-E006",
            "error",
            "Scenario overlay touches event {event_id} with status='actual'",
            "Actuals are immutable across all scenarios. Change what is forecast "
            "from cutover forward instead.",
        ),
        _spec(
            "CK-E007",
            "error",
            "Invalid param key {key!r}",
            "Param keys must match [a-z][a-z0-9_]* so formulas can reference them "
            "as p.<key>; replace dots and invalid characters with underscores.",
        ),
        _spec(
            "CK-E008",
            "error",
            "Unknown param {key!r} referenced by {referrer}",
            "Define the param with set_param() first, or reference an existing "
            "key; run describe_book() to list params.",
        ),
        _spec(
            "CK-E009",
            "error",
            "Invalid Recurrence: {reason}",
            "Use a Grain unit, every >= 1, and day in 1..31 set exactly when "
            "anchor='day_of_month'.",
        ),
        _spec(
            "CK-E010",
            "error",
            "Import conflict: ({source}, {ext_id}) exists with a different payload "
            "— batch aborted",
            "The source system rewrote history; review the conflicting rows. "
            "If the stored row is wrong, fix it with correct_event(), which "
            "tombstones it and appends the correction; if the upstream row is a "
            "genuine amendment, it must arrive under a new ext_id.",
        ),
        _spec(
            "CK-E011",
            "error",
            "Amount sign contradicts direction={direction!r} on item {item_id}",
            "Author the amount with the sign matching the item's direction "
            "(storage is signed; direction is display-only), or fix direction.",
        ),
        _spec(
            "CK-E012",
            "error",
            "Generative item {item_id} has kind='stock'",
            "In v1, 'stock' is valid on derived items only. Model the stock as a "
            "derived item with a formula (e.g. over prev()), or use kind='flow'.",
        ),
        _spec(
            "CK-E013",
            "error",
            "Concurrent writer: lock held by pid {pid} since {since}",
            "Wait for the other writer to finish. If the process is dead, the "
            "stale lock is reclaimed automatically on the next write (CK-W010).",
        ),
        _spec(
            "CK-E014",
            "error",
            "Ledger event {event_id} not found",
            "Check the event id with query_events(). Ledger rows are never "
            "deleted, so an id that does not resolve was never appended.",
        ),
        _spec(
            "CK-E015",
            "error",
            "Ledger event {event_id} cannot be {operation}: {reason}",
            "The ledger is append-only: a row that is already void or already "
            "corrected is history. Act on the row that supersedes it — "
            "query_events() shows the correcting row via its 'corrects' field.",
        ),
        _spec(
            "CK-E016",
            "error",
            "void_event refuses event {event_id} with status='actual'",
            "Voiding an actual destroys the fact instead of correcting the "
            "record. Use correct_event(book, event_id, corrected_payload, note): "
            "it tombstones the original and appends the correction, auditably.",
        ),
        _spec(
            "CK-E017",
            "error",
            "Import row {position} from source {source!r} has no ext_id — batch "
            "aborted",
            "Every imported row needs an idempotency key: UNIQUE(source, ext_id) "
            "is the only thing preventing double-counted actuals on re-import. "
            "Use add_event() for a one-off with no upstream key.",
        ),
        _spec(
            "CK-E018",
            "error",
            "Event {event_id} cannot attach to item {item_id}: {reason}",
            "Events are literal facts and attach to generative flow items. "
            "Point the event at a kind='flow' item, or leave item unset and let "
            "it carry its own tags.",
        ),
        _spec(
            "CK-E019",
            "error",
            "Tax regime {regime_id} is misconfigured: {reason}",
            "Fix the TaxRegime: give it a periodicity, a payment_offset, an "
            "accumulates selector that matches at least one item (or leave it "
            "empty for 'every item carrying a VatSpec'), and an "
            "annual_adjustment_month when credit_handling='refund_annual'.",
        ),
        _spec(
            "CK-E020",
            "error",
            "Cross-currency aggregation or fold: {currencies}",
            "Aggregation never sums mixed currencies silently. Keep the "
            "aggregated items in one currency; conversion arrives with "
            "multi-currency support.",
        ),
        _spec(
            "CK-E021",
            "error",
            "Unknown scenario {scenario_id}: {reason}",
            "Scenarios fork from scenarios. Name an existing scenario id — base "
            "is the one with parent=None — or create it first with fork().",
        ),
        _spec(
            "CK-E022",
            "error",
            "Scenario {scenario_id} already exists",
            "Scenario ids are unique. Pick a different id, or write into the "
            "existing scenario with set_item() / set_param().",
        ),
        _spec(
            "CK-E023",
            "error",
            "Scenario {scenario_id} overrides item {item_id}, which its parent "
            "chain does not define",
            "An overlay refines an item that exists. Add the item to the base "
            "book, put the full Item in the scenario's added set, or drop the "
            "overlay with unset().",
        ),
        _spec(
            "CK-E024",
            "error",
            "Reserved param {key!r} on scenario {scenario_id} is not a valid "
            "money value: {reason}",
            "opening_balance overrides a money field, so it must carry at most "
            "4 decimal places and stay inside the engine ceiling. Set it with a "
            "Decimal at 4 dp.",
        ),
        _spec(
            "CK-E025",
            "error",
            "Stored book state at {path} is not readable: {reason}",
            "The file was written by something other than the SDK, or is from a "
            "schema generation with no migration path. Restore it from a "
            "revision with at(ref), or fix the file and re-run validate().",
        ),
        _spec(
            "CK-E026",
            "error",
            "Config schema version {found} is newer than this build understands "
            "({supported})",
            "Migrations are forward-only: an older CashKit cannot read a newer "
            "book. Upgrade CashKit to a build whose schema version is at least "
            "{found}.",
        ),
        _spec(
            "CK-E027",
            "error",
            "Revision {ref!r} does not resolve: {reason}",
            "Use 'HEAD', 'HEAD~<n>' or a revision id from history(). Refs are "
            "opaque strings; no other form is addressable.",
        ),
        _spec(
            "CK-E028",
            "error",
            "Historical reproduction mismatch at revision {ref} for scenario "
            "{scenario}: {reason}",
            "The engine version matches the snapshot's, so the run should have "
            "reproduced exactly. Something outside (revision, scenario, "
            "engine_version, ledger_watermark) reached the computation — report "
            "this rather than trusting either number.",
        ),
        _spec(
            "CK-E029",
            "error",
            "No CashKit book at {path}",
            "Run 'cashkit init <path>' to create one, or point at the directory "
            "containing .cashkit/.",
        ),
        _spec(
            "CK-E031",
            "error",
            "A CashKit book already exists at {path}",
            "PRD §9.6 rule 2: open the existing book instead of creating a "
            "second one. Creating a book over a book would orphan its history, "
            "which is the one thing no revision can undo.",
        ),
        _spec(
            "CK-E032",
            "error",
            "Book creation refused: {reason}",
            "Fix the argument the reason names. A book id matches "
            "[a-z][a-z0-9_-]*, a horizon is [start, end) with start < end, and "
            "money carries at most 4 decimal places.",
        ),
        _spec(
            "CK-W001",
            "warning",
            "Settlement remainder clamped to zero on item {item_id}: fixed terms "
            "({fixed_total}) exceed the accrued amount ({accrued})",
            "This is legitimate for partial delivery (a deposit larger than "
            "delivered work is real cash). If unexpected, review the fixed "
            "amounts in the settlement terms.",
        ),
        _spec(
            "CK-W002",
            "warning",
            "Negative accrual on item {item_id} routed entirely through remainder "
            "(fixed-amount settlement legs never flip sign)",
            "Expected for credit notes against fixed-amount terms. If the fixed "
            "legs should shrink instead, restructure the settlement as shares.",
        ),
        _spec(
            "CK-W003",
            "warning",
            "Actual event {event_id} dated {event_date} is on/after cutover "
            "{cutover}",
            "Actuals after cutover are included and do not suppress generation — "
            "reconcile and advance cutover with set_cutover().",
        ),
        _spec(
            "CK-W004",
            "warning",
            "Withholding in use but no cat:tax item covers the counter-leg",
            "Withholding reduces one cash leg only; the engine does not generate "
            "the other side. Model the counter-leg — the remittance when you "
            "withhold, the credit when someone withholds from you — as an item "
            "tagged cat:tax.",
        ),
        _spec(
            "CK-W005",
            "warning",
            "Division by zero in a selected branch of {item_id} at {period}",
            "Elementwise division by zero yields 0 by design. Guard the "
            "denominator with where(), or verify the upstream zero with "
            "why_zero().",
        ),
        _spec(
            "CK-E030",
            "error",
            "This kit is bound to revision {ref} and is read-only",
            "at(ref) returns a read-only view of the past — history is not "
            "editable. Make the change on the live kit and commit it; use "
            "at(ref) to read, compare and reproduce.",
        ),
        _spec(
            "CK-W011",
            "warning",
            "Engine version moved since revision {ref}: snapshot recorded "
            "{recorded}, this build is {current}",
            "Exact historical reproduction is guaranteed only at matching engine "
            "version (ADR-0006). The comparison reports the delta field by field; "
            "read it as 'the engine changed', not as 'the model changed'.",
        ),
        _spec(
            "CK-W010",
            "warning",
            "Stale writer lock (dead pid {pid}) reclaimed",
            "No action needed. If this recurs, look for writer processes dying "
            "mid-operation.",
        ),
        _spec(
            "CK-I001",
            "info",
            "A TaxRegime is present but no non-VAT cat:tax items exist",
            "The engine schedules only what a TaxRegime accumulates. Any other "
            "obligation is modelled as an ordinary item tagged cat:tax; which "
            "ones apply is a question about the entity, not about the engine.",
        ),
        _spec(
            "CK-I002",
            "info",
            "ChangeReport empty — the write recorded nothing",
            "The value written was identical to the resolved current state. No "
            "overlay entry was created; report this to the user rather than "
            "claiming a change.",
        ),
        _spec(
            "CK-W006",
            "warning",
            "Cutover {cutover} is outside the horizon [{start}, {end}): {effect}",
            "Generation is suppressed for occurrences strictly before cutover, so "
            "a cutover past the horizon's end suppresses the entire model and one "
            "before its start suppresses nothing. Set the cutover inside the "
            "horizon — reconcile() returns the day to advance it to — or extend "
            "the horizon to cover it.",
        ),
        _spec(
            "CK-E033",
            "error",
            "The frame store is unavailable: {reason}",
            "frame(), pivot(), compare() and export() are backed by DuckDB, which "
            "is an optional extra. Install it with 'pip install cashkit[duckdb]'. "
            "summary(), trace() and why_zero() need no extra and work on a core "
            "install.",
        ),
    ]
}


def make_diagnostic(
    code: str,
    *,
    item_id: ItemId | None = None,
    field: str | None = None,
    **details: object,
) -> Diagnostic:
    """Build a ``Diagnostic`` from the catalogue.

    Returns a ``Diagnostic`` with severity, message and suggested_fix filled
    from the catalogue entry for ``code``; ``details`` supply the template
    placeholders (``item_id`` is also available as a placeholder). Raises
    ``KeyError`` for an unknown code or a missing placeholder — both are
    programmer error, not user-facing failures.
    """
    spec = CATALOGUE[code]
    fmt = dict(details)
    fmt.setdefault("item_id", item_id)
    fmt.setdefault("field", field)
    return Diagnostic(
        severity=spec.severity,
        code=spec.code,
        item_id=item_id,
        field=field,
        message=spec.message.format(**fmt),
        suggested_fix=spec.suggested_fix.format(**fmt),
    )
