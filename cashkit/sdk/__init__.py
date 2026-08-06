"""The public SDK surface (PRD §6).

Populated session by session. Phase 7 added the scenario surface: sparse
overlays authored by value, field-sparse resolution along the parent chain,
macros that expand immediately, and ``provenance()``. Phase 8 added
``summary()``, which answers "when do we run out of cash" straight off the
engine's int64 columns — deliberately not behind the DuckDB extra, since that
is the question the system exists to answer. Phase 9 adds
:class:`~cashkit.sdk.kit.CashKit`, the object a book is opened as: the PRD §3.3
layout, the stores that back it and the whole §6.6 version-control surface,
with git behind an interface and never in a signature (ADR-0018). Phase 10 adds
the introspection surface — ``trace()``, ``why_zero()``, ``depends_on()``,
``describe_book()`` — and ``validate()`` over the whole §10.1 catalogue.
Session S5.5 closes §6.1: ``create_book``, ``add_item``, ``add_derived``,
``set_param``, ``retag``, ``add_tax_regime`` and ``set_cutover``, plus the two
§6.2 verbs that need the book as well as the ledger — ``query_events`` and
``reconcile``. Until then a book could only be built by constructing a ``Book``
by hand, which the SDK-only rule forbids of everyone except the CLI that was
doing it.

:class:`~cashkit.sdk.scenarios.ScenarioSet` still reaches into no store — it
holds the authored book and its scenarios in memory, and
:class:`~cashkit.sdk.kit.CashKit` is what joins it to persistence. Frame
materialization, aggregation and Parquet export live in
:mod:`cashkit.stores.frames`.
"""

from .construction import (
    AffectedCount,
    BookRef,
    add_derived,
    add_item,
    add_tax_regime,
    create_book,
    resolve_holidays,
    retag,
    set_cutover,
    set_param,
)
from .events import query_events, reconcile
from .introspection import (
    dependents_of,
    depends_on,
    describe_book,
    render_expr,
    trace,
    why_zero,
)
from .kit import BASE_SCENARIO, CashKit, CommitReport, RunRef
from .macros import Macro, RetagItems, ScaleItems, ShiftItems
from .scenarios import Resolution, ScenarioSet
from .validation import validate
from .views import balance_series, summary

__all__ = [
    "BASE_SCENARIO",
    "AffectedCount",
    "BookRef",
    "CashKit",
    "CommitReport",
    "Macro",
    "Resolution",
    "RetagItems",
    "RunRef",
    "ScaleItems",
    "ScenarioSet",
    "ShiftItems",
    "add_derived",
    "add_item",
    "add_tax_regime",
    "balance_series",
    "create_book",
    "dependents_of",
    "depends_on",
    "describe_book",
    "query_events",
    "reconcile",
    "render_expr",
    "resolve_holidays",
    "retag",
    "set_cutover",
    "set_param",
    "summary",
    "trace",
    "validate",
    "why_zero",
]
