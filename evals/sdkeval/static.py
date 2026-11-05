"""Static hygiene rules over the candidate's code (AST, not regex, where it matters).

These encode the PRD §9.3 agent rules and the repo's non-negotiables that can
be read off the code itself. They are *hygiene* checks: a script can reach the
right numbers while breaking one, and the report counts them separately so a
model that is accurate but sloppy is visible as such.
"""

from __future__ import annotations

import ast

#: Store files an agent must never touch directly (PRD §9.3 rule 1).
_STORE_MARKERS = (".yaml", ".yml", "ledger.sqlite", "frames.duckdb", ".cashkit", "book.yaml")
_GIT_MARKERS = ("git ", "git\t", "pygit2", ".git/")
_CLOCK_CALLS = {("date", "today"), ("datetime", "now"), ("datetime", "today"), ("datetime", "utcnow"), ("time", "time")}


def _parse(code: str) -> ast.AST | None:
    try:
        return ast.parse(code)
    except SyntaxError:
        return None


def _float(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            out.append(f"float literal {node.value!r} at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
            out.append(f"float() at line {node.lineno}")
    return out


def _store_access(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if any(marker in text for marker in _STORE_MARKERS) and len(text) < 200:
                out.append(f"store path {text!r} at line {node.lineno}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                if name.split(".")[0] in {"sqlite3", "duckdb", "yaml"}:
                    out.append(f"imports {name} at line {node.lineno}")
    return out


def _git(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                if name.split(".")[0] in {"subprocess", "pygit2", "git"}:
                    out.append(f"imports {name} at line {node.lineno}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(node.value.startswith(m) or f" {m}" in node.value for m in _GIT_MARKERS):
                out.append(f"git command {node.value!r} at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "system":
            out.append(f"os.system at line {node.lineno}")
    return out


def _wall_clock(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            name = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else None
            if (name, node.func.attr) in _CLOCK_CALLS:
                out.append(f"{name}.{node.func.attr}() at line {node.lineno}")
    return out


RULES = {"float": _float, "store_access": _store_access, "git": _git, "wall_clock": _wall_clock}


def scan(code: str, rule: str) -> list[str]:
    """Findings for ``rule`` in ``code``; empty means clean. Unparseable code is clean
    here — the syntax error is reported as the sample's failure, not as hygiene."""
    tree = _parse(code)
    if tree is None:
        return []
    return RULES[rule](tree)
