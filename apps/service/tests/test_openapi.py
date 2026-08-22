"""The published OpenAPI schema (SPEC §10 contract tests).

S3 generates the TypeScript client from this file. A committed schema that has
drifted from the service is worse than none, so the drift check is a test.
"""

from __future__ import annotations

import json

from cashkit_service.openapi import SCHEMA_PATH, render


def test_the_committed_schema_matches_the_service():
    assert SCHEMA_PATH.exists(), "run `uv run python -m cashkit_service.openapi`"
    assert SCHEMA_PATH.read_text() == render(), (
        "the committed OpenAPI schema has drifted; regenerate it with "
        "`uv run python -m cashkit_service.openapi`"
    )


def test_the_schema_covers_the_spec_endpoints():
    """Every SPEC §3 endpoint S1 owns, plus the two the SPEC gained."""
    paths = set(json.loads(SCHEMA_PATH.read_text())["paths"])
    assert {
        "/auth/link", "/auth/verify", "/me", "/me/export", "/books",
        "/book/state", "/book/forecast", "/book/trace", "/book/why_zero",
        "/book/events", "/book/reconcile", "/book/edits",
        "/proposals/{proposal_id}", "/book/save", "/book/discard",
        "/book/scenarios", "/book/scenarios/{scenario_id}/activate",
        "/book/compare", "/export",
        # Added to SPEC §3 by S1: F5 needs R10 and §6-S15 needs the revision list.
        "/book/validate", "/book/history",
    } <= paths


def test_the_schema_does_not_promise_what_s1_did_not_build():
    """POST /turns and POST /import belong to S2 and S5."""
    paths = set(json.loads(SCHEMA_PATH.read_text())["paths"])
    assert "/turns" not in paths
    assert "/import" not in paths


def test_money_is_a_named_schema_with_both_forms():
    schemas = json.loads(SCHEMA_PATH.read_text())["components"]["schemas"]
    assert "Money" in schemas
    assert set(schemas["Money"]["properties"]) == {"exact", "display"}
    assert schemas["Money"]["properties"]["exact"]["type"] == "string"
    assert schemas["Money"]["properties"]["display"]["type"] == "string"


def test_the_what_if_field_is_in_the_contract():
    schemas = json.loads(SCHEMA_PATH.read_text())["components"]["schemas"]
    assert "WhatIf" in schemas
    assert set(schemas["WhatIf"]["properties"]) == {"stamped", "reason", "scenario"}


def test_the_schema_builds_without_a_database():
    """Publishing the contract must not need infrastructure to be up."""
    schema = json.loads(render())
    assert schema["info"]["title"] == "CashKit MLP service"
