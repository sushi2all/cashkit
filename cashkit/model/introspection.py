"""Introspection result types (PRD §6.5) — the part that makes agents work.

ADR-0013 makes these load-bearing rather than polish: ``trace()`` is the primary
UI interaction primitive, and the returned tree *is* the edit menu. A missing
binding or an unpopulated field is a defect, not a cosmetic gap, which is why
**every field below is non-optional and has a meaningful empty value**. There is
no ``None`` to interpret: a generative cell reports the generator it came from
rather than a null ``formula``, a cell with no bindings reports an empty tuple
rather than ``None``, and a trace that stopped at its depth limit says
``truncated=True`` rather than looking like a leaf.

Money is ``Decimal`` here, exact at 4 dp, because these types are a display
boundary — an agent that renders a trace must not be handed a float.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .primitives import CashKitModel, Diagnostic, DiagnosticSubject

__all__ = [
    "ArithmeticStep",
    "Binding",
    "BookDescription",
    "DependencyGraph",
    "Explanation",
    "GraphEdge",
    "GraphNode",
    "ItemDescription",
    "PivotVocabulary",
    "Trace",
    "ZERO_CAUSES",
]

#: What a cell's value comes from. Exhaustive over the engine's sources.
CellSource = Literal["formula", "generated", "ledger", "tax", "empty"]


class Binding(CashKitModel):
    """One resolved symbol inside a traced cell.

    ``symbol`` is the source text as written (``it("acme")``, ``p.margin``,
    ``segments[0].escalation``); ``value`` is what it resolved to at this
    period, exact. ``source`` says where the value came from in words an agent
    can act on, and ``target`` names the item or param to navigate to — empty
    when the binding is a literal with nothing behind it.
    """

    symbol: str
    kind: str
    value: Decimal
    source: str
    target: str = ""
    detail: str = ""


class ArithmeticStep(CashKitModel):
    """One step of the computation, in the order the engine performed it.

    ``inputs`` are rendered exactly as the engine held them, so a reader can
    check the arithmetic by hand. ``rounding`` names the boundary applied at
    this step (ADR-0003's canonical order) or ``"none"`` for an exact one —
    never blank, because "no rounding happened" and "nobody said" must not look
    the same.
    """

    expression: str
    operation: str
    inputs: tuple[str, ...] = ()
    value: Decimal
    rounding: str = "none"


class Trace(CashKitModel):
    """Why one cell holds the number it holds (PRD §6.5).

    Recursive to ``depth``: ``children`` are the traces of the items this cell
    reads. ``value`` is always the **engine's own** number for the cell, never a
    re-derivation, and ``reconciles`` reports whether the steps below add up to
    it. A false ``reconciles`` means the explanation drifted from the engine,
    which is a defect this type is designed to make visible rather than a
    discrepancy a reader has to notice.
    """

    item_id: DiagnosticSubject
    item_name: str
    kind: CellSource
    measure: str
    period_index: int = Field(ge=0)
    period_start: date
    period_end: date
    value: Decimal
    #: The formula as authored, or a rendering of the generator that produced
    #: the cell ("12000.00 x 1.03^2 x 0.9"). Never blank: a cell with no
    #: generator says so in words.
    formula: str
    bindings: tuple[Binding, ...] = ()
    steps: tuple[ArithmeticStep, ...] = ()
    children: tuple["Trace", ...] = ()
    depth: int = Field(ge=0)
    truncated: bool = False
    reconciles: bool = True
    notes: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def walk(self):
        """Yield this trace and every descendant, depth-first. No diagnostics."""
        yield self
        for child in self.children:
            yield from child.walk()


#: The five zero causes PRD §6.5 requires ``why_zero`` to distinguish, plus the
#: honest sixth answer for a cell that is not zero at all.
ZERO_CAUSES = (
    "outside_segments",
    "probability_zero",
    "upstream_zero",
    "cutover_suppressed",
    "no_settlement_leg",
    "not_zero",
)

ZeroCause = Literal[
    "outside_segments",
    "probability_zero",
    "upstream_zero",
    "cutover_suppressed",
    "no_settlement_leg",
    "not_zero",
]


class Explanation(CashKitModel):
    """Why one cell is zero (PRD §6.5).

    ``cause`` is one of the five documented causes, or ``"not_zero"`` when the
    question does not apply — answering "it is not zero" is a real answer and
    better than inventing one of the five. ``also`` lists causes that are *also*
    true: a period can be both outside every segment and pre-cutover, and
    reporting only the first would make the fix look smaller than it is.
    """

    item_id: DiagnosticSubject
    measure: str
    period_index: int = Field(ge=0)
    period_start: date
    value: Decimal
    cause: ZeroCause
    message: str
    detail: str = ""
    also: tuple[ZeroCause, ...] = ()
    suggested_fix: str = ""
    diagnostics: tuple[Diagnostic, ...] = ()


class GraphNode(CashKitModel):
    """One item in a dependency graph view."""

    item_id: DiagnosticSubject
    name: str
    kind: str
    synthetic: bool = False
    depth: int = Field(default=0, ge=0)


class GraphEdge(CashKitModel):
    """One dependency edge. ``relation`` distinguishes a same-period read from a
    ``prev()`` edge — the only cycle-breaker — and from an ``agg()`` membership,
    which is a same-period read the selector resolved (PRD §5.4)."""

    source: DiagnosticSubject
    target: DiagnosticSubject
    relation: Literal["same_period", "lagged", "aggregate"]


class DependencyGraph(CashKitModel):
    """What one item depends on, or what depends on it (PRD §6.5).

    ``root`` is the item asked about. ``cyclic`` is true when the traversal met
    a genuine feedback set — which is legal and expected for a cash balance —
    and ``cycle_members`` names it, so a reader can tell a designed loop from an
    accident.
    """

    root: DiagnosticSubject
    direction: Literal["depends_on", "dependents_of"]
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    cyclic: bool = False
    cycle_members: tuple[DiagnosticSubject, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


class ItemDescription(CashKitModel):
    """One item, as an agent needs to see it to write a query about it."""

    item_id: DiagnosticSubject
    name: str
    kind: str
    direction: str = ""
    currency: str
    agg_rule: str
    tags: dict[str, str] = Field(default_factory=dict)
    flags: tuple[str, ...] = ()
    formula: str = ""
    segments: int = Field(default=0, ge=0)
    settles: str = ""
    vat: str = ""
    synthetic: bool = False


class PivotVocabulary(CashKitModel):
    """Exactly the argument values ``pivot()`` accepts on this book.

    The whole point of ``describe_book()`` is that a model can write a working
    call without inventing a field name (PRD §10, "sufficient to generate a
    working UI with no field invention"). Enumerating the legal values — rather
    than describing them — is what makes that checkable: every combination below
    is asserted to run in ``tests/test_introspection.py``.
    """

    index: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    values: tuple[str, ...] = ()


class BookDescription(CashKitModel):
    """Schema, items, measures and params — enough to write a query (PRD §6.5).

    Everything an agent could otherwise guess wrong is enumerated here: the
    measures that exist, the grains that aggregate, the statuses a frame can
    carry, the tag keys and values in use, the selector grammar with a worked
    example, and :class:`PivotVocabulary`, which lists the exact argument values
    ``pivot()`` accepts. A field name absent from this description does not
    exist.
    """

    book_id: str
    base_grain: str
    horizon_start: date
    horizon_end: date
    periods: int = Field(ge=0)
    cutover: date
    opening_balance: Decimal
    currency: str
    rounding_policy: str
    engine_version: str
    schema_version: int
    params: dict[str, Decimal] = Field(default_factory=dict)
    items: tuple[ItemDescription, ...] = ()
    measures: tuple[str, ...] = ()
    grains: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    tag_keys: tuple[str, ...] = ()
    tag_values: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    flags: tuple[str, ...] = ()
    selector_grammar: str = ""
    selector_examples: tuple[str, ...] = ()
    pivot: PivotVocabulary = Field(default_factory=PivotVocabulary)
    frame_columns: tuple[str, ...] = ()
    summary_fields: tuple[str, ...] = ()
    scenarios: tuple[str, ...] = ()
    tax_regimes: tuple[str, ...] = ()
    formula_builtins: tuple[str, ...] = ()
    time_fields: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
