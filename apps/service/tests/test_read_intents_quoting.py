"""The read intents give the caller the answer, not the ingredients (S2).

S1 built the twelve read intents and proved each one matches the SDK
(``tests/test_read_intents.py``). S2 wires them to a model, and that exposed a
gap in two of them: the payload was rich enough for a **person** to finish the
sum, and finishing a sum is the one thing the model must never do.

* R1 ``project_balance`` answered with the horizon summary. "Can I afford this
  in September" is a question about September, so the caller had to subtract.
  It now carries the closing balance for every month, before and after.
* R9 ``compare_scenarios`` answered with two columns. "By how much" is the
  third one, so the caller had to subtract. It now carries the delta SPEC §5-F4
  already asked the compare view to show.

Both are additions to a payload, so nothing S1 asserted changes. Recorded as
D-MLP-26.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest

from cashkit_service.intents.read import execute

AS_OF = _dt.date(2026, 3, 17)


@pytest.fixture
def kit(seeded_client, books_root):
    # `seeded_client` first: the directory does not exist until the book does.
    from cashkit.sdk import CashKit

    book_dir = next(p for p in books_root.iterdir() if p.is_dir())
    opened, diagnostics = CashKit.open(book_dir)
    assert opened is not None, diagnostics
    try:
        yield opened
    finally:
        if opened.ledger is not None:
            opened.ledger.close()


def run(kit, intent: dict) -> dict:
    return execute(kit, intent, scenario="base", as_of=AS_OF)


# --- R1 -------------------------------------------------------------------- #


def test_r1_carries_the_closing_balance_of_every_month(kit):
    payload = run(kit, {"op": "project_balance"})
    months = payload["closing_by_month"]

    assert len(months) == 12
    assert set(months) >= {"2026-01", "2026-09", "2026-12"}
    assert set(months["2026-09"]) == {"exact", "display"}

    from cashkit_service.serialize import closing_series, period_starts

    run_ref = kit.run("base")
    expected = dict(
        zip(
            (p.isoformat()[:7] for p in period_starts(run_ref)),
            closing_series(run_ref),
            strict=True,
        )
    )
    assert Decimal(months["2026-09"]["exact"]) == expected["2026-09"]


def test_r1_shows_september_before_and_after_the_hypothetical(kit):
    """The affordability question, answered by quoting rather than subtracting."""
    payload = run(kit, {"op": "project_balance", "delta": "-1500.00",
                        "delta_date": "2026-09-15"})

    before = Decimal(payload["closing_by_month_before"]["2026-09"]["exact"])
    after = Decimal(payload["closing_by_month"]["2026-09"]["exact"])
    assert after == before - Decimal("1500.00")
    assert payload["hypothetical"] is True


def test_r1_leaves_earlier_months_alone(kit):
    """A September purchase does not change August."""
    payload = run(kit, {"op": "project_balance", "delta": "-1500.00",
                        "delta_date": "2026-09-15"})
    assert payload["closing_by_month_before"]["2026-08"] == payload["closing_by_month"]["2026-08"]


def test_r1_still_never_touches_the_book(kit):
    before = kit.status().model_dump()
    run(kit, {"op": "project_balance", "delta": "-1500.00", "delta_date": "2026-09-15"})
    assert kit.status().model_dump() == before


# --- R9 -------------------------------------------------------------------- #


def test_r9_carries_the_delta_column(kit):
    """SPEC §5-F4: two columns of the same metric, per period, with a delta."""
    payload = run(kit, {"op": "compare_scenarios", "scenarios": ["base", "downside"]})

    for period in payload["periods"]:
        values = period["values"]
        assert "delta" in values
        if values["base"] is None or values["downside"] is None:
            assert values["delta"] is None
            continue
        assert Decimal(values["delta"]["exact"]) == (
            Decimal(values["downside"]["exact"]) - Decimal(values["base"]["exact"])
        )


def test_r9_keeps_absent_apart_from_zero_in_the_delta(kit):
    """``None`` is not zero, and a delta against absent is absent."""
    payload = run(kit, {"op": "compare_scenarios", "scenarios": ["base", "downside"]})
    for period in payload["periods"]:
        values = period["values"]
        if values["delta"] is not None:
            assert set(values["delta"]) == {"exact", "display"}


def test_r9_adds_no_delta_to_a_single_scenario(kit):
    """A comparison of one thing has nothing to compare."""
    payload = run(kit, {"op": "compare_scenarios", "scenarios": ["base"]})
    assert all("delta" not in period["values"] for period in payload["periods"])
