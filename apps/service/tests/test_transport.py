"""The hardened model transport (SPEC §2.3).

The proto ranked JSON transport hardening first among everything that improved
model reliability — it killed roughly half of all failures. These tests pin
each of the four parts against the failure that earned it, so a later refactor
cannot quietly drop one.
"""

from __future__ import annotations

import json

import pytest

from fake_model import ScriptedTransport

from cashkit_service.agent.transport import (
    OpenRouterTransport,
    UnparseableOutput,
    extract_json,
    repair_brackets,
)


# --- first-object decode (proto T08) -------------------------------------- #


def test_a_clean_object_parses():
    assert extract_json('{"reply":"ok","intents":[]}') == {"reply": "ok", "intents": []}


def test_prose_after_the_object_is_ignored():
    """A model that explains itself afterwards still gets understood."""
    text = '{"reply":"done","intents":[]}\n\nI hope that helps!'
    assert extract_json(text)["reply"] == "done"


def test_prose_before_the_object_is_ignored():
    text = 'Sure, here you go:\n{"reply":"done","intents":[]}'
    assert extract_json(text)["reply"] == "done"


def test_a_fenced_object_parses():
    text = '```json\n{"reply":"fenced","intents":[]}\n```'
    assert extract_json(text)["reply"] == "fenced"


def test_no_object_at_all_is_refused():
    with pytest.raises(UnparseableOutput):
        extract_json("I am afraid I cannot do that.")


def test_a_json_array_is_not_an_object():
    with pytest.raises(UnparseableOutput):
        extract_json('[{"op":"runway"}]')


# --- bracket-stack repair (proto T09) ------------------------------------- #


def test_one_missing_closing_brace_is_repaired():
    """T09: lite dropped a single brace in an 827-character object."""
    broken = '{"reply":"ok","intents":[{"op":"runway"}]'
    assert extract_json(broken) == {"reply": "ok", "intents": [{"op": "runway"}]}


def test_several_missing_closers_are_repaired_in_order():
    broken = '{"a":{"b":[1,2,{"c":3'
    assert extract_json(broken) == {"a": {"b": [1, 2, {"c": 3}]}}


def test_a_stray_closer_is_dropped():
    assert extract_json('{"reply":"ok"}}') == {"reply": "ok"}


def test_a_wrong_closer_closes_what_was_open():
    """A ``]`` where a ``}`` belongs means the object was never closed."""
    assert json.loads(repair_brackets('{"a":[{"b":1]}')) == {"a": [{"b": 1}]}


def test_brackets_inside_strings_are_not_counted():
    text = '{"reply":"the total is {not an object} [really]","intents":[]}'
    assert extract_json(text)["reply"] == "the total is {not an object} [really]"


def test_an_escaped_quote_does_not_end_the_string():
    text = '{"reply":"she said \\"yes\\" and {left}","intents":[]}'
    assert extract_json(text)["reply"] == 'she said "yes" and {left}'


def test_an_unterminated_string_is_closed():
    assert extract_json('{"reply":"half a sentence')["reply"] == "half a sentence"


def test_repair_leaves_valid_text_alone():
    text = '{"a":[1,2],"b":{"c":"d"}}'
    assert repair_brackets(text) == text


# --- the request payload -------------------------------------------------- #


def test_the_request_asks_for_json_and_refuses_data_collection():
    """``json_object`` mode and zero-retention routing (SPEC §2.3, §9)."""
    transport = OpenRouterTransport(api_key="k", model="google/gemini-3.7-flash")
    payload = transport._payload([{"role": "user", "content": "hi"}], 0.0, None)

    assert payload["response_format"] == {"type": "json_object"}
    assert payload["provider"]["data_collection"] == "deny"
    assert payload["usage"] == {"include": True}
    assert payload["model"] == "google/gemini-3.7-flash"
    assert payload["temperature"] == 0.0


def test_the_pinned_model_is_the_default(settings):
    """ADR-0028: every turn runs flash-class, and the model is pinned."""
    assert settings.llm_model == "google/gemini-3.7-flash"


# --- one call in, one completion out --------------------------------------- #


async def test_one_call_produces_one_completion():
    """The retry loop is the pipeline's, so each call can be one journal row."""
    transport = ScriptedTransport(script=[{"kind": "answer", "reply": "hello", "intents": []}])
    completion = await transport.complete([{"role": "user", "content": "hi"}])

    assert completion.ok
    assert completion.parsed["reply"] == "hello"
    assert len(transport.calls) == 1


async def test_unparseable_output_is_reported_not_raised():
    transport = ScriptedTransport(script=["not json at all"])
    completion = await transport.complete([{"role": "user", "content": "hi"}])

    assert not completion.ok
    assert completion.error
    assert completion.parsed is None
