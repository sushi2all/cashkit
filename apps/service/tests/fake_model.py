"""A scripted stand-in for the model provider.

It replaces the **provider**, never the pipeline: a test using it still goes
through the real guard, the real dry-run, the real proposal store and the real
endpoints. That is the point. The invariants this session must hold — a turn
never writes, a question never writes, a host operation never reaches the model
— have to hold against a model that misbehaves *on purpose*, and a live model
cannot be asked to misbehave on cue.

The live model is not replaced anywhere: the ported trials in
``apps/service/trials`` call OpenRouter for real, on the pinned model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from cashkit_service.agent.transport import Completion, extract_json


@dataclass
class ScriptedTransport:
    """Answers each call with the next entry of ``script``.

    An entry may be a dict (serialized as the model's JSON object), a raw
    string (to exercise the JSON hardening), or an exception instance (to
    simulate a provider failure).
    """

    script: list[Any] = field(default_factory=list)
    model: str = "scripted/test-model"
    cost_per_call: Decimal = Decimal("0.001")
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        self.calls.append({"messages": messages, "temperature": temperature})
        if not self.script:
            raise AssertionError(
                f"the scripted transport ran out of responses on call {len(self.calls)}"
            )
        item = self.script.pop(0)

        if isinstance(item, BaseException):
            return Completion(
                text="", parsed=None, model=self.model, latency_ms=1,
                request={"model": self.model, "messages": messages},
                response={}, error=f"{type(item).__name__}: {item}",
            )

        text = item if isinstance(item, str) else json.dumps(item)
        completion = Completion(
            text=text,
            parsed=None,
            model=self.model,
            latency_ms=1,
            request={"model": self.model, "messages": messages, "temperature": temperature},
            response={"choices": [{"message": {"content": text}}]},
            prompt_tokens=100,
            completion_tokens=20,
            cost=self.cost_per_call,
        )
        try:
            completion.parsed = extract_json(text)
        except ValueError as exc:
            completion.error = str(exc)
        return completion

    async def aclose(self) -> None:
        return None

    # -- helpers for assertions -------------------------------------------- #

    @property
    def prompts(self) -> list[str]:
        """Every system prompt the transport was ever handed."""
        return [
            message["content"]
            for call in self.calls
            for message in call["messages"]
            if message["role"] == "system"
        ]

    @property
    def everything_sent(self) -> str:
        return json.dumps(self.calls)


def answer(reply: str, intents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"kind": "answer", "reply": reply, "intents": intents or []}


def clarification(reply: str) -> dict[str, Any]:
    return {"kind": "clarification", "reply": reply, "intents": []}


__all__ = ["ScriptedTransport", "answer", "clarification"]
