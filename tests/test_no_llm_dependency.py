"""No-LLM import guard (ADR-0016).

**``cashkit/`` has no LLM dependency, ever.** No package under ``cashkit/``
imports a model client, calls an inference endpoint, or embeds a prompt. The ADR
puts this in the same class as "no float in money paths", and says plainly that
a written rule without a check decays — so it is checked here, in the same place
and the same way as the wall-clock ban (ADR-0010).

Why it matters concretely rather than as a principle: the engine's determinism
guarantee is total (byte-identical dual-engine equality, no wall clock, a run
identified by four recorded values). One model call inside the engine would void
all of it, silently, and the first such call anyone writes is an
NL-to-formula helper — which is precisely the silent-error surface the design
forbids. It also keeps ``pip install cashkit`` from pulling an inference stack.

Three layers, because an import guard alone is easy to route around:

1. no import of a model client or inference SDK;
2. no inference endpoint URL in any string;
3. no embedded prompt — chat-role markers and system-prompt boilerplate.

Plus a self-test proving each layer catches a planted violation, and a check of
the dependency set, since a transitive pull is a dependency too.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import cashkit

PACKAGE_ROOT = Path(cashkit.__file__).parent
PROJECT_ROOT = PACKAGE_ROOT.parent

#: Top-level modules that are model clients, inference runtimes or agent
#: frameworks. Matching is on the *root* package, so ``langchain_core.x`` and
#: ``google.generativeai`` are both caught.
BANNED_ROOTS = {
    "anthropic",
    "openai",
    "cohere",
    "mistralai",
    "groq",
    "together",
    "replicate",
    "litellm",
    "ollama",
    "llama_cpp",
    "llama_index",
    "langchain",
    "langchain_core",
    "langchain_community",
    "langchain_openai",
    "langgraph",
    "haystack",
    "guidance",
    "dspy",
    "instructor",
    "outlines",
    "transformers",
    "sentence_transformers",
    "torch",
    "tensorflow",
    "jax",
    "vllm",
    "huggingface_hub",
    "tiktoken",
    "google",  # google.generativeai / google.genai
}

#: Inference endpoints, as substrings. A model reached over HTTP is still a
#: model dependency, and ``requests`` alone would not be caught by an import
#: check.
BANNED_ENDPOINTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.cohere.ai",
    "api.mistral.ai",
    "api.groq.com",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/messages",
    "/v1/embeddings",
    "localhost:11434",
)

#: Markers of an embedded prompt. Deliberately literal: the failure mode is
#: someone pasting a system prompt into a docstring or a template constant.
PROMPT_MARKERS = (
    "you are a helpful",
    "you are an ai",
    "as an ai language model",
    '"role": "system"',
    "'role': 'system'",
    '"role": "user"',
    "role=\"system\"",
    "role='system'",
    "<|im_start|>",
    "\\n\\nhuman:",
    "\\n\\nassistant:",
)


def _module_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    return roots


def violations_in(path: Path) -> list[str]:
    """Every LLM-dependency violation in one file. Shared by the self-test."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found: list[str] = []
    for root in sorted(_module_roots(tree) & BANNED_ROOTS):
        found.append(f"{path}: imports {root!r}")
    lowered = source.lower()
    for endpoint in BANNED_ENDPOINTS:
        if endpoint.lower() in lowered:
            found.append(f"{path}: names the inference endpoint {endpoint!r}")
    # Prompt markers are matched against the raw source, not only against string
    # constants: a chat message body is a dict of several separate constants
    # (``{"role": "system", ...}``), and none of them is a prompt on its own.
    for marker in PROMPT_MARKERS:
        if marker.lower() in lowered:
            found.append(f"{path}: embeds a prompt ({marker!r})")
    return found


def test_no_module_client_import_anywhere_in_the_package() -> None:
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        violations.extend(violations_in(path))
    assert not violations, "LLM dependency inside cashkit/:\n" + "\n".join(violations)


def test_the_guard_catches_every_planted_violation(tmp_path: Path) -> None:
    """A rule without a working check decays; so does a check that never fires."""
    samples = [
        "import anthropic\n",
        "import openai.types\n",
        "from langchain_core.prompts import ChatPromptTemplate\n",
        "from google.generativeai import GenerativeModel\n",
        "import torch\n",
        'URL = "https://api.openai.com/v1/chat/completions"\n',
        'ENDPOINT = "http://localhost:11434/api/generate"\n',
        'PROMPT = "You are a helpful financial assistant."\n',
        'BODY = [{"role": "system", "content": "explain"}]\n',
    ]
    for index, source in enumerate(samples):
        sample = tmp_path / f"planted_{index}.py"
        sample.write_text(source, encoding="utf-8")
        assert violations_in(sample), f"guard missed: {source!r}"


def test_the_guard_does_not_fire_on_ordinary_code(tmp_path: Path) -> None:
    """A guard that flags legitimate code gets disabled, which is worse."""
    sample = tmp_path / "ordinary.py"
    sample.write_text(
        "import numpy as np\n"
        "from decimal import Decimal\n"
        'NOTE = "the user role in this book is display-only"\n'
        'URL = "https://github.com/sushi2all/cashkit"\n',
        encoding="utf-8",
    )
    assert violations_in(sample) == []


def test_the_declared_dependency_set_pulls_no_inference_stack() -> None:
    """ADR-0016: ``pip install cashkit`` never pulls an inference stack.

    Every extra is checked, not only the core set: an optional dependency is
    still a supported code path, which is exactly why the ADR rejected a
    ``cashkit[llm]`` extra.
    """
    manifest = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))
    project = manifest["project"]
    declared = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        declared.extend(extra)
    for requirement in declared:
        name = re.split(r"[<>=!\[; ]", requirement, maxsplit=1)[0].strip().lower()
        assert name.replace("-", "_") not in BANNED_ROOTS, requirement


def test_the_linted_tree_is_not_empty() -> None:
    """Guard against the sweep silently sweeping nothing."""
    assert len(list(PACKAGE_ROOT.rglob("*.py"))) > 20
