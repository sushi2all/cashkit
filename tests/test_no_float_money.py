"""No-float-in-money-paths type audit (Definition of done).

Two layers, covering every module that touches money:

1. Source audit: the identifier ``float`` never appears in ``cashkit/model/``
   or ``cashkit/engine/`` source at all (annotation, call, or reference).
2. Type audit: no Pydantic model field's resolved annotation reaches ``float``
   anywhere in its type tree.
3. Behavioral: money fields reject float input outright.

The audit covers ``model/``, ``engine/`` and ``reference/`` — every money
path there is. Any exception must be argued in DECISIONS.md and allowlisted
here explicitly.
"""

from __future__ import annotations

import ast
import typing
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import cashkit
import cashkit.model as model_pkg
from cashkit.model import Event, Segment, Amount, Recurrence, Grain

PACKAGE_ROOT = Path(cashkit.__file__).parent
AUDITED_DIRS = ("model", "engine", "reference", "stores")


#: The single allowed occurrence of the identifier ``float``: the boundary
#: guard that *rejects* float input (``_reject_float`` in primitives.py).
#: Anything else is a violation. Extending this allowlist requires a
#: DECISIONS.md entry.
ALLOWED_FLOAT_LINES = {"isinstance(value, float)"}


def test_float_identifier_absent_from_source() -> None:
    violations: list[str] = []
    for dirname in AUDITED_DIRS:
        for path in sorted((PACKAGE_ROOT / dirname).rglob("*.py")):
            source_lines = path.read_text(encoding="utf-8").splitlines()
            tree = ast.parse("\n".join(source_lines), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "float":
                    line = source_lines[node.lineno - 1].strip()
                    if any(allowed in line for allowed in ALLOWED_FLOAT_LINES):
                        continue
                    violations.append(f"{path}:{node.lineno}: 'float'")
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    violations.append(f"{path}:{node.lineno}: float literal {node.value}")
    assert not violations, "float in money-path source:\n" + "\n".join(violations)


def _all_model_classes() -> list[type[BaseModel]]:
    classes: list[type[BaseModel]] = []
    for name in dir(model_pkg):
        obj = getattr(model_pkg, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            classes.append(obj)
    assert len(classes) >= 15, "model class discovery is broken"
    return classes


def _contains_float(annotation: object, seen: set[object]) -> bool:
    if annotation in seen:
        return False
    seen.add(annotation)
    if annotation is float:
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return any(
            _contains_float(field.annotation, seen)
            for field in annotation.model_fields.values()
        )
    return any(
        _contains_float(arg, seen) for arg in typing.get_args(annotation)
    )


def test_no_model_field_type_reaches_float() -> None:
    offenders = [
        f"{cls.__name__}.{name}"
        for cls in _all_model_classes()
        for name, field in cls.model_fields.items()
        if _contains_float(field.annotation, set())
    ]
    assert not offenders, f"float reachable from model fields: {offenders}"


def test_money_fields_reject_float_input() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(
            {"id": "e", "date": date(2026, 1, 1), "amount": 10.1, "status": "actual"}
        )
    with pytest.raises(ValidationError):
        Segment.model_validate(
            {
                "start": date(2026, 1, 1),
                "recurrence": Recurrence(every=1, unit=Grain.MONTH),
                "amount": Amount(constant=Decimal("1")),
                "probability": 0.5,
            }
        )
