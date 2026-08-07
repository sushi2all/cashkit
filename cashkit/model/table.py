"""``Table`` — the tabular result type the read surface returns (PRD §6.2, §6.4).

``query_events``, ``frame``, ``pivot`` and ``compare`` are all typed ``-> Table``
in the PRD and none of them is a model: they return whatever shape the query
asked for. ``Table`` is therefore a thin, dependency-free carrier — named
columns and rows of already-converted Python values, money as ``Decimal`` — and
not a DataFrame. Adding pandas or polars to the core install to hand back six
columns would be a dependency an agent never asked for; anyone who wants a
DataFrame can build one from ``columns`` and ``rows`` in one line.

It lives with the model for the reason ``reports.py`` does (DECISIONS D-P5-15):
both the stores and the SDK return it, and neither may depend on the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Sequence

from .primitives import Diagnostic

__all__ = ["Table"]


@dataclass(frozen=True)
class Table:
    """Named columns over rows of Python values.

    Rows are tuples positionally aligned with ``columns``. Order is whatever the
    producing query declared, and every producer in CashKit declares one — a
    result whose row order depends on the storage engine is not reproducible.

    ``diagnostics`` is the §6.5 channel. PRD §6.4 types ``frame``, ``pivot``
    and ``compare`` as ``-> Table`` and §6.5 requires every fallible operation
    to return diagnostics rather than raise; a carrier with no room for one
    cannot satisfy both, so the room is here. It defaults to empty and every
    existing producer leaves it that way, so an empty table that reports nothing
    still means "the query matched nothing" — the distinction between a miss and
    a refusal, which the whole catalogue exists to keep.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no error-severity diagnostic was produced. No diagnostics."""
        return not any(d.severity == "error" for d in self.diagnostics)

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self.rows)

    def index_of(self, column: str) -> int:
        """Return a column's position. Raises ``KeyError`` if absent (programmer
        error). Produces no diagnostics."""
        try:
            return self.columns.index(column)
        except ValueError as exc:
            raise KeyError(
                f"unknown column {column!r}; this table has {list(self.columns)}"
            ) from exc

    def column(self, name: str) -> tuple[Any, ...]:
        """Return one column's values in row order. No diagnostics."""
        position = self.index_of(name)
        return tuple(row[position] for row in self.rows)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return the rows as dicts keyed by column name. No diagnostics."""
        return [dict(zip(self.columns, row)) for row in self.rows]

    @classmethod
    def from_rows(
        cls, columns: Sequence[str], rows: Sequence[Sequence[Any]]
    ) -> "Table":
        """Build a Table from any row sequence. No diagnostics."""
        return cls(
            columns=tuple(columns), rows=tuple(tuple(row) for row in rows)
        )
