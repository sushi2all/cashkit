"""``CashKit`` — the kit a book is opened as: reads, writes and version control.

Two types, one shape (ADR-0033). :class:`ReadOnlyKit` is everything that reads a
book: resolve a scenario, run it, tabulate, trace, walk the history, open a
past revision. :class:`CashKit` is a ``ReadOnlyKit`` that can also write —
author, record events, commit. :meth:`ReadOnlyKit.at` returns a **kit, not a
book**, so ``kit.at("HEAD~5").run("downside").summary()`` works and eras of the
model compare through one API; and it returns the read-only type, so a write
on the past is not a diagnostic to remember to return but a method that does
not exist.

**Every write is addressed by scenario and lands on disk** (ADR-0031). There is
one ``set_item``, one ``set_param``, one ``apply_macro``; ``scenario="base"``
writes the authored book and any other id writes an overlay, and no caller
chooses a verb by which one it is. A write that changed something is written to
the §3.3 working tree before it returns, so the CLI, a human's editor and the
next process see the same book this one holds. Exploratory sweeping is still
free: the working tree is not a revision, and :meth:`CashKit.commit` marks the
boundaries that matter (PRD §6.7).

**Every write returns a** :class:`~cashkit.model.ChangeReport` (ADR-0032), and
every handle-returning call — :func:`~cashkit.sdk.construction.create_book`,
:meth:`CashKit.open`, :meth:`ReadOnlyKit.at` — returns ``(handle, diagnostics)``.

**Git never appears here.** Every version-control operation goes through
:class:`~cashkit.stores.revisions.RevisionStore` (ADR-0018); this module does not
import ``pygit2``, does not shell out, and takes no ref-spec other than the
opaque ``ref`` string.

**What a run is identified by** (PRD §6.6): ``(revision, scenario,
engine_version, ledger_watermark)``. Each of the four is recoverable from the
revision alone — the config schema and the engine settings that change the
numbers are tracked (``.cashkit/config.toml``, D-P9-04), ``engine_version`` and
the watermark are recorded in the committed snapshot, and the watermark is
stamped by ``commit()`` and never by an import (ADR-0006). A live run always sees
the whole ledger; only a run through ``at(ref)`` truncates it.

**Reproduction is checked, never assumed.** :meth:`ReadOnlyKit.reproduce`
re-runs a revision and compares against the snapshot committed with it. At
matching engine version a difference is an error (``CK-E028``); at a differing
engine version the delta is *reported* (``CK-W011``), which is the ADR-0006 rule:
never a silent failure in either direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

from pydantic import ValidationError

from cashkit.engine import ENGINE_VERSION, Engine, RoundingPolicy, RunResult
from cashkit.model import (
    Book,
    ChangeReport,
    Diagnostic,
    Event,
    EventId,
    Grain,
    Item,
    ItemDiff,
    ItemId,
    OutcomeDiff,
    ParamDiff,
    Provenance,
    ReconciliationReport,
    Reproduction,
    RevisionDiff,
    RunSummary,
    Scenario,
    ScenarioDiff,
    Table,
    WorkingState,
)
from cashkit.model.diagnostics import make_diagnostic
from cashkit.model.primitives import ScenarioId, _require_money
from cashkit.stores.clock import Timestamp
from cashkit.stores.config import (
    ITEMS_DIR,
    SCENARIOS_DIR,
    SCHEMA_VERSION,
    SNAPSHOTS_DIR,
    CommittedSummary,
    ConfigState,
    EngineSettings,
    build_state,
    is_book_root,
    load_state,
    read_working_tree,
    write_working_tree,
)
from cashkit.stores.ledger import LedgerStore, SqliteLedger
from cashkit.stores.lock import WriterLock
from cashkit.stores.revisions import Revision, RevisionState, RevisionStore, diff_states

from .construction import (
    isolated_problems,
    new_compile_problems,
    reason_of,
    regime_problem,
    validated_params,
)
from .execution import ExportReport
from .macros import Macro
from .scenarios import OPENING_BALANCE_PARAM, OVERLAY_FIELDS, Resolution, ScenarioSet
from .validation import ordered
from .views import summary as summarize

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cashkit.stores.frames import FrameStore

__all__ = [
    "BASE_SCENARIO",
    "CashKit",
    "CommitReport",
    "ExportReport",
    "ReadOnlyKit",
    "RunRef",
]

#: The scenario every book starts with. Base is a scenario with ``parent=None``;
#: it is privileged in storage only (ADR-0007).
BASE_SCENARIO: ScenarioId = "base"

#: The Book fields ``set_book`` may write. ``params`` and ``items`` have their
#: own verbs; ``id``, ``base_grain`` and ``ledger_watermark`` are not authored
#: after creation (the grain would re-index every column, the watermark is
#: ``commit()``'s alone — ADR-0006).
BOOK_FIELDS: tuple[str, ...] = ("cutover", "opening_balance", "horizon", "calendar", "tax_regimes")

#: ``RunSummary`` fields compared when checking historical reproduction. Every
#: number a reader acts on, and nothing that is merely a label.
_SUMMARY_FIELDS = (
    "grain",
    "balance_source",
    "periods",
    "opening_balance",
    "closing_balance",
    "min_cash",
    "min_cash_period",
    "runway_periods",
    "runway_end",
    "breakeven_period",
    "total_inflow",
    "total_outflow",
    "net_cash",
    "total_accrual",
)


class CommitReport(ChangeReport):
    """What ``commit()`` recorded (PRD §6.6, §6.5).

    PRD §6.6 types ``commit()`` as ``Revision | None``; §6.5 requires every
    fallible operation to return diagnostics rather than raise. Both are
    honoured: ``revision`` is the ``Revision | None`` — ``None`` exactly when
    the tree was unchanged — and the diagnostics channel stays open for the
    contended-lock case (``CK-E013``), which an agent must be able to loop on.
    """

    revision: Revision | None = None

    model_config = ChangeReport.model_config | {"arbitrary_types_allowed": True}


# --------------------------------------------------------------------------- #
# A run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunRef:
    """A completed run, and everything that reads it (PRD §6.4, §6.5, ADR-0034).

    Holds the resolved book the run evaluated — **not** the engine's augmented
    one — alongside the engine, so introspection can reach the compiled graph
    without recompiling (D-P5-10, D-P7-05), and the kit it came from, so the
    frame verbs can reach a store. ``summary()`` and the introspection verbs
    need no store at all.
    """

    scenario: ScenarioId
    book: Book
    result: RunResult
    engine: Engine
    kit: "ReadOnlyKit"
    revision: str | None = None
    policy: RoundingPolicy = RoundingPolicy.HALF_UP

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """The run's diagnostics. No diagnostics of its own."""
        return self.result.diagnostics

    # -- off the int64 columns, no store ---------------------------------- #

    def summary(self, *, grain: Grain | None = None, balance: str = "auto") -> RunSummary:
        """The headline numbers of this run — see :func:`cashkit.sdk.summary`."""
        return summarize(self.result, self.book, grain=grain, balance=balance)

    def trace(self, item: ItemId, period: int | date, *, measure: str = "accrual", depth: int = 3):
        """Explain one cell of this run — see :func:`cashkit.sdk.trace`."""
        from .introspection import trace as _trace

        return _trace(self, item, period, measure=measure, depth=depth)

    def why_zero(self, item: ItemId, period: int | date, *, measure: str = "cash"):
        """Explain a zero cell — see :func:`cashkit.sdk.why_zero`."""
        from .introspection import why_zero as _why_zero

        return _why_zero(self, item, period, measure=measure)

    def depends_on(self, item: ItemId, *, depth: int = 0):
        """What this item reads — see :func:`cashkit.sdk.depends_on`."""
        from .introspection import depends_on as _depends_on

        return _depends_on(self, item, depth=depth)

    def dependents_of(self, item: ItemId, *, depth: int = 0):
        """What reads this item — see :func:`cashkit.sdk.dependents_of`."""
        from .introspection import dependents_of as _dependents_of

        return _dependents_of(self, item, depth=depth)

    # -- through the frame store ------------------------------------------ #

    def frame(
        self,
        *,
        grain: Grain | None = None,
        measures: Sequence[str] | None = None,
        where: str | None = None,
        status: str | None = None,
        include_synthetic: bool = True,
    ) -> Table:
        """The run's tidy/long frame — see :func:`cashkit.sdk.execution.frame`."""
        from .execution import frame as _frame

        return _frame(
            self.kit,
            self,
            grain=grain,
            measures=measures,
            where=where,
            status=status,
            include_synthetic=include_synthetic,
        )

    def pivot(
        self,
        *,
        index: str = "period",
        columns: str = "tag:customer",
        values: str = "cash",
        grain: Grain | None = None,
    ) -> Table:
        """A wide view of one measure — see :func:`cashkit.sdk.execution.pivot`."""
        from .execution import pivot as _pivot

        return _pivot(self.kit, self, index=index, columns=columns, values=values, grain=grain)

    def export(
        self, path: str | Path, *, format: str = "parquet", grain: Grain | None = None
    ) -> ExportReport:
        """Write the frame to Parquet or CSV — see :func:`cashkit.sdk.execution.export`."""
        from .execution import export as _export

        return _export(self.kit, self, path, format=format, grain=grain)


# --------------------------------------------------------------------------- #
# The read-only kit
# --------------------------------------------------------------------------- #


@dataclass
class ReadOnlyKit:
    """A book, its stores and its history, read but never written (ADR-0033).

    This is what :meth:`at` returns: the config **as committed at a revision**
    and the ledger truncated to that revision's watermark, so a correction
    appended afterwards is invisible to it. That is deliberate — ``at()``
    reproduces what was believed then, errors included (ADR-0012). Sharing the
    live stores is safe precisely because nothing here can write to them.
    """

    root: Path
    #: The authored book plus every scenario, in memory. Writes go through the
    #: kit's verbs, which are what persist and validate them; reads may look.
    state: ScenarioSet
    revisions: RevisionStore
    ledger: LedgerStore | None = None
    settings: EngineSettings = field(default_factory=EngineSettings)
    summaries: dict[ScenarioId, CommittedSummary] = field(default_factory=dict)
    #: The revision this kit is bound to, or ``None`` for the live working
    #: state. A bound kit truncates the ledger to that revision's watermark.
    revision: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    #: The §6.4 frame store, opened on first use and never before: ``duckdb`` is
    #: an optional extra, so a core install must be able to hold a kit.
    frames: "FrameStore | None" = None

    # -- state ------------------------------------------------------------ #

    @property
    def book(self) -> Book:
        """The authored book. Never the engine's augmented one. No diagnostics."""
        return self.state.book

    @property
    def scenarios(self) -> Mapping[ScenarioId, Scenario]:
        """Every scenario by id, base included. A read view; no diagnostics."""
        return self.state.scenarios

    @property
    def policy(self) -> RoundingPolicy:
        """The rounding policy this book declares. No diagnostics."""
        return self.settings.rounding_policy

    def config_state(self, *, watermark_from_ledger: bool = False) -> ConfigState:
        """Assemble the current in-memory state for serialization.

        ``watermark_from_ledger`` stamps the ledger watermark onto the book,
        which only ``commit()`` does (ADR-0006): an import must never dirty
        tracked config. Produces no diagnostics.
        """
        book = self.book
        if watermark_from_ledger and self.ledger is not None:
            book = book.model_copy(update={"ledger_watermark": self.ledger.watermark()})
        return ConfigState(
            book=book,
            scenarios=dict(self.state.scenarios),
            summaries=dict(self.summaries),
            settings=self.settings,
            schema_version=SCHEMA_VERSION,
        )

    # -- scenarios, read ---------------------------------------------------- #

    def resolve(self, scenario: ScenarioId = BASE_SCENARIO) -> Resolution:
        """The concrete book a scenario evaluates as, with its audit trail.

        Returns a :class:`~cashkit.sdk.scenarios.Resolution`: ``.book`` is
        materialized (no overlays, no chain), ``.origins`` says which ancestor
        set each field, ``.diagnostics`` carries ``CK-E021`` / ``CK-E023`` /
        ``CK-E024`` for anything resolution refused to guess about.
        """
        return self.state.resolve(scenario)

    def provenance(self, item: ItemId, *, scenario: ScenarioId = BASE_SCENARIO) -> Provenance:
        """Which ancestor set each field of ``item`` in ``scenario`` (PRD §6.3)."""
        return self.state.provenance(scenario, item)

    def diff(self, left: ScenarioId, right: ScenarioId) -> ScenarioDiff:
        """Compare two scenarios semantically, from their resolved books (PRD §6.3)."""
        return self.state.diff(left, right)

    def describe_book(self, scenario: ScenarioId = BASE_SCENARIO):
        """Schema, items, measures, params and query vocabulary (PRD §6.5).

        Describes the **resolved** scenario, because that is the book a query
        would run against. Returns a
        :class:`~cashkit.model.BookDescription`; produces no diagnostics.
        """
        from .introspection import describe_book as _describe

        return _describe(
            self.resolve(scenario).book,
            scenarios=tuple(sorted(self.state.scenarios)),
            rounding_policy=self.policy.value,
            schema_version=SCHEMA_VERSION,
        )

    # -- ledger, read ------------------------------------------------------- #

    def events_for(self, scenario: ScenarioId) -> tuple[list[Event], tuple[Diagnostic, ...]]:
        """The ledger sequence this scenario sees, overlays applied.

        A live kit sees the whole ledger; a kit bound to a revision sees it
        truncated to that revision's watermark (ADR-0006). Diagnostics:
        ``CK-E006`` for an overlay targeting an actual, ``CK-E014`` for one
        naming a row the ledger does not hold.
        """
        if self.ledger is None:
            return [], ()
        watermark = self.book.ledger_watermark if self.revision is not None else None
        return self.state.resolve_events(scenario, self.ledger.facts(watermark))

    def query_events(
        self,
        where: str | None = None,
        since: date | None = None,
        until: date | None = None,
        *,
        include_voided: bool = False,
    ) -> Table:
        """Filter the ledger into a Table — see :func:`cashkit.sdk.events.query_events`."""
        from .events import query_events as _query_events

        return _query_events(self, where, since, until, include_voided=include_voided)

    def reconcile(
        self,
        until: date,
        *,
        scenario: ScenarioId = BASE_SCENARIO,
        since: date | None = None,
        measure: str = "cash",
    ) -> ReconciliationReport:
        """Compare actuals to forecast over a window — see
        :func:`cashkit.sdk.events.reconcile`."""
        from .events import reconcile as _reconcile

        return _reconcile(self, until, scenario_id=scenario, since=since, measure=measure)

    # -- execution ---------------------------------------------------------- #

    def run(
        self, scenario: ScenarioId = BASE_SCENARIO, *, cutover_override: date | None = None
    ) -> RunRef:
        """Evaluate a scenario (PRD §6.4).

        Deterministic: nothing here reads the clock, and the run is a function
        of ``(revision, scenario, engine_version, ledger_watermark)`` plus the
        tracked engine settings. ``cutover_override`` marks the run
        non-cacheable and excludes it from snapshots — it is a deliberate query,
        not a property of the model.

        Returns a :class:`RunRef`. Diagnostics ride on ``RunRef.diagnostics``;
        resolution problems (``CK-E021``, ``CK-E023``, ``CK-E024``) are folded
        in, so a run over a broken chain says so rather than quietly evaluating
        a partial book.
        """
        resolution = self.resolve(scenario)
        book = resolution.book
        if cutover_override is not None:
            book = book.model_copy(update={"cutover": cutover_override})
        events, event_diagnostics = self.events_for(scenario)
        engine = Engine(book, self.policy, tuple(events))
        result = engine.run()
        extra = tuple(resolution.diagnostics) + tuple(event_diagnostics)
        if extra:
            result = RunResult(
                book_id=result.book_id,
                periods=result.periods,
                accrual=result.accrual,
                cash=result.cash,
                diagnostics=extra + result.diagnostics,
                currencies=result.currencies,
                vat=result.vat,
            )
        return RunRef(
            scenario=scenario,
            book=book,
            result=result,
            engine=engine,
            kit=self,
            revision=self.revision,
            policy=self.policy,
        )

    def validate(self, scenario: ScenarioId = BASE_SCENARIO) -> list[Diagnostic]:
        """Every diagnostic this scenario's state produces (PRD §6.1, ADR-0034).

        Exactly ``run(scenario).diagnostics``, ordered errors-first and
        de-duplicated: the resolved book, the ledger sequence that scenario
        sees, the compile-time authoring checks and the expansion-time
        warnings, from the one implementation that produces them.
        """
        return ordered(self.run(scenario).diagnostics)

    def compare(
        self, runs: Sequence[RunRef], *, metric: str = "cash", grain: Grain | None = None
    ) -> Table:
        """One column per run of the same metric — see
        :func:`cashkit.sdk.execution.compare`."""
        from .execution import compare as _compare

        return _compare(self, runs, metric=metric, grain=grain)

    def read_export(self, path: str | Path) -> Table:
        """Read an export back in the types it was written in — see
        :func:`cashkit.sdk.execution.read_export`."""
        from .execution import read_export as _read_export

        return _read_export(self, path)

    # -- history, read ------------------------------------------------------ #

    def history(
        self,
        *,
        item: ItemId | None = None,
        scenario: ScenarioId | None = None,
        field: str | None = None,
        limit: int = 50,
    ) -> list[Revision]:
        """Revisions, newest first (PRD §6.6).

        ``item`` and ``scenario`` narrow to revisions that touched that file;
        ``field`` narrows further, to revisions in which that field of that item
        actually changed value — which is what PRD §6.6 calls ``blame``. An
        unknown field name simply never changes and returns nothing, because a
        typo must not look like a fact about the model. Produces no diagnostics.
        """
        if field is not None and field not in OVERLAY_FIELDS:
            return []
        path = _path_for(item=item, scenario=scenario)
        revisions = self.revisions.list_revisions(
            limit=limit if field is None else max(limit, 1000), path=path
        )
        if field is None or item is None:
            return revisions[:limit]
        return self._filter_by_field(revisions, item, field)[:limit]

    def _filter_by_field(
        self, revisions: Sequence[Revision], item: ItemId, field_name: str
    ) -> list[Revision]:
        """Keep revisions in which ``item.field_name`` differs from its parent's."""
        out: list[Revision] = []
        for revision in revisions:
            here = self._field_at(revision.id, item, field_name)
            there = (
                _MISSING
                if revision.parent is None
                else self._field_at(revision.parent, item, field_name)
            )
            if here != there:
                out.append(revision)
        return out

    def _field_at(self, ref: str, item: ItemId, field_name: str) -> object:
        state, _ = self.revisions.read_state(ref)
        if state is None:  # pragma: no cover - refs come from the history itself
            return _MISSING
        config, _ = load_state(state)
        if config is None:
            return _MISSING
        stored = config.book.items.get(item)
        if stored is None:
            return _MISSING
        return getattr(stored, field_name, _MISSING)

    def at(self, ref: str) -> tuple["ReadOnlyKit | None", tuple[Diagnostic, ...]]:
        """A read-only kit bound to a past revision (PRD §6.6, ADR-0033).

        The returned kit runs against the config **as committed at that
        revision** — migrated forward if it comes from an older schema
        generation (PRD §8.5) — and against the ledger truncated to that
        revision's watermark, so a correction appended afterwards is invisible
        to it. It has no write methods.

        Returns ``(kit, diagnostics)``; ``kit`` is ``None`` when the ref does
        not resolve (``CK-E027``) or the stored state cannot be read
        (``CK-E025``/``CK-E026``).
        """
        revision, reason = self.revisions.resolve(ref)
        if revision is None:
            return None, (make_diagnostic("CK-E027", ref=ref, reason=reason or "unknown"),)
        state, reason = self.revisions.read_state(revision.id)
        if state is None:  # pragma: no cover - a resolved revision always reads
            return None, (make_diagnostic("CK-E027", ref=ref, reason=reason or "unknown"),)
        config, problems = load_state(state)
        if config is None:
            return None, problems
        return (
            ReadOnlyKit(
                root=self.root,
                state=ScenarioSet(
                    book=config.book, scenarios=config.scenarios, base_id=self.state.base_id
                ),
                revisions=self.revisions,
                ledger=self.ledger,
                settings=config.settings,
                summaries=config.summaries,
                revision=revision.id,
                diagnostics=problems,
            ),
            problems,
        )

    def diff_revisions(
        self, left: str, right: str, *, scenario: ScenarioId | None = None
    ) -> RevisionDiff:
        """Compare two revisions semantically (PRD §6.6).

        Both sides are parsed into models before comparison, so a revision whose
        files were reformatted by hand diffs **empty** while ``reformatted``
        names the paths whose bytes moved. Config diff and outcome diff come
        back together, which is what PRD §10 asks a commit to show.

        Returns a :class:`RevisionDiff`. Diagnostics: ``CK-E027`` for a ref that
        does not resolve, ``CK-E025``/``CK-E026`` for state that cannot be read.
        """
        left_config, left_state, problems = self._config_at(left)
        if left_config is None:
            return RevisionDiff(left=left, right=right, scenario=scenario, diagnostics=problems)
        right_config, right_state, more = self._config_at(right)
        if right_config is None:
            return RevisionDiff(
                left=left, right=right, scenario=scenario, diagnostics=problems + more
            )

        left_book = _resolved(left_config, scenario, self.state.base_id)
        right_book = _resolved(right_config, scenario, self.state.base_id)
        items: list[ItemDiff] = []
        for item_id in sorted(set(left_book.items) | set(right_book.items)):
            here = left_book.items.get(item_id)
            there = right_book.items.get(item_id)
            if here is None:
                items.append(ItemDiff(item_id=item_id, status="added"))
            elif there is None:
                items.append(ItemDiff(item_id=item_id, status="removed"))
            else:
                fields = tuple(
                    sorted(
                        name
                        for name in OVERLAY_FIELDS
                        if getattr(here, name) != getattr(there, name)
                    )
                )
                if fields:
                    items.append(ItemDiff(item_id=item_id, status="changed", fields=fields))

        params = tuple(
            ParamDiff(
                key=key,
                left=left_book.params.get(key),
                right=right_book.params.get(key),
            )
            for key in sorted(set(left_book.params) | set(right_book.params))
            if left_book.params.get(key) != right_book.params.get(key)
        )

        paths = diff_states(left_state, right_state, left_ref=left, right_ref=right)
        return RevisionDiff(
            left=left,
            right=right,
            scenario=scenario,
            opening_balance=(
                None
                if left_book.opening_balance == right_book.opening_balance
                else (left_book.opening_balance, right_book.opening_balance)
            ),
            params=params,
            items=tuple(items),
            scenarios_added=tuple(
                sorted(set(right_config.scenarios) - set(left_config.scenarios))
            ),
            scenarios_removed=tuple(
                sorted(set(left_config.scenarios) - set(right_config.scenarios))
            ),
            scenarios_changed=tuple(
                sorted(
                    key
                    for key in set(left_config.scenarios) & set(right_config.scenarios)
                    if left_config.scenarios[key] != right_config.scenarios[key]
                )
            ),
            outcomes=_outcome_diffs(left_config, right_config, scenario),
            reformatted=tuple(
                sorted(set(paths.added) | set(paths.removed) | set(paths.changed))
            ),
            diagnostics=problems + more,
        )

    def _config_at(
        self, ref: str
    ) -> tuple[ConfigState | None, RevisionState, tuple[Diagnostic, ...]]:
        revision, reason = self.revisions.resolve(ref)
        if revision is None:
            return None, RevisionState(), (
                make_diagnostic("CK-E027", ref=ref, reason=reason or "unknown"),
            )
        state, reason = self.revisions.read_state(revision.id)
        if state is None:  # pragma: no cover - a resolved revision always reads
            return None, RevisionState(), (
                make_diagnostic("CK-E027", ref=ref, reason=reason or "unknown"),
            )
        config, problems = load_state(state)
        return config, state, problems

    def reproduce(self, ref: str, scenario: ScenarioId = BASE_SCENARIO) -> Reproduction:
        """Re-run a past revision and compare against the snapshot committed with it.

        This is the ADR-0006 guarantee made checkable. At **matching** engine
        version the recomputed summary must equal the committed one field for
        field; a difference is ``CK-E028``, because something outside
        ``(revision, scenario, engine_version, ledger_watermark)`` reached the
        computation and both numbers are now suspect. At a **differing** engine
        version the deltas are reported with ``CK-W011`` and ``reproduced`` is
        ``False`` without being an error — the engine changed, which is a fact
        about the build, not about the model.

        Returns a :class:`~cashkit.model.Reproduction`. Diagnostics:
        ``CK-E027`` for an unresolvable ref, ``CK-E025`` when the revision holds
        no snapshot for that scenario, ``CK-E028``, ``CK-W011``.
        """
        past, problems = self.at(ref)
        if past is None:
            return Reproduction(
                ref=ref,
                revision="",
                scenario=scenario,
                engine_version_recorded="",
                engine_version_current=ENGINE_VERSION,
                engine_version_matches=False,
                reproduced=False,
                diagnostics=problems,
            )
        assert past.revision is not None
        committed = past.summaries.get(scenario)
        if committed is None:
            return Reproduction(
                ref=ref,
                revision=past.revision,
                scenario=scenario,
                engine_version_recorded="",
                engine_version_current=ENGINE_VERSION,
                engine_version_matches=False,
                reproduced=False,
                diagnostics=problems
                + (
                    make_diagnostic(
                        "CK-E025",
                        field=f"{SNAPSHOTS_DIR}/{scenario}.summary.yaml",
                        path=f"{SNAPSHOTS_DIR}/{scenario}.summary.yaml",
                        reason="this revision committed no snapshot for that scenario",
                    ),
                ),
            )

        recomputed = past.run(scenario).summary()
        deltas = tuple(
            (name, str(getattr(committed.summary, name)), str(getattr(recomputed, name)))
            for name in _SUMMARY_FIELDS
            if getattr(committed.summary, name) != getattr(recomputed, name)
        )
        matches = committed.engine_version == ENGINE_VERSION
        notes = list(problems)
        if not matches:
            notes.append(
                make_diagnostic(
                    "CK-W011",
                    ref=past.revision,
                    recorded=committed.engine_version,
                    current=ENGINE_VERSION,
                )
            )
        elif deltas:
            notes.append(
                make_diagnostic(
                    "CK-E028",
                    ref=past.revision,
                    scenario=scenario,
                    reason="; ".join(
                        f"{name}: committed {was}, recomputed {now}"
                        for name, was, now in deltas
                    ),
                )
            )
        return Reproduction(
            ref=ref,
            revision=past.revision,
            scenario=scenario,
            engine_version_recorded=committed.engine_version,
            engine_version_current=ENGINE_VERSION,
            engine_version_matches=matches,
            reproduced=matches and not deltas,
            deltas=deltas,
            committed=committed.summary,
            recomputed=recomputed,
            diagnostics=tuple(notes),
        )


# --------------------------------------------------------------------------- #
# The live kit: everything above, plus writes
# --------------------------------------------------------------------------- #


@dataclass
class CashKit(ReadOnlyKit):
    """A book, its stores and its history, open for writing (PRD §6).

    Open one with :meth:`open` or create one with
    :func:`~cashkit.sdk.construction.create_book`. Every write here goes
    through :attr:`state` (the one owner of the authored book and its
    overlays), is validated at call time, returns a
    :class:`~cashkit.model.ChangeReport`, and — when it changed anything — is
    written to the §3.3 working tree before returning (ADR-0031).
    """

    # -- construction ------------------------------------------------------- #

    @classmethod
    def init(
        cls,
        root: str | Path,
        book: Book,
        *,
        settings: EngineSettings | None = None,
        ledger: LedgerStore | None = None,
        revisions: RevisionStore | None = None,
        base_id: ScenarioId = BASE_SCENARIO,
    ) -> "CashKit":
        """Create the §3.3 layout at ``root`` and return the kit on it.

        Writes the working tree and opens the ledger, but does **not** commit —
        the caller decides whether an empty book deserves a revision, and
        ``cashkit init`` makes that call for the CLI. Raises ``ValueError`` when
        ``book`` carries engine-synthesized items (programmer error: that is the
        engine's book, not the authored one).
        """
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        kit = cls(
            root=root,
            state=ScenarioSet.new(book, base_id=base_id),
            revisions=revisions if revisions is not None else _default_store(root),
            ledger=ledger if ledger is not None else SqliteLedger(root / "ledger.sqlite"),
            settings=settings or EngineSettings(),
        )
        kit._save()
        return kit

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        ledger: LedgerStore | None = None,
        revisions: RevisionStore | None = None,
    ) -> tuple["CashKit | None", tuple[Diagnostic, ...]]:
        """Open the book at ``root``.

        Returns ``(kit, diagnostics)``; ``kit`` is ``None`` when the layout
        cannot be read. Diagnostics: ``CK-E029`` when there is no book there,
        ``CK-E025``/``CK-E026`` when a stored file is malformed or comes from a
        newer schema generation. Never raises on stored content.
        """
        root = Path(root)
        if not is_book_root(root):
            return None, (make_diagnostic("CK-E029", path=str(root)),)
        config, problems = load_state(read_working_tree(root))
        if config is None:
            return None, problems
        return (
            cls(
                root=root,
                state=ScenarioSet(book=config.book, scenarios=config.scenarios),
                revisions=revisions if revisions is not None else _default_store(root),
                ledger=(
                    ledger if ledger is not None else SqliteLedger(root / "ledger.sqlite")
                ),
                settings=config.settings,
                summaries=config.summaries,
                diagnostics=problems,
            ),
            problems,
        )

    def _save(self) -> None:
        """Write the working state to the §3.3 layout on disk."""
        write_working_tree(self.root, build_state(self.config_state()))

    def _persisted(self, report: ChangeReport, extra: tuple[Diagnostic, ...] = ()) -> ChangeReport:
        """Persist after a write that recorded something; attach ``extra``."""
        if report.changed or report.created:
            self._save()
        if not extra:
            return report
        return report.model_copy(update={"diagnostics": tuple(report.diagnostics) + extra})

    def _unknown_scenario(self, scenario: ScenarioId, target: str) -> ChangeReport | None:
        if scenario in self.state.scenarios:
            return None
        return ChangeReport(
            target=target,
            diagnostics=(
                make_diagnostic(
                    "CK-E021",
                    scenario_id=scenario,
                    reason="no scenario with that id exists in this book",
                ),
            ),
        )

    # -- authoring (PRD §6.1, §6.3 — one write path, ADR-0031) -------------- #

    def set_item(
        self, item: Item, *, scenario: ScenarioId = BASE_SCENARIO, note: str = ""
    ) -> ChangeReport:
        """Write ``item`` by value into ``scenario`` (PRD §6.1, §6.3).

        ``item`` is the whole item as you want it — a flow with segments, a
        derived or stock item with a formula. An id already present is
        **re-authored**, so a construction script is idempotent; a second
        identical call reports ``CK-I002``. In base the write lands in the
        authored book; in any other scenario only the fields differing from the
        resolved parent are recorded (ADR-0009).

        Refuses, writing nothing: ``CK-E003`` (a formula that does not parse, a
        formula on a flow, a missing formula on a derived item, segments on a
        derived one), ``CK-E004`` / ``CK-E005`` (a settlement term list that
        cannot mean anything), ``CK-E011`` (an amount whose sign contradicts
        ``direction``), ``CK-E012`` (a generative stock), ``CK-E021`` (unknown
        scenario).

        Writes and reports: ``CK-E001`` (an unknown reference or a selector
        matching nothing), ``CK-E002`` (a cycle with no ``prev()`` edge),
        ``CK-E008`` (an unknown param), ``CK-E019``, ``CK-E020`` — each a
        statement about the book as a whole, which a later write can settle.

        ``note`` is accepted for signature parity and not stored; the revision
        message is where a change's reason lives. Returns a
        :class:`~cashkit.model.ChangeReport` whose ``created`` names the item
        when it is new and whose ``changed`` names the fields that moved.
        """
        missing = self._unknown_scenario(scenario, item.id)
        if missing is not None:
            return missing
        refusing = isolated_problems(item)
        if refusing:
            return ChangeReport(target=item.id, diagnostics=refusing)
        before = self.resolve(scenario).book
        report = self.state.set_item(scenario, item, note=note)
        if not (report.changed or report.created):
            return report
        reporting = new_compile_problems(before, self.resolve(scenario).book)
        return self._persisted(report, reporting)

    def remove_item(self, item_id: ItemId, *, scenario: ScenarioId = BASE_SCENARIO) -> ChangeReport:
        """Remove ``item_id`` from ``scenario`` and its descendants (PRD §6.3).

        In base the item leaves the authored book; elsewhere an inherited item
        is recorded as removed and an added one is dropped. Returns a
        :class:`~cashkit.model.ChangeReport`; ``CK-E021`` for an unknown
        scenario, ``CK-I002`` when the item is already absent. Items that read
        the removed one report ``CK-E001`` on their next run.
        """
        missing = self._unknown_scenario(scenario, item_id)
        if missing is not None:
            return missing
        return self._persisted(self.state.remove_item(scenario, item_id))

    def unset(self, item_id: ItemId, *, scenario: ScenarioId) -> ChangeReport:
        """Drop what ``scenario`` recorded about ``item_id``, reverting to the parent.

        Returns a :class:`~cashkit.model.ChangeReport` listing what was
        dropped; ``CK-E021`` for an unknown scenario, ``CK-I002`` when the
        scenario recorded nothing about the item — always the answer in base,
        which records nothing sparsely.
        """
        missing = self._unknown_scenario(scenario, item_id)
        if missing is not None:
            return missing
        return self._persisted(self.state.unset(scenario, item_id))

    def set_param(
        self,
        key: str,
        value: Decimal,
        *,
        scenario: ScenarioId = BASE_SCENARIO,
        note: str = "",
    ) -> ChangeReport:
        """Set a named scalar in ``scenario`` (PRD §6.1, §6.3).

        ``params`` is the lever surface: anything an agent might sweep must be a
        param rather than a literal inside a formula. In base this sets what
        every override falls through to; elsewhere it is recorded sparsely,
        only when it differs from the resolved parent's value.
        ``opening_balance`` is the reserved key that overrides the Book field
        (PRD §4.1) and is checked as money at the door.

        Returns a :class:`~cashkit.model.ChangeReport` whose ``changed`` is
        ``("params.<key>",)``. Diagnostics: ``CK-E007`` for a key formulas
        could not address as ``p.<key>``, ``CK-E021`` for an unknown scenario,
        ``CK-E024`` when ``opening_balance`` is not valid money, ``CK-I002``
        when the value was already this.
        """
        target = f"params.{key}"
        missing = self._unknown_scenario(scenario, target)
        if missing is not None:
            return missing
        try:
            validated_params({key: value})
        except (ValidationError, ValueError) as exc:
            return ChangeReport(
                target=target,
                diagnostics=(make_diagnostic("CK-E007", field=key, key=key, reason=reason_of(exc)),),
            )
        if key == OPENING_BALANCE_PARAM:
            try:
                _require_money(value)
            except ValueError as exc:
                return ChangeReport(
                    target=target,
                    diagnostics=(
                        make_diagnostic(
                            "CK-E024", field=key, key=key, scenario_id=scenario, reason=str(exc)
                        ),
                    ),
                )
        return self._persisted(self.state.set_param(scenario, key, value, note=note))

    def apply_macro(
        self, macro: Macro, *, scenario: ScenarioId = BASE_SCENARIO, note: str = ""
    ) -> ChangeReport:
        """Expand a macro into concrete item writes, immediately (PRD §6.3).

        ``ShiftItems``, ``ScaleItems`` and ``RetagItems`` rewrite every item
        their selector matches in the resolved scenario, and each rewritten item
        goes through :meth:`set_item` — so nothing is deferred, nothing is
        stored as a rule, and the post-macro state is indistinguishable from
        having typed the items out. ``RetagItems`` on base is what PRD §6.1
        calls ``retag``.

        Returns a :class:`~cashkit.model.ChangeReport` whose ``changed``
        entries are ``"<item_id>.<field>"``. Diagnostics: ``CK-E021``,
        ``CK-E003`` for a malformed selector, ``CK-I002`` when the macro changed
        nothing (including when the selector matched nothing).
        """
        missing = self._unknown_scenario(scenario, scenario)
        if missing is not None:
            return missing
        before = self.resolve(scenario).book
        report = self.state.apply_macro(scenario, macro, note=note)
        if not (report.changed or report.created):
            return report
        reporting = new_compile_problems(before, self.resolve(scenario).book)
        return self._persisted(report, reporting)

    def set_book(self, **fields: object) -> ChangeReport:
        """Write book-level fields of the authored book (PRD §6.1).

        Accepts ``cutover``, ``opening_balance``, ``horizon``, ``calendar`` and
        ``tax_regimes`` (the whole list, by value — a regime is replaced by
        giving the list again, the way ``segments`` is atomic). ``params`` and
        ``items`` have their own verbs; ``id`` and ``base_grain`` are fixed at
        creation. An unknown field name raises ``ValueError`` (programmer
        error).

        ``cutover`` is authored, never ``today()`` — nothing in this package
        reads the clock (ADR-0010). A cutover outside the horizon is recorded
        and warned about with ``CK-W006``, never refused: both directions are
        states an agent can mean and neither is legible from the numbers.

        Refuses, writing nothing: ``CK-E024`` (``opening_balance`` not valid
        money), ``CK-E032`` (a value that cannot make a Book — a horizon that
        is not ``start < end``, a malformed calendar), ``CK-E019`` (a regime
        unusable on its own terms). Writes and reports: ``CK-E019`` when a
        regime's selector matches nothing yet, plus any compile problem the
        change introduces. Returns a :class:`~cashkit.model.ChangeReport` whose
        ``changed`` names the fields that moved, ``CK-I002`` when none did.
        """
        unknown = sorted(set(fields) - set(BOOK_FIELDS))
        if unknown:
            raise ValueError(f"set_book accepts {BOOK_FIELDS}; got {unknown}")
        target = "book"
        refusing: list[Diagnostic] = []
        if "opening_balance" in fields:
            try:
                _require_money(fields["opening_balance"])  # type: ignore[arg-type]
            except (ValueError, TypeError) as exc:
                refusing.append(
                    make_diagnostic(
                        "CK-E024",
                        field="opening_balance",
                        key="opening_balance",
                        scenario_id=self.book.id,
                        reason=str(exc),
                    )
                )
        for regime in fields.get("tax_regimes", ()) or ():  # type: ignore[union-attr]
            problem = regime_problem(regime)
            if problem is not None:
                refusing.append(problem)
        if refusing:
            return ChangeReport(target=target, diagnostics=tuple(refusing))
        try:
            candidate = Book.model_validate({**self.book.model_dump(), **fields})
        except (ValidationError, ValueError) as exc:
            return ChangeReport(
                target=target,
                diagnostics=(make_diagnostic("CK-E032", reason=reason_of(exc)),),
            )
        before = self.book
        moved = self.state.set_book(**{name: getattr(candidate, name) for name in fields})
        if not moved:
            return ChangeReport(
                target=target,
                diagnostics=(make_diagnostic("CK-I002", field=", ".join(sorted(fields))),),
            )
        # The compile delta carries CK-W006 for a cutover that left the horizon
        # (the same check every run makes) and CK-E019 for a regime whose
        # selector matches nothing yet, alongside anything else the change
        # introduced — one source for the warning, never two copies of it.
        reporting = new_compile_problems(before, self.book)
        return self._persisted(ChangeReport(target=target, changed=moved), reporting)

    def fork(self, new_id: ScenarioId, *, parent: ScenarioId = BASE_SCENARIO, note: str = "") -> ChangeReport:
        """Fork ``parent`` into a new empty scenario ``new_id`` (PRD §6.3).

        Returns a :class:`~cashkit.model.ChangeReport` whose ``created`` names
        the new scenario. Diagnostics: ``CK-E021`` when the parent does not
        exist, ``CK-E022`` when ``new_id`` is taken. Forking base is the same
        operation as forking anything else.
        """
        return self._persisted(self.state.fork(parent, new_id, note=note))

    def flatten(self, new_id: ScenarioId, *, scenario: ScenarioId, note: str = "") -> ChangeReport:
        """Collapse ``scenario``'s chain into a standalone ``new_id`` (PRD §6.3).

        The result has ``parent=None`` and resolves to exactly the same Book.
        Returns a :class:`~cashkit.model.ChangeReport`; ``CK-E021`` for an
        unknown source, ``CK-E022`` when ``new_id`` is taken.
        """
        return self._persisted(self.state.flatten(scenario, new_id, note=note))

    # -- ledger (PRD §6.2) --------------------------------------------------- #
    #
    # The store owns append-only-ness and ``UNIQUE(source, ext_id)``; the kit
    # passes through so the ledger is reached from the one object an agent
    # holds, and so a read-only kit — which shares the live ledger — has no
    # path to it (ADR-0033).

    def _ledger(self) -> LedgerStore:
        if self.ledger is None:
            raise ValueError(
                "this kit has no ledger store; ledger operations need one "
                "(construct the kit with ledger=..., or open a book root)"
            )
        return self.ledger

    def add_event(self, event: Event) -> ChangeReport:
        """Append one event to the ledger (PRD §6.2).

        Returns the store's :class:`~cashkit.model.ChangeReport`. Diagnostics:
        ``CK-E010`` for a taken ``(source, ext_id)``, ``CK-E015`` for a taken
        id, ``CK-I002`` for an identical re-add.
        """
        return self._ledger().add_event(event)

    def import_events(self, rows: Iterable[Event], source: str) -> ChangeReport:
        """Idempotent batch import keyed on ``(source, ext_id)`` (PRD §6.2).

        Returns the store's :class:`~cashkit.model.ImportReport`; any conflict
        aborts the whole batch (ADR-0008). Diagnostics: ``CK-E010``, ``CK-E017``.
        """
        return self._ledger().import_events(rows, source)

    def void_event(self, event_id: EventId, note: str) -> ChangeReport:
        """Tombstone a committed/forecast event (PRD §6.2).

        Diagnostics: ``CK-E014``, ``CK-E015``, ``CK-E016`` (an actual is
        corrected, never voided — ADR-0012).
        """
        return self._ledger().void_event(event_id, note)

    def correct_event(self, event_id: EventId, corrected: Event, note: str) -> ChangeReport:
        """Tombstone an event and append its correction, atomically (ADR-0012).

        Diagnostics: ``CK-E014``, ``CK-E015``.
        """
        return self._ledger().correct_event(event_id, corrected, note)

    # -- version control ---------------------------------------------------- #

    def commit(
        self,
        message: str,
        *,
        scenarios: Sequence[ScenarioId] | None = None,
        author: str = "agent",
        timestamp: Timestamp | None = None,
    ) -> CommitReport:
        """Serialize state, recompute snapshots, record a revision (PRD §6.6).

        Takes the single-writer lock for the whole operation (ADR-0010): the
        config store, the ledger watermark and the snapshots are one consistency
        domain, and a second writer interleaving between them is exactly the
        silent merge this refuses to do. Stamps the ledger watermark — only
        ``commit()`` ever does (ADR-0006) — and recomputes the affected
        scenarios' snapshots so the config diff and the outcome diff land in the
        same revision.

        Returns a :class:`CommitReport` whose ``revision`` is ``None`` when the
        tree was unchanged. Diagnostics: ``CK-E013`` when another writer holds
        the lock (the second writer fails loudly and never merges), ``CK-W010``
        when a dead writer's lock was reclaimed, ``CK-I002`` when nothing
        changed, plus every error-severity diagnostic of a recomputed run.
        """
        with WriterLock(self.root, timestamp=timestamp) as lock:
            if not lock.acquired:
                return CommitReport(target=message, diagnostics=lock.diagnostics)
            notes = list(lock.diagnostics)

            targets = list(scenarios) if scenarios is not None else sorted(
                self.state.scenarios
            )
            watermark = self.ledger.watermark() if self.ledger is not None else None
            for scenario_id in targets:
                run = self.run(scenario_id)
                notes.extend(d for d in run.diagnostics if d.severity == "error")
                self.summaries[scenario_id] = CommittedSummary(
                    scenario=scenario_id,
                    engine_version=ENGINE_VERSION,
                    schema_version=SCHEMA_VERSION,
                    watermark=watermark,
                    summary=run.summary(),
                )

            state = build_state(self.config_state(watermark_from_ledger=True))
            write_working_tree(self.root, state)
            revision = self.revisions.write_revision(
                state,
                message=message,
                author=author,
                metadata={
                    "engine-version": ENGINE_VERSION,
                    "schema-version": str(SCHEMA_VERSION),
                    "watermark": "" if watermark is None else watermark.content_hash,
                },
                timestamp=timestamp,
            )

        if revision is None:
            return CommitReport(
                target=message,
                revision=None,
                diagnostics=tuple(notes) + (make_diagnostic("CK-I002"),),
            )
        # The stamped watermark is now part of the committed book; adopt it so a
        # second commit with no other change is correctly reported as empty.
        self.state.book = self.book.model_copy(update={"ledger_watermark": watermark})
        return CommitReport(
            target=message,
            revision=revision,
            created=(revision.id,),
            changed=tuple(sorted(state.paths())),
            diagnostics=tuple(notes),
        )

    def status(self) -> WorkingState:
        """The uncommitted difference between the working state and HEAD.

        Structured, never a git porcelain string (PRD §6.6). Compares the
        in-memory state to the revision it was last committed at, item by item
        and param by param, so an agent can say *what* is uncommitted rather
        than *that something* is. Diagnostics: ``CK-E025``/``CK-E026`` when the
        committed state cannot be read back.
        """
        head = self.revisions.head()
        current = build_state(self.config_state())
        if head is None:
            return WorkingState(
                revision=None,
                clean=False,
                items_added=tuple(sorted(self.book.items)),
                scenarios_changed=tuple(sorted(self.state.scenarios)),
                paths_changed=current.paths(),
            )

        stored, reason = self.revisions.read_state(head.id)
        if stored is None:
            return WorkingState(
                revision=head.id,
                clean=False,
                diagnostics=(
                    make_diagnostic("CK-E027", ref=head.id, reason=reason or "unreadable"),
                ),
            )
        committed, problems = load_state(stored)
        if committed is None:
            return WorkingState(revision=head.id, clean=False, diagnostics=problems)

        paths = diff_states(stored, current)
        mine = self.config_state()
        state = _compare_states(committed, mine)
        return WorkingState(
            revision=head.id,
            clean=not (
                state["items_added"]
                or state["items_removed"]
                or state["items_changed"]
                or state["params_changed"]
                or state["book_fields_changed"]
                or state["scenarios_changed"]
                or state["settings_changed"]
            ),
            paths_changed=tuple(
                sorted(set(paths.added) | set(paths.removed) | set(paths.changed))
            ),
            diagnostics=problems,
            **state,
        )

    def discard(self, items: Iterable[ItemId] | None = None) -> ChangeReport:
        """Throw uncommitted work away, restoring from HEAD (PRD §6.6).

        ``items=None`` restores everything — book, params, scenarios, settings.
        Naming items restores only those, leaving every other uncommitted change
        in place. Returns a :class:`ChangeReport` listing what was restored;
        ``CK-I002`` when nothing was uncommitted, ``CK-E027`` when HEAD does not
        resolve (a history with no revisions has nothing to discard *to*).
        """
        head = self.revisions.head()
        if head is None:
            return ChangeReport(
                target="discard",
                diagnostics=(
                    make_diagnostic(
                        "CK-E027",
                        ref="HEAD",
                        reason="the history has no revisions to discard back to",
                    ),
                ),
            )
        stored, reason = self.revisions.read_state(head.id)
        if stored is None:  # pragma: no cover - head always reads back
            return ChangeReport(
                target="discard",
                diagnostics=(
                    make_diagnostic("CK-E027", ref=head.id, reason=reason or "unreadable"),
                ),
            )
        committed, problems = load_state(stored)
        if committed is None:
            return ChangeReport(target="discard", diagnostics=problems)

        if items is None:
            before = self.config_state()
            self.state = ScenarioSet(
                book=committed.book, scenarios=committed.scenarios, base_id=self.state.base_id
            )
            self.settings = committed.settings
            self.summaries = dict(committed.summaries)
            restored = _restored_names(_compare_states(committed, before))
        else:
            wanted = sorted(set(items))
            merged = dict(self.book.items)
            restored = []
            for item_id in wanted:
                stored_item = committed.book.items.get(item_id)
                if stored_item == merged.get(item_id):
                    continue
                if stored_item is None:
                    merged.pop(item_id, None)
                else:
                    merged[item_id] = stored_item
                restored.append(item_id)
            self.state.book = self.book.model_copy(update={"items": merged})

        self._save()
        if not restored:
            return ChangeReport(target="discard", diagnostics=(make_diagnostic("CK-I002"),))
        return ChangeReport(target="discard", changed=tuple(restored))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_MISSING = object()


def _default_store(root: Path) -> RevisionStore:
    """The v1 revision store. The one place the git implementation is chosen."""
    from cashkit.stores.git_store import GitRevisionStore

    return GitRevisionStore(root)


def _path_for(*, item: ItemId | None, scenario: ScenarioId | None) -> str | None:
    if item is not None:
        return f"{ITEMS_DIR}/{item}.yaml"
    if scenario is not None:
        return f"{SCENARIOS_DIR}/{scenario}.yaml"
    return None


def _resolved(config: ConfigState, scenario: ScenarioId | None, base_id: ScenarioId) -> Book:
    """The book a diff should compare: the authored one, or a resolved scenario."""
    if scenario is None:
        return config.book
    return ScenarioSet(
        book=config.book, scenarios=config.scenarios, base_id=base_id
    ).resolve(scenario).book


def _outcome_diffs(
    left: ConfigState, right: ConfigState, scenario: ScenarioId | None
) -> tuple[OutcomeDiff, ...]:
    keys = sorted(set(left.summaries) | set(right.summaries))
    if scenario is not None:
        keys = [key for key in keys if key == scenario]
    out: list[OutcomeDiff] = []
    for key in keys:
        here = left.summaries.get(key)
        there = right.summaries.get(key)
        if here is None or there is None:
            out.append(
                OutcomeDiff(
                    scenario=key,
                    fields=("snapshot",),
                    left=here.summary if here else None,
                    right=there.summary if there else None,
                )
            )
            continue
        fields = tuple(
            name
            for name in _SUMMARY_FIELDS
            if getattr(here.summary, name) != getattr(there.summary, name)
        )
        out.append(
            OutcomeDiff(
                scenario=key,
                fields=fields,
                left=here.summary,
                right=there.summary,
                engine_version_changed=here.engine_version != there.engine_version,
            )
        )
    return tuple(out)


def _compare_states(committed: ConfigState, current: ConfigState) -> dict[str, tuple]:
    """Field-level comparison of two config states, for ``status`` and ``discard``."""
    here, there = committed.book, current.book
    book_fields = tuple(
        name
        for name in ("base_grain", "calendar", "horizon", "opening_balance", "cutover",
                     "tax_regimes")
        if getattr(here, name) != getattr(there, name)
    )
    return {
        "items_added": tuple(sorted(set(there.items) - set(here.items))),
        "items_removed": tuple(sorted(set(here.items) - set(there.items))),
        "items_changed": tuple(
            sorted(
                item_id
                for item_id in set(here.items) & set(there.items)
                if here.items[item_id] != there.items[item_id]
            )
        ),
        "params_changed": tuple(
            sorted(
                key
                for key in set(here.params) | set(there.params)
                if here.params.get(key) != there.params.get(key)
            )
        ),
        "book_fields_changed": book_fields,
        "scenarios_changed": tuple(
            sorted(
                key
                for key in set(committed.scenarios) | set(current.scenarios)
                if committed.scenarios.get(key) != current.scenarios.get(key)
            )
        ),
        "settings_changed": (
            ()
            if committed.settings == current.settings
            else ("rounding_policy",)
        ),
    }


def _restored_names(state: dict[str, tuple]) -> list[str]:
    names: list[str] = []
    for key in (
        "items_added",
        "items_removed",
        "items_changed",
        "params_changed",
        "book_fields_changed",
        "scenarios_changed",
        "settings_changed",
    ):
        names.extend(state[key])
    return sorted(set(names))
