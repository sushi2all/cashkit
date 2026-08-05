"""The public SDK surface (PRD §6).

Populated session by session. Phase 7 added the scenario surface: sparse
overlays authored by value, field-sparse resolution along the parent chain,
macros that expand immediately, and ``provenance()``. Phase 8 added
``summary()``, which answers "when do we run out of cash" straight off the
engine's int64 columns — deliberately not behind the DuckDB extra, since that
is the question the system exists to answer. Phase 9 adds
:class:`~cashkit.sdk.kit.CashKit`, the object a book is opened as: the PRD §3.3
layout, the stores that back it and the whole §6.6 version-control surface,
with git behind an interface and never in a signature (ADR-0018).

:class:`~cashkit.sdk.scenarios.ScenarioSet` still reaches into no store — it
holds the authored book and its scenarios in memory, and
:class:`~cashkit.sdk.kit.CashKit` is what joins it to persistence. Frame
materialization, aggregation and Parquet export live in
:mod:`cashkit.stores.frames`.
"""

from .kit import BASE_SCENARIO, CashKit, CommitReport, RunRef
from .macros import Macro, RetagItems, ScaleItems, ShiftItems
from .scenarios import Resolution, ScenarioSet
from .views import balance_series, summary

__all__ = [
    "BASE_SCENARIO",
    "CashKit",
    "CommitReport",
    "Macro",
    "Resolution",
    "RetagItems",
    "RunRef",
    "ScaleItems",
    "ScenarioSet",
    "ShiftItems",
    "balance_series",
    "summary",
]
