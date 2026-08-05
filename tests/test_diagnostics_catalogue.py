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

#: Codes added after the PRD's initial table. §10.1 states the set grows and
#: codes never change meaning, so growth is legal — but it must be deliberate,
#: which is what listing them here forces. Each names the phase that minted it
#: and why no existing code covered the condition (see DECISIONS.md).
ADDED_CODES = {
    # Phase 5 — ledger mechanics. ADR-0012 §5 requires the referential rules
    # (target exists, not already corrected, not a tombstone) to be ledger-level
    # diagnostics with codes assigned in Phase 5.
    "CK-E014",  # ledger event not found
    "CK-E015",  # ledger row is not in a state the operation can act on
    "CK-E016",  # void_event refuses a bare actual — fix names correct_event
    "CK-E017",  # import row with no ext_id: no idempotency key
    "CK-E018",  # event attached to an item that cannot carry it
    # Phase 6 — tax regimes.
    "CK-E019",  # TaxRegime misconfigured
    # Phase 7 — scenarios. Every one is something an agent can plausibly do
    # through the §6.3 surface, so none of them may be an exception; no §10.1
    # code describes a scenario-graph or overlay-resolution failure.
    "CK-E021",  # unknown scenario id (fork parent, write target, chain link)
    "CK-E022",  # scenario id already exists
    "CK-E023",  # overlay targets an item the parent chain does not define
    "CK-E024",  # reserved param opening_balance is not a valid money value
}

SEVERITY_BY_LETTER = {"E": "error", "W": "warning", "I": "info"}


def test_catalogue_contains_every_prd_code() -> None:
    assert PRD_CODES <= set(CATALOGUE)


def test_catalogue_grows_only_deliberately() -> None:
    """No code appears in the catalogue that no one wrote down here."""
    assert set(CATALOGUE) == PRD_CODES | ADDED_CODES


def test_added_codes_do_not_shadow_prd_codes() -> None:
    assert not (PRD_CODES & ADDED_CODES)


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
