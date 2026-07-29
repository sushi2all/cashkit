"""Dependency graph, SCC condensation and formula binding (PRD §5.1, §5.4).

The graph is built **including** ``prev()`` edges, then condensed. Trivial
components evaluate as whole-horizon column expressions; non-trivial ones — the
genuine feedback sets, typically 2-8 items: cash balance, overdraft interest,
VAT credit carry — get the sequential fold. A cycle that does *not* pass
through a ``prev()`` edge is not a feedback loop, it is an error (``CK-E002``),
and the diagnostic names the cycle.

``agg()`` selectors resolve to concrete item ids here, at graph-build time, so
the DAG is static for the whole run (PRD §5.4). A selector that would make an
item depend on itself is rejected.

Compilation never raises on a bad book: every user-reachable problem becomes a
:class:`~cashkit.model.Diagnostic` and the offending item evaluates to zero, so
one broken formula cannot take down a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cashkit.model import Book, Diagnostic, Item, ItemId
from cashkit.model.diagnostics import make_diagnostic

from .formula import Agg, Cum, Expr, ItemRef, Param, Prev, iter_refs, map_expr, parse_formula

__all__ = ["Component", "CompiledBook", "CompiledItem", "compile_book"]

#: Item kinds whose value comes from a formula rather than from segments.
DERIVED_KINDS = ("derived", "stock")


@dataclass(frozen=True)
class CompiledItem:
    """One item, ready to evaluate.

    ``expr`` is the bound formula (``agg()`` nodes carry resolved item ids) for
    derived and stock items, ``None`` for generative flows. ``broken`` marks an
    item that produced an error diagnostic; it evaluates to zero columns so a
    single bad formula degrades one line instead of the whole run.
    """

    item: Item
    expr: Expr | None
    same_period_deps: frozenset[ItemId]
    lagged_deps: frozenset[ItemId]
    broken: bool = False

    @property
    def id(self) -> ItemId:
        """The item's id. No diagnostics."""
        return self.item.id

    @property
    def is_derived(self) -> bool:
        """True when the value comes from a formula. No diagnostics."""
        return self.item.kind in DERIVED_KINDS


@dataclass(frozen=True)
class Component:
    """One strongly connected component of the dependency graph.

    ``members`` is ordered by the same-period topological order inside the
    component, which is the order the sequential fold must use. ``trivial``
    means no cycle at all — the overwhelming majority — and licenses whole-column
    evaluation.
    """

    members: tuple[ItemId, ...]
    trivial: bool


@dataclass(frozen=True)
class CompiledBook:
    """A book with its formulas parsed, references resolved and graph condensed."""

    book: Book
    items: dict[ItemId, CompiledItem]
    components: tuple[Component, ...]
    dependents: dict[ItemId, frozenset[ItemId]]
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    @property
    def has_errors(self) -> bool:
        """True when compilation produced any error-severity diagnostic. No diagnostics."""
        return any(d.severity == "error" for d in self.diagnostics)

    def downstream(self, changed: set[ItemId]) -> set[ItemId]:
        """Return ``changed`` plus every item transitively depending on it.

        The delta-recompute cone: items outside it keep their cached columns.
        Produces no diagnostics.
        """
        cone = set(changed)
        stack = list(changed)
        while stack:
            current = stack.pop()
            for dependent in self.dependents.get(current, frozenset()):
                if dependent not in cone:
                    cone.add(dependent)
                    stack.append(dependent)
        return cone


# --------------------------------------------------------------------------- #
# Tarjan
# --------------------------------------------------------------------------- #


def _tarjan(nodes: list[ItemId], edges: dict[ItemId, frozenset[ItemId]]) -> list[list[ItemId]]:
    """Strongly connected components, emitted dependencies-first.

    Edges point from dependent to dependency, and Tarjan completes a component
    only after everything it can reach, so the emission order is exactly the
    evaluation order. Iterative — a 2,000-item chain must not blow the stack.
    """
    index_of: dict[ItemId, int] = {}
    low: dict[ItemId, int] = {}
    on_stack: set[ItemId] = set()
    stack: list[ItemId] = []
    components: list[list[ItemId]] = []
    counter = 0

    for root in nodes:
        if root in index_of:
            continue
        work: list[tuple[ItemId, list[ItemId]]] = [(root, sorted(edges.get(root, ())))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            if pending:
                child = pending.pop()
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, sorted(edges.get(child, ()))))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: list[ItemId] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


def _topological_within(
    members: list[ItemId], edges: dict[ItemId, frozenset[ItemId]]
) -> tuple[tuple[ItemId, ...], tuple[ItemId, ...] | None]:
    """Order ``members`` dependencies-first over the same-period edges between them.

    Returns ``(order, None)`` when the induced subgraph is acyclic, or
    ``((), cycle)`` naming one cycle when it is not.
    """
    inside = set(members)
    local = {node: sorted(edges.get(node, frozenset()) & inside) for node in members}
    state: dict[ItemId, int] = {}
    order: list[ItemId] = []
    path: list[ItemId] = []

    def visit(node: ItemId) -> tuple[ItemId, ...] | None:
        state[node] = 1
        path.append(node)
        for child in local[node]:
            if state.get(child, 0) == 1:
                start = path.index(child)
                return tuple(path[start:]) + (child,)
            if state.get(child, 0) == 0:
                cycle = visit(child)
                if cycle is not None:
                    return cycle
        path.pop()
        state[node] = 2
        order.append(node)
        return None

    for node in sorted(members):
        if state.get(node, 0) == 0:
            cycle = visit(node)
            if cycle is not None:
                return (), cycle
    return tuple(order), None


# --------------------------------------------------------------------------- #
# Compilation
# --------------------------------------------------------------------------- #


def _resolve_selector(book: Book, agg: Agg, owner: ItemId) -> tuple[tuple[ItemId, ...], str | None]:
    matched = tuple(
        sorted(
            item_id
            for item_id, item in book.items.items()
            if agg.selector.matches(item.tags, item.flags)
        )
    )
    if owner in matched:
        return (), "self"
    if not matched:
        return (), "empty"
    return matched, None


def compile_book(book: Book) -> CompiledBook:
    """Parse formulas, resolve references, build and condense the graph.

    Returns a :class:`CompiledBook`. Diagnostics cover every user-reachable
    problem: ``CK-E001`` (unknown item id, or a selector matching nothing),
    ``CK-E002`` (a cycle with no ``prev()`` edge, including self-dependency),
    ``CK-E003`` (formula rejected, or kind/formula/segments inconsistency),
    ``CK-E008`` (unknown param) and ``CK-E020`` (aggregation across currencies).
    Never raises on book content.
    """
    diagnostics: list[Diagnostic] = []
    compiled: dict[ItemId, CompiledItem] = {}
    param_keys = _param_keys(book)

    for item_id, item in sorted(book.items.items()):
        broken = False
        expr: Expr | None = None
        same: set[ItemId] = set()
        lagged: set[ItemId] = set()

        if item.kind in DERIVED_KINDS:
            if item.segments:
                diagnostics.append(
                    make_diagnostic(
                        "CK-E003",
                        item_id=item_id,
                        field="segments",
                        reason=(
                            f"kind={item.kind!r} takes its value from a formula, so it "
                            "must have no segments"
                        ),
                    )
                )
                broken = True
            if item.formula is None:
                diagnostics.append(
                    make_diagnostic(
                        "CK-E003",
                        item_id=item_id,
                        field="formula",
                        reason=f"kind={item.kind!r} requires a formula",
                    )
                )
                broken = True
            else:
                outcome = parse_formula(item.formula, item_id=item_id)
                diagnostics.extend(outcome.diagnostics)
                if outcome.expr is None:
                    broken = True
                else:
                    expr = outcome.expr
        elif item.formula is not None:
            diagnostics.append(
                make_diagnostic(
                    "CK-E003",
                    item_id=item_id,
                    field="formula",
                    reason="formula is valid on derived and stock items only",
                )
            )
            broken = True

        if expr is not None:
            bindings: dict[Agg, tuple[ItemId, ...]] = {}
            for ref in iter_refs(expr):
                if isinstance(ref, Agg):
                    matched, problem = _resolve_selector(book, ref, item_id)
                    if problem == "self":
                        diagnostics.append(
                            make_diagnostic(
                                "CK-E002",
                                item_id=item_id,
                                field="formula",
                                cycle=(
                                    f"{item_id} -> agg({ref.selector.source!r}) -> {item_id}"
                                ),
                            )
                        )
                        broken = True
                        continue
                    if problem == "empty":
                        diagnostics.append(
                            make_diagnostic(
                                "CK-E001",
                                item_id=item_id,
                                field="formula",
                                reference=f'agg(tag="{ref.selector.source}")',
                            )
                        )
                        broken = True
                        continue
                    currencies = {book.items[member].currency for member in matched}
                    if len(currencies) > 1:
                        diagnostics.append(
                            make_diagnostic(
                                "CK-E020",
                                item_id=item_id,
                                field="formula",
                                currencies=", ".join(sorted(currencies)),
                            )
                        )
                        broken = True
                        continue
                    bindings[ref] = matched
                    same.update(matched)
                else:
                    target = ref.item_id
                    if target not in book.items:
                        diagnostics.append(
                            make_diagnostic(
                                "CK-E001",
                                item_id=item_id,
                                field="formula",
                                reference=f'{type(ref).__name__.lower()}("{target}")',
                            )
                        )
                        broken = True
                        continue
                    if isinstance(ref, Prev):
                        lagged.add(target)
                        if isinstance(ref.init, Param) and ref.init.key not in param_keys:
                            diagnostics.append(
                                make_diagnostic(
                                    "CK-E008",
                                    item_id=item_id,
                                    field="formula",
                                    key=ref.init.key,
                                    referrer=f"prev(init=p.{ref.init.key})",
                                )
                            )
                            broken = True
                    else:
                        same.add(target)

            for node in _iter_params(expr):
                if node.key not in param_keys:
                    diagnostics.append(
                        make_diagnostic(
                            "CK-E008",
                            item_id=item_id,
                            field="formula",
                            key=node.key,
                            referrer=f"item {item_id} formula",
                        )
                    )
                    broken = True

            if bindings:
                expr = map_expr(
                    expr,
                    lambda node: (
                        Agg(node.selector, node.measure, bindings[node])
                        if isinstance(node, Agg) and node in bindings
                        else node
                    ),
                )

        for segment_index, segment in enumerate(item.segments):
            escalation = segment.escalation
            if escalation is not None and isinstance(escalation.rate, str):
                if escalation.rate not in book.params:
                    diagnostics.append(
                        make_diagnostic(
                            "CK-E008",
                            item_id=item_id,
                            field=f"segments[{segment_index}].escalation.rate",
                            key=escalation.rate,
                            referrer=f"item {item_id} escalation",
                        )
                    )
                    broken = True

        compiled[item_id] = CompiledItem(
            item=item,
            expr=None if broken else expr,
            same_period_deps=frozenset() if broken else frozenset(same),
            lagged_deps=frozenset() if broken else frozenset(lagged),
            broken=broken,
        )

    same_edges = {node: compiled[node].same_period_deps for node in compiled}
    all_edges = {
        node: compiled[node].same_period_deps | compiled[node].lagged_deps for node in compiled
    }

    dependents: dict[ItemId, set[ItemId]] = {node: set() for node in compiled}
    for node, targets in all_edges.items():
        for target in targets:
            dependents[target].add(node)

    components: list[Component] = []
    for members in _tarjan(sorted(compiled), all_edges):
        self_looped = len(members) == 1 and members[0] in all_edges[members[0]]
        trivial = len(members) == 1 and not self_looped
        if trivial:
            components.append(Component(members=(members[0],), trivial=True))
            continue
        order, cycle = _topological_within(members, same_edges)
        if cycle is not None:
            diagnostics.append(
                make_diagnostic(
                    "CK-E002",
                    item_id=cycle[0],
                    field="formula",
                    cycle=" -> ".join(cycle),
                )
            )
            # Break the illegal cycle by disabling its members, keeping the run
            # total: a zero column plus a loud error beats a nonterminating fold.
            for member in members:
                compiled[member] = CompiledItem(
                    item=compiled[member].item,
                    expr=None,
                    same_period_deps=frozenset(),
                    lagged_deps=frozenset(),
                    broken=True,
                )
            components.extend(
                Component(members=(member,), trivial=True) for member in sorted(members)
            )
            continue
        components.append(Component(members=order, trivial=False))

    return CompiledBook(
        book=book,
        items=compiled,
        components=tuple(components),
        dependents={node: frozenset(value) for node, value in dependents.items()},
        diagnostics=tuple(diagnostics),
    )


def _param_keys(book: Book) -> frozenset[str]:
    """Param keys visible to formulas, including the reserved ``opening_balance``."""
    return frozenset(book.params) | {"opening_balance"}


def _iter_params(expr: Expr):
    from .formula import Binary, Builtin, Compare, Logical, Unary, Where

    if isinstance(expr, Param):
        yield expr
    elif isinstance(expr, Unary):
        yield from _iter_params(expr.operand)
    elif isinstance(expr, (Binary, Compare)):
        yield from _iter_params(expr.left)
        yield from _iter_params(expr.right)
    elif isinstance(expr, Logical):
        for operand in expr.operands:
            yield from _iter_params(operand)
    elif isinstance(expr, Where):
        yield from _iter_params(expr.cond)
        yield from _iter_params(expr.then)
        yield from _iter_params(expr.otherwise)
    elif isinstance(expr, Builtin):
        for arg in expr.args:
            yield from _iter_params(arg)
    # Prev/ItemRef/Cum/Agg are leaves here: a Prev's `init` param is checked in
    # the reference walk, so yielding it again would double-report CK-E008.
