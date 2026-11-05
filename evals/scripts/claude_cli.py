#!/usr/bin/env python3
"""Adapter: drive the ``claude`` CLI as a plain completion model for ``cmd:``.

Usage::

    uv run python -m sdkeval run --model "cmd:python3 scripts/claude_cli.py --model sonnet"

Reads ``{"system": ..., "messages": [...]}`` on stdin and prints the reply.
The CLI is run with **every tool disabled** (``--tools ""``), no MCP servers,
no settings or project memory, from an empty temporary directory — so the
model answers from the system prompt and the task alone, exactly like an API
call. Without that, an agentic CLI could read the repo, run the SDK and
iterate, and the score would measure the harness, not the model.

Multi-turn conversations are rendered into one prompt (the CLI's ``-p`` mode is
single-shot), with each earlier turn labelled.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile


def render(messages: list[dict]) -> str:
    if len(messages) == 1:
        return messages[0]["content"]
    parts = []
    for m in messages:
        who = "USER" if m["role"] == "user" else "YOUR EARLIER REPLY"
        parts.append(f"=== {who} ===\n{m['content']}")
    parts.append("=== Reply to the last USER message. ===")
    return "\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    job = json.loads(sys.stdin.read())
    argv = [
        "claude", "-p", render(job["messages"]),
        "--system-prompt", job["system"],
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "json",
    ]
    if args.model:
        argv += ["--model", args.model]
    with tempfile.TemporaryDirectory() as empty:
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=empty, timeout=args.timeout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-2000:] or proc.stdout[-2000:])
        return proc.returncode
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stdout.write(proc.stdout)
        return 0
    if data.get("is_error"):
        sys.stderr.write(str(data.get("result"))[:2000])
        return 1
    usage = data.get("usage") or {}
    input_tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    sys.stdout.write(json.dumps({
        "reply": data.get("result") or "",
        "input_tokens": input_tokens or None,
        "output_tokens": usage.get("output_tokens"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
