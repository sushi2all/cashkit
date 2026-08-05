"""The public SDK surface (PRD §6).

Populated session by session. Phase 7 adds the scenario surface: sparse
overlays authored by value, field-sparse resolution along the parent chain,
macros that expand immediately, and ``provenance()``.

Nothing here reaches into a store: :class:`~cashkit.sdk.scenarios.ScenarioSet`
holds the authored book and its scenarios in memory, and persisting them is the
config store's job (Session S5).
"""

from .macros import Macro, RetagItems, ScaleItems, ShiftItems
from .scenarios import Resolution, ScenarioSet

__all__ = [
    "Macro",
    "Resolution",
    "RetagItems",
    "ScaleItems",
    "ScenarioSet",
    "ShiftItems",
]
