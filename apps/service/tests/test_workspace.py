"""The workspace invariant.

The app track must never change what a bare ``uv run pytest`` runs. That is a
promise to the engine track, so it is asserted rather than remembered.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_bare_pytest_still_runs_only_the_engine_suite():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


def test_the_service_is_a_workspace_member():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert "apps/service" in config["tool"]["uv"]["workspace"]["members"]


def test_the_service_never_imports_a_model_client():
    """S1 is model-free (PROMPT session table, S1 scope).

    No model key, no model call, no prompt text anywhere in this package. The
    grep is the gate; S2 adds the model layer in its own module tree.
    """
    package = Path(__file__).resolve().parents[1] / "cashkit_service"
    banned = ("openrouter", "openai", "anthropic", "OPENROUTER_API_KEY")
    offenders = [
        f"{path.relative_to(package)}: {needle}"
        for path in package.rglob("*.py")
        for needle in banned
        if needle.lower() in path.read_text().lower()
    ]
    assert offenders == [], offenders
