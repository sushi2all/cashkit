"""Unit tests for the eval's machinery: extraction, static rules, statistics,
the sandbox, the multi-turn loop, suite determinism and the SDK reference."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from sdkeval import checks, report, sandbox, static
from sdkeval.extract import extract_code
from sdkeval.models import Completion, ReplayModel, resolve
from sdkeval.prompt import DEFAULT_CONTEXT, feedback, load_context, system_prompt
from sdkeval.runner import RunConfig, load_results, run_sample, run_suite
from sdkeval.sandbox import Limits
from sdkeval.tasks import FAMILIES, HELDOUT_FROM, instance, suite

# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def test_extract_prefers_last_python_block():
    reply = "Draft:\n```python\nx = 1\n```\nFixed:\n```python\nx = 2\n```\n"
    assert extract_code(reply) == "x = 2\n"


def test_extract_bare_fence_and_raw_code():
    assert extract_code("```\nimport os\n```") == "import os\n"
    assert extract_code("from decimal import Decimal\nanswer = Decimal('1')") is not None


def test_extract_rejects_prose():
    assert extract_code("I cannot help with that.") is None
    assert extract_code("") is None
    assert extract_code("hello") is None


# --------------------------------------------------------------------------- #
# Static hygiene
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code,rule", [
    ("x = Decimal(0.1)", "float"),
    ("x = float(y)", "float"),
    ("open('book.yaml').read()", "store_access"),
    ("import sqlite3", "store_access"),
    ("import subprocess", "git"),
    ("os.system('git log')", "git"),
    ("d = date.today()", "wall_clock"),
    ("d = datetime.now()", "wall_clock"),
    ("d = datetime.datetime.now()", "wall_clock"),
])
def test_static_rules_catch(code, rule):
    assert static.scan(code, rule)


@pytest.mark.parametrize("rule", sorted(static.RULES))
def test_static_rules_pass_clean_code(rule):
    code = 'from decimal import Decimal\nkit.set_item(Item(id="x", formula="-0.05 * it(\\"s\\")"))\nx = Decimal("0.1")\n'
    assert static.scan(code, rule) == []


def test_float_inside_formula_string_is_fine():
    assert static.scan("f = '-0.05 * it(\"sales\")'", "float") == []


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #


def test_wilson_bounds():
    lo, hi = report.wilson(10, 10)
    assert hi == 1.0 and 0.65 < lo < 0.75
    assert report.wilson(0, 0) == (0.0, 0.0)
    lo, hi = report.wilson(5, 10)
    assert lo < 0.5 < hi


def test_pass_at_k():
    assert report.pass_at_k(5, 0, 1) == 0.0
    assert report.pass_at_k(5, 5, 3) == 1.0
    assert report.pass_at_k(4, 2, 1) == pytest.approx(0.5)
    assert report.pass_at_k(4, 2, 4) == 1.0


def _record(iid, family, sample, passed, failure=None, hygiene_ok=True):
    return {
        "instance_id": iid, "family": family, "category": "construction", "variant": 0, "sample": sample,
        "split": "dev", "difficulty": 1, "passed": passed, "passed_first_turn": passed, "turns": 1,
        "failure_class": failure,
        "checks": [{"kind": "static", "label": "no float", "critical": False, "passed": hygiene_ok, "detail": ""}],
        "attempts": [{"turn": 0, "reply": "", "code": None, "observation": None, "input_tokens": 10,
                      "output_tokens": 5, "latency_s": 1.0, "error": None}],
    }


def test_summarize_consistency_and_model_errors():
    records = [
        _record("a#0", "a", 0, True), _record("a#0", "a", 1, True),
        _record("b#0", "b", 0, True), _record("b#0", "b", 1, False, "wrong_numbers", hygiene_ok=False),
        _record("c#0", "c", 0, False, "model_error"),
    ]
    s = report.summarize(records)
    assert s["overall"]["n"] == 4 and s["overall"]["passed"] == 3
    assert s["model_errors"] == 1
    assert s["consistency"]["pass^2"] == 0.5
    assert s["consistency"]["pass@2"] == 1.0
    assert s["hygiene_violations"] == {"no float": 1}
    assert s["failure_classes"] == {"wrong_numbers": 1}
    md = report.markdown(s, {"model": "x"}, records)
    assert "pass^2" in md and "wrong_numbers" in md


# --------------------------------------------------------------------------- #
# Sandbox
# --------------------------------------------------------------------------- #


def test_sandbox_hides_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    obs = sandbox.execute("import os\nanswer = os.environ.get('ANTHROPIC_API_KEY')\n")
    assert obs["status"] == "ok"
    assert obs["answer"] == {"type": "none", "value": None}


def test_sandbox_timeout():
    obs = sandbox.execute("while True:\n    pass\n", limits=Limits(timeout_s=3))
    assert obs["status"] == "timeout"


def test_sandbox_reports_exception_and_syntax_error():
    assert sandbox.execute("raise ValueError('boom')\n")["status"] == "exception"
    assert sandbox.execute("def (:\n")["status"] == "syntax_error"


def test_sandbox_records_calls_but_not_setup():
    task = instance("downside_param", 0)
    obs = sandbox.execute(task.reference, setup=task.setup)
    names = [c["name"] for c in obs["calls"]]
    assert names == ["fork", "set_param"], names  # setup's writes are not recorded
    assert obs["calls"][1]["scenario"] == "downside"


def test_sandbox_observes_disk_not_memory():
    """Mutating a Python object without the SDK changes nothing that is graded."""
    task = instance("downside_param", 0)
    code = "kit.book.items.clear()\nanswer = len(kit.book.items)\n"  # in-memory only
    obs = sandbox.execute(code, setup=task.setup)
    assert obs["status"] == "ok", obs.get("exception")
    assert obs["answer"]["value"] == "0"  # the object was emptied …
    assert "acme" in obs["observation"]["scenarios"]["base"]["items"]  # … the graded book was not


# --------------------------------------------------------------------------- #
# The multi-turn loop
# --------------------------------------------------------------------------- #


class ScriptedModel:
    """First reply breaks; once fed back the error, it answers with the reference."""

    name = "scripted"

    def __init__(self):
        self.seen: list[list[dict]] = []

    def complete(self, system, messages, *, task, sample, turn):
        self.seen.append(messages)
        if turn == 0:
            return Completion("```python\nkit.set_item(None)\n```")
        return Completion(f"```python\n{task.reference}```")


def test_feedback_turn_recovers_and_is_counted():
    task = instance("monthly_expense", 0)
    model = ScriptedModel()
    result = run_sample(model, task, sample=0, system="s", max_turns=3, limits=Limits())
    assert result.passed and not result.passed_first_turn and result.turns == 2
    feedback_message = model.seen[1][-1]["content"]
    assert "failed" in feedback_message
    # Feedback never leaks the expected figures.
    for check in task.checks:
        for value in (check.args.get("values") or {}).values():
            if value not in ("0.0000",):
                assert value not in feedback_message


def test_single_turn_does_not_retry():
    task = instance("monthly_expense", 0)
    result = run_sample(ScriptedModel(), task, sample=0, system="s", max_turns=1, limits=Limits())
    assert not result.passed and result.turns == 1 and result.failure_class == "exception"


def test_feedback_is_none_when_clean_and_reports_refused_writes():
    assert feedback({"status": "ok", "calls": [], "observation": {"book": {"id": "x"}, "scenarios": {}}}) is None
    msg = feedback({"status": "ok", "calls": [{"name": "set_item", "write": True, "scenario": "base", "diagnostics": ["error:CK-E011"]}],
                    "observation": {"book": {"id": "x"}, "scenarios": {}}})
    assert "CK-E011" in msg


def test_run_suite_writes_results_and_replay_regrades(tmp_path):
    tasks = suite(families=["monthly_expense", "total_outflow"], variants=1)
    run_dir = run_suite(resolve("reference"), tasks, RunConfig(model="reference", jobs=2), out_root=tmp_path)
    records = load_results(run_dir)
    assert len(records) == 2 and all(r["passed"] for r in records)
    summary = report.write(run_dir, records)
    assert summary["overall"]["rate"] == 1.0
    assert (run_dir / "report.md").exists() and (run_dir / "system_prompt.md").exists()
    replay = run_suite(ReplayModel(str(run_dir)), tasks, RunConfig(model="replay", jobs=2), out_root=tmp_path / "r")
    assert all(r["passed"] for r in load_results(replay))


def test_resume_skips_finished_samples(tmp_path):
    tasks = suite(families=["monthly_expense"], variants=2)
    run_dir = run_suite(resolve("reference"), tasks[:1], RunConfig(model="reference"), out_root=tmp_path)
    run_suite(resolve("reference"), tasks, RunConfig(model="reference"), out_root=tmp_path, resume=run_dir)
    ids = sorted(r["instance_id"] for r in load_results(run_dir))
    assert ids == sorted(t.instance_id for t in tasks)


# --------------------------------------------------------------------------- #
# Suite determinism and splits
# --------------------------------------------------------------------------- #


def test_instances_are_deterministic():
    for name in FAMILIES:
        a, b = instance(name, 2), instance(name, 2)
        assert a.prompt == b.prompt and a.reference == b.reference and a.checks == b.checks


def test_variants_differ_and_splits_are_disjoint():
    for name in FAMILIES:
        prompts = {instance(name, v).prompt for v in (0, 1, 2, HELDOUT_FROM)}
        assert len(prompts) >= 3, f"{name}: variants barely differ"
    dev = {t.instance_id for t in suite(variants=2, split="dev")}
    held = {t.instance_id for t in suite(variants=2, split="heldout")}
    assert dev and held and not (dev & held)
    assert all(t.split == "heldout" for t in suite(variants=1, split="heldout"))


def test_every_category_is_covered():
    from sdkeval.schema import CATEGORIES

    assert {f.category for f in FAMILIES.values()} == set(CATEGORIES)


def test_unknown_family_is_an_error():
    with pytest.raises(KeyError):
        suite(families=["nope"])


# --------------------------------------------------------------------------- #
# The SDK reference given to models
# --------------------------------------------------------------------------- #


def test_reference_doc_snippets_run(tmp_path):
    """Every ```python block of the reference executes, in order, against the real SDK
    (blocks that only show a constructor are compiled). A doc that drifts from the SDK
    teaches models the wrong calls — this is what keeps it honest."""
    text = DEFAULT_CONTEXT.read_text()
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    assert len(blocks) >= 8
    namespace: dict = {"__name__": "__main__"}
    for index, block in enumerate(blocks):
        code = compile(block, f"<sdk_reference block {index}>", "exec")
        if block.lstrip().startswith(("Item(", "Segment(", "Settlement.")):
            continue
        exec(code, namespace)


def test_reference_doc_lists_every_diagnostic_code():
    from cashkit.model import CATALOGUE

    text = DEFAULT_CONTEXT.read_text()
    missing = [code for code in CATALOGUE if code not in text]
    assert not missing, f"sdk_reference.md does not explain {missing}"


def test_context_ablation_options():
    assert load_context("none") == ""
    assert "CashKit SDK reference" in system_prompt(load_context("reference"))
    assert "CashKit SDK reference" not in system_prompt(load_context("none"))
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write("custom docs")
    assert load_context(fh.name) == "custom docs"


def test_model_spec_parsing():
    assert resolve("reference").name == "reference"
    assert resolve("anthropic:claude-x").name == "anthropic:claude-x"
    assert resolve("openai-compat:qwen@http://localhost:8000/v1").url == "http://localhost:8000/v1/chat/completions"
    with pytest.raises(ValueError):
        resolve("mystery")


def test_feedback_ignores_probes_and_handled_refusals():
    book = {"book": {"id": "x"}, "scenarios": {}}
    probe = {"status": "ok", "observation": book, "calls": [
        {"name": "open", "write": False, "diagnostics": ["error:CK-E029"]},
        {"name": "create_book", "write": True, "diagnostics": ["error:CK-E031"]},
    ]}
    assert feedback(probe) is None
    refused = {"status": "ok", "observation": book, "calls": [
        {"name": "set_item", "write": True, "scenario": "base", "diagnostics": ["error:CK-E011"]}]}
    assert "CK-E011" in feedback(refused)


def test_command_model_reads_token_envelope(tmp_path):
    from sdkeval.models import CommandModel

    script = tmp_path / "m.py"
    script.write_text('import json,sys; sys.stdin.read(); print(json.dumps({"reply": "hi", "input_tokens": 7, "output_tokens": 3}))')
    out = CommandModel(f"python3 {script}").complete("s", [{"role": "user", "content": "x"}],
                                                    task=instance("monthly_expense", 0), sample=0, turn=0)
    assert (out.text, out.input_tokens, out.output_tokens) == ("hi", 7, 3)


def test_project_readme_lists_every_family():
    """The root README's "All cases tested" list must match the suite."""
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    section = readme.split("### All cases tested", 1)[1]
    listed = set(re.findall(r"^\| `([a-z_]+)` \| \d \|", section, re.M))
    assert listed == set(FAMILIES), sorted(set(FAMILIES) ^ listed)
