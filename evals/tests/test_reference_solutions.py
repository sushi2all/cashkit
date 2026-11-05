"""The eval's own correctness gate, half one: every reference solution passes.

Each family states its expected figures independently (``sdkeval.oracle``,
no engine). The reference solution is run through the real SDK in the
sandbox and graded. A failure here means the oracle and the engine disagree —
either the family's arithmetic is wrong or the engine moved; someone must
decide which before any model score means anything.

Covers one dev and one heldout variant of every family.
"""

from __future__ import annotations

import pytest

from sdkeval import checks, sandbox
from sdkeval.tasks import FAMILIES, HELDOUT_FROM, instance

CASES = [(name, v) for name in sorted(FAMILIES) for v in (0, HELDOUT_FROM)]


@pytest.mark.parametrize("family,variant", CASES, ids=[f"{f}#{v}" for f, v in CASES])
def test_reference_passes(family, variant):
    task = instance(family, variant)
    obs = sandbox.execute(task.reference, setup=task.setup, run_twice=task.run_twice)
    assert obs["status"] == "ok", obs.get("exception")
    results = checks.grade(checks.checks_for(task.checks, read_only=task.read_only), obs, task.reference)
    failed = [f"{r.label}: {r.detail}" for r in results if not r.passed]
    assert not failed, failed


def test_every_family_has_critical_numeric_or_answer_checks():
    """A family that only checks structure could pass a book with wrong numbers."""
    numeric = {"series", "item_total", "closing", "balance_at", "answer", "synthetic_series", "events_total",
               "synthetic_total", "tag_total"}
    for name in FAMILIES:
        task = instance(name, 0)
        kinds = {c.kind for c in task.checks if c.critical}
        assert kinds & numeric, f"{name} has no critical numeric check"


def test_reference_solutions_are_hygienic():
    """References model good practice: no floats, no store access, no clock."""
    from sdkeval import static

    for name in FAMILIES:
        task = instance(name, 0)
        for rule in static.RULES:
            assert static.scan(task.reference, rule) == [], (name, rule)
