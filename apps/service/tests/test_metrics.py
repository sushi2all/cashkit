"""Metrics (SPEC §11), and the hard rule made mechanical.

SPEC §11: *no user identifiers in metric names or labels; metrics stay
content-free.* The interesting tests here are not that a counter counts. They
are that the rule cannot be broken by accident:

* every label value a metric may carry is declared, and an undeclared one is
  **refused** rather than recorded;
* the route label is a matched template, so no id reaches a series;
* the alarm rules and the metric names agree, so a rule cannot be watching a
  series nothing produces — which is the quietest way an alarm never fires.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from cashkit_service.db import books, deletions, llm_calls, proposals, turns, users
from cashkit_service.metrics import (
    SPECS,
    LabelRefused,
    MetricsRegistry,
    refresh_db_gauges,
    render,
    status_class,
)

ALERTS = Path(__file__).resolve().parents[3] / "ops" / "observability" / "alerts.yml"
NOW = dt.datetime(2026, 8, 23, 12, 0, tzinfo=dt.timezone.utc)


# --- the content-free rule ------------------------------------------------ #


def test_an_undeclared_label_value_is_refused():
    """The rule as a mechanism, not as a convention.

    A user id in a label is the failure SPEC §11 names. It cannot get in
    without first being added to the vocabulary in `metrics.py`, which is a
    diff a reviewer sees.
    """
    registry = MetricsRegistry()
    registry.gauge("cashkit_turns_24h", 1.0, kind="answer", outcome="ok")

    with pytest.raises(LabelRefused, match="vocabulary"):
        registry.gauge(
            "cashkit_turns_24h", 1.0, kind=str(uuid.uuid4()), outcome="ok"
        )
    with pytest.raises(LabelRefused, match="takes labels"):
        registry.gauge("cashkit_turns_24h", 1.0, kind="answer", outcome="ok", user="u@x.com")


def test_no_metric_declares_a_label_that_could_name_a_person():
    """A structural check on the vocabulary itself.

    Three labels are unconstrained — `route` and the three `cashkit_build_info`
    labels — and each is checked separately below. Every other label has a
    finite declared set, so its cardinality is bounded and its values are code.
    """
    free = {
        (spec.name, label)
        for spec in SPECS.values()
        for label, allowed in spec.labels.items()
        if allowed is None
    }
    assert free == {
        ("cashkit_http_requests_total", "route"),
        ("cashkit_http_request_duration_seconds", "route"),
        ("cashkit_build_info", "version"),
        ("cashkit_build_info", "engine_version"),
        ("cashkit_build_info", "environment"),
    }, f"a new unconstrained label appeared: {sorted(free)}"

    # No label is named for a thing that has an identity. `origin`, `kind`,
    # `purpose`, `status`, `component` are categories; `user`, `book`,
    # `session` would be identifiers wearing a category's clothes.
    banned = {"user", "user_id", "email", "account", "book", "book_id",
              "session", "turn", "turn_id", "proposal", "proposal_id", "id"}
    for spec in SPECS.values():
        for label in spec.labels:
            assert label not in banned, f"{spec.name} takes a {label!r} label"


async def test_the_route_label_is_a_template_never_a_path(book_client, app):
    """End to end: hit a route with an id in it, read the series."""
    created = await book_client.post(
        "/book/edits",
        json={"origin": "settings", "ops": [{"op": "set_opening_balance", "amount": "77.00"}]},
    )
    proposal_id = created.json()["proposal"]["id"]
    await book_client.post(f"/proposals/{proposal_id}", json={"action": "discard"})

    exposition = render(app.state.metrics)
    assert 'route="/proposals/{proposal_id}"' in exposition
    assert proposal_id not in exposition


async def test_the_exposition_carries_no_identifier_after_a_realistic_walk(book_client, app):
    """Auth, a read, a card and its confirmation — then scan the whole document.

    A uuid or an @ in the exposition is the failure; the scan is over every
    byte rather than over the labels somebody remembered to check.
    """
    created = await book_client.post(
        "/book/edits",
        json={"origin": "settings", "ops": [{"op": "set_opening_balance", "amount": "88.00"}]},
    )
    await book_client.post(
        f"/proposals/{created.json()['proposal']['id']}", json={"action": "accept"}
    )
    await book_client.get("/book/state")
    await book_client.get("/me")

    exposition = render(app.state.metrics)
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", exposition
    ), "a uuid reached the exposition"
    assert "@" not in exposition, "an address reached the exposition"


# --- the exposition ------------------------------------------------------- #


def test_the_exposition_parses_as_prometheus_text():
    registry = MetricsRegistry()
    registry.inc("cashkit_http_requests_total", route="/me", method="GET", status="2xx")
    registry.observe("cashkit_http_request_duration_seconds", 0.42, route="/me", method="GET")
    registry.gauge("cashkit_llm_spend_usd_24h", 1.25)

    text = render(registry)
    assert "# TYPE cashkit_http_requests_total counter" in text
    assert 'cashkit_http_requests_total{method="GET",route="/me",status="2xx"} 1.0' in text
    assert 'cashkit_http_request_duration_seconds_bucket{method="GET",route="/me",le="1.0"} 1' in text
    assert 'cashkit_http_request_duration_seconds_bucket{method="GET",route="/me",le="+Inf"} 1' in text
    assert 'cashkit_http_request_duration_seconds_count{method="GET",route="/me"} 1' in text
    assert "cashkit_llm_spend_usd_24h 1.25" in text

    for line in text.splitlines():
        assert line.startswith("#") or re.match(r"^[a-z0-9_]+(\{.*\})? \S+$", line), line


def test_a_histogram_bucket_is_cumulative():
    registry = MetricsRegistry()
    for seconds in (0.02, 0.2, 5.0):
        registry.observe("cashkit_http_request_duration_seconds", seconds, route="/x", method="GET")
    text = render(registry)
    assert 'le="0.05"} 1' in text
    assert 'le="0.3"} 2' in text
    assert 'le="8.0"} 3' in text


@pytest.mark.parametrize("code,expected", [(200, "2xx"), (404, "4xx"), (503, "5xx")])
def test_status_class(code: int, expected: str):
    assert status_class(code) == expected


# --- the gauges that come from Postgres ----------------------------------- #


async def _seed(database) -> None:
    user_id, book_id = uuid.uuid4(), uuid.uuid4()
    async with database.connect() as conn:
        await conn.execute(users.insert().values(id=user_id, email="m@x.com", created_at=NOW))
        await conn.execute(
            books.insert().values(id=book_id, user_id=user_id, storage_path="/x", created_at=NOW)
        )
        for i, (kind, outcome, latency, cost, purposes) in enumerate(
            [
                ("answer", "ok", 8000, "0.0004", ["interpret", "qa"]),
                ("answer", "ok", 4000, "0.0003", ["interpret", "qa"]),
                ("proposal", "ok", 3000, "0.0002", ["interpret"]),
                ("refusal", "over_budget", 5, None, []),
                # A stale turn, outside the 24-hour window: it must not count.
                ("answer", "ok", 99000, "9.99", ["interpret", "repair"]),
            ]
        ):
            created = NOW - (dt.timedelta(days=3) if i == 4 else dt.timedelta(hours=1))
            turn_id = uuid.uuid4()
            await conn.execute(
                turns.insert().values(
                    id=turn_id, user_id=user_id, book_id=book_id, request_id=f"r{i}",
                    input_text="x", kind=kind, outcome=outcome, latency_ms=latency,
                    cost=Decimal(cost) if cost else None, created_at=created,
                )
            )
            for seq, purpose in enumerate(purposes):
                await conn.execute(
                    llm_calls.insert().values(
                        id=uuid.uuid4(), turn_id=turn_id, seq=seq, purpose=purpose,
                        cost=Decimal(cost or "0"), created_at=created,
                    )
                )
        # A repair inside the window, so the ratio is not zero.
        turn_id = uuid.uuid4()
        await conn.execute(
            turns.insert().values(
                id=turn_id, user_id=user_id, book_id=book_id, request_id="rr",
                input_text="x", kind="proposal", outcome="ok", latency_ms=6000,
                created_at=NOW - dt.timedelta(hours=2),
            )
        )
        for seq, purpose in enumerate(["interpret", "repair"]):
            await conn.execute(
                llm_calls.insert().values(
                    id=uuid.uuid4(), turn_id=turn_id, seq=seq, purpose=purpose,
                    cost=Decimal("0.0001"), created_at=NOW - dt.timedelta(hours=2),
                )
            )
        for status, count in (("accepted", 3), ("discarded", 1)):
            for _ in range(count):
                await conn.execute(
                    proposals.insert().values(
                        id=uuid.uuid4(), book_id=book_id, origin="turn", scenario="base",
                        ops=[], deltas={}, overlay_fingerprint="f", status=status,
                        expires_at=NOW, created_at=NOW - dt.timedelta(hours=1),
                    )
                )
        await conn.execute(
            deletions.insert().values(
                user_id=uuid.uuid4(),
                deleted_at=NOW - dt.timedelta(days=40),
                backup_purge_due_at=NOW - dt.timedelta(days=10),
            )
        )


def value(registry: MetricsRegistry, name: str, **labels: str) -> float:
    key = (name, tuple(sorted(labels.items())))
    return registry.snapshot()["gauges"][key]  # type: ignore[index]


async def test_the_window_is_twenty_four_hours_and_the_numbers_are_the_tables(database):
    """Read from `turns` and `llm_calls`, which SPEC §11 makes the record.

    The stale turn carries a $9.99 cost and a repair call three days back. If
    the window were wrong, the spend gauge and the repair ratio would both say
    so loudly, which is why it is there.
    """
    await _seed(database)
    registry = MetricsRegistry()
    async with database.connect() as conn:
        await refresh_db_gauges(registry, conn, now=NOW)

    assert value(registry, "cashkit_turns_24h", kind="answer", outcome="ok") == 2.0
    assert value(registry, "cashkit_turns_24h", kind="refusal", outcome="over_budget") == 1.0
    assert value(registry, "cashkit_llm_calls_total_24h") == 7.0
    assert value(registry, "cashkit_llm_calls_24h", purpose="repair") == 1.0
    assert value(registry, "cashkit_llm_repair_ratio_24h") == pytest.approx(1 / 7)
    assert value(registry, "cashkit_llm_spend_usd_24h") == pytest.approx(0.0004 * 2 + 0.0003 * 2 + 0.0002 + 0.0002)
    assert value(registry, "cashkit_deletion_backup_windows_overdue") == 1.0


async def test_turn_latency_percentiles_are_reported_per_kind(database):
    """SPEC §8's budgets are per kind, so the series is too."""
    await _seed(database)
    registry = MetricsRegistry()
    async with database.connect() as conn:
        await refresh_db_gauges(registry, conn, now=NOW)
    assert value(registry, "cashkit_turn_latency_seconds_p50_24h", kind="answer") == pytest.approx(6.0)
    assert value(registry, "cashkit_turn_latency_seconds_p95_24h", kind="answer") == pytest.approx(7.8)


async def test_the_accept_rate_is_derivable(database):
    """The MLP's core product metric (SPEC §11): three accepted, one discarded."""
    await _seed(database)
    registry = MetricsRegistry()
    async with database.connect() as conn:
        await refresh_db_gauges(registry, conn, now=NOW)
    assert value(registry, "cashkit_proposals_24h", origin="turn", status="accepted") == 3.0
    assert value(registry, "cashkit_proposals_24h", origin="turn", status="discarded") == 1.0


async def test_a_stale_series_stops_being_reported(database):
    """A combination that no longer occurs must not stay on the dashboard.

    Derived gauges are cleared before each refresh. Without that, a
    `{kind, outcome}` that happened yesterday keeps reporting yesterday's
    value for ever, which is the way a dashboard becomes fiction.
    """
    await _seed(database)
    registry = MetricsRegistry()
    async with database.connect() as conn:
        await refresh_db_gauges(registry, conn, now=NOW)
        assert value(registry, "cashkit_turns_24h", kind="answer", outcome="ok") == 2.0
        # Move the clock past the window; nothing is in it any more.
        await refresh_db_gauges(registry, conn, now=NOW + dt.timedelta(days=5))
    with pytest.raises(KeyError):
        value(registry, "cashkit_turns_24h", kind="answer", outcome="ok")


async def test_an_unenumerated_outcome_lands_in_unknown_rather_than_failing(database):
    """Observability must not stop working when something unexpected happens.

    A turn outcome nobody enumerated is exactly the moment an operator needs
    the scrape to succeed. It goes into a visible `unknown` bucket.
    """
    user_id, book_id = uuid.uuid4(), uuid.uuid4()
    async with database.connect() as conn:
        await conn.execute(users.insert().values(id=user_id, email="u@x.com", created_at=NOW))
        await conn.execute(
            books.insert().values(id=book_id, user_id=user_id, storage_path="/x", created_at=NOW)
        )
        await conn.execute(
            turns.insert().values(
                id=uuid.uuid4(), user_id=user_id, book_id=book_id, request_id="q",
                input_text="x", kind="something-new", outcome="who-knows",
                created_at=NOW - dt.timedelta(hours=1),
            )
        )
    registry = MetricsRegistry()
    async with database.connect() as conn:
        await refresh_db_gauges(registry, conn, now=NOW)
    assert value(registry, "cashkit_turns_24h", kind="unknown", outcome="unknown") == 1.0


async def test_the_backup_marker_becomes_a_timestamp(database, tmp_path: Path):
    marker = tmp_path / "backup-last-success.txt"
    marker.write_text("2026-08-23T03:15:00+00:00\n")
    registry = MetricsRegistry()
    async with database.connect() as conn:
        await refresh_db_gauges(registry, conn, now=NOW, backup_marker=marker)
    assert value(registry, "cashkit_backup_last_success_timestamp_seconds") == pytest.approx(
        dt.datetime(2026, 8, 23, 3, 15, tzinfo=dt.timezone.utc).timestamp()
    )


# --- the alarms and the metrics agree ------------------------------------- #


def test_every_cashkit_series_an_alarm_watches_is_one_this_service_produces():
    """The quietest way an alarm never fires: it watches a name nothing emits.

    Only `cashkit_*` names are checked here; `up`, `probe_success` and the
    `node_*` series come from Prometheus itself, the Grafana Cloud probe and
    node-exporter, and the alarm drill drives all three for real.
    """
    document = yaml.safe_load(ALERTS.read_text())
    referenced = set()
    for group in document["groups"]:
        for rule in group["rules"]:
            referenced |= set(re.findall(r"\bcashkit_[a-z0-9_]+", rule["expr"]))
    unknown = referenced - set(SPECS)
    assert unknown == set(), f"alarms watch series nothing emits: {sorted(unknown)}"
    assert referenced, "the alert file references no cashkit series at all"


def test_the_service_metrics_endpoint_is_not_in_the_public_schema():
    """`/metrics` and `/healthz` are operational, not product API.

    A health probe in the generated TypeScript client is a path somebody
    eventually calls from a screen.
    """
    import json

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text()
    )
    assert "/metrics" not in schema["paths"]
    assert "/healthz" not in schema["paths"]


async def test_metrics_is_served_and_healthz_reports_the_database(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "cashkit_build_info" in response.text

    health = await client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
