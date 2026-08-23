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


PACKAGE = Path(__file__).resolve().parents[1] / "cashkit_service"

#: Anything that names a model provider. S1 banned these from the whole
#: package because S1 was model-free; S2 added the agent layer, so the rule
#: narrows to a boundary rather than disappearing.
PROVIDER_NEEDLES = ("openrouter", "openai", "anthropic", "OPENROUTER_API_KEY")

#: Where a provider may be named: the agent layer, the configuration that
#: names one, and the wiring that builds one. Nowhere else.
PROVIDER_MODULES = {"config.py", "app.py"}


def _may_name_a_provider(relative: str) -> bool:
    return relative.startswith("agent/") or relative in PROVIDER_MODULES


def test_the_model_provider_is_confined_to_the_agent_layer():
    """The deterministic core stays deterministic (SPEC §2.1, ADR-0016).

    Nothing under ``cashkit/`` may know a model exists, and inside the service
    only the agent layer may. A read endpoint, the applier, the proposal store
    and the engine wrappers are computed truth; a provider name appearing in
    one of them would mean a model had reached the money path.
    """
    offenders = sorted(
        f"{path.relative_to(PACKAGE)}: {needle}"
        for path in PACKAGE.rglob("*.py")
        if not _may_name_a_provider(str(path.relative_to(PACKAGE)))
        for needle in PROVIDER_NEEDLES
        if needle.lower() in path.read_text().lower()
    )
    assert offenders == [], offenders


def test_the_engine_never_learns_about_a_model():
    """ADR-0016: the engine and SDK never call a model, and never will."""
    engine = REPO_ROOT / "cashkit"
    offenders = sorted(
        f"{path.relative_to(engine)}: {needle}"
        for path in engine.rglob("*.py")
        for needle in PROVIDER_NEEDLES
        if needle.lower() in path.read_text().lower()
    )
    assert offenders == [], offenders


def test_no_module_outside_the_agent_layer_imports_the_transport():
    """One door to the provider, and the routers do not open it themselves."""
    allowed = {"app.py", "routers/turns.py"}
    offenders = sorted(
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if not str(path.relative_to(PACKAGE)).startswith("agent/")
        and str(path.relative_to(PACKAGE)) not in allowed
        and "agent.transport" in path.read_text()
    )
    assert offenders == [], offenders
