"""Wall-clock lint (non-negotiable constraint 3, ADR-0010).

Nothing in ``cashkit/engine/`` or ``cashkit/model/`` may read the wall clock:
``date.today()``, ``datetime.now()``, ``datetime.utcnow()``,
``datetime.today()``, ``time.time()`` are banned. ``cutover`` is a stored
field; reading the clock during evaluation destroys reproducibility.

The check is deliberately conservative: *any* attribute access named
``today`` / ``now`` / ``utcnow``, any ``time.time`` reference, and importing
``time`` from the ``time`` module are all violations, call or no call.
"""

from __future__ import annotations

import ast
from pathlib import Path

import cashkit

PACKAGE_ROOT = Path(cashkit.__file__).parent
LINTED_DIRS = ("engine", "model")

BANNED_ATTRIBUTES = {"today", "now", "utcnow"}


def _violations_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRIBUTES:
            found.append(f"{path}:{node.lineno}: attribute '{node.attr}'")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "time"
            and isinstance(node.value, ast.Name)
            and node.value.id == "time"
        ):
            found.append(f"{path}:{node.lineno}: time.time")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "time":
                    found.append(f"{path}:{node.lineno}: import time")
        if isinstance(node, ast.ImportFrom) and node.module == "time":
            found.append(f"{path}:{node.lineno}: from time import ...")
    return found


def test_no_wall_clock_in_engine_or_model() -> None:
    violations: list[str] = []
    for dirname in LINTED_DIRS:
        for path in sorted((PACKAGE_ROOT / dirname).rglob("*.py")):
            violations.extend(_violations_in(path))
    assert not violations, "wall-clock access in deterministic code:\n" + "\n".join(
        violations
    )


def test_lint_actually_detects_violations(tmp_path: Path) -> None:
    """The lint itself is load-bearing — prove it catches every banned form."""
    samples = [
        "from datetime import date\nx = date.today()\n",
        "from datetime import datetime\nx = datetime.now()\n",
        "from datetime import datetime\nx = datetime.utcnow()\n",
        "from datetime import datetime\nx = datetime.today()\n",
        "import time\nx = time.time()\n",
        "from time import time\nx = time()\n",
    ]
    for index, source in enumerate(samples):
        sample = tmp_path / f"sample_{index}.py"
        sample.write_text(source, encoding="utf-8")
        assert _violations_in(sample), f"lint missed: {source!r}"


def test_linted_directories_exist() -> None:
    """Guard against the lint silently linting nothing."""
    for dirname in LINTED_DIRS:
        assert (PACKAGE_ROOT / dirname).is_dir()
        assert list((PACKAGE_ROOT / dirname).rglob("*.py"))
