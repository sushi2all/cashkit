"""Run a suite against a model: prompt → extract → execute → (feedback →) grade.

One **sample** is one independent attempt at one task instance. With
``max_turns > 1`` a sample is a short agent loop: after each turn the model
sees what an agent would see — a traceback, or the error diagnostics its SDK
calls and the final run returned (never the expected values) — and may reply
with a corrected script, which is executed from scratch. The final turn is
graded; the first turn is graded too, so a report shows both "right first
time" and "right after reading the diagnostics".

Results stream to ``results.jsonl`` as samples finish, so an interrupted run
loses nothing and ``--resume`` picks up where it stopped.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import checks as grading
from . import prompt as prompting
from .extract import extract_code
from .models import Model, ModelError
from .sandbox import Limits, execute
from .schema import Attempt, SampleResult, TaskInstance


@dataclass
class RunConfig:
    model: str
    context: str = "reference"
    samples: int = 1
    max_turns: int = 1
    jobs: int = 4
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_s: float = 60.0
    families: list[str] | None = None
    categories: list[str] | None = None
    variants: int = 3
    split: str = "dev"
    max_difficulty: int | None = None
    label: str | None = None
    extra: dict = field(default_factory=dict)


def run_sample(model: Model, task: TaskInstance, *, sample: int, system: str, max_turns: int,
               limits: Limits) -> SampleResult:
    """One sample, possibly multi-turn. Never raises for model or candidate failures."""
    task_checks = grading.checks_for(task.checks, read_only=task.read_only)
    messages = [{"role": "user", "content": prompting.user_prompt(task)}]
    attempts: list[Attempt] = []
    graded_first: list | None = None
    graded_last: list = []
    obs_last: dict | None = None
    code_last: str | None = None

    for turn in range(max_turns):
        try:
            completion = model.complete(system, messages, task=task, sample=sample, turn=turn)
        except (ModelError, OSError, ValueError) as exc:
            attempts.append(Attempt(turn, "", None, None, error=f"{type(exc).__name__}: {exc}"))
            break
        code = extract_code(completion.text)
        observation = None
        if code is not None:
            observation = execute(code, setup=task.setup, run_twice=task.run_twice, limits=limits)
        attempts.append(Attempt(turn, completion.text, code, observation, completion.input_tokens,
                                completion.output_tokens, completion.latency_s))
        graded = grading.grade(task_checks, observation or {}, code) if observation is not None else []
        if graded_first is None:
            graded_first = graded
        graded_last, obs_last, code_last = graded, observation, code
        if turn + 1 >= max_turns:
            break
        if code is None:
            follow = "I could not find a ```python code block in your reply. Reply with the complete script in one block."
        else:
            follow = prompting.feedback(observation or {})
        if follow is None:
            break
        messages = messages + [{"role": "assistant", "content": completion.text}, {"role": "user", "content": follow}]

    def passed(graded: list | None, obs: dict | None) -> bool:
        return bool(graded) and obs is not None and obs.get("status") == "ok" and all(
            r.passed for r in graded if r.critical)

    first_obs = attempts[0].observation if attempts else None
    ok = passed(graded_last, obs_last)
    failure = None
    if not ok:
        if attempts and attempts[-1].error and not attempts[-1].reply:
            failure = "model_error"
        else:
            failure = grading.classify(obs_last, code_last, graded_last)
            failure = failure or "wrong_numbers"
    return SampleResult(
        instance_id=task.instance_id, family=task.family, category=task.category, variant=task.variant,
        sample=sample, split=task.split, difficulty=task.difficulty, passed=ok,
        passed_first_turn=passed(graded_first, first_obs), turns=len(attempts), failure_class=failure,
        checks=graded_last, attempts=attempts,
    )


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:60]


def run_suite(
    model: Model,
    tasks: list[TaskInstance],
    config: RunConfig,
    *,
    out_root: Path,
    resume: Path | None = None,
    progress: Callable[[SampleResult, int, int], None] | None = None,
) -> Path:
    """Run every ``(task, sample)``; write results under a new run directory and return it."""
    if resume is not None:
        run_dir = resume
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = out_root / f"{stamp}_{_slug(config.label or config.model)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    context = prompting.load_context(config.context)
    system = prompting.system_prompt(context)
    (run_dir / "config.json").write_text(json.dumps(asdict(config), indent=2))
    (run_dir / "system_prompt.md").write_text(system)
    (run_dir / "tasks.jsonl").write_text("".join(json.dumps(t.to_json()) + "\n" for t in tasks))

    results_path = run_dir / "results.jsonl"
    done: set[tuple[str, int]] = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            record = json.loads(line)
            done.add((record["instance_id"], record["sample"]))
    jobs = [(t, s) for t in tasks for s in range(config.samples) if (t.instance_id, s) not in done]
    limits = Limits(timeout_s=config.timeout_s)
    lock = threading.Lock()
    total = len(jobs)
    finished = 0

    with ThreadPoolExecutor(max_workers=max(1, config.jobs)) as pool:
        futures = {
            pool.submit(run_sample, model, t, sample=s, system=system, max_turns=config.max_turns, limits=limits): (t, s)
            for t, s in jobs
        }
        for future in as_completed(futures):
            result = future.result()
            with lock:
                with results_path.open("a") as fh:
                    fh.write(json.dumps(result.to_json(), default=str) + "\n")
                finished += 1
                if progress:
                    progress(result, finished, total)
    return run_dir


def load_results(run_dir: Path) -> list[dict]:
    path = Path(run_dir) / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def regrade(run_dir: Path, tasks_by_id: dict[str, TaskInstance], *, limits: Limits = Limits()) -> list[dict]:
    """Re-execute and re-grade the recorded final scripts of a run with today's
    SDK and checks — the regression question "would last month's answers still
    be right?" without calling a model."""
    out = []
    for record in load_results(run_dir):
        task = tasks_by_id.get(record["instance_id"])
        if task is None:
            continue
        code = record["attempts"][-1]["code"] if record["attempts"] else None
        obs = execute(code, setup=task.setup, run_twice=task.run_twice, limits=limits) if code else None
        graded = grading.grade(grading.checks_for(task.checks, read_only=task.read_only), obs or {}, code)
        ok = obs is not None and obs.get("status") == "ok" and all(r.passed for r in graded if r.critical)
        record = dict(record)
        record["regraded_passed"] = ok
        record["regraded_failure_class"] = None if ok else grading.classify(obs, code, graded)
        out.append(record)
    return out
