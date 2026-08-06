"""The two ledger verbs the SDK was missing (PRD §6.2): querying and reconciling.

``add_event``, ``import_events``, ``void_event`` and ``correct_event`` are
:class:`~cashkit.stores.ledger.LedgerStore` operations — the store owns
append-only-ness and ``UNIQUE(source, ext_id)``, and putting them anywhere else
would put the idempotency key somewhere it can be bypassed.
:meth:`~cashkit.sdk.kit.CashKit.add_event` and friends are thin passes through to
it. What lives here is the pair that needs the *book* as well as the ledger:

* :func:`query_events` shapes ledger rows into the §6.2 ``Table``;
* :func:`reconcile` compares actuals against what was forecast for the same
  window, which needs the engine and therefore the book.

**Reconciliation is two runs, not a re-derivation.** Comparing a bank statement
to a forecast means comparing two numbers that went through the same rounding
order, the same settlement split and the same VAT gross-up; a reconciliation
that summed raw ``Event.amount`` values against forecast *cash* would compare a
net accrual to a gross settlement and call the difference drift. So both sides
are engine runs over the same book: the forecast side with no ledger at all, the
actual side with the window's actuals and every generative segment stripped. The
two are then commensurable by construction rather than by argument.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np

from cashkit.engine import Engine, RunResult
from cashkit.engine.numeric import from_minor
from cashkit.model import (
    Book,
    Diagnostic,
    ItemId,
    ReconciliationLine,
    ReconciliationReport,
    Table,
)

from .kit import BASE_SCENARIO

if TYPE_CHECKING:  # pragma: no cover - annotations only; kit imports this lazily
    from .kit import CashKit

__all__ = ["EVENT_COLUMNS", "query_events", "reconcile"]

#: The §6.2 event table's columns, in order. Money is a ``Decimal``, dates are
#: ``date`` objects and tags a sorted mapping — already-converted Python values,
#: which is what :class:`~cashkit.model.Table` carries.
EVENT_COLUMNS: tuple[str, ...] = (
    "id",
    "date",
    "amount",
    "status",
    "item",
    "currency",
    "source",
    "ext_id",
    "corrects",
    "note",
    "tags",
)


def query_events(
    book: "CashKit",
    where: str | None = None,
    since: date | None = None,
    until: date | None = None,
    *,
    include_voided: bool = False,
) -> Table:
    """Filter the ledger by selector and date window (PRD §6.2).

    ``where`` is the one §5.4 selector grammar — space-separated ``key:value``
    terms, ANDed — matched against each event's own tags. ``since``/``until``
    bound the event date **inclusively**.

    This reads the ledger, which every scenario shares: a scenario is a view
    over it and never writes to it, so no overlay is applied here. Use
    :meth:`~cashkit.sdk.kit.CashKit.events_for` for the sequence one scenario
    sees. A kit bound to a past revision sees the ledger truncated to that
    revision's watermark (ADR-0006).

    Returns a :class:`~cashkit.model.Table` with :data:`EVENT_COLUMNS`, in
    ledger order. Produces no diagnostics: a query that matches nothing is an
    empty table, which is an answer.
    """
    if book.ledger is None:
        return Table(columns=EVENT_COLUMNS)
    watermark = book.book.ledger_watermark if book.bound_to is not None else None
    rows = book.ledger.query_events(
        where=where,
        since=since,
        until=until,
        watermark=watermark,
        include_voided=include_voided,
    )
    return Table.from_rows(
        EVENT_COLUMNS,
        [
            (
                event.id,
                event.date,
                event.amount,
                event.status,
                event.item,
                event.currency,
                event.source,
                event.ext_id,
                event.corrects,
                event.note,
                dict(sorted(event.tags.items())),
            )
            for event in rows
        ],
    )


def _stripped(book: Book) -> Book:
    """``book`` with every generative segment removed.

    A run over this book produces columns holding **only** what the ledger put
    there, with each event still routed to its item's settlement and VAT — which
    is exactly the actual side of a reconciliation. Nothing else about the book
    changes, so the two runs share their calendar, params, regimes and rounding.
    """
    return book.model_copy(
        update={
            "items": {
                item_id: (item if not item.segments else item.model_copy(update={"segments": []}))
                for item_id, item in book.items.items()
            }
        }
    )


def _window_mask(starts: list[date], since: date, until: date) -> np.ndarray:
    """Base periods whose start falls inside ``[since, until]``.

    The window is therefore a whole number of base periods: at day grain that is
    the literal date range, and at a coarser grain a period is in or out as a
    unit rather than being apportioned. Apportioning a month across a boundary
    would invent a number no measure supports.
    """
    return np.fromiter(
        (since <= start <= until for start in starts), dtype=bool, count=len(starts)
    )


def reconcile(
    book: "CashKit",
    until: date,
    *,
    scenario_id: str = BASE_SCENARIO,
    since: date | None = None,
    measure: str = "cash",
) -> ReconciliationReport:
    """Compare actuals to what was forecast for the same window (PRD §6.2).

    The window is ``[since, until]`` inclusive, with ``since`` defaulting to the
    book's ``cutover`` — the boundary from which generation is live and events
    apply alongside it (ADR-0004). Both sides are engine runs over the same
    resolved book, so both went through the canonical rounding order and the
    difference between them is drift rather than an artefact of how each was
    computed:

    * **forecast** — a run with no ledger at all: what the model generates for
      the window on its own.
    * **actual** — a run over the window's ``status="actual"`` rows with every
      generative segment stripped: what the ledger says happened, put through
      the same settlement and VAT machinery.

    ``drift`` is ``actual - forecast`` per item; an actual referencing no item
    lands on the engine's ``_event:<digest>`` carrier and is reported under that
    id rather than folded into a total with no name.

    ``suggested_cutover`` is the day after ``until``: once the ledger is the
    complete record through ``until``, generation should resume the next day.
    Feed it straight to :func:`~cashkit.sdk.construction.set_cutover`.

    Returns a :class:`~cashkit.model.ReconciliationReport`. Diagnostics: the
    scenario-resolution and event-overlay problems the window's events raise
    (``CK-E006``, ``CK-E014``, ``CK-E021``…), plus every error-severity
    diagnostic of either run — a reconciliation computed over a book the engine
    refused part of is not one anyone should act on.
    """
    resolution = book.scenarios.resolution(scenario_id)
    model = resolution.book
    start = since if since is not None else model.cutover
    events, event_problems = book.events_for(scenario_id)
    actuals = [
        event
        for event in events
        if event.status == "actual" and start <= event.date <= until
    ]

    forecast_run = Engine(model, book.policy, ()).run()
    actual_run = Engine(_stripped(model), book.policy, tuple(actuals)).run()
    mask = _window_mask(list(forecast_run.periods.starts), start, until)

    lines: list[ReconciliationLine] = []
    forecast_total = 0
    actual_total = 0
    for item_id in sorted(set(forecast_run.accrual) | set(actual_run.accrual)):
        forecast = _windowed(forecast_run, item_id, measure, mask)
        actual = _windowed(actual_run, item_id, measure, mask)
        if forecast == 0 and actual == 0:
            continue
        forecast_total += forecast
        actual_total += actual
        lines.append(
            ReconciliationLine(
                item_id=item_id,
                forecast=from_minor(forecast),
                actual=from_minor(actual),
                drift=from_minor(actual - forecast),
            )
        )

    diagnostics: list[Diagnostic] = list(resolution.diagnostics) + list(event_problems)
    for run in (forecast_run, actual_run):
        diagnostics.extend(d for d in run.diagnostics if d.severity == "error")

    return ReconciliationReport(
        book_id=model.id,
        scenario=scenario_id,
        measure=measure,
        since=start,
        until=until,
        suggested_cutover=until + timedelta(days=1),
        lines=tuple(lines),
        forecast_total=from_minor(forecast_total),
        actual_total=from_minor(actual_total),
        drift_total=from_minor(actual_total - forecast_total),
        actual_events=len(actuals),
        diagnostics=_deduplicated(diagnostics),
    )


def _windowed(
    run: RunResult, item_id: ItemId, measure: str, mask: np.ndarray
) -> int:
    """One item's total over the window, in int64 minor units.

    Integer addition throughout: the comparison a reconciliation makes is exact
    or it is worthless.
    """
    try:
        column = run.column(item_id, measure)
    except KeyError:
        return 0
    return int(column[mask].sum())


def _deduplicated(diagnostics: list[Diagnostic]) -> tuple[Diagnostic, ...]:
    """Both runs see the same book, so both report the same book problems once."""
    seen: set[tuple] = set()
    out: list[Diagnostic] = []
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.item_id, diagnostic.field, diagnostic.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(diagnostic)
    return tuple(out)
