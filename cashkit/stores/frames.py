"""The frame store: tidy/long facts in DuckDB, views computed on demand (PRD §5.5).

Canonical storage is **tidy/long** — one row per ``(period, item, measure,
status)`` — because ``measure`` as a column means adding a measure later costs
nothing. Wide format, coarser-grain aggregation and tag slicing are **views**,
never a second copy of the data.

**Tags are not denormalized into the fact table.** They live in
``frame_tags``, an item-dimension table joined on demand. Denormalizing them
would mean rewriting every fact row the first time a tag changes, which is the
fight PRD §5.5 tells you not to pick.

**Money is ``DECIMAL(18,4)`` end to end**, including through Parquet. No float
touches a value on the way in or on the way out: the engine hands over int64
minor units at 4 dp, :func:`~cashkit.engine.numeric.from_minor` turns them into
exact ``Decimal``s, and DuckDB stores those as fixed-point. The one place a
rounding decision exists — ``agg_rule="mean"`` — is done in integer minor units
under the engine's declared policy, not by a SQL division.

Storage stays swappable: :class:`FrameStore` is the protocol the rest of the
system codes against, ``duckdb`` is imported here and nowhere else, and Parquet
export is the stable sharing path (PRD §3.4 — Quack is optional and not
load-bearing).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

import duckdb
import numpy as np

from cashkit.engine.calendars import GRAIN_COLUMN, bucket_of
from cashkit.engine.formula import parse_selector
from cashkit.engine.numeric import RoundingPolicy, from_minor, round_div, to_minor
from cashkit.engine.result import MEASURE_NAMES, RunResult
from cashkit.model import Book, Diagnostic, Grain, ItemId, Table
from cashkit.model.diagnostics import make_diagnostic

__all__ = [
    "DECIMAL_PRECISION",
    "DECIMAL_SCALE",
    "DuckdbFrameStore",
    "FRAME_COLUMNS",
    "FrameStore",
    "effective_agg_rule",
]

#: PRD §5.5 / Phase 8: money materializes as ``DECIMAL(18,4)``.
DECIMAL_PRECISION = 18
DECIMAL_SCALE = 4
#: Largest magnitude ``DECIMAL(18,4)`` can hold, in int64 minor units at 4 dp.
DECIMAL_CEILING_MINOR = 10**DECIMAL_PRECISION - 1

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS frame_runs (
    run_id          VARCHAR PRIMARY KEY,
    book_id         VARCHAR NOT NULL,
    scenario        VARCHAR,
    base_grain      VARCHAR NOT NULL,
    opening_balance DECIMAL({DECIMAL_PRECISION},{DECIMAL_SCALE}) NOT NULL,
    cutover         DATE NOT NULL,
    engine_version  VARCHAR NOT NULL,
    periods         BIGINT NOT NULL
);

-- The period dimension. Every coarser grain is a precomputed bucket, so the
-- fiscal year (which quarters and years follow, D-P2-07) is applied once here
-- and never re-derived in SQL where it could drift from the engine's own.
CREATE TABLE IF NOT EXISTS frame_periods (
    run_id        VARCHAR NOT NULL,
    period_index  BIGINT  NOT NULL,
    day_start     DATE NOT NULL, day_end     DATE NOT NULL,
    week_start    DATE NOT NULL, week_end    DATE NOT NULL,
    month_start   DATE NOT NULL, month_end   DATE NOT NULL,
    quarter_start DATE NOT NULL, quarter_end DATE NOT NULL,
    year_start    DATE NOT NULL, year_end    DATE NOT NULL
);

-- The item dimension: attributes that are properties of the item, not of a
-- cell. `agg_rule` lives here because aggregation to a coarser grain is a
-- property of what is being aggregated (PRD §5.5).
CREATE TABLE IF NOT EXISTS frame_items (
    run_id    VARCHAR NOT NULL,
    item_id   VARCHAR NOT NULL,
    name      VARCHAR NOT NULL,
    kind      VARCHAR NOT NULL,
    direction VARCHAR,
    currency  VARCHAR NOT NULL,
    agg_rule  VARCHAR NOT NULL,
    synthetic BOOLEAN NOT NULL
);

-- Tags, joined on demand. NOT denormalized into frame_facts.
CREATE TABLE IF NOT EXISTS frame_tags (
    run_id    VARCHAR NOT NULL,
    item_id   VARCHAR NOT NULL,
    tag_key   VARCHAR NOT NULL,
    tag_value VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS frame_flags (
    run_id  VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    flag    VARCHAR NOT NULL
);

-- The fact table: tidy/long, one row per (period, item, measure, status).
CREATE TABLE IF NOT EXISTS frame_facts (
    run_id       VARCHAR NOT NULL,
    period_index BIGINT  NOT NULL,
    item_id      VARCHAR NOT NULL,
    measure      VARCHAR NOT NULL,
    value        DECIMAL({DECIMAL_PRECISION},{DECIMAL_SCALE}) NOT NULL,
    currency     VARCHAR NOT NULL,
    status       VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS frame_facts_run ON frame_facts (run_id, item_id);
CREATE INDEX IF NOT EXISTS frame_tags_run ON frame_tags (run_id, tag_key, tag_value);
"""

def effective_agg_rule(kind: str, agg_rule: str) -> str:
    """The rule a coarser grain actually applies to one item (PRD §5.5).

    "Flows sum, stocks take last-in-period" is stated in terms of ``kind``,
    while ``agg_rule`` is authored per item and defaults to ``"sum"`` for every
    item including stocks. The two only conflict in one direction, and there is
    only one honest resolution: **a stock never sums.** A balance added up over
    thirty-one days is not a quantity anyone has a use for, so a stock left at
    the default resolves to ``last``.

    An explicitly different rule on a stock is honoured: ``mean`` on a balance is
    the average balance over the bucket, which is a real thing to want. Returns
    the rule to apply; produces no diagnostics.
    """
    if kind == "stock" and agg_rule == "sum":
        return "last"
    return agg_rule


#: Columns of the canonical tidy/long frame, in canonical order.
FRAME_COLUMNS = (
    "period_start",
    "period_end",
    "item_id",
    "measure",
    "value",
    "currency",
    "status",
)


# --------------------------------------------------------------------------- #
# The protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class FrameStore(Protocol):
    """What the rest of the system codes against (PRD §3.4).

    The protocol is the swappability guarantee: DuckDB is one implementation and
    Parquet export is the stable sharing path, so nothing above this line may
    assume either. Every method returns already-converted Python values —
    ``Decimal`` for money — so a different backend cannot leak its own types.
    """

    def materialize(
        self,
        run_id: str,
        result: RunResult,
        book: Book,
        *,
        scenario: str | None = None,
    ) -> tuple[Diagnostic, ...]:
        """Write one run's facts and dimensions. Returns any diagnostics."""
        ...  # pragma: no cover - protocol

    def frame(
        self,
        run_id: str,
        *,
        grain: Grain | None = None,
        measures: Sequence[str] | None = None,
        where: str | None = None,
        status: str | None = None,
    ) -> Table:
        """The tidy/long frame, optionally aggregated, sliced and filtered."""
        ...  # pragma: no cover - protocol

    def pivot(
        self, run_id: str, *, index: str = "period", columns: str, values: str = "cash"
    ) -> Table:
        """A wide view: one row per index value, one column per column value."""
        ...  # pragma: no cover - protocol

    def compare(self, run_ids: Sequence[str], *, metric: str = "cash") -> Table:
        """One column per run of the same metric, aligned on the period."""
        ...  # pragma: no cover - protocol

    def export(self, run_id: str, path: str | Path, *, format: str = "parquet") -> Path:
        """Write the frame to a portable file and return its path."""
        ...  # pragma: no cover - protocol


# --------------------------------------------------------------------------- #
# The DuckDB implementation
# --------------------------------------------------------------------------- #


class DuckdbFrameStore:
    """A :class:`FrameStore` over a DuckDB database (PRD §3.3 ``frames.duckdb``).

    Open one per book. ``":memory:"`` is a legitimate path — evaluation is cheap
    enough that recompute-on-doubt is a strategy (PRD §5.2), so a frame store
    that lives for one process is not a degraded mode.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        policy: RoundingPolicy = RoundingPolicy.HALF_UP,
    ) -> None:
        self.path = str(path)
        self.policy = policy
        self._db = duckdb.connect(self.path)
        self._db.execute("BEGIN TRANSACTION")
        for statement in filter(None, (s.strip() for s in _SCHEMA.split(";"))):
            self._db.execute(statement)
        self._db.execute("COMMIT")

    def close(self) -> None:
        """Close the connection. No diagnostics."""
        self._db.close()

    def __enter__(self) -> "DuckdbFrameStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- materialization ---------------------------------------------------- #

    def materialize(
        self,
        run_id: str,
        result: RunResult,
        book: Book,
        *,
        scenario: str | None = None,
        engine_version: str = "",
    ) -> tuple[Diagnostic, ...]:
        """Write one run's facts and dimensions into the store.

        Replaces any previously materialized run under ``run_id`` — a run is
        identified by ``(revision, scenario, engine version)`` and recomputing it
        must not append a second copy.

        ``book`` supplies the item dimension. It is the book the *engine*
        evaluated, synthetic items included: ``_tax:<regime>:*`` carries real
        cash and ``_event:<digest>`` carries real ledger rows, so a frame that
        dropped them would not sum to the model. They are flagged
        ``synthetic=true`` in ``frame_items`` so a view can exclude them
        deliberately rather than by accident.

        Returns diagnostics; ``CK-E020`` if a value cannot be represented as
        ``DECIMAL(18,4)`` (unreachable for engine columns, which the addition-safe
        ceiling already bounds far below — checked because "unreachable" is a
        claim, not a guarantee). Never raises on run content.
        """
        diagnostics: list[Diagnostic] = []
        periods = result.periods
        fiscal = book.calendar.fiscal_year_start_month

        # The base-grain period keeps the index's own half-open bounds; only the
        # coarser buckets are calendar-derived.
        period_stage: dict[str, np.ndarray] = {
            "period_index": np.arange(len(periods), dtype=np.int64),
            "day_start": _dates(periods.starts),
            "day_end": _dates(periods.ends),
        }
        for grain in (Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.YEAR):
            buckets = [bucket_of(start, grain, fiscal) for start in periods.starts]
            name = GRAIN_COLUMN[grain]
            period_stage[f"{name}_start"] = _dates([bucket[0] for bucket in buckets])
            period_stage[f"{name}_end"] = _dates([bucket[1] for bucket in buckets])

        item_rows = []
        tag_rows = []
        flag_rows = []
        # Driven by the *facts*, not by the book: an item whose column exists but
        # whose dimension row does not would be silently dropped by every join in
        # this module, which is a frame that does not sum to the model.
        for item_id in sorted(set(result.accrual) | set(book.items)):
            item = book.items.get(item_id)
            if item is None:
                item_rows.append(
                    (
                        run_id,
                        item_id,
                        item_id,
                        "flow",
                        None,
                        result.currencies.get(item_id, "EUR"),
                        "sum",
                        True,
                    )
                )
                continue
            item_rows.append(
                (
                    run_id,
                    item_id,
                    item.name,
                    item.kind,
                    item.direction,
                    item.currency,
                    effective_agg_rule(item.kind, item.agg_rule),
                    item_id.startswith("_"),
                )
            )
            for key, value in sorted(item.tags.items()):
                tag_rows.append((run_id, item_id, key, value))
            for flag in sorted(item.flags):
                flag_rows.append((run_id, item_id, flag))

        ordered_items = sorted(result.accrual)
        minor, over_ceiling = _stack_columns(result, ordered_items)
        for position in np.flatnonzero(over_ceiling):
            item_id = ordered_items[int(position) // 2]
            diagnostics.append(
                make_diagnostic(
                    "CK-E020",
                    item_id=item_id,
                    field=MEASURE_NAMES[int(position) % 2],
                    currencies=(
                        f"a value on item {item_id!r} exceeds "
                        f"DECIMAL({DECIMAL_PRECISION},{DECIMAL_SCALE})"
                    ),
                )
            )

        self._db.execute("BEGIN TRANSACTION")
        for table in (
            "frame_facts",
            "frame_tags",
            "frame_flags",
            "frame_items",
            "frame_periods",
            "frame_runs",
        ):
            self._db.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
        self._insert(
            "frame_runs",
            [
                (
                    run_id,
                    result.book_id,
                    scenario,
                    book.base_grain.value,
                    book.opening_balance,
                    book.cutover,
                    engine_version,
                    len(periods),
                )
            ],
        )
        self._insert_periods(run_id, period_stage)
        self._insert("frame_items", item_rows)
        self._insert("frame_tags", tag_rows)
        self._insert("frame_flags", flag_rows)
        self._insert_facts(run_id, ordered_items, len(periods), minor)
        self._db.execute("COMMIT")
        return tuple(diagnostics)

    def _insert(self, table: str, rows: Sequence[Sequence[object]]) -> None:
        """Insert dimension rows as literal SQL, in chunks.

        DuckDB's parameter binding costs the better part of a millisecond per
        value, which turns a 1,826-period calendar into two seconds; the same
        rows as literal SQL take milliseconds. Every literal goes through
        :func:`_literal`, which quotes strings by doubling the quote and emits
        Decimals unquoted so DuckDB parses them as fixed-point rather than
        through a float.
        """
        if not rows:
            return
        for start in range(0, len(rows), _INSERT_CHUNK):
            chunk = rows[start : start + _INSERT_CHUNK]
            values = ", ".join(
                "(" + ", ".join(_literal(value) for value in row) + ")" for row in chunk
            )
            self._db.execute(f"INSERT INTO {table} VALUES {values}")

    def _insert_periods(self, run_id: str, stage: dict[str, np.ndarray]) -> None:
        """Insert the period dimension column-wise.

        A five-year day-grain horizon is 1,826 rows of ten dates each, and
        18,260 ``DATE`` literals cost DuckDB's parser more than the whole fact
        table costs its executor. Handed over as ``datetime64[D]`` arrays it is
        one vectorized read.
        """
        self._db.register("_period_stage", stage)
        columns = ", ".join(
            f"p.{name}::DATE" for name in stage if name != "period_index"
        )
        self._db.execute(
            f"INSERT INTO frame_periods SELECT {_literal(run_id)}, p.period_index, "
            f"{columns} FROM _period_stage p"
        )
        self._db.unregister("_period_stage")

    def _insert_facts(
        self,
        run_id: str,
        items: Sequence[ItemId],
        periods: int,
        minor: np.ndarray,
    ) -> None:
        """Insert the fact table column-wise, straight from the int64 columns.

        The engine's output is already columnar, so it is handed over as numpy
        arrays and DuckDB reads them without a per-value Python conversion.
        The minor units become ``DECIMAL(18,4)`` **through their decimal string**,
        not through a division: DuckDB's decimal division is not exact at the
        top of the type's range, and "exact except for large numbers" is not a
        property a money column may have.
        """
        blocks = len(items) * len(MEASURE_NAMES)
        stage = {
            "period_index": np.tile(np.arange(periods, dtype=np.int64), blocks),
            "item_ord": np.repeat(
                np.arange(len(items), dtype=np.int64), len(MEASURE_NAMES) * periods
            ),
            "measure_ord": np.tile(
                np.repeat(np.arange(len(MEASURE_NAMES), dtype=np.int64), periods),
                len(items),
            ),
            "minor": minor,
        }
        ordinals = [
            (run_id, ordinal, item_id)
            for ordinal, item_id in enumerate(items)
        ]
        self._db.execute("DROP TABLE IF EXISTS _fact_ords")
        self._db.execute(
            "CREATE TEMP TABLE _fact_ords (run_id VARCHAR, ord BIGINT, item_id VARCHAR)"
        )
        self._insert("_fact_ords", ordinals)
        self._db.register("_fact_stage", stage)
        measure_case = " ".join(
            f"WHEN {ordinal} THEN {_literal(name)}"
            for ordinal, name in enumerate(MEASURE_NAMES)
        )
        self._db.execute(
            f"""
            INSERT INTO frame_facts
            SELECT {_literal(run_id)}, s.period_index, o.item_id,
                   CASE s.measure_ord {measure_case} END,
                   {_MINOR_TO_DECIMAL}, i.currency, {_literal(_STATUS)}
            FROM _fact_stage s
            JOIN _fact_ords o ON o.ord = s.item_ord
            JOIN frame_items i
              ON i.run_id = {_literal(run_id)} AND i.item_id = o.item_id
            """
        )
        self._db.unregister("_fact_stage")
        self._db.execute("DROP TABLE _fact_ords")

    # -- views -------------------------------------------------------------- #

    def frame(
        self,
        run_id: str,
        *,
        grain: Grain | None = None,
        measures: Sequence[str] | None = None,
        where: str | None = None,
        status: str | None = None,
        include_synthetic: bool = True,
    ) -> Table:
        """Return the tidy/long frame as a view (PRD §6.4).

        ``grain`` aggregates to a coarser calendar bucket respecting each item's
        ``agg_rule``: ``sum`` sums (exact — a sum of 4 dp decimals needs no
        rounding), ``last`` takes the last period in the bucket (what a stock
        level means), ``mean`` divides in int64 minor units under the store's
        declared rounding policy. ``where`` is a §5.4 selector resolved against
        the tag dimension **by join**, never against denormalized tags.
        ``status`` and ``measures`` filter rows.

        Returns a :class:`~cashkit.model.Table` with columns
        ``(period_start, period_end, item_id, measure, value, currency, status)``
        ordered by period, item, measure. Raises ``ValueError`` for an unknown
        run, measure or malformed selector — all programmer error at this layer;
        selectors an agent authors are validated by the SDK first.
        """
        self._require_run(run_id)
        column = GRAIN_COLUMN[grain] if grain is not None else "day"
        clauses = ["f.run_id = ?"]
        params: list[object] = [run_id]
        if measures is not None:
            unknown = sorted(set(measures) - set(MEASURE_NAMES))
            if unknown:
                raise ValueError(f"unknown measures {unknown}; expected {MEASURE_NAMES}")
            clauses.append(f"f.measure IN ({', '.join('?' * len(measures))})")
            params.extend(measures)
        if status is not None:
            clauses.append("f.status = ?")
            params.append(status)
        if not include_synthetic:
            clauses.append("NOT i.synthetic")
        if where is not None:
            selector_sql, selector_params = self._selector_clause(run_id, where)
            clauses.append(selector_sql)
            params.extend(selector_params)

        if grain is None:
            # At the base grain every group holds exactly one row, so the rule
            # would be applied to a group of one — the same number, one Python
            # loop later. Skipping it keeps a whole-book frame a fetch rather
            # than a per-cell recomputation.
            base_sql = f"""
                SELECT p.day_start, p.day_end, f.item_id, f.measure, f.value,
                       f.currency, f.status
                FROM frame_facts f
                JOIN frame_periods p
                  ON p.run_id = f.run_id AND p.period_index = f.period_index
                JOIN frame_items i ON i.run_id = f.run_id AND i.item_id = f.item_id
                WHERE {' AND '.join(clauses)}
                ORDER BY p.day_start, f.item_id, f.measure, f.status
            """
            return Table(
                columns=FRAME_COLUMNS,
                rows=tuple(self._db.execute(base_sql, params).fetchall()),
            )

        sql = f"""
            SELECT p.{column}_start, p.{column}_end, f.item_id, f.measure,
                   f.currency, f.status, i.agg_rule,
                   SUM(f.value), COUNT(*), arg_max(f.value, f.period_index)
            FROM frame_facts f
            JOIN frame_periods p
              ON p.run_id = f.run_id AND p.period_index = f.period_index
            JOIN frame_items i ON i.run_id = f.run_id AND i.item_id = f.item_id
            WHERE {' AND '.join(clauses)}
            GROUP BY p.{column}_start, p.{column}_end, f.item_id, f.measure,
                     f.currency, f.status, i.agg_rule
            ORDER BY p.{column}_start, f.item_id, f.measure, f.status
        """
        rows = []
        for record in self._db.execute(sql, params).fetchall():
            (
                period_start,
                period_end,
                item_id,
                measure,
                currency,
                row_status,
                agg_rule,
                total,
                count,
                last,
            ) = record
            rows.append(
                (
                    period_start,
                    period_end,
                    item_id,
                    measure,
                    self._apply_agg_rule(agg_rule, total, count, last),
                    currency,
                    row_status,
                )
            )
        return Table(columns=FRAME_COLUMNS, rows=tuple(rows))

    def _apply_agg_rule(
        self, agg_rule: str, total: Decimal, count: int, last: Decimal
    ) -> Decimal:
        """Collapse a bucket under one item's ``agg_rule``.

        The rule is applied in exactly one place, on already-exact inputs: the
        SQL layer groups and sums, this decides what the group *means*. ``mean``
        is the only rule that rounds, and it rounds in int64 minor units under
        the engine's declared policy rather than through a SQL division whose
        rounding mode is the database's business, not ours.
        """
        if agg_rule == "last":
            return last
        if agg_rule == "mean":
            return from_minor(round_div(to_minor(total), count, self.policy))
        return total

    def _selector_clause(self, run_id: str, where: str) -> tuple[str, list[object]]:
        """Turn a §5.4 selector into an EXISTS-per-term join on the dimensions."""
        selector, reason = parse_selector(where)
        if selector is None:
            raise ValueError(f"malformed selector {where!r}: {reason}")
        fragments: list[str] = []
        params: list[object] = []
        for key, value in selector.tags:
            fragments.append(
                "EXISTS (SELECT 1 FROM frame_tags t WHERE t.run_id = f.run_id "
                "AND t.item_id = f.item_id AND t.tag_key = ? AND t.tag_value = ?)"
            )
            params.extend((key, value))
        for flag in selector.flags:
            fragments.append(
                "EXISTS (SELECT 1 FROM frame_flags g WHERE g.run_id = f.run_id "
                "AND g.item_id = f.item_id AND g.flag = ?)"
            )
            params.append(flag)
        return "(" + " AND ".join(fragments) + ")", params

    def items_matching(self, run_id: str, where: str) -> tuple[ItemId, ...]:
        """Return the item ids a selector resolves to, in id order.

        The same join the frame uses, exposed on its own so "a tag-sliced sum
        equals the sum of the corresponding items" is checkable rather than
        assumed. Raises ``ValueError`` for a malformed selector.
        """
        clause, params = self._selector_clause(run_id, where)
        sql = (
            "SELECT DISTINCT f.item_id FROM frame_facts f "
            f"WHERE f.run_id = ? AND {clause} ORDER BY f.item_id"
        )
        return tuple(
            row[0] for row in self._db.execute(sql, [run_id, *params]).fetchall()
        )

    def pivot(
        self,
        run_id: str,
        *,
        index: str = "period",
        columns: str,
        values: str = "cash",
        grain: Grain | None = None,
    ) -> Table:
        """Return a wide view of one measure (PRD §6.4).

        ``index`` is ``"period"`` or ``"item"``. ``columns`` is ``"tag:<key>"``,
        ``"item"`` or ``"measure"``. Column order is the sorted distinct value
        set, so the same query gives the same table every time.

        Items carrying no value for a ``tag:<key>`` column go into a column named
        ``"(untagged)"`` rather than being dropped: a pivot whose columns do not
        sum back to the frame total is a quiet way to lose money. Returns a
        :class:`~cashkit.model.Table`; raises ``ValueError`` on an unknown run,
        index, column spec or measure.
        """
        self._require_run(run_id)
        if values not in MEASURE_NAMES:
            raise ValueError(f"unknown measure {values!r}; expected {MEASURE_NAMES}")
        if index not in ("period", "item"):
            raise ValueError(f"unknown pivot index {index!r}; expected 'period' or 'item'")
        column_expr, column_params = self._pivot_key(columns)
        grain_column = GRAIN_COLUMN[grain] if grain is not None else "day"
        index_expr = (
            f"p.{grain_column}_start" if index == "period" else "f.item_id"
        )
        sql = f"""
            SELECT {index_expr} AS idx, {column_expr} AS col,
                   i.agg_rule, SUM(f.value), COUNT(*),
                   arg_max(f.value, f.period_index)
            FROM frame_facts f
            JOIN frame_periods p
              ON p.run_id = f.run_id AND p.period_index = f.period_index
            JOIN frame_items i ON i.run_id = f.run_id AND i.item_id = f.item_id
            WHERE f.run_id = ? AND f.measure = ?
            GROUP BY idx, col, i.agg_rule
        """
        records = self._db.execute(sql, [*column_params, run_id, values]).fetchall()
        cells: dict[tuple[object, str], Decimal] = {}
        for idx, col, agg_rule, total, count, last in records:
            key = (idx, col)
            cells[key] = cells.get(key, Decimal(0)) + self._apply_agg_rule(
                agg_rule, total, count, last
            )
        index_values = sorted({key[0] for key in cells})
        column_values = sorted({key[1] for key in cells})
        rows = [
            (
                idx,
                *(cells.get((idx, col), Decimal("0.0000")) for col in column_values),
            )
            for idx in index_values
        ]
        return Table(
            columns=(index, *column_values), rows=tuple(rows)
        )

    def _pivot_key(self, columns: str) -> tuple[str, list[object]]:
        if columns == "item":
            return "f.item_id", []
        if columns == "measure":
            return "f.measure", []
        if columns.startswith("tag:"):
            key = columns[len("tag:") :]
            if not key:
                raise ValueError("pivot columns 'tag:' needs a tag key after the colon")
            return (
                "COALESCE((SELECT t.tag_value FROM frame_tags t "
                "WHERE t.run_id = f.run_id AND t.item_id = f.item_id "
                "AND t.tag_key = ?), '(untagged)')",
                [key],
            )
        raise ValueError(
            f"unknown pivot columns {columns!r}; expected 'tag:<key>', 'item' or 'measure'"
        )

    def compare(
        self,
        run_ids: Sequence[str],
        *,
        metric: str = "cash",
        grain: Grain | None = None,
    ) -> Table:
        """Compare the same metric across runs, aligned on the period (PRD §6.4).

        Returns a :class:`~cashkit.model.Table` with one column per run in the
        order given; a period absent from a run reports ``None`` rather than
        zero, because "not evaluated" and "evaluated to zero" are different
        answers. Raises ``ValueError`` for an unknown run or metric.
        """
        if metric not in MEASURE_NAMES:
            raise ValueError(f"unknown metric {metric!r}; expected {MEASURE_NAMES}")
        totals: dict[str, dict[date, Decimal]] = {}
        starts: set[date] = set()
        for run_id in run_ids:
            self._require_run(run_id)
            frame = self.frame(run_id, grain=grain, measures=[metric])
            per_period: dict[date, Decimal] = {}
            for row in frame.rows:
                per_period[row[0]] = per_period.get(row[0], Decimal(0)) + row[4]
            totals[run_id] = per_period
            starts.update(per_period)
        rows = [
            (start, *(totals[run_id].get(start) for run_id in run_ids))
            for start in sorted(starts)
        ]
        return Table(columns=("period_start", *run_ids), rows=tuple(rows))

    def export(
        self,
        run_id: str,
        path: str | Path,
        *,
        format: str = "parquet",
        grain: Grain | None = None,
    ) -> Path:
        """Export the frame to Parquet or CSV and return the written path.

        Parquet is the stable sharing path (PRD §3.4) and it carries the
        ``DECIMAL(18,4)`` type through: a value written and read back is the same
        ``Decimal``, not a float that happens to print the same. The export is a
        single ``COPY`` of the canonical tidy/long frame with the period
        dimension already joined, so the file stands alone.

        Raises ``ValueError`` for an unknown run or an unsupported format.
        """
        self._require_run(run_id)
        if format not in ("parquet", "csv"):
            raise ValueError(f"unsupported export format {format!r}; use parquet or csv")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        column = GRAIN_COLUMN[grain] if grain is not None else "day"
        # The value expression mirrors frame(): only `sum` is expressible in SQL
        # without a rounding decision, so a book carrying `last`/`mean` items
        # exports through the Python path to stay byte-identical to frame().
        if grain is None or self._only_summed(run_id):
            select = f"""
                SELECT p.{column}_start AS period_start,
                       p.{column}_end   AS period_end,
                       f.item_id, f.measure,
                       SUM(f.value)::DECIMAL({DECIMAL_PRECISION},{DECIMAL_SCALE}) AS value,
                       f.currency, f.status
                FROM frame_facts f
                JOIN frame_periods p
                  ON p.run_id = f.run_id AND p.period_index = f.period_index
                WHERE f.run_id = '{_quote(run_id)}'
                GROUP BY period_start, period_end, f.item_id, f.measure,
                         f.currency, f.status
                ORDER BY period_start, f.item_id, f.measure, f.status
            """
            self._db.execute(
                f"COPY ({select}) TO '{_quote(str(target))}' (FORMAT {format.upper()})"
            )
            return target
        self._export_table(self.frame(run_id, grain=grain), target, format)
        return target

    def _only_summed(self, run_id: str) -> bool:
        row = self._db.execute(
            "SELECT COUNT(*) FROM frame_items WHERE run_id = ? AND agg_rule <> 'sum'",
            [run_id],
        ).fetchone()
        return bool(row) and row[0] == 0

    def _export_table(self, table: Table, target: Path, format: str) -> None:
        self._db.execute("DROP TABLE IF EXISTS _export_buffer")
        self._db.execute(
            f"""CREATE TEMP TABLE _export_buffer (
                period_start DATE, period_end DATE, item_id VARCHAR,
                measure VARCHAR,
                value DECIMAL({DECIMAL_PRECISION},{DECIMAL_SCALE}),
                currency VARCHAR, status VARCHAR)"""
        )
        self._insert("_export_buffer", list(table.rows))
        self._db.execute(
            f"COPY (SELECT * FROM _export_buffer) TO '{_quote(str(target))}' "
            f"(FORMAT {format.upper()})"
        )
        self._db.execute("DROP TABLE _export_buffer")

    def read_export(self, path: str | Path) -> Table:
        """Read a Parquet or CSV export back into a :class:`Table`.

        Exists so the round-trip is checkable in the same types it was written
        in: money comes back as ``Decimal``, not as a float that prints the
        same. No diagnostics.
        """
        target = str(path)
        reader = "read_parquet" if target.endswith(".parquet") else "read_csv"
        relation = self._db.execute(f"SELECT * FROM {reader}('{_quote(target)}')")
        columns = tuple(description[0] for description in relation.description)
        return Table(columns=columns, rows=tuple(relation.fetchall()))

    # -- introspection ------------------------------------------------------ #

    def runs(self) -> Table:
        """List the materialized runs, newest insertion last. No diagnostics."""
        relation = self._db.execute(
            "SELECT run_id, book_id, scenario, base_grain, opening_balance, "
            "cutover, engine_version, periods FROM frame_runs ORDER BY run_id"
        )
        columns = tuple(description[0] for description in relation.description)
        return Table(columns=columns, rows=tuple(relation.fetchall()))

    def tags(self, run_id: str) -> Table:
        """The item-dimension tag table for one run. No diagnostics."""
        relation = self._db.execute(
            "SELECT item_id, tag_key, tag_value FROM frame_tags WHERE run_id = ? "
            "ORDER BY item_id, tag_key",
            [run_id],
        )
        return Table(
            columns=("item_id", "tag_key", "tag_value"), rows=tuple(relation.fetchall())
        )

    def _require_run(self, run_id: str) -> None:
        row = self._db.execute(
            "SELECT 1 FROM frame_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        if row is None:
            raise ValueError(
                f"no materialized run {run_id!r}; call materialize() first"
            )


#: Every fact row carries the status the run gave it. The engine reports one
#: status per run rather than per contribution (see DECISIONS D-P8-06), so this
#: is a single value today and a real column tomorrow — the frame's grain
#: already includes it, so widening costs nothing here.
_STATUS = "forecast"


#: Rows per literal INSERT statement. Large enough that statement overhead
#: vanishes, small enough that the generated SQL stays a sane size.
_INSERT_CHUNK = 2000

#: Exact int64-minor-units to ``DECIMAL(18,4)``, via the decimal string.
#: Deliberately not ``minor / 10000``: DuckDB's decimal division loses digits at
#: the top of the range, and a money column that is exact only for small numbers
#: is the silent-error class this project exists to prevent.
_MINOR_TO_DECIMAL = (
    "CAST(CASE WHEN s.minor < 0 THEN '-' ELSE '' END "
    "|| CAST(abs(s.minor) // 10000 AS VARCHAR) || '.' "
    "|| lpad(CAST(abs(s.minor) % 10000 AS VARCHAR), 4, '0') "
    f"AS DECIMAL({DECIMAL_PRECISION},{DECIMAL_SCALE}))"
)


def _stack_columns(
    result: RunResult, items: Sequence[ItemId]
) -> tuple[np.ndarray, np.ndarray]:
    """Flatten every item's columns into one int64 array, item-major.

    Returns ``(minor, over_ceiling)`` where ``over_ceiling`` flags the blocks
    (one per item and measure) holding a value ``DECIMAL(18,4)`` cannot
    represent. Out-of-range blocks are zeroed, so a refused value is visibly
    zero next to its error rather than silently truncated.
    """
    blocks: list[np.ndarray] = []
    flags: list[bool] = []
    for item_id in items:
        for measure in MEASURE_NAMES:
            column = result.column(item_id, measure).astype(np.int64, copy=True)
            bad = bool(column.size) and bool(
                np.abs(column).max() > DECIMAL_CEILING_MINOR
            )
            flags.append(bad)
            blocks.append(np.zeros_like(column) if bad else column)
    if not blocks:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool)
    return np.concatenate(blocks), np.array(flags, dtype=bool)


def _dates(days: Sequence[date]) -> np.ndarray:
    """Render a date sequence as a ``datetime64[s]`` array for DuckDB.

    Second resolution rather than day: DuckDB reads ``datetime64[s]`` as
    ``TIMESTAMP_S`` and refuses ``datetime64[D]`` outright. Every value is
    midnight, so the ``::DATE`` cast on the way in is lossless.
    """
    return np.array([day.isoformat() for day in days], dtype="datetime64[s]")


def _literal(value: object) -> str:
    """Render one Python value as an exact DuckDB SQL literal.

    ``Decimal`` is emitted unquoted so DuckDB parses it as fixed-point; strings
    are single-quoted with the quote doubled. ``float`` has no rendering here on
    purpose — nothing in a frame is ever a float.
    """
    if value is None:
        return "NULL"
    if value is True:
        return "TRUE"
    if value is False:
        return "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def _quote(value: str) -> str:
    return value.replace("'", "''")
