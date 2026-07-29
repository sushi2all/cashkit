"""The §10.1 diagnostic catalogue as data: complete, consistent, instantiable."""

from __future__ import annotations

from cashkit.model import CATALOGUE, Diagnostic, make_diagnostic

#: Exactly the codes in PRD §10.1. The set grows; codes never change meaning.
PRD_CODES = {
    "CK-E001",
    "CK-E002",
    "CK-E003",
    "CK-E004",
    "CK-E005",
    "CK-E006",
    "CK-E007",
    "CK-E008",
    "CK-E009",
    "CK-E010",
    "CK-E011",
    "CK-E012",
    "CK-E013",
    "CK-E020",
    "CK-W001",
    "CK-W002",
    "CK-W003",
    "CK-W004",
    "CK-W005",
    "CK-W010",
    "CK-I001",
    "CK-I002",
}

SEVERITY_BY_LETTER = {"E": "error", "W": "warning", "I": "info"}


def test_catalogue_matches_prd_exactly() -> None:
    assert set(CATALOGUE) == PRD_CODES


def test_severity_consistent_with_code_letter() -> None:
    for code, spec in CATALOGUE.items():
        assert spec.code == code
        assert spec.severity == SEVERITY_BY_LETTER[code[3]]


def test_every_spec_has_message_and_fix() -> None:
    for spec in CATALOGUE.values():
        assert spec.message.strip()
        assert spec.suggested_fix.strip()


def test_every_code_is_instantiable() -> None:
    """Fill each spec's placeholders with dummy values and build a Diagnostic."""
    for code, spec in CATALOGUE.items():
        kwargs = {name: f"<{name}>" for name in spec.placeholders()}
        kwargs.pop("item_id", None)
        diagnostic = make_diagnostic(code, item_id="some_item", **kwargs)
        assert isinstance(diagnostic, Diagnostic)
        assert diagnostic.severity == spec.severity
        assert diagnostic.item_id == "some_item"
        assert diagnostic.message
        assert diagnostic.suggested_fix


def test_unknown_code_is_programmer_error() -> None:
    try:
        make_diagnostic("CK-E999")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("unknown catalogue code must raise KeyError")
