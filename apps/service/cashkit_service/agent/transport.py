"""The hardened model transport (SPEC §2.3).

The proto measured this and put it first in its "what moved the needle" list:
JSON transport hardening killed roughly half of all model failures. It is not
polish. Four parts, each earned by a specific observed failure:

1. ``response_format: {"type": "json_object"}`` — asks the provider for JSON
   rather than hoping for it (proto T08).
2. **First-object decode.** ``raw_decode`` takes the first complete object and
   ignores whatever prose follows it, so a model that adds a sentence after
   its JSON still gets understood (proto T08).
3. **Bracket-stack repair.** A model that drops one closing brace in an
   otherwise perfect 800-character object used to lose the whole turn, and a
   temperature-0 retry reproduced the same broken bytes (proto T09).
4. **Temperature bump on a repair ask.** Same reason: at temperature 0 the
   model re-emits the identical broken output, so the caller retries warmer.

One call in, one call out. :meth:`OpenRouterTransport.complete` performs
exactly **one** model call and returns exactly one :class:`Completion`, so the
caller can record exactly one ``llm_calls`` row for it (SPEC §4, §11). The
repair loop lives in the pipeline, where each retry is its own recorded call
with ``purpose="repair"`` — a retry is a model call and is logged like one.

Nothing here touches a book, and nothing here may be called while the book lock
is held (SPEC §2.2).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx

#: Purposes SPEC §4 enumerates for ``llm_calls.purpose``.
Purpose = str
PURPOSES = ("interpret", "repair", "verify", "qa", "import")


class ModelUnavailable(RuntimeError):
    """The provider could not be reached, or answered with nothing usable.

    A service failure, not a diagnostic: no engine number is involved and the
    user's book is untouched.
    """


class UnparseableOutput(ValueError):
    """The model's output was not a JSON object, even after repair."""


class UnreadableAnswer(RuntimeError):
    """The provider answered, and no answer was usable JSON.

    Distinct from :class:`ModelUnavailable` on purpose: the provider was
    reached and did reply, so telling the user the assistant could not be
    reached would be false. It is a turn that failed, not an outage, and the
    user is asked to say it again.
    """


@dataclass
class Completion:
    """One model call, and everything worth recording about it."""

    text: str
    parsed: dict[str, Any] | None
    model: str
    latency_ms: int
    request: dict[str, Any]
    response: dict[str, Any]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost: Decimal | None = None
    error: str | None = None
    #: True when the bracket-stack repair had to run before the object parsed.
    repaired: bool = False

    @property
    def ok(self) -> bool:
        return self.parsed is not None and self.error is None


class Transport(Protocol):
    """What the pipeline needs from a model provider."""

    model: str

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        """Make exactly one model call and return exactly one completion."""

    async def aclose(self) -> None:
        """Release provider connections."""


# --- JSON extraction ------------------------------------------------------ #


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON object out of whatever it actually sent.

    Raises :class:`UnparseableOutput` when even the repaired text will not
    decode — the caller then re-asks at a higher temperature.
    """
    body = _strip_fences(text.strip())
    start = body.find("{")
    if start < 0:
        raise UnparseableOutput("no JSON object in the model's output")
    # A bare array means the model answered with the intents alone. Salvaging
    # its first element would silently drop the rest, so the caller re-asks.
    bracket = body.find("[")
    if 0 <= bracket < start:
        raise UnparseableOutput("expected a JSON object, got an array")
    candidate = body[start:]
    try:
        obj, _ = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        try:
            obj, _ = json.JSONDecoder().raw_decode(repair_brackets(candidate))
        except json.JSONDecodeError as exc:
            raise UnparseableOutput(f"unparseable output: {exc}") from exc
    if not isinstance(obj, dict):
        raise UnparseableOutput(f"expected a JSON object, got {type(obj).__name__}")
    return obj


def _strip_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    without_first_line = text.split("\n", 1)[1] if "\n" in text else ""
    return without_first_line.rsplit("```", 1)[0]


def repair_brackets(text: str) -> str:
    """Close the brackets a model forgot, and drop the ones it invented.

    Walks the text outside string literals, keeping a stack of expected
    closers. A closer that contradicts the stack means one or more openers were
    never closed, so the missing closers are inserted before it; a closer with
    an empty stack is a stray and is dropped; whatever is still open at the end
    is closed. Proto T09: a single dropped brace used to cost a whole turn.
    """
    out: list[str] = []
    stack: list[str] = []
    in_string = False
    escaped = False

    for char in text:
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            while stack and stack[-1] != char:
                out.append(stack.pop())
            if not stack:
                continue  # a stray closer: drop it
            stack.pop()
        out.append(char)

    if in_string:
        out.append('"')
    while stack:
        out.append(stack.pop())
    return "".join(out)


# --- the OpenRouter transport --------------------------------------------- #


@dataclass
class OpenRouterTransport:
    """One model call over OpenRouter's chat-completions API.

    ``provider.data_collection: "deny"`` is the zero-retention requirement of
    SPEC §2.3 and §9 expressed on the wire: it refuses providers that would
    keep the payload. It travels on every call rather than relying on an
    account setting, so a misconfigured account cannot silently opt in.
    """

    api_key: str
    model: str
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 120.0
    max_tokens: int = 16000
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    def _payload(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int | None
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
            "provider": {"data_collection": "deny"},
        }

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        payload = self._payload(messages, temperature, max_tokens)
        started = time.monotonic()
        try:
            response = await self._http().post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "CashKit MLP",
                },
            )
        except httpx.HTTPError as exc:
            return Completion(
                text="",
                parsed=None,
                model=self.model,
                latency_ms=_elapsed_ms(started),
                request=payload,
                response={},
                error=f"{type(exc).__name__}: {exc}",
            )
        latency_ms = _elapsed_ms(started)

        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:4000]}
        if response.status_code >= 400 or not body.get("choices"):
            return Completion(
                text="",
                parsed=None,
                model=self.model,
                latency_ms=latency_ms,
                request=payload,
                response=body,
                error=f"HTTP {response.status_code}: {json.dumps(body)[:600]}",
            )

        return _completion_from(body, payload, self.model, latency_ms)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _completion_from(
    body: dict[str, Any], request: dict[str, Any], model: str, latency_ms: int
) -> Completion:
    text = body["choices"][0].get("message", {}).get("content") or ""
    usage = body.get("usage") or {}
    completion = Completion(
        text=text,
        parsed=None,
        model=body.get("model") or model,
        latency_ms=latency_ms,
        request=request,
        response=body,
        prompt_tokens=_int_or_none(usage.get("prompt_tokens")),
        completion_tokens=_int_or_none(usage.get("completion_tokens")),
        # Provider spend, not book money: a USD figure the provider reports,
        # never a figure that reaches a forecast. It goes through str() so no
        # float arithmetic is done on it here or later.
        cost=None if usage.get("cost") is None else Decimal(str(usage["cost"])),
    )
    before = text
    try:
        completion.parsed = extract_json(text)
    except UnparseableOutput as exc:
        completion.error = str(exc)
        return completion
    completion.repaired = _needed_repair(before)
    return completion


def _needed_repair(text: str) -> bool:
    body = _strip_fences(text.strip())
    start = body.find("{")
    if start < 0:
        return True
    try:
        json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError:
        return True
    return False


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = [
    "Completion",
    "ModelUnavailable",
    "OpenRouterTransport",
    "PURPOSES",
    "Purpose",
    "Transport",
    "UnparseableOutput",
    "UnreadableAnswer",
    "extract_json",
    "repair_brackets",
]
