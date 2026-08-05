"""The public SDK surface (PRD §6).

Populated session by session. Phase 7 adds the scenario surface: sparse
overlays authored by value, field-sparse resolution along the parent chain,
macros that expand immediately, and ``provenance()``. Phase 8 adds
``summary()``, which answers "when do we run out of cash" straight off the
engine's int64 columns — deliberately not behind the DuckDB extra, since that
is the question the system exists to answer.

Nothing here reaches into a store: :class:`~cashkit.sdk.scenarios.ScenarioSet`
holds the authored book and its scenarios in memory, persisting them is the
config store's job (Session S5), and frame materialization, aggregation and
Parquet export live in :mod:`cashkit.stores.frames`.
"""

from .macros import Macro, RetagItems, ScaleItems, ShiftItems
from .scenarios import Resolution, ScenarioSet
from .views import balance_series, summary

__all__ = [
    "Macro",
    "Resolution",
    "RetagItems",
    "ScaleItems",
    "ScenarioSet",
    "ShiftItems",
    "balance_series",
    "summary",
]
