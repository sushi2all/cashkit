"""Wall-clock lint (non-negotiable constraint 3, ADR-0010).

Nothing in ``cashkit/`` may read the wall clock: ``date.today()``,
``datetime.now()``, ``datetime.utcnow()``, ``datetime.today()`` and
``time.time()`` are banned. ``cutover`` is a stored field; reading the clock
during evaluation destroys reproducibility.

The check is deliberately conservative: *any* attribute access named
``today`` / ``now`` / ``utcnow``, any ``time.time`` reference, and importing
``time`` from the ``time`` module are all violations, call or no call.

**Phase 9 widened the ban from five directories to the whole package, and
introduced exactly one exemption.** Two operational artifacts genuinely need a
timestamp — a commit and a writer lock — and neither is an evaluation. Rather
than let the ban decay into "except where it was inconvenient", the clock lives
in one allowlisted module, :mod:`cashkit.stores.clock`, and
:func:`test_only_operational_stores_import_the_clock` asserts that no
evaluation path can reach it. A second file needing a clock fails this lint,
which is the point.
"""

from __future__ import annotations

import ast
from pathlib import Path

import cashkit

PACKAGE_ROOT = Path(cashkit.__file__).parent

#: The whole package. ``stores`` joined the ban in Phase 5 (a ledger entry is
#: ordered by its sequence number, never by a timestamp — D-P5-01), ``sdk`` in
#: Phase 7 (a macro shifted by "today" would make a resolved book depend on
#: when it was resolved), and Phase 9 replaced the directory list with the
#: package so ``cli/`` and any future directory are covered by default.
LINTED_ROOT = PACKAGE_ROOT

#: Directories that must exist and be linted — a guard against the sweep
#: silently sweeping nothing.
LINTED_DIRS = ("cli", "engine", "model", "reference", "sdk", "stores")

#: The single exemption, with its reasoning in the module's own docstring.
CLOCK_MODULE = "stores/clock.py"

#: Modules allowed to reach :func:`cashkit.stores.clock.wall_clock`. Everything
#: that evaluates anything is absent from this list on purpose. Importing the
#: ``Timestamp`` *type* is unrestricted — a signature that accepts an injected
#: timestamp is the opposite of a module that reads one.
CLOCK_READERS = {"stores/clock.py", "stores/git_store.py", "stores/lock.py"}

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


def _relative(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).as_posix()


def test_no_wall_clock_anywhere_in_the_package() -> None:
    violations: list[str] = []
    for path in sorted(LINTED_ROOT.rglob("*.py")):
        if _relative(path) == CLOCK_MODULE:
            continue
        violations.extend(_violations_in(path))
    assert not violations, "wall-clock access in deterministic code:\n" + "\n".join(
        violations
    )


def test_the_exemption_is_a_single_named_file() -> None:
    """The allowlist is one file, and it is the one that documents why."""
    clock = PACKAGE_ROOT / CLOCK_MODULE
    assert clock.is_file()
    assert _violations_in(clock), "the exempted module should be the one reading a clock"
    source = clock.read_text(encoding="utf-8")
    assert "single exemption" in source


def test_only_operational_stores_import_the_clock() -> None:
    """Nothing that evaluates anything can reach a timestamp.

    A commit and a lock may be stamped; an item, a column, a fold and a summary
    may not. This is the compensating check that makes the exemption safe.
    """
    readers: set[str] = set()
    for path in sorted(LINTED_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "wall_clock":
                readers.add(_relative(path))
            if isinstance(node, ast.Attribute) and node.attr == "wall_clock":
                readers.add(_relative(path))
            if isinstance(node, ast.FunctionDef) and node.name == "wall_clock":
                readers.add(_relative(path))
    assert readers <= CLOCK_READERS, sorted(readers - CLOCK_READERS)
    # And the check must be able to see something, or it proves nothing.
    assert readers == CLOCK_READERS


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
