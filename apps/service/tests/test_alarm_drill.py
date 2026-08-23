"""The §11 alarms, fired for real (the S6 gate clause).

The clause says the alarms **fire in a drill**, not that a rule file exists. A
rule that has never fired has an unknown success rate, and the two ways a rule
silently does nothing — an expression that never matches, and a route that
delivers nowhere — are both invisible from reading it.

So: a real Prometheus loading `ops/observability/alerts.yml`, a real
Alertmanager loading `ops/observability/alertmanager.yml`, a webhook receiver
recording what arrived, and synthetic series driving each alarm in turn. The
whole path is exercised — evaluation, grouping, routing, delivery — and the
assertion is on **what the receiver got**, not on what Prometheus thought.

Two differences from production, both stated and neither touching a rule:

* the `for:` durations are shortened, because waiting fifteen minutes for a
  debounce is not a test. The drill generates that copy and **asserts every
  `expr` string is byte-identical** to the committed file, so what fires here
  is what fires there;
* the series come from a fixture rather than from the service and
  node-exporter, so a disk alarm can be driven without filling a disk. The
  uptime alarm is the exception: it is driven by a scrape target that genuinely
  does not exist, so `up == 0` is Prometheus's own, not a fabricated series.

    uv run pytest apps/service/tests/test_alarm_drill.py -m drill -q
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import yaml

pytestmark = pytest.mark.drill

OPS = Path(__file__).resolve().parents[3] / "ops"
COMPOSE = OPS / "drills" / "docker-compose.alarms.yml"
ALERTS = OPS / "observability" / "alerts.yml"
ALERTMANAGER = OPS / "observability" / "alertmanager.yml"
PROM = "http://127.0.0.1:59090"

#: Every alarm SPEC §11 and the gate name, and the fixture that drives it.
#: `CashKitServiceDown` has no fixture: the drill's Prometheus scrapes a host
#: that does not exist, so Prometheus generates its own `up == 0`.
EXPECTED = {
    "CashKitDailyModelSpendOverCeiling",  # spend ceiling
    "CashKitRepairRateHigh",              # repair rate over 20% / 24h
    "CashKitDiskLow",                     # disk
    "CashKitBackupStale",                 # backup failure
    "CashKitServiceDown",                 # uptime
    "CashKitDeletionBackupWindowOverdue",  # the SPEC §9 window
}


def write_fixture(root: Path, template: str, *, backup_age_hours: float) -> None:
    """Render a fixture with a backup timestamp relative to now."""
    (root / "fixtures" / "metrics.txt").write_text(
        template.format(backup_ts=repr(time.time() - backup_age_hours * 3600))
    )


def compose(*args: str, env: dict[str, str], timeout: int = 300) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        capture_output=True, text=True, env={**os.environ, **env}, timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"$ docker compose {' '.join(args)}\nexit {result.returncode}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def shorten_for_durations(source: str) -> str:
    """The one edit the drill makes to the rule file: `for:` → `for: 0s`."""
    return re.sub(r"^(\s*)for:\s*\S+\s*$", r"\g<1>for: 0s", source, flags=re.MULTILINE)


def expressions(document: str) -> list[str]:
    parsed = yaml.safe_load(document)
    return [
        rule["expr"]
        for group in parsed["groups"]
        for rule in group["rules"]
        if "expr" in rule
    ]


#: Series that trip every rule at once. The names, labels and value shapes are
#: the ones `cashkit_service.metrics` produces and node-exporter produces;
#: `test_metrics.py` checks the service half agrees with this fixture.
#:
#: `probe_success` is **1** here — the site is reachable. That is deliberate:
#: `alertmanager.yml` inhibits the model, backup and compliance alarms while
#: `CashKitSiteUnreachable` is firing, on the grounds that everything scraped
#: from inside an unreachable box is silent or misleading. The first run of this
#: drill had the probe failing and found exactly four alarms firing in
#: Prometheus and never reaching the receiver. The inhibit rule gets its own
#: phase below rather than quietly swallowing the gate's evidence.
FIRING = """\
# HELP cashkit_llm_spend_usd_24h Model spend over 24h.
# TYPE cashkit_llm_spend_usd_24h gauge
cashkit_llm_spend_usd_24h 7.4
# TYPE cashkit_llm_repair_ratio_24h gauge
cashkit_llm_repair_ratio_24h 0.34
# TYPE cashkit_llm_calls_total_24h gauge
cashkit_llm_calls_total_24h 140
# TYPE cashkit_deletion_backup_windows_overdue gauge
cashkit_deletion_backup_windows_overdue 2
# TYPE cashkit_backup_last_success_timestamp_seconds gauge
cashkit_backup_last_success_timestamp_seconds {backup_ts}
# TYPE node_filesystem_avail_bytes gauge
node_filesystem_avail_bytes{{fstype="ext4",mountpoint="/",instance="drill"}} 2000000000
# TYPE node_filesystem_size_bytes gauge
node_filesystem_size_bytes{{fstype="ext4",mountpoint="/",instance="drill"}} 40000000000
# TYPE probe_success gauge
probe_success{{instance="cashkit.example"}} 1
"""

#: The same series, healthy. A rule that fires on both is a rule that fires on
#: everything, which is the other way a rule file can be useless.
#:
#: The backup timestamp is **present and fresh**. Leaving it out was the first
#: version and it kept `CashKitBackupStale` firing — correctly, because the rule
#: carries `or absent(...)` on purpose: a metric that stopped being reported and
#: a backup that stopped running look the same from a rule's point of view, and
#: both need somebody to look. A fixture that omits it is testing the `absent`
#: branch, not the healthy one.
QUIET = """\
# TYPE cashkit_llm_spend_usd_24h gauge
cashkit_llm_spend_usd_24h 0.42
# TYPE cashkit_llm_repair_ratio_24h gauge
cashkit_llm_repair_ratio_24h 0.03
# TYPE cashkit_llm_calls_total_24h gauge
cashkit_llm_calls_total_24h 140
# TYPE cashkit_deletion_backup_windows_overdue gauge
cashkit_deletion_backup_windows_overdue 0
# TYPE cashkit_backup_last_success_timestamp_seconds gauge
cashkit_backup_last_success_timestamp_seconds {backup_ts}
# TYPE node_filesystem_avail_bytes gauge
node_filesystem_avail_bytes{{fstype="ext4",mountpoint="/",instance="drill"}} 30000000000
# TYPE node_filesystem_size_bytes gauge
node_filesystem_size_bytes{{fstype="ext4",mountpoint="/",instance="drill"}} 40000000000
# TYPE probe_success gauge
probe_success{{instance="cashkit.example"}} 1
"""


@pytest.fixture(scope="module")
def drill(tmp_path_factory):
    root = tmp_path_factory.mktemp("alarms").resolve()
    for name in ("fixtures", "rules", "out", "am"):
        (root / name).mkdir()

    source = ALERTS.read_text()
    shortened = shorten_for_durations(source)
    assert expressions(shortened) == expressions(source), (
        "shortening the debounce changed a rule expression; the drill would be "
        "firing something other than what production runs"
    )
    assert "for: 0s" in shortened
    (root / "rules" / "alerts.yml").write_text(shortened)

    # Alertmanager does not expand environment variables in its config file,
    # so the drill substitutes the one placeholder and asserts nothing else
    # changed.
    am = ALERTMANAGER.read_text()
    (root / "am" / "alertmanager.yml").write_text(
        am.replace("${ALERT_WEBHOOK_URL}", "http://sink:9099/")
    )

    # A backup whose last success was three days ago: stale, and present, so
    # the rule fires on its age rather than on its absence.
    write_fixture(root, FIRING, backup_age_hours=72)
    env = {"CASHKIT_ALARM_DRILL_ROOT": str(root)}
    compose("up", "-d", "--wait", env=env)
    try:
        yield root, env
    finally:
        compose("down", "-v", "--remove-orphans", env=env)


def wait_for(predicate, *, seconds: float = 180.0, every: float = 2.0):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(every)
    return last


def firing_now() -> set[str]:
    try:
        body = httpx.get(f"{PROM}/api/v1/alerts", timeout=10.0).json()
    except httpx.HTTPError:
        return set()
    return {
        a["labels"]["alertname"]
        for a in body.get("data", {}).get("alerts", [])
        if a.get("state") == "firing"
    }


def delivered(root: Path) -> set[str]:
    path = root / "out" / "alerts.jsonl"
    if not path.exists():
        return set()
    names: set[str] = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        for alert in payload.get("alerts", []):
            if alert.get("status") == "firing":
                names.add(alert["labels"]["alertname"])
    return names


def test_the_rule_file_prometheus_loaded_is_the_committed_one(drill):
    """No rule failed to parse, and none was quietly dropped."""
    root, _env = drill
    body = wait_for(
        lambda: httpx.get(f"{PROM}/api/v1/rules", timeout=10.0).json()
        if httpx.get(f"{PROM}/-/ready", timeout=10.0).status_code == 200
        else None
    )
    loaded = {
        rule["name"]
        for group in body["data"]["groups"]
        for rule in group["rules"]
    }
    assert EXPECTED <= loaded, f"rules Prometheus did not load: {sorted(EXPECTED - loaded)}"
    # `CashKitSiteUnreachable` is loaded too and is driven by the fixture's
    # probe_success; it is not in EXPECTED because Alertmanager's inhibit rule
    # is entitled to swallow the ones it makes redundant.
    assert "CashKitSiteUnreachable" in loaded


def test_every_alarm_fires(drill):
    """All six, in Prometheus, from the committed expressions."""
    root, _env = drill
    got = wait_for(lambda: EXPECTED if EXPECTED <= firing_now() else None)
    assert got is not None, (
        f"alarms that never fired: {sorted(EXPECTED - firing_now())}; "
        f"firing: {sorted(firing_now())}"
    )


def test_every_alarm_is_delivered_to_a_receiver(drill):
    """…and arrives at the far end.

    This is the half a rule file cannot tell you about. An expression that
    matches and a notification that reaches somebody are different claims, and
    the second is the one an operator is relying on at three in the morning.
    """
    root, _env = drill
    got = wait_for(
        lambda: delivered(root) if EXPECTED <= delivered(root) else None,
        seconds=240.0,
    )
    assert got is not None, (
        f"fired but never delivered: {sorted(EXPECTED - delivered(root))}; "
        f"delivered: {sorted(delivered(root))}"
    )


def test_an_unreachable_site_inhibits_the_alarms_it_makes_meaningless(drill):
    """The inhibit rule, driven rather than read.

    `alertmanager.yml` says that while the site is unreachable from outside,
    the model, backup and compliance alarms are noise: everything scraped from
    inside the box is silent or misleading, and the useful sentence is "the
    site is down". This turns the probe off and asserts Alertmanager marks
    those alarms inhibited — by whom, not merely that they went quiet.
    """
    root, _env = drill
    write_fixture(root, FIRING.replace('probe_success{{instance="cashkit.example"}} 1',
                                       'probe_success{{instance="cashkit.example"}} 0'),
                  backup_age_hours=72)

    def inhibited() -> dict[str, list[str]]:
        try:
            body = httpx.get("http://127.0.0.1:59093/api/v2/alerts", timeout=10.0).json()
        except httpx.HTTPError:
            return {}
        return {
            a["labels"]["alertname"]: a["status"].get("inhibitedBy", [])
            for a in body
            if a["status"].get("state") == "suppressed"
        }

    suppressed = wait_for(
        lambda: inhibited() if {"CashKitRepairRateHigh", "CashKitBackupStale"} <= set(inhibited()) else None,
        seconds=180.0,
    )
    assert suppressed, f"nothing was inhibited; alertmanager saw {inhibited()}"
    assert "CashKitDiskLow" not in suppressed, (
        "the disk alarm is about the box and stays useful when the site is "
        "unreachable; inhibiting it would hide the likely cause"
    )


def test_the_alarms_go_quiet_on_healthy_series(drill):
    """The other way a rule file is useless: firing on everything.

    The same metric names with healthy values must silence every alarm the
    fixture drives. `CashKitServiceDown` stays firing throughout — its target
    genuinely does not exist — which is itself the check that the fixture is
    what silences the others.
    """
    root, _env = drill
    write_fixture(root, QUIET, backup_age_hours=1)
    fixture_driven = EXPECTED - {"CashKitServiceDown"}
    got = wait_for(
        lambda: True if not (fixture_driven & firing_now()) else None, seconds=180.0
    )
    assert got, f"still firing on healthy series: {sorted(fixture_driven & firing_now())}"
    assert "CashKitServiceDown" in firing_now(), (
        "the unreachable target stopped firing, so this test proved nothing "
        "about why the others went quiet"
    )
