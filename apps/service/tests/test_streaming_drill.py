"""The streaming drill: does a real Caddy, on the committed Caddyfile, let
server-sent events through unbuffered? (S5's handoff §8.)

This is the clause that is easy to claim and hard to prove. The service is
correct whether or not the proxy buffers, the E2E harness forwards frames
because S5 made it (D-MLP-95), and **no other test in this repository would
fail** if the deployment started holding the import stream until it finished.

So the drill measures arrival times through the real proxy, and — the part
that makes the measurement mean something — it runs the identical measurement
through a **misconfigured nginx** beside it. If the buffered case did not fail
the same assertion, the assertion would not be evidence of anything. The
negative control is the test's own proof of validity — and building it
corrected two pieces of folklore about how a proxy breaks SSE; see
`ops/drills/nginx-buffering.conf`.

`ops/Caddyfile` is mounted unchanged; only `SITE_ADDRESS` differs, so the
drill routes through the same `@import_stream` matcher and the same
`flush_interval -1` that production uses.

    uv run pytest apps/service/tests/test_streaming_drill.py -m drill -q
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.drill

COMPOSE = (
    Path(__file__).resolve().parents[3] / "ops" / "drills" / "docker-compose.streaming.yml"
)
CADDY = "http://127.0.0.1:58080/imports/drill/stream"
NGINX = "http://127.0.0.1:58081/imports/drill/stream"

#: The origin emits 8 frames 300 ms apart, so an unbuffered read spans ~2.1 s.
FRAMES = 8
INTERVAL = 0.3


def compose(*args: str, timeout: int = 300) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        capture_output=True, text=True, env=os.environ.copy(), timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"$ docker compose {' '.join(args)}\nexit {result.returncode}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def arrival_offsets(url: str) -> list[float]:
    """Seconds from **the request** to each frame's arrival at this process.

    From the request, not from the response headers, and that distinction is
    the whole measurement. A compressing proxy holds the response *headers*
    too, so a clock started at the headers would see the frames arrive
    perfectly spaced — after a two-second wait it never recorded. The first run
    of this drill measured exactly that and reported a buffered proxy as
    unbuffered.

    Measured on the wire, not from the payload: what is under test is when the
    bytes reached this process, not what the origin claims it did.
    """
    offsets: list[float] = []
    started = time.monotonic()
    with httpx.stream("GET", url, timeout=60.0) as response:
        response.raise_for_status()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                offsets.append(time.monotonic() - started)
    return offsets


@pytest.fixture(scope="module")
def proxies():
    compose("up", "-d", "--wait")
    # The origin's HTTP server needs a moment past container start.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            httpx.get(CADDY.replace("/imports/drill/stream", "/nothing"), timeout=2.0)
            break
        except httpx.HTTPError:
            time.sleep(0.5)
    try:
        yield
    finally:
        compose("down", "-v", "--remove-orphans")


def test_caddy_delivers_the_frames_as_they_happen(proxies):
    """Through the real Caddyfile: eight frames, spread over the run.

    The assertion is about *spread*, not about total time — a buffered proxy
    delivers everything at the end, so the gap between the first frame and the
    last collapses to nothing while the total stays the same.
    """
    offsets = arrival_offsets(CADDY)
    assert len(offsets) == FRAMES, offsets

    spread = offsets[-1] - offsets[0]
    assert spread > (FRAMES - 1) * INTERVAL * 0.6, (
        f"frames arrived within {spread:.2f}s of each other through Caddy — "
        "the import stream is being buffered, and the import screen would show "
        f"nothing until the run ended. Offsets: {offsets}"
    )
    # The first frame is the one the user waits for: it must not wait for the
    # rest. Half of one interval is generous and still ten times shorter than
    # the buffered case.
    assert offsets[0] < INTERVAL * 3, f"the first frame took {offsets[0]:.2f}s: {offsets}"


def test_a_buffering_proxy_fails_the_same_assertion(proxies):
    """The negative control, and the reason the test above is evidence.

    An nginx that compresses the stream returns every frame correctly and
    returns them all at once, after the run has finished. If this passed the
    spread assertion, that assertion would be measuring nothing.

    Two earlier versions of this control did **not** buffer, and both failures
    are recorded in `ops/drills/nginx-buffering.conf`: nginx honours the
    service's own `X-Accel-Buffering: no`, and `proxy_buffering on` protects
    the upstream from a slow client rather than delaying a slow producer. It is
    compression that holds the stream.
    """
    offsets = arrival_offsets(NGINX)
    assert len(offsets) == FRAMES, (
        f"the control did not deliver the payload at all ({len(offsets)} frames); "
        "it is meant to deliver the same bytes late, not to fail"
    )
    spread = offsets[-1] - offsets[0]
    assert spread < (FRAMES - 1) * INTERVAL * 0.6, (
        "the buffering control did not buffer, so this drill proves nothing "
        f"about the Caddy case. Offsets: {offsets}"
    )
    assert offsets[0] > (FRAMES - 1) * INTERVAL * 0.6, (
        f"the control's first frame arrived at {offsets[0]:.2f}s, which is not "
        "the buffered behaviour this control exists to demonstrate"
    )
