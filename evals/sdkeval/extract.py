"""Pull the script out of a model reply."""

from __future__ import annotations

import ast
import re

_FENCE = re.compile(r"```(?P<lang>[A-Za-z0-9_+-]*)[ \t]*\n(?P<body>.*?)```", re.DOTALL)


def extract_code(reply: str) -> str | None:
    """The script in ``reply``, or ``None`` when there is none.

    Preference: the **last** ```python (or ```py) block — models that show a
    draft then a fix put the fix last; then the last unlabelled block; then the
    whole reply, if it parses as Python and is not just prose.
    """
    if not reply:
        return None
    blocks = [(m.group("lang").lower(), m.group("body")) for m in _FENCE.finditer(reply)]
    python = [body for lang, body in blocks if lang in ("python", "py", "python3")]
    if python:
        return python[-1].strip("\n") + "\n"
    bare = [body for lang, body in blocks if lang == ""]
    if bare:
        return bare[-1].strip("\n") + "\n"
    text = reply.strip()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    # A bare expression or a single name is prose that happens to parse.
    if not any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.Expr)) for node in tree.body):
        return None
    if all(isinstance(node, ast.Expr) and isinstance(node.value, ast.Name) for node in tree.body):
        return None
    return text + "\n"
