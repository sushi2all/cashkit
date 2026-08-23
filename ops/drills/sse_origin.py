"""A minimal SSE origin, for the streaming drill only.

It exists so the drill can measure **a proxy's** behaviour without spending a
model call and without waiting a minute for a real import. What is under test
is `ops/Caddyfile`, not this file: the origin's whole job is to emit frames at
a known cadence so an arrival pattern means something.

It answers `GET /imports/<anything>/stream` — the real path, so the real
`path_regexp` matcher in the Caddyfile is the thing that routes it — with the
same media type and the same `X-Accel-Buffering` header the service sends
(`routers/imports.py`).

Not imported by the service. Not on any deployed path.
"""

from __future__ import annotations

import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FRAMES = int(os.environ.get("SSE_FRAMES", "8"))
INTERVAL = float(os.environ.get("SSE_INTERVAL", "0.3"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        if "/stream" not in self.path:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        # Chunked, because the length is not known in advance — which is
        # exactly the case a buffering proxy mishandles.
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for n in range(FRAMES):
            payload = f"event: progress\ndata: {{\"n\": {n}, \"t\": {time.time():.3f}}}\n\n"
            body = payload.encode()
            self.wfile.write(f"{len(body):X}\r\n".encode() + body + b"\r\n")
            self.wfile.flush()
            time.sleep(INTERVAL)
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, *args: object) -> None:  # keep the drill output clean
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
