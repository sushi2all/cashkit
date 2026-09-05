"""The public SDK surface (PRD §6).

One object, :class:`~cashkit.sdk.kit.CashKit`, holds a book, its three stores
and its history. Every write on it is addressed by scenario and returns a
:class:`~cashkit.model.ChangeReport` (ADR-0031, ADR-0032); every read of a run
hangs off the :class:`~cashkit.sdk.kit.RunRef` that ``run()`` returns, and a
past revision is a :class:`~cashkit.sdk.kit.ReadOnlyKit` with no write methods
(ADR-0033, ADR-0034). :func:`create_book` is the one module-level verb, and it
returns ``(kit, diagnostics)`` like ``open()`` and ``at()`` do.

The module-level introspection functions (``trace``, ``why_zero``,
``depends_on``, ``dependents_of``, ``describe_book``) and the summary helpers
are exported for callers holding a run or a book without a kit; the kit and
the run delegate to them, so there is one implementation of each.
"""

from .construction import create_book, resolve_holidays
from .introspection import (
    dependents_of,
    depends_on,
    describe_book,
    render_expr,
    trace,
    why_zero,
)
from .kit import BASE_SCENARIO, CashKit, CommitReport, ExportReport, ReadOnlyKit, RunRef
from .macros import Macro, RetagItems, ScaleItems, ShiftItems
from .scenarios import Resolution, ScenarioSet
from .validation import validate
from .views import balance_series, summary

__all__ = [
    "BASE_SCENARIO",
    "CashKit",
    "CommitReport",
    "ExportReport",
    "Macro",
    "ReadOnlyKit",
    "Resolution",
    "RetagItems",
    "RunRef",
    "ScaleItems",
    "ScenarioSet",
    "ShiftItems",
    "balance_series",
    "create_book",
    "dependents_of",
    "depends_on",
    "describe_book",
    "render_expr",
    "resolve_holidays",
    "summary",
    "trace",
    "validate",
    "why_zero",
]
