"""Phase 4 gate: hostile and malformed formulas produce diagnostics, nothing else.

The formula string is the one place where text an agent wrote reaches something
that looks like code. Three properties are gated here, over a corpus of hostile
and malformed sources:

* **never an exception** — every rejection is a ``Diagnostic`` with a
  ``suggested_fix``, because a traceback is not something an agent can act on;
* **never execution** — a recorder wrapped around every dangerous builtin the
  corpus tries to reach must stay silent, and the parser's own source is walked
  to prove it contains no call that could execute anything;
* **never a partial accept** — a rejected source yields no expression at all,
  so nothing downstream can evaluate half a hostile formula.

The corpus is not only a list. ``test_every_ast_node_type_is_decided`` walks the
`ast` module's own grammar and asserts that *every* node type is either
translated or rejected — so a future Python release adding a node type shows up
here rather than as a hole in the whitelist.
"""

from __future__ import annotations

import ast
import builtins
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cashkit.engine.formula import (
    MAX_FORMULA_LENGTH,
    parse_formula,
    parse_selector,
)
from cashkit.engine.graph import compile_book
from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
)
from cashkit.model.diagnostics import CATALOGUE

# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #

ATTRIBUTE_ACCESS = [
    '__import__("os").system("echo pwned")',
    "__builtins__",
    '__builtins__["eval"]("1")',
    "().__class__",
    "().__class__.__bases__",
    '"".__class__.__mro__[1].__subclasses__()',
    "(1).__class__.__name__",
    "p.__class__",
    "t.__dict__",
    "p.rate.__class__",
    "p.a.b",
    "p.a.b.c",
    "t.index.real",
    'it("a").__class__',
    "object.__subclasses__",
    "globals()",
    "locals()",
    "vars()",
    "dir()",
    "type(1)",
    "getattr(p, 'rate')",
    "setattr(p, 'rate', 1)",
    "eval('1+1')",
    "exec('x=1')",
    "compile('1', '<s>', 'eval')",
    "open('/etc/passwd')",
    "input()",
    "breakpoint()",
    "exit()",
    "help()",
    "print(1)",
    "id(1)",
    "hash(1)",
    "repr(1)",
    "str(1)",
    "int('1')",
    "float('1')",
    "list()",
    "dict()",
    "set()",
    "tuple()",
    "range(10)",
    "sum([1])",
    "len('a')",
    "sorted([1])",
    "map(abs_, [1])",
    "filter(None, [1])",
    "zip([1], [2])",
    "iter([1])",
    "next(iter([1]))",
    "memoryview(b'')",
    "bytearray(b'')",
    "__loader__",
    "__spec__",
    "__name__",
    "__file__",
    "__debug__",
    "__builtins__.__dict__",
    "().__class__.__base__.__subclasses__()[0]",
    "[].__class__.__mro__",
    "{}.__class__",
    "(1).__reduce__()",
    "().__reduce_ex__(2)",
    "().__sizeof__()",
    "p.__init__",
    "p.__getattribute__",
    "t.__class__.__call__",
    "abs_.__globals__",
    "where.__code__",
    "it.__closure__",
    "min.__self__",
    "print.__module__",
    "__import__",
    "__build_class__",
    "super()",
    "object()",
    "classmethod(1)",
    "staticmethod(1)",
    "property(1)",
    "isinstance(1, int)",
    "issubclass(int, object)",
    "callable(1)",
    "hasattr(p, 'rate')",
    "delattr(p, 'rate')",
    "format(1)",
    "ascii(1)",
    "bin(1)",
    "oct(1)",
    "hex(1)",
    "chr(65)",
    "ord('a')",
    "bytes(1)",
    "frozenset()",
    "enumerate([])",
    "reversed([])",
    "slice(1)",
    "divmod(1, 2)",
    "pow(2, 3)",
    "round(1.5)",
    "abs(1)",
    "min",
    "max",
    "clip",
    "abs_",
    "round_",
    "where",
    "it",
    "agg",
    "prev",
    "cum",
]

COMPREHENSIONS_AND_BINDINGS = [
    "[x for x in range(3)]",
    "{x for x in range(3)}",
    "{x: x for x in range(3)}",
    "(x for x in range(3))",
    "[x for x in range(3) if x]",
    "lambda: 1",
    "lambda x: x",
    "(lambda x: x)(1)",
    "(y := 3)",
    "(y := 3) + y",
    "1 if t.index else 2",
    "[1, 2, 3]",
    "(1, 2)",
    "{1, 2}",
    "{'a': 1}",
    "[1][0]",
    "'abc'[0]",
    "p.rate[0]",
    "it('a')[0]",
    "'a' 'b'",
    "f'{1}'",
    "f'{__import__(\"os\")}'",
    "b'bytes'",
    "...",
    "None",
    "True and __import__",
    "*[1]",
    "1 ; 2",
    "x = 1",
    "del p",
    "import os",
    "from os import system",
    "assert 1",
    "yield 1",
    "await 1",
    "raise ValueError",
    "class A: pass",
    "def f(): pass",
    "return 1",
    "while 1: pass",
    "with open('x') as f: pass",
    "try: pass\nexcept: pass",
]

MALFORMED = [
    "",
    "   ",
    "\n",
    "\t",
    "1 +",
    "+",
    "(",
    ")",
    "()",
    "((((",
    "1 ++++",
    "*",
    "1 * * 2",
    "it(",
    'it("a"',
    "it()",
    'it("a", "b")',
    'it("a", measure="bogus")',
    'it("A")',
    'it("1abc")',
    'it("")',
    "it(1)",
    "it(p.rate)",
    'cum()',
    'cum("a", "b")',
    'prev()',
    'prev("a", n=0)',
    'prev("a", n=-1)',
    'prev("a", n=1.5)',
    'prev("a", n=t.index)',
    'prev("a", n="1")',
    'prev("a", init=it("b"))',
    'prev("a", bogus=1)',
    'prev("a", n=1, n=2)',
    "agg()",
    'agg("a", tag="b")',
    'agg("nocolon")',
    'agg("a:b:c")',
    'agg("")',
    "agg(1)",
    "agg(tag=p.rate)",
    "where()",
    "where(1)",
    "where(1, 2)",
    "where(1, 2, 3, 4)",
    "where(1, 2, 3, bogus=4)",
    "min()",
    "min(1)",
    "max()",
    "clip(1)",
    "clip(1, 2)",
    "clip(1, 2, 3, 4)",
    "abs_()",
    "abs_(1, 2)",
    "round_()",
    "round_(1, 2)",
    "round_(1, ndigits=5)",
    "round_(1, ndigits=-1)",
    'round_(1, ndigits="2")',
    "if_(1, 2, 3)",
    "unknown_function(1)",
    # Method names of the translator: reachable when dispatch was name-based.
    "numeric(1, 2)",
    "keywords(1)",
    "visit(1)",
    "constant(1)",
    "attribute(1)",
    "call(1)",
    "bounded(1)",
    "measure(1)",
    "item_id(1)",
    "string_arg(1)",
    "literal_int(1)",
    "init_value(1)",
    "numeric_literal(1)",
    "bare_name",
    "'a string'",
    "1 < 2 < 3",
    "1 in [1]",
    "1 is 1",
    "1 not in [1]",
    "1 @ 2",
    "1 // 2",
    "1 % 2",
    "1 ** 2",
    "1 << 2",
    "1 >> 2",
    "1 & 2",
    "1 | 2",
    "1 ^ 2",
    "~1",
    "it(**{'a': 1})",
    "min(*[1, 2])",
    "t.bogus",
    "t.today",
    "q.rate",
    "p.BAD",
    "p.9bad",
    "p.",
    "1j",
    "nan",
    "inf",
]

DEEP = [
    "abs_(" * 60 + "1" + ")" * 60,
    "1" + " + 1" * 2000,
    "-" * 100 + "1",
    "not " * 100 + "1",
    "abs_(" * 5000 + "1" + ")" * 5000,
    "x" * (MAX_FORMULA_LENGTH + 1),
    "1e400",
    "-1e400",
    "999999999999999999999",
]

#: Legal, if unusual. Listed so the corpus states which side of the line they
#: are on rather than leaving it to chance: redundant parentheses collapse, a
#: wide `min()` is still one column operation, and a hex or underscored integer
#: literal is an integer.
ACCEPTED_ODDITIES = [
    "(" * 90 + "1" + ")" * 90,
    "min(" + "1, " * 500 + "1)",
    "0x10",
    "1_000",
    "0b101",
    "0o17",
]

#: Sources that are *accepted* and whose zero-division is handled at run time,
#: not at parse time — masked-safe division is the specified behaviour (§5.4).
ACCEPTED_DIVISION = [
    "1 / 0",
    "1 / 0.0",
    'it("a") / 0',
    'it("a") / it("b")',
    'p.rate / 0',
    'where(it("b") == 0, 0, it("a") / it("b"))',
]

CORPUS = ATTRIBUTE_ACCESS + COMPREHENSIONS_AND_BINDINGS + MALFORMED + DEEP


# --------------------------------------------------------------------------- #
# The canary: nothing in the corpus may ever be called
# --------------------------------------------------------------------------- #


class _Recorder:
    """Wraps a builtin, records every call, and delegates to the original.

    Delegating rather than replacing matters: a canary that swallowed calls
    would change the behaviour of anything else running in the process, and a
    canary that raised would turn an observation into a failure mode of its own.
    """

    def __init__(self, name: str, original: object) -> None:
        self.name = name
        self.original = original
        self.calls: list[str] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append(self.name)
        return self.original(*args, **kwargs)  # type: ignore[operator]


#: Builtins the corpus tries to reach. `__import__` is deliberately absent —
#: wrapping the import hook destabilises anything that imports lazily while the
#: wrapper is installed, and the parser's imports are all module-level anyway.
#: `compile` is absent because ``ast.parse`` calls it; `type`, `getattr` and
#: friends because replacing those breaks the interpreter, not the parser.
_RECORDED_NAMES = (
    "eval", "exec", "open", "input", "print", "breakpoint", "exit", "quit",
)


@pytest.fixture
def recorders(monkeypatch: pytest.MonkeyPatch) -> list[_Recorder]:
    """Install a recorder over each reachable dangerous builtin."""
    installed: list[_Recorder] = []
    for name in _RECORDED_NAMES:
        original = getattr(builtins, name, None)
        if original is None:
            continue
        recorder = _Recorder(name, original)
        monkeypatch.setattr(builtins, name, recorder, raising=False)
        installed.append(recorder)
    return installed


#: Names that must never be *called* anywhere in the parser's source. The
#: check is structural, over the module's own AST, so it holds for inputs the
#: corpus never thought of.
_FORBIDDEN_CALLS = frozenset(
    {"eval", "exec", "__import__", "getattr", "setattr", "delattr", "globals",
     "locals", "vars", "open", "input", "compile", "exec_module"}
)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def _assert_is_a_proper_diagnostic(source: str, outcome) -> None:
    assert outcome.expr is None, f"{source!r} was accepted"
    assert outcome.diagnostics, f"{source!r} produced neither expression nor diagnostic"
    for diagnostic in outcome.diagnostics:
        assert diagnostic.code in CATALOGUE, diagnostic.code
        assert diagnostic.severity == "error"
        assert diagnostic.message.strip()
        assert diagnostic.suggested_fix.strip()
        assert diagnostic.field == "formula"


@pytest.mark.parametrize("source", CORPUS, ids=range(len(CORPUS)))
def test_hostile_and_malformed_sources_are_rejected(source: str) -> None:
    """The gate: a diagnostic, never an exception, never an expression."""
    _assert_is_a_proper_diagnostic(source, parse_formula(source, item_id="probe"))


def test_the_whole_corpus_never_executes_anything(recorders: list[_Recorder]) -> None:
    """Nothing in the corpus reaches a dangerous builtin — the parser
    translates, it does not evaluate."""
    for source in CORPUS + ACCEPTED_ODDITIES + ACCEPTED_DIVISION:
        parse_formula(source, item_id="probe")
    fired = [name for recorder in recorders for name in recorder.calls]
    assert not fired, f"the parser called {sorted(set(fired))}"


def test_the_parser_calls_nothing_that_could_execute_user_input() -> None:
    """Structural, not empirical: the parser's own source contains no call to
    eval, exec, __import__ or any dynamic attribute access. The only `compile`
    in the path is the one inside ``ast.parse``, which compiles to an AST and
    never to code."""
    import inspect

    from cashkit.engine import formula as formula_module

    tree = ast.parse(inspect.getsource(formula_module))
    offenders = [
        f"{node.func.id} at line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FORBIDDEN_CALLS
    ]
    assert not offenders, f"the parser can execute: {offenders}"
    # The only compilation in the path is `ast.parse`, which produces an AST
    # and never code; a bare `compile(...)` would be caught by the walk above.
    assert "ast.parse" in inspect.getsource(formula_module)


def test_corpus_is_large_enough_to_be_a_corpus() -> None:
    assert len(CORPUS) >= 200, len(CORPUS)
    assert len(set(CORPUS)) == len(CORPUS), "duplicate entries dilute the corpus"


def test_parse_is_pure_and_repeatable() -> None:
    """Parsing is memoized (D-P3-04); a hostile string must not be able to
    poison the cache into accepting itself on a second call."""
    for source in CORPUS:
        first = parse_formula(source, item_id="probe")
        second = parse_formula(source, item_id="probe")
        assert first.expr is second.expr is None
        assert [d.code for d in first.diagnostics] == [d.code for d in second.diagnostics]


def test_no_translator_method_is_reachable_as_a_function_name() -> None:
    """Dispatch is an explicit table, not `getattr(self, f"_call_{name}")`.

    Under name-based dispatch, every method whose name started with the prefix
    was callable from a formula string, and `numeric(1, 2)` reached the
    variadic-builtin handler with the wrong signature and raised a TypeError
    out of the parser. The table makes the call surface exactly PRD §5.4.
    """
    from cashkit.engine import formula as formula_module

    for attribute in dir(formula_module._Translator):
        if attribute.startswith("__"):
            continue
        for spelling in (attribute, attribute.lstrip("_"),
                         attribute.removeprefix("_call_")):
            if not spelling or not spelling.isidentifier():
                continue
            outcome = parse_formula(f"{spelling}(1, 2, 3)", item_id="probe")
            if outcome.ok:
                assert spelling in {"it", "cum", "prev", "agg", "where", "min",
                                    "max", "clip", "round_", "abs_"}, spelling
            else:
                _assert_is_a_proper_diagnostic(spelling, outcome)


def test_dotted_param_keys_are_reported_as_CK_E007() -> None:
    for source in ("p.a.b", "p.a.b.c", "p.BAD", "p.rate.__class__"):
        outcome = parse_formula(source, item_id="probe")
        assert outcome.diagnostics[0].code == "CK-E007", source


def test_an_oversized_source_is_refused_before_parsing() -> None:
    outcome = parse_formula("1 + " * MAX_FORMULA_LENGTH + "1", item_id="probe")
    assert not outcome.ok
    assert "character" in outcome.diagnostics[0].message


@pytest.mark.parametrize("source", ACCEPTED_ODDITIES)
def test_legal_oddities_stay_legal(source: str) -> None:
    """Pinned so a future tightening of the whitelist is a deliberate change."""
    outcome = parse_formula(source, item_id="probe")
    assert outcome.ok, [d.message for d in outcome.diagnostics]


@pytest.mark.parametrize("source", ACCEPTED_DIVISION)
def test_division_by_zero_parses_and_is_a_run_time_warning(source: str) -> None:
    """PRD §5.4: division is masked-safe. Rejecting `a / x` at parse time would
    be wrong — both `where` branches always evaluate, so `a / 0` is by design."""
    outcome = parse_formula(source, item_id="probe")
    assert outcome.ok, [d.message for d in outcome.diagnostics]


# --------------------------------------------------------------------------- #
# The whitelist is the translator: prove it decides every node type
# --------------------------------------------------------------------------- #

#: One expression source per `ast` expression node type. Statement and helper
#: node types cannot appear in `mode="eval"` and are excluded by construction.
NODE_SAMPLES: dict[str, str] = {
    "Attribute": "p.rate",
    "Await": "await 1",
    "BinOp": "1 + 1",
    "BoolOp": "t.is_business_day and t.is_quarter_end",
    "Call": "abs_(1)",
    "Compare": "1 < 2",
    "Constant": "1",
    "Dict": "{1: 2}",
    "DictComp": "{x: x for x in y}",
    "FormattedValue": "f'{1}'",
    "GeneratorExp": "(x for x in y)",
    "IfExp": "1 if 1 else 2",
    "JoinedStr": "f'a{1}'",
    "Lambda": "lambda: 1",
    "List": "[1]",
    "ListComp": "[x for x in y]",
    "Name": "bare",
    "NamedExpr": "(x := 1)",
    "Set": "{1}",
    "SetComp": "{x for x in y}",
    "Slice": "a[1:2]",
    "Starred": "f(*a)",
    "Subscript": "a[1]",
    "Tuple": "(1, 2)",
    "UnaryOp": "-1",
    "Yield": "yield 1",
    "YieldFrom": "yield from x",
}


def _expression_node_types() -> set[str]:
    return {
        name
        for name, value in vars(ast).items()
        if isinstance(value, type)
        and issubclass(value, ast.expr)
        and value is not ast.expr
        and not name.startswith("_")
    }


def test_every_ast_expression_node_type_has_a_sample() -> None:
    """A Python release adding an expression node type must fail here, not
    silently widen what the whitelist lets through."""
    missing = _expression_node_types() - set(NODE_SAMPLES)
    assert not missing, f"no hostility sample for: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(NODE_SAMPLES))
def test_every_ast_node_type_is_decided(name: str) -> None:
    """Either the translator handles the node type, or it rejects it with a
    diagnostic. There is no third outcome — no exception, no silent pass."""
    outcome = parse_formula(NODE_SAMPLES[name], item_id="probe")
    if outcome.ok:
        assert name in {"Attribute", "BinOp", "BoolOp", "Call", "Compare",
                        "Constant", "UnaryOp"}, (
            f"{name} was accepted; the whitelist should not include it"
        )
    else:
        _assert_is_a_proper_diagnostic(NODE_SAMPLES[name], outcome)


# --------------------------------------------------------------------------- #
# Random strings
# --------------------------------------------------------------------------- #


@given(source=st.text(max_size=120))
@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_arbitrary_text_never_raises(source: str) -> None:
    outcome = parse_formula(source, item_id="probe")
    assert outcome.ok or outcome.diagnostics


_TOKENS = [
    "it(", "agg(", "prev(", "cum(", "where(", "min(", "max(", "clip(",
    "round_(", "abs_(", "p.", "t.", '"a"', "1", "0", "+", "-", "*", "/",
    ")", ",", "==", "and", "not", "__import__", "lambda", "[", "]", "{", "}",
    ":", "=", ".", "n=", "init=", "tag=", "measure=", "ndigits=",
]


@given(tokens=st.lists(st.sampled_from(_TOKENS), max_size=30))
@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_token_soup_never_raises(tokens: list[str]) -> None:
    outcome = parse_formula(" ".join(tokens), item_id="probe")
    assert outcome.ok or outcome.diagnostics


@given(source=st.text(max_size=60))
@settings(max_examples=400, deadline=None)
def test_arbitrary_selectors_never_raise(source: str) -> None:
    selector, reason = parse_selector(source)
    assert (selector is None) != (reason is None)


# --------------------------------------------------------------------------- #
# Graph-build-time rejection: references and cycles
# --------------------------------------------------------------------------- #


def _book(*formulas: tuple[str, str], tags: dict[str, str] | None = None) -> Book:
    items: dict[str, Item] = {
        "seed": Item(
            id="seed",
            name="seed",
            kind="flow",
            tags=tags if tags is not None else {"cat": "revenue"},
            flags={"cashflow"},
            segments=[
                Segment(
                    start=date(2026, 1, 1),
                    recurrence=Recurrence(every=1, unit=Grain.MONTH),
                    amount=Amount(constant=Decimal("100")),
                )
            ],
        )
    }
    for item_id, formula in formulas:
        items[item_id] = Item(
            id=item_id, name=item_id, kind="derived", tags={"cat": "derived"},
            formula=formula,
        )
    return Book(
        id="hardening",
        base_grain=Grain.DAY,
        calendar=CalendarSpec(weekend={5, 6}),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 3, 1)),
        opening_balance=Decimal("0"),
        cutover=date(2026, 1, 1),
        params={"rate": Decimal("0.1")},
        items=items,
    )


def _codes(book: Book) -> set[str]:
    return {diagnostic.code for diagnostic in compile_book(book).diagnostics}


def test_unknown_item_reference_is_CK_E001() -> None:
    assert "CK-E001" in _codes(_book(("bad", 'it("nope")')))
    assert "CK-E001" in _codes(_book(("bad", 'prev("nope")')))
    assert "CK-E001" in _codes(_book(("bad", 'cum("nope")')))


def test_selector_matching_nothing_is_CK_E001() -> None:
    assert "CK-E001" in _codes(_book(("bad", 'agg(tag="cat:absent")')))
    assert "CK-E001" in _codes(_book(("bad", 'agg(tag="flag:absent")')))


def test_self_dependency_through_agg_names_the_cycle() -> None:
    """PRD §5.4: a selector that would make an item depend on itself is
    rejected — and the diagnostic has to say which cycle."""
    book = _book(("total", 'agg(tag="cat:derived", measure="accrual")'))
    diagnostics = [
        d for d in compile_book(book).diagnostics if d.code == "CK-E002"
    ]
    assert diagnostics, _codes(book)
    assert "total" in diagnostics[0].message
    assert "agg(" in diagnostics[0].message


def test_direct_self_reference_names_the_cycle() -> None:
    book = _book(("mirror", 'it("mirror") + 1'))
    diagnostics = [d for d in compile_book(book).diagnostics if d.code == "CK-E002"]
    assert diagnostics
    assert "mirror -> mirror" in diagnostics[0].message


def test_indirect_cycle_names_every_member() -> None:
    book = _book(("a", 'it("b")'), ("b", 'it("c")'), ("c", 'it("a")'))
    diagnostics = [d for d in compile_book(book).diagnostics if d.code == "CK-E002"]
    assert diagnostics
    cycle = diagnostics[0].message
    assert all(member in cycle for member in ("a", "b", "c"))


def test_a_cycle_through_prev_is_legal_and_not_reported() -> None:
    book = _book(("a", 'prev("b", init=0) + 1'), ("b", 'it("a") * 2'))
    compiled = compile_book(book)
    assert "CK-E002" not in {d.code for d in compiled.diagnostics}
    assert any(not component.trivial for component in compiled.components)


def test_a_broken_item_degrades_one_line_not_the_run() -> None:
    """D-P2-08: the run continues, the broken item evaluates to zero, and the
    diagnostic says why."""
    import numpy as np

    import cashkit.engine as engine

    book = _book(("bad", 'it("nope")'), ("good", 'it("seed", measure="accrual") * 2'))
    result = engine.run(book)
    assert (result.column("bad", "accrual") == 0).all()
    assert np.any(result.column("good", "accrual") != 0)
    assert any(d.code == "CK-E001" for d in result.diagnostics)


def test_selector_resolution_is_graph_build_time_and_static() -> None:
    """The resolved ids are baked into the Agg node, so the DAG is static for
    the whole run (PRD §5.4)."""
    from cashkit.engine.formula import Agg, iter_refs

    book = _book(("total", 'agg(tag="cat:revenue", measure="accrual")'))
    compiled = compile_book(book)
    entry = compiled.items["total"]
    assert entry.expr is not None
    aggs = [ref for ref in iter_refs(entry.expr) if isinstance(ref, Agg)]
    assert aggs and aggs[0].items == ("seed",)
    assert entry.same_period_deps == frozenset({"seed"})
