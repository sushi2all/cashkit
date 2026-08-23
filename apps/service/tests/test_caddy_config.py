"""The deployment's own configuration, checked per commit.

S5's handoff names one thing a deployment can break in silence: **`GET
/imports/{id}/stream` must reach the browser unbuffered.** A proxy that buffers
it turns the import screen from "watch it happen" into "wait ninety seconds,
then see all of it at once", and *no test in the repository fails* — the
service is correct either way and the E2E harness forwards frames because S5
made it (D-MLP-95).

This file is the cheap half of the answer: it reads the committed
`ops/Caddyfile` and `ops/docker-compose.prod.yml` on every commit, with no
containers. `test_streaming_drill.py -m drill` is the expensive half — a real
Caddy, timed, with a buffering nginx beside it as the negative control.

A configuration file that nothing reads is a configuration file that drifts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[3] / "ops"
CADDYFILE = (OPS / "Caddyfile").read_text()
PROD = (OPS / "docker-compose.prod.yml").read_text()


def stream_block() -> str:
    """The Caddyfile handler for the import stream, as text."""
    match = re.search(
        r"handle @import_stream \{(.*?)\n\t\}", CADDYFILE, re.DOTALL
    )
    assert match, "the Caddyfile has no `handle @import_stream` block at all"
    return match.group(1)


def test_the_import_stream_has_its_own_matcher():
    """It cannot inherit the catch-all's behaviour by accident."""
    assert "path_regexp import_stream ^/imports/[^/]+/stream$" in CADDYFILE
    assert CADDYFILE.index("@import_stream") < CADDYFILE.index("handle {"), (
        "the stream matcher must come before the catch-all handler, or the "
        "catch-all wins and the flush setting never applies"
    )


def test_the_stream_is_flushed_immediately():
    """`flush_interval -1`: every write goes straight through.

    Caddy already does this for `text/event-stream`, so the line is belt and
    braces — and it is the braces that matter, because the belt is a default
    a Caddy upgrade is entitled to revisit.
    """
    assert "flush_interval -1" in stream_block(), (
        "the import stream is proxied without `flush_interval -1`; a buffering "
        "proxy would break the import screen and fail no test in this repository"
    )


def test_the_stream_route_tolerates_a_long_model_call():
    """An import's model call can take a minute; the read timeout must not.

    The stream sends a heartbeat comment every fifteen seconds (D-MLP-83), so
    a read timeout shorter than a model call would cut a healthy import off
    mid-run and look exactly like a crash.
    """
    block = stream_block()
    match = re.search(r"read_timeout (\d+)s", block)
    assert match, f"no read_timeout on the stream route:\n{block}"
    assert int(match.group(1)) >= 120, "a read timeout under two minutes will cut a long import"


def test_the_service_sends_the_no_buffering_hint_as_well():
    """`X-Accel-Buffering: no` on the response itself.

    Caddy ignores it; nginx and several CDNs do not. The header costs nothing
    and it is the only signal that survives a proxy this repository does not
    configure — which is the proxy most likely to be in the way one day.
    """
    router = (
        Path(__file__).resolve().parents[1] / "cashkit_service" / "routers" / "imports.py"
    ).read_text()
    assert '"X-Accel-Buffering": "no"' in router


def test_metrics_is_not_published():
    """SPEC §11 metrics stay off the public internet.

    They carry no user identifier (that is a separate, tested rule), but
    per-endpoint latency and daily model spend are still nobody else's.
    """
    assert re.search(r"handle /metrics \{\s*respond 404", CADDYFILE), CADDYFILE


@pytest.mark.parametrize(
    "header",
    [
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "X-Frame-Options",
    ],
)
def test_the_security_headers_are_set(header: str):
    assert header in CADDYFILE


def test_only_caddy_publishes_a_port():
    """The service, Postgres and the agent are reachable on the compose
    network and from nowhere else.

    A `ports:` on the service would put an unauthenticated `/metrics` and an
    un-TLS'd API on the VM's public interface, past everything the Caddyfile
    above decides.
    """
    published = re.findall(r"^  (\w[\w-]*):|^    ports:", PROD, re.MULTILINE)
    # Walk the file: remember the last service name seen, flag any that has ports.
    service = None
    with_ports: list[str] = []
    for line in PROD.splitlines():
        name = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if name:
            service = name.group(1)
        elif re.match(r"^    ports:\s*$", line) and service:
            with_ports.append(service)
    assert with_ports == ["caddy"], f"services publishing ports: {with_ports}"
    assert published  # the walk found something to walk


def test_the_pool_and_max_connections_are_stated_together():
    """D-MLP-104: the pool is sized against a number, and that number is here."""
    assert "max_connections=100" in PROD
    assert 'CASHKIT_DB_POOL_SIZE: "20"' in PROD
    assert 'CASHKIT_DB_MAX_OVERFLOW: "20"' in PROD


def test_no_secret_has_a_value_in_the_compose_file():
    """SPEC §12: secrets via environment, never in the repo.

    Every secret is an unset-is-an-error interpolation (`${X:?}`), so a deploy
    without the env file fails at `docker compose up` rather than starting a
    service with an empty key and discovering it on the first turn.
    """
    for name in (
        "POSTGRES_PASSWORD",
        "OPENROUTER_API_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "BACKUP_AGE_RECIPIENT",
    ):
        assert f"${{{name}:?}}" in PROD, f"{name} is not a required interpolation"
        # Every mention of the name on a value line must be an interpolation.
        for line in PROD.splitlines():
            if re.match(rf"^\s*[A-Z_]*{name}[A-Z_]*:\s*\S", line):
                assert "${" in line, f"{name} carries a literal value: {line.strip()}"
