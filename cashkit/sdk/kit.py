"""``CashKit`` — the kit a book is opened as, and its version control (PRD §6.6).

``at()`` returns a **kit, not a book**, so ``kit.at("HEAD~5").run("downside")
.summary()`` works and eras of the model compare through one API. That is the
whole shape of this module: one object holding a book root, its three stores and
its scenarios, plus a read-only twin of itself bound to a past revision.

**Git never appears here.** Every version-control operation goes through
:class:`~cashkit.stores.revisions.RevisionStore` (ADR-0018); this module does not
import ``pygit2``, does not shell out, and takes no ref-spec other than the
opaque ``ref`` string. Swapping the git store for an append-only SQLite one is a
constructor argument.

**What a run is identified by** (PRD §6.6): ``(revision, scenario,
engine_version, ledger_watermark)``. Each of the four is recoverable from the
revision alone — the config schema and the engine settings that change the
numbers are tracked (``.cashkit/config.toml``, D-P9-04), ``engine_version`` and
the watermark are recorded in the committed snapshot, and the watermark is
stamped by ``commit()`` and never by an import (ADR-0006). A live run always sees
the whole ledger; only a run through ``at(ref)`` truncates it.

**Reproduction is checked, never assumed.** :meth:`CashKit.reproduce` re-runs a
revision and compares against the snapshot committed with it. At matching engine
version a difference is an error (``CK-E028``) — something outside the four-tuple
reached the computation. At a differing engine version the delta is *reported*
(``CK-W011``), which is the ADR-0006 rule: never a silent failure in either
direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from cashkit.engine import ENGINE_VERSION, Engine, RoundingPolicy, RunResult
from cashkit.model import (
    Book,
    ChangeReport,
    Diagnostic,
    Event,
    ItemDiff,
    ItemId,
    OutcomeDiff,
    ParamDiff,
    Reproduction,
    RevisionDiff,
    RunSummary,
    WorkingState,
)
from cashkit.model.diagnostics import make_diagnostic
from cashkit.model.primitives import ScenarioId
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

from .scenarios import OVERLAY_FIELDS, ScenarioSet
from .views import summary as summarize

__all__ = ["BASE_SCENARIO", "CashKit", "CommitReport", "RunRef"]

#: The scenario every book starts with. Base is a scenario with ``parent=None``;
#: it is privileged in storage only (ADR-0007).
BASE_SCENARIO: ScenarioId = "base"

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


@dataclass(frozen=True)
class RunRef:
    """A completed run, and the handles onto it (PRD §6.4).

    Holds the resolved book the run evaluated — **not** the engine's augmented
    one — alongside the engine, so introspection can reach the compiled graph
    without recompiling and without the caller having to keep two books straight
    (D-P5-10, D-P7-05).
    """

    scenario: ScenarioId
    book: Book
    result: RunResult
    engine: Engine
    revision: str | None = None
    policy: RoundingPolicy = RoundingPolicy.HALF_UP

    def summary(self, **kwargs: object) -> RunSummary:
        """The headline numbers of this run (PRD §6.4).

        Delegates to :func:`cashkit.sdk.views.summary`; see it for what min
        cash, runway and breakeven mean. Diagnostics: every error-severity
        diagnostic of the run is carried through.
        """
        return summarize(self.result, self.book, **kwargs)  # type: ignore[arg-type]

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """The run's diagnostics. No diagnostics of its own."""
        return self.result.diagnostics

    def trace(self, item: ItemId, period, *, measure: str = "accrual", depth: int = 3):
        """Explain one cell of this run — see :func:`cashkit.sdk.trace`."""
        from .introspection import trace as _trace

        return _trace(self, item, period, measure=measure, depth=depth)

    def why_zero(self, item: ItemId, period, *, measure: str = "cash"):
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


@dataclass
class CashKit:
    """A book, its stores and its history (PRD §6).

    Open one with :meth:`open` or create one with :meth:`init`. ``scenarios``
    is the Phase 7 :class:`~cashkit.sdk.scenarios.ScenarioSet`, holding the
    **authored** book; the engine's augmented book never leaves a run.

    The *working state* is what this kit holds in memory. :meth:`save` writes it
    to the §3.3 layout so another process — the CLI, a human's editor — can see
    it, and :meth:`commit` records it as a revision. Exploratory sweeping stays
    in memory and costs nothing (PRD §6.7).
    """

    root: Path
    scenarios: ScenarioSet
    revisions: RevisionStore
    ledger: LedgerStore | None = None
    settings: EngineSettings = field(default_factory=EngineSettings)
    summaries: dict[ScenarioId, CommittedSummary] = field(default_factory=dict)
    #: Set when this kit is bound to a past revision: every write refuses, and
    #: the ledger is truncated to that revision's watermark (ADR-0006).
    bound_to: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

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
            scenarios=ScenarioSet.new(book, base_id=base_id),
            revisions=revisions if revisions is not None else _default_store(root),
            ledger=ledger if ledger is not None else SqliteLedger(root / "ledger.sqlite"),
            settings=settings or EngineSettings(),
        )
        kit.save()
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
                scenarios=ScenarioSet(book=config.book, scenarios=config.scenarios),
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

    # -- state -------------------------------------------------------------- #

    @property
    def book(self) -> Book:
        """The authored book. Never the engine's augmented one. No diagnostics."""
        return self.scenarios.book

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
            scenarios=dict(self.scenarios.scenarios),
            summaries=dict(self.summaries),
            settings=self.settings,
            schema_version=SCHEMA_VERSION,
        )

    def save(self) -> None:
        """Write the working state to the §3.3 layout on disk. No diagnostics."""
        write_working_tree(self.root, build_state(self.config_state()))

    # -- execution ---------------------------------------------------------- #

    def events_for(self, scenario_id: ScenarioId) -> tuple[list[Event], tuple[Diagnostic, ...]]:
        """The ledger sequence this scenario sees, overlays applied.

        A live kit sees the whole ledger; a kit bound to a revision sees it
        truncated to that revision's watermark (ADR-0006). Diagnostics:
        ``CK-E006`` for an overlay targeting an actual, ``CK-E014`` for one
        naming a row the ledger does not hold.
        """
        if self.ledger is None:
            return [], ()
        watermark = self.book.ledger_watermark if self.bound_to is not None else None
        return self.scenarios.resolve_events(scenario_id, self.ledger.facts(watermark))

    def run(
        self, scenario_id: ScenarioId = BASE_SCENARIO, *, cutover_override: object = None
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
        resolution = self.scenarios.resolution(scenario_id)
        book = resolution.book
        if cutover_override is not None:
            book = book.model_copy(update={"cutover": cutover_override})
        events, event_diagnostics = self.events_for(scenario_id)
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
            scenario=scenario_id,
            book=book,
            result=result,
            engine=engine,
            revision=self.bound_to,
            policy=self.policy,
        )

    # -- introspection ------------------------------------------------------ #

    def validate(self, scenario_id: ScenarioId = BASE_SCENARIO) -> list[Diagnostic]:
        """Every diagnostic this book's state produces (PRD §6.1).

        Validates the **resolved** scenario against the ledger sequence that
        scenario sees, so ``CK-W003`` (an actual dated on or after cutover) and
        ``CK-E018`` (an event on an item that cannot carry it) are visible —
        both are statements about the book and the ledger together. Resolution's
        own diagnostics are folded in, so a broken chain is reported rather than
        validated around.

        Returns the diagnostics sorted errors-first. See
        :func:`cashkit.sdk.validate` for the full list of codes.
        """
        from .validation import validate as _validate

        resolution = self.scenarios.resolution(scenario_id)
        events, event_diagnostics = self.events_for(scenario_id)
        found = list(resolution.diagnostics) + list(event_diagnostics)
        found.extend(_validate(resolution.book, events=events, policy=self.policy))
        return found

    def describe_book(self, scenario_id: ScenarioId = BASE_SCENARIO):
        """Schema, items, measures, params and query vocabulary (PRD §6.5).

        Describes the **resolved** scenario, because that is the book a query
        would run against. Returns a
        :class:`~cashkit.model.BookDescription`; produces no diagnostics.
        """
        from .introspection import describe_book as _describe

        return _describe(
            self.scenarios.resolution(scenario_id).book,
            scenarios=tuple(sorted(self.scenarios.scenarios)),
            rounding_policy=self.policy.value,
            schema_version=SCHEMA_VERSION,
        )

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
        if self.bound_to is not None:
            return CommitReport(target=message, diagnostics=(_read_only(self.bound_to),))

        with WriterLock(self.root, timestamp=timestamp) as lock:
            if not lock.acquired:
                return CommitReport(target=message, diagnostics=lock.diagnostics)
            notes = list(lock.diagnostics)

            targets = list(scenarios) if scenarios is not None else sorted(
                self.scenarios.scenarios
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
        self.scenarios.book = self.book.model_copy(
            update={"ledger_watermark": watermark}
        )
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
                scenarios_changed=tuple(sorted(self.scenarios.scenarios)),
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
        if self.bound_to is not None:
            return ChangeReport(target="discard", diagnostics=(_read_only(self.bound_to),))
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
            self.scenarios = ScenarioSet(
                book=committed.book, scenarios=committed.scenarios
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
            self.scenarios.book = self.book.model_copy(update={"items": merged})

        self.save()
        if not restored:
            return ChangeReport(target="discard", diagnostics=(make_diagnostic("CK-I002"),))
        return ChangeReport(target="discard", changed=tuple(restored))

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
        actually changed value — which is what makes ``blame()`` a one-liner on
        top of this. Produces no diagnostics.
        """
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

    def blame(self, item: ItemId, field_name: str) -> list[Revision]:
        """Every revision in which one field of one item changed (PRD §6.6).

        Newest first. An empty list means the field has never moved, which is a
        different statement from the item never having existed — use
        :meth:`history` with ``item=`` for that. Produces no diagnostics; an
        unknown field name simply never changes and returns nothing, because a
        typo must not look like a fact about the model.
        """
        if field_name not in OVERLAY_FIELDS:
            return []
        return self.history(item=item, field=field_name, limit=10_000)

    def at(self, ref: str) -> tuple["CashKit | None", tuple[Diagnostic, ...]]:
        """A read-only kit bound to a past revision (PRD §6.6).

        The returned kit runs against the config **as committed at that
        revision** — migrated forward if it comes from an older schema
        generation (PRD §8.5) — and against the ledger truncated to that
        revision's watermark, so a correction appended afterwards is invisible
        to it. That is deliberate: ``at()`` reproduces what was believed then,
        errors included (ADR-0012).

        Returns ``(kit, diagnostics)``; ``kit`` is ``None`` when the ref does
        not resolve (``CK-E027``) or the stored state cannot be read
        (``CK-E025``/``CK-E026``). Every write on the returned kit refuses with
        ``CK-E030``.
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
            CashKit(
                root=self.root,
                scenarios=ScenarioSet(book=config.book, scenarios=config.scenarios),
                revisions=self.revisions,
                ledger=self.ledger,
                settings=config.settings,
                summaries=config.summaries,
                bound_to=revision.id,
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

        left_book = _resolved(left_config, scenario)
        right_book = _resolved(right_config, scenario)
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

    # -- reproduction ------------------------------------------------------- #

    def reproduce(
        self, ref: str, scenario_id: ScenarioId = BASE_SCENARIO
    ) -> Reproduction:
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
                scenario=scenario_id,
                engine_version_recorded="",
                engine_version_current=ENGINE_VERSION,
                engine_version_matches=False,
                reproduced=False,
                diagnostics=problems,
            )
        assert past.bound_to is not None
        committed = past.summaries.get(scenario_id)
        if committed is None:
            return Reproduction(
                ref=ref,
                revision=past.bound_to,
                scenario=scenario_id,
                engine_version_recorded="",
                engine_version_current=ENGINE_VERSION,
                engine_version_matches=False,
                reproduced=False,
                diagnostics=problems
                + (
                    make_diagnostic(
                        "CK-E025",
                        field=f"{SNAPSHOTS_DIR}/{scenario_id}.summary.yaml",
                        path=f"{SNAPSHOTS_DIR}/{scenario_id}.summary.yaml",
                        reason="this revision committed no snapshot for that scenario",
                    ),
                ),
            )

        recomputed = past.run(scenario_id).summary()
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
                    ref=past.bound_to,
                    recorded=committed.engine_version,
                    current=ENGINE_VERSION,
                )
            )
        elif deltas:
            notes.append(
                make_diagnostic(
                    "CK-E028",
                    ref=past.bound_to,
                    scenario=scenario_id,
                    reason="; ".join(
                        f"{name}: committed {was}, recomputed {now}"
                        for name, was, now in deltas
                    ),
                )
            )
        return Reproduction(
            ref=ref,
            revision=past.bound_to,
            scenario=scenario_id,
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
# Helpers
# --------------------------------------------------------------------------- #

_MISSING = object()


def _default_store(root: Path) -> RevisionStore:
    """The v1 revision store. The one place the git implementation is chosen."""
    from cashkit.stores.git_store import GitRevisionStore

    return GitRevisionStore(root)


def _read_only(ref: str) -> Diagnostic:
    return make_diagnostic("CK-E030", ref=ref)


def _path_for(*, item: ItemId | None, scenario: ScenarioId | None) -> str | None:
    if item is not None:
        return f"{ITEMS_DIR}/{item}.yaml"
    if scenario is not None:
        return f"{SCENARIOS_DIR}/{scenario}.yaml"
    return None


def _resolved(config: ConfigState, scenario: ScenarioId | None) -> Book:
    """The book a diff should compare: the authored one, or a resolved scenario."""
    if scenario is None:
        return config.book
    return ScenarioSet(book=config.book, scenarios=config.scenarios).resolve(scenario)


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
