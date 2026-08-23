"""The import loop — the one free-running agentic loop (SPEC §7, ADR-0030 §4).

The shape, from SPEC §7.2: parse the workbook, plan the sections, then per
section author intents → dry-run → compare the engine's figures against **the
sheet's own subtotal, total and balance rows** → on a mismatch investigate with
``trace()`` and revise → on a match, next section. Hard cap twenty model calls,
then present the partial result honestly.

Four things this module refuses to do, each of which would be the easy way:

1. **It never applies anything.** The whole run produces one proposal (origin
   ``import``) and a report. `POST /import` is not a write route; T13's route
   inventory says so and this module keeps it true (ADR-0029, SPEC §7.4).
2. **It never lets the model choose where the import lands.** SPEC §7.3 is a
   data-safety rule, not a convenience: an empty book takes the import into
   base, and a **non-empty book always gets a fresh fork named from the file,
   never base**. The target is decided here, and every authored operation is
   stamped with it after the guard — so a model that names a scenario cannot
   move the change anywhere.
3. **It never widens the book to make a sheet fit.** The horizon and the
   opening balance are book-level, not scenario-level, so setting either on a
   non-empty book would change base — which §7.3 forbids. On a fork the book
   keeps its own, and the report says which sheet rows fell outside as a result.
   Import never merges silently and never destroys existing items.
4. **It never makes the numbers agree.** A divergence is reported; a 1-cent one
   is labelled (SPEC §7.5). Nothing is rounded to fit.
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from cashkit.model import Diagnostic
from cashkit.sdk import CashKit
from pydantic import ValidationError
from .. import proposals as proposal_store
from ..agent import prompts, snapshot as snapshot_module
from ..agent.guard import guard
from ..agent.journal import TurnJournal, log_chain
from ..agent.pipeline import TurnCallLimit, ask_json
from ..agent.transport import Transport, UnreadableAnswer
from ..books import BASE_SCENARIO, scratch_copy
from ..clock import Clock
from ..config import Settings
from ..db import Database
from ..ops.applier import CK_E901, CK_E902, CK_E903, OpResult, app_diagnostic, apply_op
from ..serialize import DiagnosticOut, diagnostics_out
from . import sheets as sheet_reader
from .checks import CheckResult, CheckSpec, Figures, ReconciliationReport, evaluate, evidence_for

#: The operations an import may author. A budget is lines, changes to lines,
#: and one-off amounts; the macros need existing items to select and a
#: correction needs a recorded event, so neither belongs in a first import.
#: Anything else the model names is dropped with a diagnostic, which is the
#: same treatment the guard gives an operation outside the model's surface.
IMPORT_OPS = frozenset({"add_item", "set_amount", "add_event"})

#: The operations an import may author **into a fork**, which is a strictly
#: smaller set, and the reason is SPEC §7.3 rather than taste. The ledger is
#: append-only and shared by every scenario (`books.py`), so an event authored
#: "into a fork" is visible from base — which is precisely the silent merge
#: §7.3 forbids. On a fork a one-off is therefore a line whose start and end
#: are one month apart, which is scenario-scoped and expresses the same thing.
FORK_SAFE_OPS = frozenset({"add_item", "set_amount"})

#: How many operations one import may carry into its proposal. A year of a
#: household budget is well inside it; an unbounded list is unbounded work on
#: the event-loop thread.
MAX_IMPORT_OPERATIONS = 200


@dataclass(frozen=True)
class Target:
    """Where this import lands, decided by the host (SPEC §7.3)."""

    scenario: str
    reason: str  # "empty_book" | "non_empty_book"
    created_fork: bool


@dataclass
class ImportOutcome:
    """Everything `POST /import` produced."""

    report: ReconciliationReport
    proposal_id: uuid.UUID | None
    operations: list[dict[str, Any]]
    diagnostics: list[DiagnosticOut] = field(default_factory=list)
    status: str = "done"


# --- the target rule (SPEC §7.3) ------------------------------------------ #


def book_is_empty(kit: CashKit) -> bool:
    """An empty book: no lines, no ledger rows, and no forks.

    Conservative on purpose. Every case this gets wrong, it gets wrong in the
    direction of forking, which cannot destroy anything.
    """
    base = kit.scenarios.resolve(BASE_SCENARIO)
    if base.items:
        return False
    if kit.query_events(include_voided=True).rows:
        return False
    return not (set(kit.scenarios.scenarios) - {BASE_SCENARIO})


def fork_name_for(filename: str, taken: set[str]) -> str:
    """A scenario name from the file name, never colliding with an existing one."""
    stem = Path(filename or "import").stem
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:40] or "import"
    if slug == BASE_SCENARIO:
        slug = f"{slug}-import"
    candidate = slug
    suffix = 2
    while candidate in taken:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


def decide_target(kit: CashKit, filename: str) -> Target:
    """SPEC §7.3, and the one place it is decided."""
    if book_is_empty(kit):
        return Target(scenario=BASE_SCENARIO, reason="empty_book", created_fork=False)
    name = fork_name_for(filename, set(kit.scenarios.scenarios))
    return Target(scenario=name, reason="non_empty_book", created_fork=True)


# --- the loop ------------------------------------------------------------- #


@dataclass
class _Section:
    name: str
    spec: dict[str, Any]
    operations: list[dict[str, Any]] = field(default_factory=list)


class ImportLoop:
    """One import, from bytes to a proposal and a report."""

    def __init__(
        self,
        *,
        kit_lock,  # an async context manager yielding the book's kit
        database: Database,
        book_id: uuid.UUID,
        clock: Clock,
        settings: Settings,
        transport: Transport,
        journal: TurnJournal,
        emit: Callable[[dict[str, Any]], None],
        filename: str,
        data: bytes,
        request_id: str,
        target: Target,
    ) -> None:
        self.kit_lock = kit_lock
        self.database = database
        self.book_id = book_id
        self.clock = clock
        self.settings = settings
        # The cap is the import's, not the turn's: SPEC §7.2 allows twenty
        # model calls here where a turn gets far fewer. `ask_json` reads the
        # ceiling off the settings it is handed, so the cap travels with them
        # rather than being re-implemented.
        self.call_settings = settings.model_copy(
            update={"llm_max_calls_per_turn": settings.import_max_llm_calls}
        )
        self.transport = transport
        self.journal = journal
        self.emit = emit
        self.filename = filename
        self.data = data
        self.request_id = request_id

        self.sheets: sheet_reader.Sheets | None = None
        #: Decided by the endpoint, under the book lock, before the first model
        #: call — so the answer to `POST /import` can already say where this is
        #: going, and so exactly one decision governs the run (SPEC §7.3).
        self.target: Target = target
        self.as_of: _dt.date = clock.today()
        self.prelude: list[dict[str, Any]] = []
        self.sections: list[_Section] = []
        self.checks: list[CheckSpec] = []
        self.diagnostics: list[Diagnostic] = []
        #: The lines the book already had. An import adds its own; it never
        #: takes one of these over (SPEC §7.3, "never destroys existing items").
        self.existing_items: set[str] = set()
        #: Imported id → the id it was given, when the two collided.
        self.renames: dict[str, str] = {}
        #: Every id this import authored, under whatever name it ended up with.
        self.authored_ids: set[str] = set()
        self.capped = False
        self.incomplete_reason = ""
        self.plan_note = ""

    # -- helpers ----------------------------------------------------------- #

    def _note(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)

    async def _ask(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return await ask_json(
            self.transport,
            self.journal,
            messages,
            purpose="import",
            settings=self.call_settings,
        )

    @property
    def operations(self) -> list[dict[str, Any]]:
        ops = list(self.prelude)
        for section in self.sections:
            ops.extend(section.operations)
        return ops[:MAX_IMPORT_OPERATIONS]

    # -- the run ----------------------------------------------------------- #

    async def run(self) -> ImportOutcome:
        self.emit({"stage": "parsing", "filename": self.filename})
        self.sheets = sheet_reader.parse(self.data)
        self.emit(
            {
                "stage": "parsed",
                "sheets": self.sheets.names,
                "cells": self.sheets.filled_cells,
            }
        )

        async with self.kit_lock() as kit:
            as_of = self.clock.today()
            book_json = snapshot_module.compact(
                snapshot_module.build(kit, scenario=BASE_SCENARIO, as_of=as_of)
            )
            self.existing_items = set(
                kit.scenarios.resolve(self.target.scenario).items
                if self.target.scenario in kit.scenarios.scenarios
                else kit.scenarios.resolve(BASE_SCENARIO).items
            )
        self.as_of = as_of
        target = self.target

        self.emit(
            {
                "stage": "target",
                "scenario": target.scenario,
                "reason": target.reason,
                "created_fork": target.created_fork,
                "message": (
                    f"This book already has a plan, so the import goes into a new "
                    f"scenario called {target.scenario!r}. Base is left exactly as it is."
                    if target.created_fork
                    else "This book is empty, so the import goes into the plan itself."
                ),
            }
        )

        try:
            await self._plan(book_json)
            for index, section in enumerate(self.sections):
                await self._author(section, index, book_json)
        except TurnCallLimit as exc:
            self.capped = True
            self.incomplete_reason = (
                f"The import reached its limit of {self.settings.import_max_llm_calls} "
                "assistant calls. What it worked out so far is below, unfinished."
            )
            self._note(app_diagnostic(CK_E901, str(exc)[:300]))
            log_chain(
                "import.capped",
                request_id=self.request_id,
                turn_id=self.journal.turn_id,
                calls=self.journal.seq,
            )
        except UnreadableAnswer as exc:
            self.incomplete_reason = (
                "The assistant's answer could not be read, so the import stopped "
                "part-way. What it worked out so far is below."
            )
            self._note(app_diagnostic(CK_E902, str(exc)[:300]))

        return await self._finish()

    # -- stage one: plan --------------------------------------------------- #

    async def _plan(self, book_json: str) -> None:
        assert self.sheets is not None
        self.emit({"stage": "planning"})
        parsed = await self._ask(
            prompts.import_plan_messages(
                sheet_reader.sheet_text(self.sheets),
                candidates=sheet_reader.total_row_candidates(self.sheets),
                headers=sheet_reader.header_hints(self.sheets),
                book_json=book_json,
                filename=self.filename,
            )
        )
        self.plan_note = str(parsed.get("reply") or "")
        self.checks = self._checks_from(parsed.get("checks"))
        self.sections = [
            _Section(name=str(s.get("name") or f"section {i + 1}"), spec=s)
            for i, s in enumerate(parsed.get("sections") or [])
            if isinstance(s, dict)
        ] or [_Section(name="the whole sheet", spec={"name": "the whole sheet"})]
        self.prelude = self._prelude_from(parsed)

        self.emit(
            {
                "stage": "planned",
                "reply": self.plan_note,
                "sections": [s.name for s in self.sections],
                "checks": len(self.checks),
                "book_ops": [op["op"] for op in self.prelude],
            }
        )

    def _checks_from(self, raw: Any) -> list[CheckSpec]:
        specs: list[CheckSpec] = []
        for entry in raw or []:
            if not isinstance(entry, dict):
                continue
            payload = dict(entry)
            # A model that helpfully supplies the figure is ignored, not
            # trusted: the value comes from the workbook, always.
            payload.pop("value", None)
            payload.pop("sheet_value", None)
            payload.pop("engine_value", None)
            try:
                specs.append(CheckSpec.model_validate(payload))
            except ValidationError as exc:
                self._note(
                    app_diagnostic(
                        CK_E902,
                        f"A reconciliation check was dropped: {exc.error_count()} bad field(s) "
                        f"in {entry.get('ref', 'an unnamed cell')!r}.",
                    )
                )
        return specs

    def _prelude_from(self, parsed: dict[str, Any]) -> list[dict[str, Any]]:
        """The book-level operations, composed host-side.

        On a fork this is the fork itself and nothing more. The horizon and the
        opening balance are book-level, so changing either would change base —
        which SPEC §7.3 forbids for a non-empty book. The report carries the
        consequence rather than the service taking the shortcut.
        """
        assert self.sheets is not None
        if self.target.created_fork:
            return [
                {
                    "op": "fork_scenario",
                    "name": self.target.scenario,
                    "parent": BASE_SCENARIO,
                    "note": f"imported from {self.filename}",
                }
            ]

        prelude: list[dict[str, Any]] = []
        opening = self._opening_balance(parsed.get("opening_balance"))
        if opening is not None:
            prelude.append({"op": "set_opening_balance", "amount": str(opening)})
        horizon = parsed.get("horizon")
        if isinstance(horizon, dict) and horizon.get("start") and horizon.get("end"):
            try:
                start = _as_date(horizon["start"])
                end = _as_date(horizon["end"])
            except ValueError:
                start = end = None
            if start and end and end > start:
                prelude.append(
                    {"op": "set_horizon", "start": start.isoformat(), "end": end.isoformat()}
                )
        return prelude

    def _opening_balance(self, raw: Any) -> Decimal | None:
        """The sheet's starting balance, read from the workbook where possible."""
        assert self.sheets is not None
        if not isinstance(raw, dict):
            return None
        ref = raw.get("cell")
        if isinstance(ref, str) and ref.strip():
            value = sheet_reader.read_number(self.sheets, ref)
            if value is not None:
                return value
            self._note(
                app_diagnostic(
                    CK_E903,
                    f"{ref} was named as the opening balance and holds no number; "
                    "the book keeps the balance it had.",
                )
            )
            return None
        amount = raw.get("amount")
        if isinstance(amount, str):
            try:
                return Decimal(amount)
            except (InvalidOperation, ValueError):
                return None
        return None

    # -- stage two: author, reconcile, revise ------------------------------ #

    async def _author(self, section: _Section, index: int, book_json: str) -> None:
        assert self.sheets is not None
        total = len(self.sections)
        self.emit(
            {
                "stage": "section",
                "section": section.name,
                "index": index + 1,
                "of": total,
            }
        )
        remaining = [s.name for s in self.sections[index + 1 :]]
        parsed = await self._ask(
            prompts.import_author_messages(
                sheet_reader.sheet_text(self.sheets),
                section=section.spec,
                remaining=remaining,
                already=[op for s in self.sections[:index] for op in s.operations],
                book_json=book_json,
                plan_note=self.plan_note,
                one_off_style=self._one_off_style(),
            )
        )
        section.operations = self._accept(parsed.get("intents"), section.name)
        self.emit(
            {
                "stage": "authored",
                "section": section.name,
                "operations": len(section.operations),
                "reply": str(parsed.get("reply") or ""),
            }
        )

        latest: list[OpResult] = []
        for round_number in range(self.settings.import_revise_rounds + 1):
            results, latest, evidence = await self._reconcile()
            self._emit_checks(results, section.name)
            broken = self._broken_operations(latest, section)
            failures = [r for r in results if r.status == "mismatched"]
            if not failures and not broken:
                return
            if round_number >= self.settings.import_revise_rounds:
                break
            self.emit(
                {
                    "stage": "revising",
                    "section": section.name,
                    "round": round_number + 1,
                    "failing": len(failures),
                    "refused": len(broken),
                }
            )
            revised = await self._ask(
                prompts.import_revise_messages(
                    sheet_reader.sheet_text(self.sheets),
                    section=section.spec,
                    operations=section.operations,
                    failures=[_failure(r) for r in failures] + broken,
                    evidence=evidence,
                )
            )
            replacement = self._accept(revised.get("intents"), section.name)
            if not replacement:
                break
            section.operations = replacement

        # Whatever the engine still refuses is dropped rather than carried into
        # a card that cannot be applied. The report says which, verbatim.
        self._drop_refused(latest, section)

    def _one_off_style(self) -> str:
        if self.target.created_fork:
            return (
                "This import lands in a scenario, not in the plan itself, so a one-off "
                "amount is a line whose start and end are ONE MONTH apart — for example "
                'start "2026-06-01", end "2026-07-01". Do not use add_event here: the '
                "ledger is shared by every scenario, so it would reach the plan."
            )
        return (
            "A one-off amount is an add_event on its own date, or a line whose start and "
            "end are one month apart. Either is correct."
        )

    def _accept(self, raw: Any, section_name: str) -> list[dict[str, Any]]:
        """Guard the model's output, keep what an import may author, stamp the target."""
        guarded = guard(raw)
        self.diagnostics.extend(guarded.diagnostics)
        allowed = FORK_SAFE_OPS if self.target.created_fork else IMPORT_OPS
        accepted: list[dict[str, Any]] = []
        for operation in guarded.mutations:
            if operation["op"] not in allowed:
                self._note(
                    app_diagnostic(
                        CK_E901,
                        f"{operation['op']!r} is not something an import authors"
                        + (
                            " into a scenario, because it would reach the plan itself"
                            if operation["op"] in IMPORT_OPS
                            else ""
                        )
                        + f"; it was left out of {section_name}.",
                        fix=(
                            "A one-off in a scenario is a line whose start and end are "
                            "one month apart."
                            if operation["op"] == "add_event"
                            else ""
                        ),
                    )
                )
                continue
            # SPEC §7.3 made structural: the target is the host's, and it is
            # written over whatever the model put here.
            operation["scenario"] = self.target.scenario
            if not self._name_safely(operation, section_name):
                continue
            accepted.append(operation)
        if guarded.reads:
            self._note(
                app_diagnostic(
                    CK_E901,
                    f"{len(guarded.reads)} read operation(s) were ignored while authoring "
                    f"{section_name}; an import authors, it does not ask.",
                )
            )
        return accepted

    def _name_safely(self, operation: dict[str, Any], section_name: str) -> bool:
        """Keep an imported line off a line the book already had (SPEC §7.3).

        Two halves of one rule. A new line whose id collides with an existing
        one is **renamed**, because `add_item` on an existing id replaces it and
        a replacement inside the fork is exactly the silent merge §7.3 forbids.
        And a change to a line is allowed only against a line **this import
        authored**: an import adds a budget, it does not edit the one already
        there. Both are refusals the model cannot talk its way past.
        """
        kind = operation["op"]
        if kind == "add_item":
            original = operation["id"]
            if original in self.existing_items:
                renamed = self.renames.get(original) or self._free_id(original)
                self.renames[original] = renamed
                operation["id"] = renamed
                operation.setdefault("name", original.replace("_", " ").title())
                self._note(
                    Diagnostic(
                        severity="info",
                        code=CK_E901,
                        message=(
                            f"This book already has a line called {original!r}, so the "
                            f"imported one was added as {renamed!r}. Nothing was replaced."
                        ),
                        suggested_fix="Rename or merge them yourself once the import is applied.",
                        item_id=renamed,
                        field=None,
                    )
                )
            self.authored_ids.add(operation["id"])
            return True

        if kind == "set_amount":
            target_item = self.renames.get(operation["item"], operation["item"])
            if target_item not in self.authored_ids:
                self._note(
                    app_diagnostic(
                        CK_E901,
                        f"{operation['item']!r} is a line this book already had, and an "
                        f"import does not change one; it was left out of {section_name}.",
                        fix="Change that line yourself, as a normal change.",
                        item=operation["item"],
                    )
                )
                return False
            operation["item"] = target_item
        return True

    def _free_id(self, original: str) -> str:
        candidate = f"{original}_imported"
        suffix = 2
        while candidate in self.existing_items:
            candidate = f"{original}_imported_{suffix}"
            suffix += 1
        return candidate

    def _effective_checks(self) -> list[CheckSpec]:
        """The plan's checks, with any renamed line followed to its new id."""
        if not self.renames:
            return self.checks
        rewritten: list[CheckSpec] = []
        for check in self.checks:
            rewritten.append(
                check.model_copy(
                    update={
                        "item": self.renames.get(check.item, check.item),
                        "items": [self.renames.get(i, i) for i in check.items],
                    }
                )
            )
        return rewritten

    async def _reconcile(self) -> tuple[list[CheckResult], list[OpResult], list[dict[str, Any]]]:
        """Dry-run everything authored so far and compare against the sheet."""
        assert self.sheets is not None
        operations = self.operations
        async with self.kit_lock() as kit:
            with scratch_copy(kit, Path(kit.root)) as scratch:
                op_results = [
                    apply_op(
                        scratch,
                        operation,
                        scenario=BASE_SCENARIO,
                        as_of=self.as_of,
                        context=None,
                        seq=index,
                    )
                    for index, operation in enumerate(operations)
                ]
                scratch.save()
                figures = Figures.of(scratch, self.target.scenario)
                if self.target.created_fork:
                    # SPEC §7.3: the fork carries the book's own plan too, so
                    # the sheet's totals are compared against what this import
                    # added rather than against the whole scenario.
                    figures = Figures.added(figures, Figures.of(scratch, BASE_SCENARIO))
                results = evaluate(figures, self.sheets, self._effective_checks())
                evidence = evidence_for(scratch, self.target.scenario, results)
        return results, op_results, evidence

    def _broken_operations(
        self, op_results: list[OpResult], section: _Section
    ) -> list[dict[str, Any]]:
        """This section's operations the engine refused, as revision input."""
        broken: list[dict[str, Any]] = []
        for result in op_results:
            # Identity, not equality: two operations may serialize the same and
            # only one of them belongs to this section.
            if result.ok or not any(result.op is op for op in section.operations):
                continue
            broken.append(
                {
                    "operation": result.op,
                    "refused_because": [d.message for d in result.diagnostics],
                    "suggested_fix": [d.suggested_fix for d in result.diagnostics if d.suggested_fix],
                }
            )
        return broken

    def _drop_refused(self, op_results: list[OpResult], section: _Section) -> None:
        refused = [
            r
            for r in op_results
            if not r.ok and any(r.op is op for op in section.operations)
        ]
        if not refused:
            return
        for result in refused:
            section.operations = [op for op in section.operations if op is not result.op]
            for diagnostic in result.diagnostics:
                self._note(diagnostic)
            self.emit(
                {
                    "stage": "dropped",
                    "section": section.name,
                    "operation": result.op,
                    "diagnostics": [d.model_dump() for d in diagnostics_out(result.diagnostics)],
                }
            )

    def _emit_checks(self, results: list[CheckResult], section: str) -> None:
        for result in results:
            self.emit(
                {
                    "stage": "check",
                    "section": section,
                    **result.model_dump(mode="json"),
                }
            )

    # -- the report and the one proposal ----------------------------------- #

    async def _finish(self) -> ImportOutcome:
        results, op_results, _evidence = await self._reconcile()
        for result in op_results:
            if not result.ok:
                self.diagnostics.extend(result.diagnostics)
        operations = [op for op in self.operations if _applied_cleanly(op, op_results)]

        report = ReconciliationReport(
            target_scenario=self.target.scenario,
            target_reason=self.target.reason,  # type: ignore[arg-type]
            created_fork=self.target.created_fork,
            source_filename=self.filename,
            checks=results,
            llm_calls=self.journal.seq,
            call_cap=self.settings.import_max_llm_calls,
            capped=self.capped,
            incomplete_reason=self.incomplete_reason,
        ).tally()
        report.partial = bool(self.capped or self.incomplete_reason or report.mismatched)

        proposal_id: uuid.UUID | None = None
        if operations:
            # The connection is opened here rather than held for the whole run:
            # an import is tens of seconds long, and a pooled connection idle
            # across a model call is a latency cliff for everyone else
            # (S2 handoff, connection-pool note).
            async with self.database.connect() as conn, self.kit_lock() as kit:
                proposal_id, _dry = await proposal_store.create(
                    conn,
                    kit=kit,
                    book_id=self.book_id,
                    origin="import",
                    scenario=BASE_SCENARIO,
                    operations=operations,
                    as_of=self.as_of,
                    clock=self.clock,
                    settings=self.settings,
                    turn_id=self.journal.turn_id,
                )
        else:
            self._note(
                app_diagnostic(
                    CK_E901,
                    "Nothing in this file could be turned into a plan, so there is no "
                    "card to apply. The report below says what happened.",
                )
            )

        log_chain(
            "import.finished",
            request_id=self.request_id,
            turn_id=self.journal.turn_id,
            proposal_id=proposal_id,
            calls=self.journal.seq,
            capped=self.capped,
            matched=report.matched,
            mismatched=report.mismatched,
            skipped=report.skipped,
            scenario=self.target.scenario,
        )
        return ImportOutcome(
            report=report,
            proposal_id=proposal_id,
            operations=operations,
            diagnostics=diagnostics_out(self.diagnostics),
            status="partial" if report.partial else "done",
        )


# --- small helpers -------------------------------------------------------- #


def _applied_cleanly(operation: dict[str, Any], results: list[OpResult]) -> bool:
    for result in results:
        if result.op is operation:
            return result.ok
    return True


def _failure(result: CheckResult) -> dict[str, Any]:
    return {
        "cell": result.ref,
        "label": result.label,
        "measure": result.measure,
        "period": result.period.isoformat() if result.period else None,
        "sheet_value": result.sheet_value,
        "engine_value": None if result.engine_value is None else result.engine_value.exact,
        "delta": result.delta,
        "note": result.note,
    }


def _as_date(value: Any) -> _dt.date:
    return value if isinstance(value, _dt.date) else _dt.date.fromisoformat(str(value))


__all__ = [
    "IMPORT_OPS",
    "MAX_IMPORT_OPERATIONS",
    "ImportLoop",
    "ImportOutcome",
    "Target",
    "book_is_empty",
    "decide_target",
    "fork_name_for",
]
