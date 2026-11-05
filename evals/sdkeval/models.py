"""Model adapters — stdlib HTTP only, so the eval adds no dependency.

A model is named by a spec string:

=============================  ==============================================
``reference``                  the task's reference solution (self-test: 100%)
``noop``                       an empty reply (self-test: 0%)
``anthropic:<model>``          Anthropic Messages API — ``ANTHROPIC_API_KEY``
``openai:<model>``             OpenAI Chat Completions — ``OPENAI_API_KEY``
``openrouter:<model>``         OpenRouter — ``OPENROUTER_API_KEY`` or
                               ``CASHKIT_LLM_API_KEY`` (same as the trials)
``openai-compat:<model>@<url>``  any OpenAI-compatible server (vLLM, llama.cpp,
                               Ollama, LM Studio): ``<url>`` is the base
                               ending in ``/v1``; key from
                               ``OPENAI_COMPAT_API_KEY`` if set
``cmd:<shell command>``        pipe the conversation to a command on stdin,
                               read the reply from stdout (e.g. a local
                               runner, or ``claude -p``); stdout may also be
                               ``{"reply", "input_tokens", "output_tokens"}``
``replay:<run dir>``           replay the replies recorded in a previous run
                               (re-grade without calling a model)
=============================  ==============================================

This module lives under ``evals/``, never under ``cashkit/`` — the engine and
SDK stay model-free (ADR-0016), and ``tests/test_no_llm_dependency.py`` only
scans ``cashkit/``.
"""

from __future__ import annotations

import json
import os
import shlex
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .schema import TaskInstance

Message = dict[str, str]


@dataclass
class Completion:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float | None = None


class ModelError(RuntimeError):
    """The model could not be reached or refused the request (after retries)."""


class Model(Protocol):
    name: str

    def complete(self, system: str, messages: list[Message], *, task: TaskInstance, sample: int, turn: int) -> Completion: ...


# --------------------------------------------------------------------------- #
# Offline models
# --------------------------------------------------------------------------- #


class ReferenceModel:
    """Answers with the task's own reference solution."""

    name = "reference"

    def complete(self, system, messages, *, task, sample, turn):
        return Completion(f"```python\n{task.reference}```\n")


class NoopModel:
    name = "noop"

    def complete(self, system, messages, *, task, sample, turn):
        return Completion("I cannot help with that.")


class ReplayModel:
    """Replays ``attempts[turn].reply`` from a previous run's ``results.jsonl``."""

    def __init__(self, run_dir: str):
        self.name = f"replay:{run_dir}"
        self.replies: dict[tuple[str, int, int], str] = {}
        path = Path(run_dir) / "results.jsonl"
        for line in path.read_text().splitlines():
            record = json.loads(line)
            for attempt in record["attempts"]:
                self.replies[(record["instance_id"], record["sample"], attempt["turn"])] = attempt["reply"]

    def complete(self, system, messages, *, task, sample, turn):
        key = (task.instance_id, sample, turn)
        if key not in self.replies:
            raise ModelError(f"no recorded reply for {key}")
        return Completion(self.replies[key])


class CommandModel:
    """Sends a JSON object ``{"system": ..., "messages": [...]}`` on stdin; stdout is the reply."""

    def __init__(self, command: str, timeout_s: float = 600):
        self.name = f"cmd:{command}"
        self.argv = shlex.split(command)
        self.timeout_s = timeout_s

    def complete(self, system, messages, *, task, sample, turn):
        started = time.monotonic()
        payload = json.dumps({"system": system, "messages": messages})
        proc = subprocess.run(self.argv, input=payload, capture_output=True, text=True, timeout=self.timeout_s)
        if proc.returncode != 0:
            raise ModelError(f"command exited {proc.returncode}: {proc.stderr[-500:]}")
        latency = time.monotonic() - started
        # Optional envelope: {"reply": ..., "input_tokens": ..., "output_tokens": ...}
        out = proc.stdout.strip()
        if out.startswith("{") and out.endswith("}"):
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and "reply" in data:
                return Completion(data["reply"] or "", data.get("input_tokens"), data.get("output_tokens"), latency)
        return Completion(proc.stdout, latency_s=latency)


# --------------------------------------------------------------------------- #
# HTTP models
# --------------------------------------------------------------------------- #

_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


def _post(url: str, headers: dict[str, str], body: dict[str, Any], *, timeout_s: float, retries: int = 5) -> dict[str, Any]:
    data = json.dumps(body).encode()
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=timeout_s, context=_ssl_context()) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            last = ModelError(f"HTTP {exc.code} from {url}: {detail}")
            if exc.code not in _RETRY_STATUS:
                raise last from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = ModelError(f"{type(exc).__name__} talking to {url}: {exc}")
        if attempt < retries:
            time.sleep(delay)
            delay = min(delay * 2, 60)
    raise last  # type: ignore[misc]


def _key(*names: str, required: bool = True) -> str | None:
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    if required:
        raise ModelError(f"set one of {', '.join(names)} to use this model")
    return None


@dataclass
class Sampling:
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_s: float = 300.0


class AnthropicModel:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, sampling: Sampling):
        self.name = f"anthropic:{model}"
        self.model = model
        self.sampling = sampling

    def complete(self, system, messages, *, task, sample, turn):
        key = _key("ANTHROPIC_API_KEY")
        started = time.monotonic()
        body = {
            "model": self.model,
            "system": system,
            "messages": messages,
            "max_tokens": self.sampling.max_tokens,
            "temperature": self.sampling.temperature,
        }
        data = _post(self.URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}, body,
                     timeout_s=self.sampling.timeout_s)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage", {})
        return Completion(text, usage.get("input_tokens"), usage.get("output_tokens"), time.monotonic() - started)


class OpenAICompatibleModel:
    def __init__(self, name: str, model: str, base_url: str, key: str | None, sampling: Sampling, extra_headers=None):
        self.name = name
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = key
        self.sampling = sampling
        self.extra_headers = extra_headers or {}

    def complete(self, system, messages, *, task, sample, turn):
        started = time.monotonic()
        headers = dict(self.extra_headers)
        if self.key:
            headers["authorization"] = f"Bearer {self.key}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": self.sampling.max_tokens,
            "temperature": self.sampling.temperature,
        }
        data = _post(self.url, headers, body, timeout_s=self.sampling.timeout_s)
        choices = data.get("choices") or []
        if not choices:
            raise ModelError(f"no choices in response: {str(data)[:300]}")
        text = choices[0].get("message", {}).get("content") or ""
        usage = data.get("usage") or {}
        return Completion(text, usage.get("prompt_tokens"), usage.get("completion_tokens"), time.monotonic() - started)


def resolve(spec: str, sampling: Sampling | None = None) -> Model:
    """Build a model from its spec string (see the module docstring)."""
    sampling = sampling or Sampling()
    if spec == "reference":
        return ReferenceModel()
    if spec == "noop":
        return NoopModel()
    kind, _, rest = spec.partition(":")
    if not rest:
        raise ValueError(f"model spec {spec!r} needs a provider prefix, e.g. anthropic:<model>")
    if kind == "anthropic":
        return AnthropicModel(rest, sampling)
    if kind == "openai":
        return OpenAICompatibleModel(spec, rest, "https://api.openai.com/v1", _key("OPENAI_API_KEY", required=False), sampling)
    if kind == "openrouter":
        return OpenAICompatibleModel(spec, rest, "https://openrouter.ai/api/v1",
                                     _key("OPENROUTER_API_KEY", "CASHKIT_LLM_API_KEY", required=False), sampling)
    if kind == "openai-compat":
        model, _, url = rest.rpartition("@")
        if not model or not url:
            raise ValueError("openai-compat spec is openai-compat:<model>@<base url>")
        return OpenAICompatibleModel(spec, model, url, _key("OPENAI_COMPAT_API_KEY", required=False), sampling)
    if kind == "cmd":
        return CommandModel(rest, timeout_s=sampling.timeout_s)
    if kind == "replay":
        return ReplayModel(rest)
    raise ValueError(f"unknown model provider {kind!r}")
