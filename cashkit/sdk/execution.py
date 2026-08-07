"""The §6.4 execution surface: ``frame``, ``pivot``, ``compare``, ``export``.

``run()`` and ``summary()`` were already reachable from the object an agent
holds. These four were not: they existed only on
:class:`~cashkit.stores.frames.DuckdbFrameStore`, which is a *store* — below the
SDK line, reached by importing a module the §6 surface never names. An agent
following PRD §6 could evaluate a book and could not tabulate it.

This module is the wiring, and it is deliberately thin. Aggregation rules,
selector joins, the ``DECIMAL(18,4)`` path and Parquet export all stay in the
frame store; nothing here recomputes a number. What it adds is the three things
a store cannot do for itself:

* **The run becomes a frame without anyone saying so.** The store takes a
  ``run_id`` and expects the caller to have materialized it. Here a
  :class:`~cashkit.sdk.kit.RunRef` is the argument, and materialization happens
  on the way through, keyed on the PRD §6.6 four-tuple.
* **DuckDB stays optional.** ``duckdb`` is imported inside
  :mod:`cashkit.stores.frames` and nowhere else, and that module is imported
  *lazily* from here. On a core install these four calls return ``CK-E033`` —
  a diagnostic naming the extra to install — rather than an ``ImportError``
  from three frames down. ``summary()``, ``trace()`` and ``why_zero()`` are
  unaffected: they never needed the extra.
* **Agent-authored strings are validated first.** The store raises
  ``ValueError`` for a malformed selector and says so in its own docstring:
  "selectors an agent authors are validated by the SDK first". This is that
  layer — ``where=`` goes through the one §5.4 grammar and comes back as
  ``CK-E003``.

A **revision-bound kit reads normally**. ``at(ref)`` refuses writes with
``CK-E030``; a frame is a read, and the run key carries the revision, so
``kit.at(ref).frame(kit.at(ref).run())`` tabulates that revision's numbers and
cannot collide with the live ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from cashkit.engine import ENGINE_VERSION
from cashkit.model import ChangeReport, Diagnostic, Grain, Table
from cashkit.model.diagnostics import make_diagnostic

from .macros import resolve_selector

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cashkit.stores.frames import FrameStore

    from .kit import CashKit, RunRef

__all__ = ["EXPORTS_DIR", "ExportReport", "compare", "export", "frame", "pivot"]

#: Where a relative export path lands, per the PRD §3.3 layout. Git-ignored:
#: an export is a copy of what a revision already reproduces.
EXPORTS_DIR = "exports"


class ExportReport(ChangeReport):
    """What ``export()`` wrote (PRD §6.4, §6.5).

    §6.4 types ``export`` as ``-> Path`` and §6.5 requires every fallible
    operation to return diagnostics rather than raise. Both are honoured the way
    ``commit()`` already does it (D-P9-09, C-S55-01): ``path`` is the ``Path``
    — ``None`` exactly when nothing was written — and the diagnostics channel
    carries ``CK-E033`` when the extra is absent.
    """

    path: Path | None = None

    model_config = ChangeReport.model_config | {"arbitrary_types_allowed": True}


# --------------------------------------------------------------------------- #
# Getting a run into a store
# --------------------------------------------------------------------------- #


def run_key(run: "RunRef") -> str:
    """The cache key a run materializes under (PRD §6.6).

    ``(revision, scenario, engine_version, ledger_watermark)`` is the PRD's own
    four-tuple, plus the effective ``cutover``: a ``cutover_override`` run is
    the same four-tuple as the run without it and must not overwrite it, since
    the PRD calls it "a deliberate query, not a property of the model".

    A live kit has no revision, so its key says ``working``. That is honest
    rather than unique — two different working states share a key — which is why
    every call here **re-materializes before reading**. The key exists to keep
    distinct runs apart inside one store, not to avoid recomputation; PRD §5.2
    makes recomputation the cheap option.

    Returns the key; produces no diagnostics.
    """
    watermark = run.book.ledger_watermark
    return "|".join(
        (
            run.revision or "working",
            run.scenario,
            ENGINE_VERSION,
            str(watermark.max_rowid) if watermark is not None else "-",
            run.book.cutover.isoformat(),
        )
    )


def _store(kit: "CashKit") -> tuple["FrameStore | None", Diagnostic | None]:
    """The kit's frame store, opened on first use.

    The import is local **and** the failure is a diagnostic: those are two
    separate requirements. Local, so a core install can import
    :mod:`cashkit.sdk` at all; a diagnostic, so an agent that asked for a pivot
    on a core install is told which extra to install instead of catching an
    ``ImportError`` raised three frames below the surface it is coding against.

    **In memory, not** ``frames.duckdb``. The frame is a derived cache — every
    call here re-materializes the run before reading it, so persistence buys no
    correctness — and a kit holding the file open would collide with the two
    other things that legitimately want it: ``cashkit serve``'s read-only Quack
    connection, and the kit ``at(ref)`` returns, which shares this kit's root.
    ``DuckdbFrameStore(root / "frames.duckdb")`` remains available to anyone who
    wants the on-disk store; nothing about it changed.

    Returns ``(store, None)`` or ``(None, CK-E033)``.
    """
    if kit.frames is not None:
        return kit.frames, None
    try:
        from cashkit.stores.frames import DuckdbFrameStore
    except ImportError as exc:
        return None, make_diagnostic("CK-E033", reason=str(exc))
    kit.frames = DuckdbFrameStore(policy=kit.policy)
    return kit.frames, None


def _materialized(
    kit: "CashKit", run: "RunRef", *, key: str | None = None
) -> tuple["FrameStore | None", str, tuple[Diagnostic, ...]]:
    """Write ``run`` into the kit's store and return ``(store, run_id, diags)``."""
    store, problem = _store(kit)
    if store is None:
        return None, "", (problem,)  # type: ignore[arg-type]
    run_id = key if key is not None else run_key(run)
    diagnostics = store.materialize(
        run_id,
        run.result,
        run.book,
        scenario=run.scenario,
        engine_version=ENGINE_VERSION,
    )
    return store, run_id, tuple(diagnostics)


def _refused(diagnostics: tuple[Diagnostic, ...], columns: tuple[str, ...]) -> Table:
    """An empty table carrying why it is empty."""
    return Table(columns=columns, diagnostics=diagnostics)


# --------------------------------------------------------------------------- #
# The four verbs
# --------------------------------------------------------------------------- #


def frame(
    kit: "CashKit",
    run: "RunRef",
    *,
    grain: Grain | None = None,
    measures: Sequence[str] | None = None,
    where: str | None = None,
    status: str | None = None,
    include_synthetic: bool = True,
) -> Table:
    """The run's tidy/long frame, aggregated and sliced (PRD §6.4, §5.5).

    One row per ``(period, item, measure, status)``, columns
    ``(period_start, period_end, item_id, measure, value, currency, status)``,
    money as ``Decimal``. ``period_end`` is exclusive throughout (C-P8-01).

    ``grain`` aggregates to a coarser calendar bucket under each item's
    ``agg_rule`` — flows sum, stocks take the last period in the bucket.
    ``where`` is a §5.4 selector resolved against the tag dimension by join.
    ``status`` and ``measures`` filter rows. ``include_synthetic=False`` drops
    the engine's ``_tax:`` and ``_event:`` carriers, which carry real cash: the
    default keeps them so the frame sums to the model.

    Returns a :class:`~cashkit.model.Table`. Diagnostics: ``CK-E033`` when the
    duckdb extra is absent, ``CK-E003`` for a malformed selector, ``CK-E020``
    from materialization. Raises ``ValueError`` for an unknown measure or grain
    — a fixed vocabulary ``describe_book()`` lists, so a wrong one is programmer
    error (PRD §6.5), unlike a selector, which an agent composes.
    """
    if where is not None:
        _, problem = resolve_selector(where, dict(run.book.items))
        if problem is not None:
            return _refused((problem,), _FRAME_COLUMNS)
    store, run_id, diagnostics = _materialized(kit, run)
    if store is None:
        return _refused(diagnostics, _FRAME_COLUMNS)
    table = store.frame(
        run_id,
        grain=grain,
        measures=measures,
        where=where,
        status=status,
        include_synthetic=include_synthetic,
    )
    return table if not diagnostics else _with(table, diagnostics)


def pivot(
    kit: "CashKit",
    run: "RunRef",
    *,
    index: str = "period",
    columns: str = "tag:customer",
    values: str = "cash",
    grain: Grain | None = None,
) -> Table:
    """A wide view of one measure (PRD §6.4).

    ``index`` is ``"period"`` or ``"item"``; ``columns`` is ``"tag:<key>"``,
    ``"item"`` or ``"measure"``. Column order is the sorted distinct value set,
    so the same query gives the same table every time, and items carrying no
    value for a ``tag:`` column land in ``"(untagged)"`` rather than being
    dropped — a pivot whose columns do not sum back to the frame total is a
    quiet way to lose money.

    Returns a :class:`~cashkit.model.Table`. Diagnostics: ``CK-E033``,
    ``CK-E020``. Raises ``ValueError`` for an unknown index, column spec or
    measure.
    """
    store, run_id, diagnostics = _materialized(kit, run)
    if store is None:
        return _refused(diagnostics, (index,))
    table = store.pivot(
        run_id, index=index, columns=columns, values=values, grain=grain
    )
    return table if not diagnostics else _with(table, diagnostics)


def compare(
    kit: "CashKit",
    runs: Sequence["RunRef"],
    *,
    metric: str = "cash",
    grain: Grain | None = None,
) -> Table:
    """One column per run of the same metric, aligned on the period (PRD §6.4).

    A period absent from a run reports ``None``, never zero: "not evaluated" and
    "evaluated to zero" are different answers and a comparison that conflates
    them is a comparison of the wrong two things.

    Columns are the runs' keys — ``revision|scenario|engine|watermark|cutover``
    — in the order given. Two runs that produce the same key (the same scenario
    of the same working tree, run twice) are kept apart with a ``#n`` suffix
    rather than collapsed: the caller asked for two columns.

    Returns a :class:`~cashkit.model.Table`. Diagnostics: ``CK-E033``,
    ``CK-E020``. Raises ``ValueError`` for an unknown metric.
    """
    store, problem = _store(kit)
    if store is None:
        return _refused((problem,), ("period_start",))  # type: ignore[arg-type]
    run_ids: list[str] = []
    diagnostics: list[Diagnostic] = []
    for run in runs:
        key = run_key(run)
        if key in run_ids:
            key = f"{key}#{sum(1 for held in run_ids if held.split('#')[0] == key) + 1}"
        _, run_id, found = _materialized(kit, run, key=key)
        run_ids.append(run_id)
        diagnostics.extend(found)
    table = store.compare(run_ids, metric=metric, grain=grain)
    return table if not diagnostics else _with(table, tuple(diagnostics))


def export(
    kit: "CashKit",
    run: "RunRef",
    path: str | Path,
    *,
    format: str = "parquet",
    grain: Grain | None = None,
) -> ExportReport:
    """Write the run's frame to a portable file (PRD §6.4, §3.4).

    Parquet is the stable sharing path and carries ``DECIMAL(18,4)`` through, so
    a value written and read back is the same ``Decimal`` rather than a float
    that prints the same. ``format="csv"`` is available and is not that.

    **A relative path lands under** ``<root>/exports/`` — the PRD §3.3 layout,
    git-ignored, because an export is a copy of what a revision already
    reproduces. An absolute path is honoured as given: "write this file over
    there for someone else" is the whole reason the verb exists, and silently
    relocating it would be worse than either choice.

    Returns an :class:`ExportReport` whose ``path`` is the file written, or
    ``None`` with ``CK-E033`` when the duckdb extra is absent. Raises
    ``ValueError`` for an unsupported format.
    """
    store, run_id, diagnostics = _materialized(kit, run)
    if store is None:
        return ExportReport(target=str(path), diagnostics=diagnostics)
    target = Path(path)
    if not target.is_absolute():
        target = kit.root / EXPORTS_DIR / target
    written = store.export(run_id, target, format=format, grain=grain)
    return ExportReport(
        target=str(written),
        created=(str(written),),
        path=written,
        diagnostics=diagnostics,
    )


def read_export(kit: "CashKit", path: str | Path) -> Table:
    """Read an export back in the types it was written in (PRD §3.4).

    Exists so a round-trip is checkable without importing the store: money comes
    back as ``Decimal``. Returns a :class:`~cashkit.model.Table`; diagnostics:
    ``CK-E033``.
    """
    store, problem = _store(kit)
    if store is None:
        return _refused((problem,), ())  # type: ignore[arg-type]
    return store.read_export(path)  # type: ignore[attr-defined]


#: The canonical frame columns, so a refused ``frame()`` still says what shape
#: it would have had. Spelled out rather than imported from
#: :data:`cashkit.stores.frames.FRAME_COLUMNS`, because importing that module to
#: name seven strings would pull ``duckdb`` in on a core install and defeat the
#: whole point of ``CK-E033``. ``tests/test_execution.py`` asserts the two agree.
_FRAME_COLUMNS = (
    "period_start",
    "period_end",
    "item_id",
    "measure",
    "value",
    "currency",
    "status",
)


def _with(table: Table, diagnostics: tuple[Diagnostic, ...]) -> Table:
    return Table(columns=table.columns, rows=table.rows, diagnostics=diagnostics)
