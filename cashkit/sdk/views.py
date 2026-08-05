"""Run summaries: the headline numbers, computed exactly (PRD §6.4).

``summary()`` answers "when do we run out of cash", which is the question the
whole system exists to answer, so it is deliberately **not** behind the DuckDB
extra: it works on a core install, straight off the int64 minor-unit columns the
engine produced. Nothing here rounds — the arithmetic is integer addition and
comparison, and ``Decimal`` appears only on the way out.

Aggregation, pivoting and export are the frame store's job
(:mod:`cashkit.stores.frames`), because those are the operations that genuinely
want a columnar engine.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np

from cashkit.engine.calendars import bucket_of
from cashkit.engine.numeric import from_minor, to_minor
from cashkit.engine.result import RunResult
from cashkit.model import Book, Grain, ItemId, RunSummary

__all__ = ["balance_series", "summary"]

#: How the auto-derived cash balance is described in ``RunSummary``.
AUTO_BALANCE = "auto: opening_balance + cumulative cash over every non-stock item"


def _flow_items(result: RunResult, book: Book) -> list[ItemId]:
    """Items whose cash column is a movement rather than a level.

    A stock is a level, so it produces no cash leg at all (D-P2-05) and summing
    its cash column would be summing zeros — it is excluded explicitly so the
    derivation reads the way it means rather than relying on that.
    """
    return [
        item_id
        for item_id in sorted(result.cash)
        if book.items.get(item_id) is None or book.items[item_id].kind != "stock"
    ]


def balance_series(
    result: RunResult, book: Book, *, balance: str = "auto"
) -> tuple[np.ndarray, str]:
    """Return the cash balance per period, in int64 minor units.

    ``balance="auto"`` derives it as ``opening_balance`` plus the running total
    of every non-stock item's cash column — the same ``net[t]`` fold the engine
    describes in PRD §5.1, and well-defined for any book.

    ``balance="<item_id>"`` reads a designated balance item's column instead
    (its accrual, since a stock's level lives there). Use it when the book
    models the balance explicitly with ``prev("cash", init=p.opening_balance)``.

    **The auto derivation counts every cash leg in the model, including one a
    derived item creates by re-aggregating items that already settled.** That is
    not a bug in the derivation: such an item *is* a second cash leg as the book
    is written. If the number looks doubled, the book has two.

    Returns ``(series, description)``. Raises ``KeyError`` for an unknown
    balance item (programmer error). Produces no diagnostics.
    """
    length = len(result.periods)
    if balance != "auto":
        column = result.accrual[balance]
        return column.astype(np.int64, copy=True), f"item {balance!r}"
    total = np.zeros(length, dtype=np.int64)
    for item_id in _flow_items(result, book):
        total = total + result.cash[item_id]
    return to_minor(book.opening_balance) + np.cumsum(total), AUTO_BALANCE


def _bucket_ends(result: RunResult, book: Book, grain: Grain) -> list[tuple[date, int]]:
    """The last base period of each coarser bucket, in order.

    A balance is a level, so aggregating it to a coarser grain is
    last-in-period — the same rule ``agg_rule="last"`` states for stocks.
    """
    fiscal = book.calendar.fiscal_year_start_month
    last_index: dict[date, int] = {}
    for index, start in enumerate(result.periods.starts):
        bucket_start, _ = bucket_of(start, grain, fiscal)
        last_index[bucket_start] = index
    return sorted(last_index.items())


def summary(
    result: RunResult,
    book: Book,
    *,
    grain: Grain | None = None,
    balance: str = "auto",
) -> RunSummary:
    """Summarize a run: min cash, runway, breakeven, totals (PRD §6.4).

    ``grain`` reports the balance at a coarser calendar bucket, taking the last
    period in each bucket, which is what a balance means. It defaults to the
    book's base grain.

    Definitions, stated because each of them has more than one defensible
    reading and only one of them can be the number a founder acts on:

    * **min cash** — the lowest balance, over the **base grain**, whatever
      ``grain`` reports. A coarser grain shows bucket closes, and a trough that
      opens and closes inside one month is exactly the trough that kills a
      company; smoothing it away to make the summary tidy is not a trade this
      function makes.
    * **runway** — the first **base** period whose balance is negative, for the
      same reason. ``None`` means it does not happen *inside the horizon*, which
      is not the same as never.
    * **breakeven** — the first bucket at ``grain`` from which net cash flow is
      non-negative and stays non-negative for the rest of the horizon. This one
      is genuinely a question about the reported grain: a single good day inside
      a losing month is not breakeven, and neither is a single good month inside
      a losing year.

    Returns a :class:`~cashkit.model.RunSummary`. Diagnostics: every
    error-severity diagnostic from the run is carried through, because a summary
    computed over a book the engine refused part of is not a summary anyone
    should act on. Raises ``KeyError`` for an unknown balance item.
    """
    grain = grain or book.base_grain
    series, source = balance_series(result, book, balance=balance)
    opening = to_minor(book.opening_balance)

    buckets = _bucket_ends(result, book, grain)
    starts = [start for start, _ in buckets]
    closes = [int(series[index]) for _, index in buckets]
    flows = [
        close - (opening if position == 0 else closes[position - 1])
        for position, close in enumerate(closes)
    ]

    # min cash and runway are read off the *base* series, never off the reported
    # buckets: a trough that opens and closes inside one bucket is invisible in
    # the bucket's close, and it is the trough that matters.
    base_starts = result.periods.starts
    if len(series):
        trough = int(np.argmin(series))
        min_cash = int(series[trough])
        min_period: date | None = base_starts[trough]
        negative = np.flatnonzero(series < 0)
        runway_periods = int(negative[0]) if negative.size else None
        runway_end = base_starts[int(negative[0])] if negative.size else None
    else:  # pragma: no cover - a horizon always has at least one period
        min_cash, min_period = opening, None
        runway_periods, runway_end = None, None

    breakeven: date | None = None
    for position in range(len(flows) - 1, -1, -1):
        if flows[position] < 0:
            breakeven = starts[position + 1] if position + 1 < len(starts) else None
            break
    else:
        breakeven = starts[0] if starts else None

    inflow = 0
    outflow = 0
    for item_id in _flow_items(result, book):
        column = result.cash[item_id]
        inflow += int(column[column > 0].sum())
        outflow += int(column[column < 0].sum())
    accrual_total = sum(
        int(result.accrual[item_id].sum())
        for item_id in sorted(result.accrual)
        if book.items.get(item_id) is None or book.items[item_id].kind != "stock"
    )

    errors = tuple(d for d in result.diagnostics if d.severity == "error")
    return RunSummary(
        book_id=result.book_id,
        grain=grain.value,
        balance_source=source,
        periods=len(closes),
        opening_balance=book.opening_balance,
        closing_balance=from_minor(closes[-1]) if closes else book.opening_balance,
        min_cash=from_minor(min_cash),
        min_cash_period=min_period,
        runway_periods=runway_periods,
        runway_end=runway_end,
        breakeven_period=breakeven,
        total_inflow=from_minor(inflow),
        total_outflow=from_minor(outflow),
        net_cash=from_minor(inflow + outflow),
        total_accrual=from_minor(accrual_total),
        diagnostics=errors,
    )
