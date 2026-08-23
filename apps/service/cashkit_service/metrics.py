"""Prometheus metrics (SPEC §11), with the content-free rule made mechanical.

SPEC §11's hard rule: **no user identifiers in metric names or labels; metrics
stay content-free.** A rule like that survives exactly as long as the next
person who adds a metric remembers it, so here it is not a rule — it is a
closed vocabulary. Every metric is declared up front with the label names it
may carry and, for every label, the **complete set of values it may take**. A
value outside that set is refused at record time. There is no way to write a
user id into a label without first adding it to a list in this file, which is
a diff a reviewer will see.

That also fixes the other problem with free labels, which is cardinality: a
route label carrying raw paths is one time series per proposal id.

**Where the numbers come from, and why it is mostly not from this process.**

SPEC §11 already names `turns` and `llm_calls` as the system of record. So the
product and model metrics are **read from Postgres at scrape time** rather than
counted in memory: two indexed aggregates over a 24-hour window. Three reasons,
and the third is the one that decided it.

1. An in-process counter loses its window on every deploy, and "model spend
   over the last day" that resets when you ship is not an alarm.
2. The tables are what an operator would query by hand to check the alarm, so
   the alarm and the check agree by construction.
3. It needs **no change to the turn pipeline or the proposal store**. A metric
   call threaded through S1's and S2's code would be a second place the truth
   is written, free to disagree with the first — and the first is the one the
   §11 correlation chain hangs off.

Only HTTP requests are counted in process, because no table records them.

There is no `prometheus_client` dependency. The exposition format is a dozen
lines, and the thing worth owning here is the vocabulary, not the encoder.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Mapping

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from .db import deletions, llm_calls, proposals, turns

#: SPEC §8's budgets are in seconds; these buckets bracket them (300 ms for a
#: read endpoint, 1 s for accept) so a quantile near a budget is read off a
#: bucket edge rather than interpolated across a decade.
DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.3, 0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 30.0, 60.0, 120.0)

#: The window every derived gauge is computed over.
WINDOW = dt.timedelta(hours=24)


class LabelRefused(ValueError):
    """A label value outside the metric's declared vocabulary.

    Raised rather than dropped: a metric silently not recorded is worse than a
    loud failure, and this can only fire on a value the code itself produced —
    nothing here is ever labelled with something that came from a request.
    """


@dataclass(frozen=True)
class Spec:
    """One metric: its name, its help, and every label value it may carry."""

    name: str
    help: str
    kind: str  # counter | gauge | histogram
    labels: Mapping[str, frozenset[str] | None] = field(default_factory=dict)

    def check(self, values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
        if set(values) != set(self.labels):
            raise LabelRefused(
                f"{self.name} takes labels {sorted(self.labels)}, got {sorted(values)}"
            )
        for key, value in values.items():
            allowed = self.labels[key]
            if allowed is not None and value not in allowed:
                raise LabelRefused(
                    f"{self.name}{{{key}={value!r}}} is outside the declared "
                    f"vocabulary {sorted(allowed)} — SPEC §11 keeps metrics "
                    "content-free, so a new value is a deliberate edit here"
                )
        return tuple(sorted(values.items()))


#: The route label is unconstrained because the route set belongs to the
#: router — but `requestlog.route_template()` is its only producer and returns
#: a matched **template** (`/proposals/{proposal_id}`) or `<unmatched>`, never
#: a path with an id in it. `test_metrics.py` asserts that.
_FREE: frozenset[str] | None = None

METHODS = frozenset({"GET", "POST", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS"})
STATUS_CLASSES = frozenset({"1xx", "2xx", "3xx", "4xx", "5xx"})
TURN_KINDS = frozenset(
    {"answer", "proposal", "clarification", "refusal", "import", "unknown"}
)
TURN_OUTCOMES = frozenset(
    {
        "ok", "running", "rate_limited", "over_budget", "model_unavailable",
        "unreadable", "failed", "refused", "error", "unknown",
    }
)
CALL_PURPOSES = frozenset({"interpret", "repair", "verify", "qa", "import"})
PROPOSAL_ORIGINS = frozenset(
    {"turn", "cell_edit", "onboarding", "import", "settings", "button", "unknown"}
)
PROPOSAL_STATES = frozenset(
    {"pending", "accepted", "discarded", "expired", "superseded", "unknown"}
)

SPECS: dict[str, Spec] = {
    s.name: s
    for s in [
        # --- in process: no table records an HTTP request --------------- #
        Spec(
            "cashkit_http_requests_total",
            "HTTP requests by route template, method and status class.",
            "counter",
            {"route": _FREE, "method": METHODS, "status": STATUS_CLASSES},
        ),
        Spec(
            "cashkit_http_request_duration_seconds",
            "Request duration by route template, for the SPEC §8 per-endpoint budgets.",
            "histogram",
            {"route": _FREE, "method": METHODS},
        ),
        # --- from Postgres at scrape time -------------------------------- #
        Spec(
            "cashkit_turns_24h",
            "Turns in the last 24 hours by kind and outcome (SPEC §11 product layer).",
            "gauge",
            {"kind": TURN_KINDS, "outcome": TURN_OUTCOMES},
        ),
        Spec(
            "cashkit_turn_latency_seconds_p50_24h",
            "Median turn latency over 24 hours, by kind. Compare against SPEC §8.",
            "gauge",
            {"kind": TURN_KINDS},
        ),
        Spec(
            "cashkit_turn_latency_seconds_p95_24h",
            "95th-percentile turn latency over 24 hours, by kind. Compare against SPEC §8.",
            "gauge",
            {"kind": TURN_KINDS},
        ),
        Spec(
            "cashkit_llm_calls_24h",
            "Model calls in the last 24 hours by purpose.",
            "gauge",
            {"purpose": CALL_PURPOSES},
        ),
        Spec(
            "cashkit_llm_spend_usd_24h",
            "Model spend across the whole service over the last 24 hours, from llm_calls.",
            "gauge",
            {},
        ),
        Spec(
            "cashkit_llm_repair_ratio_24h",
            "Repair calls as a fraction of all model calls over 24 hours. Above 0.2 the prompt is regressing or the provider changed (SPEC §11).",
            "gauge",
            {},
        ),
        Spec(
            "cashkit_llm_calls_total_24h",
            "All model calls in the window. The repair ratio means nothing below a handful.",
            "gauge",
            {},
        ),
        Spec(
            "cashkit_proposals_24h",
            "Proposals raised in the last 24 hours by origin and status. Accept rate is the MLP's core product metric.",
            "gauge",
            {"origin": PROPOSAL_ORIGINS, "status": PROPOSAL_STATES},
        ),
        Spec(
            "cashkit_deletion_backup_windows_overdue",
            "Account deletions past their 30-day backup window and still open. Above zero is a SPEC §9 breach in progress.",
            "gauge",
            {},
        ),
        Spec(
            "cashkit_backup_last_success_timestamp_seconds",
            "When the backup sidecar last finished a run, from the marker on the shared volume.",
            "gauge",
            {},
        ),
        Spec(
            "cashkit_build_info",
            "Always 1. Its labels carry the build.",
            "gauge",
            {"version": _FREE, "engine_version": _FREE, "environment": _FREE},
        ),
    ]
}


class MetricsRegistry:
    """In-process counters and histograms, plus the scrape-time gauges."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._hist_buckets: dict[tuple[str, tuple[tuple[str, str], ...]], list[int]] = {}
        self._hist_sum: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._hist_count: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        key = (name, SPECS[name].check(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        key = (name, SPECS[name].check(labels))
        with self._lock:
            buckets = self._hist_buckets.setdefault(key, [0] * len(DURATION_BUCKETS))
            for i, edge in enumerate(DURATION_BUCKETS):
                if seconds <= edge:
                    buckets[i] += 1
            self._hist_sum[key] = self._hist_sum.get(key, 0.0) + seconds
            self._hist_count[key] = self._hist_count.get(key, 0) + 1

    def gauge(self, name: str, value: float, **labels: str) -> None:
        key = (name, SPECS[name].check(labels))
        with self._lock:
            self._gauges[key] = value

    def clear_gauges(self, prefix: str) -> None:
        """Drop derived gauges before recomputing them.

        A `{kind, outcome}` combination that stops occurring must stop being
        reported, or a series that was true yesterday is still on the dashboard
        today at yesterday's value.
        """
        with self._lock:
            for key in [k for k in self._gauges if k[0].startswith(prefix)]:
                del self._gauges[key]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "hist_buckets": {k: list(v) for k, v in self._hist_buckets.items()},
                "hist_sum": dict(self._hist_sum),
                "hist_count": dict(self._hist_count),
                "gauges": dict(self._gauges),
            }


# --- the gauges that come from Postgres ----------------------------------- #


def _known(value: str | None, vocabulary: frozenset[str]) -> str:
    """Map a stored value onto the declared vocabulary, or `unknown`.

    The alternative is for a scrape to fail because a turn outcome nobody
    enumerated reached the database. Observability that stops working when
    something unexpected happens is observability that stops working exactly
    when it is needed; the `unknown` bucket is visible and the scrape survives.
    """
    return value if value in vocabulary else "unknown"


async def refresh_db_gauges(
    registry: MetricsRegistry,
    conn: AsyncConnection,
    *,
    now: dt.datetime,
    backup_marker: Path | None = None,
) -> None:
    """Recompute every derived gauge from the tables, over the 24-hour window."""
    since = now - WINDOW
    for prefix in ("cashkit_turns_", "cashkit_turn_latency_", "cashkit_llm_", "cashkit_proposals_"):
        registry.clear_gauges(prefix)

    # --- turns: counts, and latency percentiles against SPEC §8 ---------- #
    rows = (
        await conn.execute(
            sa.select(turns.c.kind, turns.c.outcome, sa.func.count())
            .where(turns.c.created_at >= since)
            .group_by(turns.c.kind, turns.c.outcome)
        )
    ).all()
    for kind, outcome, count in rows:
        registry.gauge(
            "cashkit_turns_24h",
            float(count),
            kind=_known(kind, TURN_KINDS),
            outcome=_known(outcome, TURN_OUTCOMES),
        )

    latency = (
        await conn.execute(
            sa.select(
                turns.c.kind,
                sa.func.percentile_cont(0.5).within_group(turns.c.latency_ms.asc()),
                sa.func.percentile_cont(0.95).within_group(turns.c.latency_ms.asc()),
            )
            .where(turns.c.created_at >= since, turns.c.latency_ms.isnot(None))
            .group_by(turns.c.kind)
        )
    ).all()
    for kind, p50, p95 in latency:
        label = _known(kind, TURN_KINDS)
        registry.gauge("cashkit_turn_latency_seconds_p50_24h", float(p50 or 0) / 1000.0, kind=label)
        registry.gauge("cashkit_turn_latency_seconds_p95_24h", float(p95 or 0) / 1000.0, kind=label)

    # --- model calls: per purpose, spend, and the repair ratio ----------- #
    calls = (
        await conn.execute(
            sa.select(
                llm_calls.c.purpose,
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(llm_calls.c.cost), Decimal("0")),
            )
            .where(llm_calls.c.created_at >= since)
            .group_by(llm_calls.c.purpose)
        )
    ).all()
    total = 0
    repairs = 0
    spend = Decimal("0")
    for purpose, count, cost in calls:
        total += int(count)
        spend += Decimal(cost or 0)
        if purpose == "repair":
            repairs += int(count)
        if purpose in CALL_PURPOSES:
            registry.gauge("cashkit_llm_calls_24h", float(count), purpose=purpose)
    registry.gauge("cashkit_llm_spend_usd_24h", float(spend))
    registry.gauge("cashkit_llm_calls_total_24h", float(total))
    registry.gauge("cashkit_llm_repair_ratio_24h", (repairs / total) if total else 0.0)

    # --- proposals: the accept rate ------------------------------------- #
    cards = (
        await conn.execute(
            sa.select(proposals.c.origin, proposals.c.status, sa.func.count())
            .where(proposals.c.created_at >= since)
            .group_by(proposals.c.origin, proposals.c.status)
        )
    ).all()
    for origin, status, count in cards:
        registry.gauge(
            "cashkit_proposals_24h",
            float(count),
            origin=_known(origin, PROPOSAL_ORIGINS),
            status=_known(status, PROPOSAL_STATES),
        )

    # --- the SPEC §9 window, and the backup's own heartbeat -------------- #
    overdue = (
        await conn.execute(
            sa.select(sa.func.count())
            .select_from(deletions)
            .where(
                deletions.c.backups_purged_at.is_(None),
                deletions.c.backup_purge_due_at <= now,
            )
        )
    ).scalar_one()
    registry.gauge("cashkit_deletion_backup_windows_overdue", float(overdue))

    if backup_marker is not None and backup_marker.is_file():
        try:
            stamp = dt.datetime.fromisoformat(backup_marker.read_text().strip())
        except ValueError:
            stamp = None
        if stamp is not None:
            registry.gauge("cashkit_backup_last_success_timestamp_seconds", stamp.timestamp())


# --- exposition ----------------------------------------------------------- #


def _labels(pairs: Iterable[tuple[str, str]]) -> str:
    items = list(pairs)
    if not items:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in items) + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return repr(float(value))


def render(registry: MetricsRegistry) -> str:
    """The Prometheus text exposition format, 0.0.4."""
    state = registry.snapshot()
    lines: list[str] = []
    emitted: set[str] = set()

    def header(name: str) -> None:
        if name in emitted:
            return
        emitted.add(name)
        spec = SPECS[name]
        lines.append(f"# HELP {name} {spec.help}")
        lines.append(f"# TYPE {name} {spec.kind}")

    for (name, labels), value in sorted(state["counters"].items()):  # type: ignore[union-attr]
        header(name)
        lines.append(f"{name}{_labels(labels)} {_number(value)}")
    for (name, labels), value in sorted(state["gauges"].items()):  # type: ignore[union-attr]
        header(name)
        lines.append(f"{name}{_labels(labels)} {_number(value)}")
    for (name, labels), buckets in sorted(state["hist_buckets"].items()):  # type: ignore[union-attr]
        header(name)
        for edge, count in zip(DURATION_BUCKETS, buckets, strict=True):
            lines.append(
                f"{name}_bucket{_labels(list(labels) + [('le', repr(float(edge)))])} {count}"
            )
        total = state["hist_count"][(name, labels)]  # type: ignore[index]
        lines.append(f"{name}_bucket{_labels(list(labels) + [('le', '+Inf')])} {total}")
        lines.append(f"{name}_sum{_labels(labels)} {_number(state['hist_sum'][(name, labels)])}")  # type: ignore[index]
        lines.append(f"{name}_count{_labels(labels)} {total}")
    return "\n".join(lines) + "\n"


def status_class(code: int) -> str:
    return f"{code // 100}xx"


__all__ = [
    "DURATION_BUCKETS",
    "LabelRefused",
    "MetricsRegistry",
    "SPECS",
    "WINDOW",
    "refresh_db_gauges",
    "render",
    "status_class",
]
