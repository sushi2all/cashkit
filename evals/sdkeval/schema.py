"""The data shapes of the eval: a task instance, a check, a graded sample.

A **task family** is a parametrised generator (``tasks/``); a **task instance**
is one seeded draw from it — a natural-language request, an optional setup
script that builds the starting book, a reference solution, and the checks the
final state must satisfy. Every expected figure in a check is computed by the
family from the numbers it put in the prompt, in ``Decimal``, *without* running
the engine — so a check is an independent statement of the right answer, not a
copy of what the engine happened to produce (the rule the service trials follow,
``apps/service/trials/live.py``).

Checks are either **critical** (the task is failed when one fails — wrong
numbers, wrong structure, wrong answer) or **hygiene** (reported separately —
floats in money code, committing when not asked, touching store files). A
sample *passes* when it executed and every critical check passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Category = Literal["construction", "settlement", "tax", "formula", "ledger", "scenario", "query", "repair", "workflow"]

CATEGORIES: tuple[str, ...] = (
    "construction",
    "settlement",
    "tax",
    "formula",
    "ledger",
    "scenario",
    "query",
    "repair",
    "workflow",
)


@dataclass(frozen=True)
class Check:
    """One assertion about the observed final state.

    ``kind`` names a grader in :mod:`sdkeval.checks`; ``args`` are its
    arguments (JSON-serialisable: money and dates travel as strings). ``label``
    is the human sentence shown in reports when the check fails.
    """

    kind: str
    args: dict[str, Any]
    label: str
    critical: bool = True

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "args": self.args, "label": self.label, "critical": self.critical}


@dataclass(frozen=True)
class TaskInstance:
    """One seeded draw of a task family — everything needed to prompt and grade."""

    family: str
    category: str
    variant: int
    prompt: str
    reference: str
    checks: tuple[Check, ...]
    setup: str | None = None
    #: Execute the candidate script twice against the same book: the second run
    #: starts from whatever the first left on disk. Tests that a script is
    #: idempotent the way an agent re-running it after an interruption needs.
    run_twice: bool = False
    #: The task is a read-only question: any write call is a hygiene violation.
    read_only: bool = False
    difficulty: int = 1
    tags: tuple[str, ...] = ()
    split: str = "dev"

    @property
    def instance_id(self) -> str:
        return f"{self.family}#{self.variant}"

    def to_json(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "family": self.family,
            "category": self.category,
            "variant": self.variant,
            "prompt": self.prompt,
            "setup": self.setup,
            "reference": self.reference,
            "checks": [c.to_json() for c in self.checks],
            "run_twice": self.run_twice,
            "read_only": self.read_only,
            "difficulty": self.difficulty,
            "tags": list(self.tags),
            "split": self.split,
        }


@dataclass(frozen=True)
class CheckResult:
    kind: str
    label: str
    critical: bool
    passed: bool
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "critical": self.critical,
            "passed": self.passed,
            "detail": self.detail,
        }


#: Why a sample failed, coarsest first. One per failed sample, so the counts
#: in a report add up to the number of failures.
FAILURE_CLASSES: tuple[str, ...] = (
    "model_error",  # the model could not be reached (not counted against it in rates)
    "no_code",  # the reply carried no runnable Python
    "syntax_error",
    "timeout",
    "exception",  # the script raised
    "no_book",  # ran, but there is no book to grade
    "error_diagnostics",  # the final state carries error diagnostics
    "wrong_answer",  # a query task's answer is wrong
    "wrong_structure",  # items / params / scenarios not as asked
    "wrong_numbers",  # structure fine, figures wrong
)


@dataclass
class Attempt:
    """One turn: what the model said, what ran, what was observed."""

    turn: int
    reply: str
    code: str | None
    observation: dict[str, Any] | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float | None = None
    error: str | None = None  # transport error talking to the model


@dataclass
class SampleResult:
    """The graded outcome of one ``(instance, sample index)``."""

    instance_id: str
    family: str
    category: str
    variant: int
    sample: int
    split: str
    difficulty: int
    passed: bool
    passed_first_turn: bool
    turns: int
    failure_class: str | None
    checks: list[CheckResult]
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def hygiene_violations(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.critical and not c.passed]

    def to_json(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "family": self.family,
            "category": self.category,
            "variant": self.variant,
            "sample": self.sample,
            "split": self.split,
            "difficulty": self.difficulty,
            "passed": self.passed,
            "passed_first_turn": self.passed_first_turn,
            "turns": self.turns,
            "failure_class": self.failure_class,
            "checks": [c.to_json() for c in self.checks],
            "attempts": [
                {
                    "turn": a.turn,
                    "reply": a.reply,
                    "code": a.code,
                    "observation": a.observation,
                    "input_tokens": a.input_tokens,
                    "output_tokens": a.output_tokens,
                    "latency_s": a.latency_s,
                    "error": a.error,
                }
                for a in self.attempts
            ],
        }
