"""What the model is allowed to see (SPEC §2.3, §2.5, ADR-0030).

Two rules, both mechanical rather than review habit (PROMPT, "Definition of
done"):

* no raw SDK mutation verb appears in any prompt template;
* no host operation appears in any prompt template.

The check runs over the module's own strings and over everything the scripted
transport was actually sent, so a rule broken by string interpolation at
runtime fails too.
"""

from __future__ import annotations

import inspect

import pytest

from cashkit_service.agent import prompts
from cashkit_service.agent.guard import MODEL_OPS
from cashkit_service.ops.schema import HOST_OPS

#: SDK verbs the intent grammar deliberately replaces (ADR-0019). A model that
#: sees one has been handed SDK composition, which is the surface ADR-0019
#: rejected. ``set_cutover`` is absent from this list on purpose: it is M8.
SDK_MUTATION_VERBS = (
    "set_item",
    "set_param",
    "add_derived",
    "correct_event",
    "void_event",
    "import_events",
    "apply_macro",
    "set_book",
    "create_book",
    "retag",
)


def prompt_strings() -> dict[str, str]:
    """Every module-level template string in :mod:`prompts`."""
    return {
        name: value
        for name, value in vars(prompts).items()
        if isinstance(value, str) and not name.startswith("__") and len(value) > 40
    }


def rendered_prompts() -> dict[str, str]:
    """The templates as the pipeline actually renders them."""
    snapshot = '{"as_of":"2026-03-17","book":{},"items":[],"results":{}}'
    return {
        "interpret_system": prompts.interpret_system(snapshot),
        "qa_system": prompts.qa_system(snapshot),
        "verify_system": prompts.VERIFY_SYSTEM + prompts.CHANGE_GRAMMAR,
        "diagnostic_repair": prompts.diagnostic_repair_message([], snapshot)["content"],
        "json_repair": prompts.json_repair_message("boom")["content"],
        "qa_results": prompts.qa_results_message([])["content"],
        # The import loop's three prompts, rendered (SPEC §7, S5). They are the
        # newest surface and therefore the likeliest to leak a reserved verb.
        "import_plan": _joined(
            prompts.import_plan_messages(
                "## sheet Budget\nA1='Salary'",
                candidates=[{"ref": "Budget!B7", "label": "Total in", "value": "2800"}],
                headers=["Budget row 3: Jan | Feb"],
                book_json=snapshot,
                filename="budget.xlsx",
            )
        ),
        "import_author": _joined(
            prompts.import_author_messages(
                "## sheet Budget\nA1='Salary'",
                section={"name": "Income"},
                remaining=["Expenses"],
                already=[],
                book_json=snapshot,
                plan_note="a family budget",
                one_off_style="a one-off is a one-month line",
            )
        ),
        "import_revise": _joined(
            prompts.import_revise_messages(
                "## sheet Budget\nA1='Salary'",
                section={"name": "Income"},
                operations=[],
                failures=[],
                evidence=[],
            )
        ),
    }


def _joined(messages: list[dict[str, str]]) -> str:
    return "\n".join(m["content"] for m in messages)


@pytest.mark.parametrize("verb", SDK_MUTATION_VERBS)
def test_no_raw_sdk_mutation_verb_appears_in_a_prompt(verb):
    offenders = [
        name for name, text in {**prompt_strings(), **rendered_prompts()}.items()
        if verb in text
    ]
    assert offenders == [], f"{verb!r} appears in {offenders}"


@pytest.mark.parametrize("host_op", sorted(HOST_OPS))
def test_no_host_operation_appears_in_a_prompt(host_op):
    offenders = [
        name for name, text in {**prompt_strings(), **rendered_prompts()}.items()
        if host_op in text
    ]
    assert offenders == [], f"{host_op!r} appears in {offenders}"


def test_the_prompt_module_names_no_operation_outside_the_model_surface():
    """Every ``{"op":"…"}`` example in the grammar is on the surface."""
    import re

    source = inspect.getsource(prompts)
    named = set(re.findall(r'"op"\s*:\s*"([a-z_]+)"', source))
    assert named <= MODEL_OPS, sorted(named - MODEL_OPS)


def test_the_grammar_shows_every_intent_the_model_may_use():
    """A surface the model cannot see is a surface it cannot use."""
    grammar = prompts.READ_GRAMMAR + prompts.CHANGE_GRAMMAR
    missing = [op for op in sorted(MODEL_OPS) if f'"op":"{op}"' not in grammar]
    assert missing == []


def test_the_rules_forbid_recomputation():
    """Proto T11: without this rule the model does arithmetic and gets it wrong."""
    assert "NEVER RECOMPUTE" in prompts.RULES
    assert "results" in prompts.RULES


def test_the_rules_forbid_approximating_what_the_book_cannot_express():
    """Proto T03's failure class: a plausible number with no diagnostic."""
    assert "Never approximate" in prompts.RULES


def test_the_voice_rule_is_in_the_prompt():
    """SPEC §5-F1, D-MLP-05(c): two short sentences, no apologies, no hedging."""
    assert "two short sentences" in prompts.RULES
    assert "No apologies" in prompts.RULES


def test_the_prompt_never_asks_the_model_for_as_of():
    """ADR-0019 rule 2: the host fills it; the model must not supply it."""
    assert "The host fills the\n  as-of date" in prompts.OUTPUT_CONTRACT


async def test_nothing_reserved_reaches_the_provider_on_a_real_turn(
    seeded_client, transport, model_script
):
    """The end-to-end version: check the bytes that were actually sent."""
    model_script.append({"kind": "answer", "reply": "Your runway ends in June.",
                         "intents": [{"op": "runway"}]})
    model_script.append({"kind": "answer", "reply": "Your runway ends in June.",
                         "intents": []})
    response = await seeded_client.post("/turns", json={"text": "how long does my money last?"})
    assert response.status_code == 200, response.text

    sent = transport.everything_sent
    for verb in SDK_MUTATION_VERBS:
        assert verb not in sent, verb
    for host_op in HOST_OPS:
        assert host_op not in sent, host_op
