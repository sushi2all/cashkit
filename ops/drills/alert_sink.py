"""Where the alarm drill's notifications land. Not a deployment artifact.

Alertmanager posts here; every payload is appended to /out/alerts.jsonl. The
drill reads that file and asserts which alarms arrived. Using a real webhook
receiver rather than Alertmanager's API is deliberate: it proves the whole
path — rule evaluation, grouping, routing, delivery — and not just that
Prometheus thought something was firing.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUT = "/out/alerts.jsonl"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {"unparseable": body.decode("utf-8", "replace")}
        with open(OUT, "a") as fh:
            fh.write(json.dumps(payload) + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:
        return


if __name__ == "__main__":
    open(OUT, "a").close()
    ThreadingHTTPServer(("0.0.0.0", 9099), Handler).serve_forever()
