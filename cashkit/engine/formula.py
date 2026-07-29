"""The formula front-end: restricted-AST parser and symbol table (PRD §5.4).

Built in Phase 2 with the reference engine and hardened in Phase 4 (ADR-0001).
Both evaluators consume the same parsed tree, so the language has exactly one
definition.

Three rules shape everything here:

* **`where`, not `if`.** There is no `if_`; `where(cond, a, b)` selects
  elementwise and both branches always evaluate (D8). Every node below is
  expressible as a masked column operation.
* **Nothing executes.** The parser translates a Python expression AST into the
  closed set of node types in this module. No `eval`, no attribute access
  beyond `p.<param>` / `t.<field>`, no call outside the builtin table.
* **Rejection is a diagnostic.** A malformed or hostile formula returns
  ``CK-E003`` (or ``CK-E008`` for an unknown param), never an exception and
  never execution.

Numeric literals are read from the *source text*, not from the Python
``Constant`` value: ``0.05`` in a formula becomes ``Decimal("0.05")`` exactly,
so the author's digits never pass through a binary fraction.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from cashkit.model import Diagnostic, ItemId
from cashkit.model.diagnostics import make_diagnostic
from cashkit.model.primitives import IDENT_RE

__all__ = [
    "Agg",
    "Binary",
    "Builtin",
    "Compare",
    "Cum",
    "Expr",
    "ItemRef",
    "Literal",
    "Logical",
    "MEASURES",
    "Param",
    "ParseOutcome",
    "Prev",
    "Selector",
    "TimeField",
    "Unary",
    "Where",
    "iter_refs",
    "parse_formula",
    "parse_selector",
]

#: The two measures every flow-producing item carries.
MEASURES = ("cash", "accrual")

#: Default measure for item references. CashKit forecasts cash, and the
#: canonical PRD example — a cash balance folding over `agg(tag="cat:revenue")`
#: — is only correct against settled cash. Accrual is one keyword away.
DEFAULT_MEASURE = "cash"

#: Period metadata exposed as `t.<field>` (PRD §5.4).
TIME_FIELDS = ("index", "month", "is_quarter_end", "is_business_day")

#: Numeric-valued builtins. Each is a masked column operation.
NUMERIC_BUILTINS = {"min": None, "max": None, "clip": 3, "round_": None, "abs_": 1}

_IDENT_FULL = re.compile(rf"^{IDENT_RE}$")
_TAG_VALUE = re.compile(r"^[^\s:]+$")

#: Guard against a formula that is technically legal but pathologically deep.
MAX_AST_DEPTH = 40


# --------------------------------------------------------------------------- #
# Selector grammar (PRD §5.4)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Selector:
    """A parsed selector: space-separated terms, ANDed.

    A term is ``key:value`` (tag equality) or ``flag:name`` (flag membership).
    No OR, no negation, no wildcards in v1 — finer slices are modelled as tags.
    """

    source: str
    tags: tuple[tuple[str, str], ...]
    flags: tuple[str, ...]

    def matches(self, tags: dict[str, str], flags: set[str]) -> bool:
        """True when every term holds for the given tag map and flag set.

        Produces no diagnostics.
        """
        return all(tags.get(key) == value for key, value in self.tags) and all(
            flag in flags for flag in self.flags
        )


def parse_selector(source: str) -> tuple[Selector | None, str | None]:
    """Parse a selector string.

    Returns ``(selector, None)`` on success or ``(None, reason)`` with a
    human-readable reason. The caller decides which diagnostic code carries the
    reason, so this function produces no diagnostics itself.
    """
    terms = source.split()
    if not terms:
        return None, "selector is empty; it would match no items"
    tags: list[tuple[str, str]] = []
    flags: list[str] = []
    for term in terms:
        if term.count(":") != 1:
            return None, (
                f"term {term!r} is not 'key:value' or 'flag:name' "
                "(exactly one colon per term)"
            )
        key, value = term.split(":", 1)
        if not _IDENT_FULL.match(key):
            return None, f"selector key {key!r} must match {IDENT_RE}"
        if key == "flag":
            if not _IDENT_FULL.match(value):
                return None, f"flag name {value!r} must match {IDENT_RE}"
            flags.append(value)
        else:
            if not _TAG_VALUE.match(value):
                return None, (
                    f"tag value {value!r} must contain no whitespace and no colon"
                )
            tags.append((key, value))
    return (
        Selector(source=source, tags=tuple(tags), flags=tuple(flags)),
        None,
    )


# --------------------------------------------------------------------------- #
# The restricted AST
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Literal:
    """A numeric or boolean constant, exact as authored."""

    value: Decimal


@dataclass(frozen=True)
class Param:
    """``p.<key>`` — a named scalar from ``Book.params``."""

    key: str


@dataclass(frozen=True)
class TimeField:
    """``t.<field>`` — period metadata."""

    name: str


@dataclass(frozen=True)
class ItemRef:
    """``it("id")`` — another item's value this period."""

    item_id: ItemId
    measure: str = DEFAULT_MEASURE


@dataclass(frozen=True)
class Prev:
    """``prev("id", n=1, init=0)`` — the only cycle-breaker.

    ``lag`` is a literal (never an expression) so the dependency graph stays
    static. For ``t < lag`` the reference yields ``init``.
    """

    item_id: ItemId
    lag: int
    init: Literal | Param
    measure: str = DEFAULT_MEASURE


@dataclass(frozen=True)
class Agg:
    """``agg(tag="cat:revenue")`` — row-sum over the items a selector resolves to.

    ``items`` is filled at graph-build time (PRD §5.4: selectors resolve to
    concrete ids so the DAG stays static) and is ``None`` until then.
    """

    selector: Selector
    measure: str = DEFAULT_MEASURE
    items: tuple[ItemId, ...] | None = None


@dataclass(frozen=True)
class Cum:
    """``cum("id")`` — running total of an item since horizon start."""

    item_id: ItemId
    measure: str = DEFAULT_MEASURE


@dataclass(frozen=True)
class Unary:
    """``-x`` / ``+x`` / ``not x``."""

    op: str
    operand: "Expr"


@dataclass(frozen=True)
class Binary:
    """``+`` ``-`` ``*`` ``/`` — division is masked-safe (PRD §5.4)."""

    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Compare:
    """A single elementwise comparison; chained comparisons are rejected."""

    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Logical:
    """``and`` / ``or`` as elementwise mask combination — never short-circuit."""

    op: str
    operands: tuple["Expr", ...]


@dataclass(frozen=True)
class Where:
    """``where(cond, a, b)`` — both branches always evaluate (D8)."""

    cond: "Expr"
    then: "Expr"
    otherwise: "Expr"


@dataclass(frozen=True)
class Builtin:
    """A safe numeric builtin: ``min``, ``max``, ``clip``, ``round_``, ``abs_``."""

    name: str
    args: tuple["Expr", ...]


Expr = (
    Literal
    | Param
    | TimeField
    | ItemRef
    | Prev
    | Agg
    | Cum
    | Unary
    | Binary
    | Compare
    | Logical
    | Where
    | Builtin
)


def iter_refs(expr: Expr):
    """Yield every item/aggregate reference node in ``expr``, depth-first.

    Yields :class:`ItemRef`, :class:`Prev`, :class:`Agg` and :class:`Cum` nodes;
    produces no diagnostics.
    """
    if isinstance(expr, (ItemRef, Prev, Agg, Cum)):
        yield expr
        return
    if isinstance(expr, Unary):
        yield from iter_refs(expr.operand)
    elif isinstance(expr, (Binary, Compare)):
        yield from iter_refs(expr.left)
        yield from iter_refs(expr.right)
    elif isinstance(expr, Logical):
        for operand in expr.operands:
            yield from iter_refs(operand)
    elif isinstance(expr, Where):
        yield from iter_refs(expr.cond)
        yield from iter_refs(expr.then)
        yield from iter_refs(expr.otherwise)
    elif isinstance(expr, Builtin):
        for arg in expr.args:
            yield from iter_refs(arg)


def map_expr(expr: Expr, transform) -> Expr:
    """Rebuild ``expr`` applying ``transform`` to every node, bottom-up.

    Used by graph build to bind resolved item ids onto :class:`Agg` nodes.
    Returns a new expression tree; produces no diagnostics.
    """
    if isinstance(expr, Unary):
        expr = Unary(expr.op, map_expr(expr.operand, transform))
    elif isinstance(expr, Binary):
        expr = Binary(expr.op, map_expr(expr.left, transform), map_expr(expr.right, transform))
    elif isinstance(expr, Compare):
        expr = Compare(expr.op, map_expr(expr.left, transform), map_expr(expr.right, transform))
    elif isinstance(expr, Logical):
        expr = Logical(expr.op, tuple(map_expr(item, transform) for item in expr.operands))
    elif isinstance(expr, Where):
        expr = Where(
            map_expr(expr.cond, transform),
            map_expr(expr.then, transform),
            map_expr(expr.otherwise, transform),
        )
    elif isinstance(expr, Builtin):
        expr = Builtin(expr.name, tuple(map_expr(arg, transform) for arg in expr.args))
    return transform(expr)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParseOutcome:
    """Result of parsing one formula: an expression, or diagnostics explaining
    why there is none. Never both empty."""

    expr: Expr | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        """True when an expression was produced. No diagnostics."""
        return self.expr is not None


class _Rejected(Exception):
    """Internal: a formula-level rejection carrying its reason."""

    def __init__(self, reason: str, code: str = "CK-E003", **details: object) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.details = details


_BINARY_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
_UNARY_OPS = {ast.USub: "-", ast.UAdd: "+", ast.Not: "not"}
_COMPARE_OPS = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}


class _Translator:
    """Translate a Python expression AST into the restricted node set."""

    def __init__(self, source: str) -> None:
        self.source = source

    # -- helpers ---------------------------------------------------------- #

    def _numeric_literal(self, node: ast.Constant) -> Decimal:
        """Read a numeric constant from its source text so the author's decimal
        digits survive verbatim."""
        text = ast.get_source_segment(self.source, node)
        if text is None:  # pragma: no cover - defensive
            raise _Rejected("numeric literal could not be read from the source text")
        try:
            return Decimal(text.strip())
        except (InvalidOperation, ValueError) as exc:
            raise _Rejected(
                f"literal {text.strip()!r} is not a decimal number"
            ) from exc

    def _string_arg(self, node: ast.expr, what: str) -> str:
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            raise _Rejected(f"{what} must be a literal string")
        return node.value

    def _item_id(self, node: ast.expr, what: str) -> str:
        value = self._string_arg(node, what)
        if not _IDENT_FULL.match(value):
            raise _Rejected(f"{what} {value!r} is not a valid item id ({IDENT_RE})")
        return value

    def _measure(self, node: ast.expr | None) -> str:
        if node is None:
            return DEFAULT_MEASURE
        value = self._string_arg(node, "measure")
        if value not in MEASURES:
            raise _Rejected(f"measure must be one of {list(MEASURES)}, got {value!r}")
        return value

    def _literal_int(self, node: ast.expr, what: str) -> int:
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(
            node.value, bool
        ):
            return node.value
        raise _Rejected(f"{what} must be an integer literal")

    def _init_value(self, node: ast.expr | None) -> Literal | Param:
        if node is None:
            return Literal(Decimal(0))
        translated = self.visit(node)
        if isinstance(translated, (Literal, Param)):
            return translated
        if isinstance(translated, Unary) and isinstance(translated.operand, Literal):
            sign = -1 if translated.op == "-" else 1
            return Literal(translated.operand.value * sign)
        raise _Rejected("prev(init=...) must be a numeric literal or a p.<param> reference")

    @staticmethod
    def _keywords(node: ast.Call, allowed: tuple[str, ...]) -> dict[str, ast.expr]:
        found: dict[str, ast.expr] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise _Rejected("** unpacking is not allowed in a formula call")
            if keyword.arg not in allowed:
                raise _Rejected(
                    f"unknown keyword {keyword.arg!r}; allowed: {list(allowed)}"
                )
            if keyword.arg in found:
                raise _Rejected(f"duplicate keyword {keyword.arg!r}")
            found[keyword.arg] = keyword.value
        return found

    # -- dispatch --------------------------------------------------------- #

    def visit(self, node: ast.expr, depth: int = 0) -> Expr:
        if depth > MAX_AST_DEPTH:
            raise _Rejected(
                f"expression nests deeper than the {MAX_AST_DEPTH}-level limit"
            )
        if isinstance(node, ast.Constant):
            return self._constant(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Call):
            return self._call(node, depth)
        if isinstance(node, ast.BinOp):
            operator = _BINARY_OPS.get(type(node.op))
            if operator is None:
                raise _Rejected(
                    f"operator {type(node.op).__name__} is not allowed; "
                    "use + - * / only"
                )
            return Binary(
                operator, self.visit(node.left, depth + 1), self.visit(node.right, depth + 1)
            )
        if isinstance(node, ast.UnaryOp):
            operator = _UNARY_OPS.get(type(node.op))
            if operator is None:
                raise _Rejected(f"unary {type(node.op).__name__} is not allowed")
            return Unary(operator, self.visit(node.operand, depth + 1))
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise _Rejected(
                    "chained comparisons are not allowed; combine them with and/or"
                )
            operator = _COMPARE_OPS.get(type(node.ops[0]))
            if operator is None:
                raise _Rejected(
                    f"comparison {type(node.ops[0]).__name__} is not allowed "
                    "(no 'in', 'is')"
                )
            return Compare(
                operator,
                self.visit(node.left, depth + 1),
                self.visit(node.comparators[0], depth + 1),
            )
        if isinstance(node, ast.BoolOp):
            operator = "and" if isinstance(node.op, ast.And) else "or"
            return Logical(
                operator,
                tuple(self.visit(value, depth + 1) for value in node.values),
            )
        if isinstance(node, ast.Name):
            raise _Rejected(
                f"bare name {node.id!r} is not a value; use p.<param>, t.<field>, "
                "it(...), agg(...), cum(...) or prev(...)"
            )
        raise _Rejected(f"AST node {type(node).__name__} is not allowed in a formula")

    def _constant(self, node: ast.Constant) -> Expr:
        value = node.value
        if isinstance(value, bool):
            return Literal(Decimal(1) if value else Decimal(0))
        if isinstance(value, int):
            return Literal(Decimal(value))
        if isinstance(value, str):
            raise _Rejected(
                "a bare string is not a value; strings appear only as it()/prev()/"
                "cum() item ids, agg() selectors and measure names"
            )
        if value is None or value is Ellipsis:
            raise _Rejected(f"constant {value!r} is not a number")
        return Literal(self._numeric_literal(node))

    def _attribute(self, node: ast.Attribute) -> Expr:
        base = node.value
        if not isinstance(base, ast.Name):
            raise _Rejected(
                "attribute access is restricted to p.<param> and t.<field>"
            )
        if base.id == "p":
            if not _IDENT_FULL.match(node.attr):
                raise _Rejected(f"param key {node.attr!r} must match {IDENT_RE}")
            return Param(node.attr)
        if base.id == "t":
            if node.attr not in TIME_FIELDS:
                raise _Rejected(
                    f"unknown period field t.{node.attr}; available: {list(TIME_FIELDS)}"
                )
            return TimeField(node.attr)
        raise _Rejected(
            f"attribute access on {base.id!r} is not allowed; only p.<param> and t.<field>"
        )

    def _call(self, node: ast.Call, depth: int) -> Expr:
        if not isinstance(node.func, ast.Name):
            raise _Rejected(
                "only direct calls to the builtin table are allowed; "
                "no method calls, no computed callees"
            )
        name = node.func.id
        handler = getattr(self, f"_call_{name}", None)
        if handler is not None:
            return handler(node, depth)
        if name in NUMERIC_BUILTINS:
            return self._call_numeric(name, node, depth)
        if name == "if_":
            raise _Rejected(
                "if_ does not exist; use where(cond, a, b) — both branches always "
                "evaluate (D8)"
            )
        raise _Rejected(
            f"unknown function {name!r}; the builtin table is it, prev, agg, cum, "
            "where, min, max, clip, round_, abs_"
        )

    def _call_it(self, node: ast.Call, depth: int) -> Expr:
        keywords = self._keywords(node, ("measure",))
        if len(node.args) != 1:
            raise _Rejected("it() takes exactly one item id")
        return ItemRef(self._item_id(node.args[0], "it() item id"), self._measure(keywords.get("measure")))

    def _call_cum(self, node: ast.Call, depth: int) -> Expr:
        keywords = self._keywords(node, ("measure",))
        if len(node.args) != 1:
            raise _Rejected("cum() takes exactly one item id")
        return Cum(self._item_id(node.args[0], "cum() item id"), self._measure(keywords.get("measure")))

    def _call_prev(self, node: ast.Call, depth: int) -> Expr:
        keywords = self._keywords(node, ("n", "init", "measure"))
        if len(node.args) != 1:
            raise _Rejected("prev() takes exactly one item id, then keyword arguments")
        lag = 1 if "n" not in keywords else self._literal_int(keywords["n"], "prev(n=...)")
        if lag < 1:
            raise _Rejected("prev(n=...) must be at least 1")
        return Prev(
            item_id=self._item_id(node.args[0], "prev() item id"),
            lag=lag,
            init=self._init_value(keywords.get("init")),
            measure=self._measure(keywords.get("measure")),
        )

    def _call_agg(self, node: ast.Call, depth: int) -> Expr:
        keywords = self._keywords(node, ("tag", "measure"))
        if len(node.args) > 1:
            raise _Rejected("agg() takes one selector, positionally or as tag=")
        if len(node.args) == 1 and "tag" in keywords:
            raise _Rejected("agg() selector given both positionally and as tag=")
        source_node = node.args[0] if node.args else keywords.get("tag")
        if source_node is None:
            raise _Rejected('agg() needs a selector, e.g. agg(tag="cat:revenue")')
        selector, reason = parse_selector(self._string_arg(source_node, "agg() selector"))
        if selector is None:
            raise _Rejected(f"agg() selector rejected: {reason}")
        return Agg(selector=selector, measure=self._measure(keywords.get("measure")))

    def _call_where(self, node: ast.Call, depth: int) -> Expr:
        self._keywords(node, ())
        if len(node.args) != 3:
            raise _Rejected("where() takes exactly three arguments: cond, a, b")
        return Where(
            self.visit(node.args[0], depth + 1),
            self.visit(node.args[1], depth + 1),
            self.visit(node.args[2], depth + 1),
        )

    def _call_numeric(self, name: str, node: ast.Call, depth: int) -> Expr:
        keywords = self._keywords(node, ("ndigits",) if name == "round_" else ())
        arity = NUMERIC_BUILTINS[name]
        args = [self.visit(arg, depth + 1) for arg in node.args]
        if name == "round_":
            if len(args) != 1:
                raise _Rejected("round_() takes one value and an optional ndigits=")
            digits_node = keywords.get("ndigits")
            digits = 0 if digits_node is None else self._literal_int(digits_node, "ndigits")
            if not 0 <= digits <= 4:
                raise _Rejected(
                    "round_(ndigits=...) must be between 0 and 4 — the core holds 4 dp"
                )
            return Builtin("round_", (args[0], Literal(Decimal(digits))))
        if arity is not None and len(args) != arity:
            raise _Rejected(f"{name}() takes exactly {arity} arguments")
        if arity is None and len(args) < 2:
            raise _Rejected(f"{name}() takes at least two arguments")
        return Builtin(name, tuple(args))


def parse_formula(source: str, *, item_id: ItemId | None = None) -> ParseOutcome:
    """Parse a formula string into the restricted AST.

    Returns a :class:`ParseOutcome`. On rejection ``expr`` is ``None`` and
    ``diagnostics`` carries ``CK-E003`` naming the reason; a hostile or
    malformed string never raises and never executes. ``agg()`` selectors are
    parsed but not yet resolved to item ids — that happens at graph build.
    """
    if not source.strip():
        return ParseOutcome(
            None,
            (
                make_diagnostic(
                    "CK-E003",
                    item_id=item_id,
                    field="formula",
                    reason="formula is empty",
                ),
            ),
        )
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return ParseOutcome(
            None,
            (
                make_diagnostic(
                    "CK-E003",
                    item_id=item_id,
                    field="formula",
                    reason=f"not a parseable expression ({type(exc).__name__})",
                ),
            ),
        )
    try:
        expr = _Translator(source).visit(tree.body)
    except _Rejected as rejection:
        return ParseOutcome(
            None,
            (
                make_diagnostic(
                    rejection.code,
                    item_id=item_id,
                    field="formula",
                    reason=rejection.reason,
                    **rejection.details,
                ),
            ),
        )
    except RecursionError:
        return ParseOutcome(
            None,
            (
                make_diagnostic(
                    "CK-E003",
                    item_id=item_id,
                    field="formula",
                    reason="expression nests too deeply to translate",
                ),
            ),
        )
    return ParseOutcome(expr, ())
