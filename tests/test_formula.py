"""The formula front-end: what the language accepts and what it means.

Phase 2 builds the parser (ADR-0001); Phase 4 hardens it, and the hostile-input
corpus lives in ``tests/test_formula_hardening.py``. This module pins the
*accepted* surface — the symbol table of PRD §5.4 — and the selector grammar.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cashkit.engine.formula import (
    Agg,
    Binary,
    Builtin,
    Compare,
    Cum,
    ItemRef,
    Literal,
    Logical,
    Param,
    Prev,
    TimeField,
    Unary,
    Where,
    iter_refs,
    parse_formula,
    parse_selector,
)


def _parse(source: str):
    outcome = parse_formula(source, item_id="probe")
    assert outcome.ok, [d.message for d in outcome.diagnostics]
    return outcome.expr


# --------------------------------------------------------------------------- #
# Symbol table
# --------------------------------------------------------------------------- #


def test_it_defaults_to_the_cash_measure() -> None:
    expr = _parse('it("revenue")')
    assert expr == ItemRef("revenue", "cash")


def test_measure_is_selectable() -> None:
    assert _parse('it("revenue", measure="accrual")') == ItemRef("revenue", "accrual")
    assert _parse('cum("revenue", measure="accrual")') == Cum("revenue", "accrual")


def test_prev_defaults_and_keywords() -> None:
    assert _parse('prev("cash")') == Prev("cash", 1, Literal(Decimal(0)), "cash")
    expr = _parse('prev("cash", n=3, init=p.opening_balance)')
    assert expr == Prev("cash", 3, Param("opening_balance"), "cash")


def test_prev_init_accepts_a_negative_literal() -> None:
    assert _parse('prev("cash", init=-250.50)') == Prev(
        "cash", 1, Literal(Decimal("-250.50")), "cash"
    )


def test_decimal_literals_keep_the_authored_digits() -> None:
    """The literal is read from the source text, so it never passes through a
    binary fraction — 0.1 is exactly one tenth here."""
    expr = _parse("0.1")
    assert isinstance(expr, Literal)
    assert expr.value == Decimal("0.1")
    assert str(expr.value) == "0.1"


def test_param_and_time_fields() -> None:
    assert _parse("p.vat_standard") == Param("vat_standard")
    for field in ("index", "month", "is_quarter_end", "is_business_day"):
        assert _parse(f"t.{field}") == TimeField(field)


def test_agg_accepts_the_selector_positionally_or_by_keyword() -> None:
    positional = _parse('agg("cat:revenue")')
    keyword = _parse('agg(tag="cat:revenue")')
    assert isinstance(positional, Agg) and isinstance(keyword, Agg)
    assert positional.selector.tags == (("cat", "revenue"),)
    assert positional == keyword


def test_where_and_operators() -> None:
    expr = _parse('where(it("a") > 0, it("a") * p.rate, -it("b"))')
    assert isinstance(expr, Where)
    assert isinstance(expr.cond, Compare)
    assert isinstance(expr.then, Binary)
    assert isinstance(expr.otherwise, Unary)


def test_logical_operators_are_elementwise_not_short_circuit() -> None:
    expr = _parse("t.is_business_day and t.is_quarter_end")
    assert isinstance(expr, Logical)
    assert expr.op == "and"
    assert len(expr.operands) == 2


def test_safe_builtins() -> None:
    assert isinstance(_parse('min(it("a"), it("b"))'), Builtin)
    assert isinstance(_parse('max(it("a"), it("b"), 0)'), Builtin)
    assert isinstance(_parse('clip(it("a"), 0, 100)'), Builtin)
    assert isinstance(_parse('abs_(it("a"))'), Builtin)
    rounded = _parse('round_(it("a"), ndigits=2)')
    assert isinstance(rounded, Builtin)
    assert rounded.args[1] == Literal(Decimal(2))


def test_if_underscore_does_not_exist() -> None:
    outcome = parse_formula('if_(it("a") > 0, 1, 0)', item_id="probe")
    assert not outcome.ok
    assert outcome.diagnostics[0].code == "CK-E003"
    assert "where" in outcome.diagnostics[0].message


def test_iter_refs_finds_every_dependency() -> None:
    expr = _parse(
        'where(agg(tag="cat:revenue") > 0, '
        'prev("cash", init=0) + cum("fees"), it("other"))'
    )
    kinds = sorted(type(ref).__name__ for ref in iter_refs(expr))
    assert kinds == ["Agg", "Cum", "ItemRef", "Prev"]


# --------------------------------------------------------------------------- #
# Selector grammar
# --------------------------------------------------------------------------- #


def test_selector_terms_are_anded() -> None:
    selector, reason = parse_selector("cat:revenue customer:acme flag:committed")
    assert reason is None and selector is not None
    assert selector.tags == (("cat", "revenue"), ("customer", "acme"))
    assert selector.flags == ("committed",)
    assert selector.matches({"cat": "revenue", "customer": "acme"}, {"committed"})
    assert not selector.matches({"cat": "revenue"}, {"committed"})
    assert not selector.matches({"cat": "revenue", "customer": "acme"}, set())


@pytest.mark.parametrize(
    "source",
    ["", "  ", "norcolon", "a:b:c", "CAT:revenue", "cat:with space", "flag:Bad"],
)
def test_malformed_selectors_are_rejected_with_a_reason(source: str) -> None:
    selector, reason = parse_selector(source)
    assert selector is None
    assert reason


def test_selector_has_no_or_negation_or_wildcards() -> None:
    """v1 keeps the grammar boring on purpose: finer slices are modelled as tags."""
    for source in ("cat:revenue|cat:other", "!cat:revenue", "cat:*"):
        selector, _ = parse_selector(source)
        if selector is not None:
            # A wildcard, if it parsed at all, is a literal tag value and matches
            # nothing but itself — never a pattern.
            assert not selector.matches({"cat": "revenue"}, set())
