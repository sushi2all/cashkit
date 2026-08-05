"""Canonical YAML serialization (Phase 1 gate).

One byte-stable canonical form per model:

- fixed field order per model (the declaration order of its Pydantic fields);
- Decimals as quoted strings (``str(Decimal)`` — exponent and trailing zeros
  preserved, so ``"0.10"`` never phantom-diffs into ``"0.1"``);
- dates as quoted ISO strings;
- ``None``-valued fields omitted (except *recorded* ``None`` on sparse
  overlays, emitted as an explicit ``null``);
- user-data mapping keys always double-quoted and sorted; field-name keys
  bare; sets emitted as sorted block sequences;
- no flow-style collections, except the unavoidable ``[]`` / ``{}`` for empty
  collections (which have no block representation — see DECISIONS.md);
- ``Amount.schedule`` entries as ``{date, amount}`` maps (legible git diffs);
- LF line endings, exactly one trailing newline.

Never ``yaml.dump()`` on a model: the emitter below is hand-written so the
canonical form is defined by this module, not by a library's defaults.
Parsing uses ``yaml.safe_load`` plus Pydantic validation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import TypeVar

import yaml
from pydantic import BaseModel

from .primitives import Amount, SparseOverlay

__all__ = ["from_canonical_yaml", "to_canonical_yaml"]

M = TypeVar("M", bound=BaseModel)

_INDENT = "  "

# Tokens are pre-formatted scalar strings; containers are dict / list.
_Tree = dict[str, "_Tree"] | list["_Tree"] | str

_NULL = "null"

_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "\x00": "\\0",
    "\x07": "\\a",
    "\x08": "\\b",
    "\x0b": "\\v",
    "\x0c": "\\f",
    "\x1b": "\\e",
}


def _quote(text: str) -> str:
    """Deterministically double-quote a string with YAML escapes.

    Every user string (values and mapping keys) is emitted double-quoted so
    that no string is ever mistaken for a YAML scalar of another type and the
    canonical form is independent of YAML's plain-scalar heuristics.
    """
    out: list[str] = ['"']
    for ch in text:
        escaped = _SHORT_ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
            continue
        code = ord(ch)
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
        elif (
            ch in ("\u2028", "\u2029", "\ufeff")
            or 0xD800 <= code <= 0xDFFF
            # U+FFFE / U+FFFF are the remaining characters a YAML reader refuses
            # outright. Emitting them raw produces a document that cannot be
            # parsed back, so escaping them is what makes the round trip total.
            or 0xFFFE <= code <= 0xFFFF
        ):
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _scalar_token(value: object) -> str:
    """Format a leaf value as its canonical scalar token."""
    if value is None:
        return _NULL
    if isinstance(value, bool):  # before int: bool subclasses int
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return _quote(str(value))
    if isinstance(value, date):
        return _quote(value.isoformat())
    if isinstance(value, Enum):
        return _scalar_token(value.value)
    if isinstance(value, str):
        return _quote(value)
    raise TypeError(f"no canonical scalar form for {type(value).__name__}")


def _value_to_tree(value: object, *, schedule_points: bool = False) -> _Tree:
    if isinstance(value, BaseModel):
        return _model_to_tree(value)
    if isinstance(value, dict):
        # User-data mapping: quoted keys, sorted bytewise on the raw key.
        return {
            _quote(str(k)): _value_to_tree(v)
            for k, v in sorted(value.items(), key=lambda kv: kv[0])
        }
    if isinstance(value, (set, frozenset)):
        return [_value_to_tree(v) for v in sorted(value)]
    if isinstance(value, tuple) and schedule_points:
        # ``Amount.schedule``'s ``(date, Money)`` pairs, emitted as
        # ``{date, amount}`` maps for legible git diffs. The pair form is keyed
        # off the *field* (see :func:`_model_to_tree`), never off the value's
        # shape: a value-sniffing rule would silently reinterpret any other
        # two-element tuple, and a canonical emitter must not guess.
        point_date, point_amount = value
        return {
            "date": _scalar_token(point_date),
            "amount": _scalar_token(point_amount),
        }
    if isinstance(value, (list, tuple)):
        return [
            _value_to_tree(v, schedule_points=schedule_points) for v in value
        ]
    return _scalar_token(value)


#: The one field whose list elements are ``(date, Money)`` pairs rather than
#: models or scalars. Any other tuple-valued field serializes as a sequence.
_SCHEDULE_FIELD = (Amount, "schedule")


def _model_to_tree(model: BaseModel) -> dict[str, _Tree]:
    tree: dict[str, _Tree] = {}
    sparse = isinstance(model, SparseOverlay)
    model_type = type(model)
    for name in model_type.model_fields:
        if sparse and name not in model.model_fields_set:
            continue
        value = getattr(model, name)
        if value is None and not sparse:
            continue
        tree[name] = _value_to_tree(
            value, schedule_points=(model_type, name) == _SCHEDULE_FIELD
        )
    return tree


def _emit_map(tree: dict[str, _Tree], indent: int) -> list[str]:
    pad = _INDENT * indent
    lines: list[str] = []
    for key, value in tree.items():
        if isinstance(value, dict):
            if value:
                lines.append(f"{pad}{key}:")
                lines.extend(_emit_map(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: {{}}")
        elif isinstance(value, list):
            if value:
                lines.append(f"{pad}{key}:")
                lines.extend(_emit_seq(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: []")
        else:
            lines.append(f"{pad}{key}: {value}")
    return lines


def _emit_seq(items: list[_Tree], indent: int) -> list[str]:
    pad = _INDENT * indent
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            if item:
                inner = _emit_map(item, indent + 1)
                lines.append(f"{pad}- {inner[0].lstrip()}")
                lines.extend(inner[1:])
            else:
                lines.append(f"{pad}- {{}}")
        elif isinstance(item, list):
            if item:
                inner = _emit_seq(item, indent + 1)
                lines.append(f"{pad}- {inner[0].lstrip()}")
                lines.extend(inner[1:])
            else:
                lines.append(f"{pad}- []")
        else:
            lines.append(f"{pad}- {item}")
    return lines


def to_canonical_yaml(model: BaseModel) -> str:
    """Serialize a model to its canonical YAML document.

    Returns the canonical text (LF endings, one trailing newline). The same
    model always serializes to the same bytes; produces no diagnostics.
    Raises ``TypeError`` for values outside the model vocabulary (programmer
    error).
    """
    tree = _model_to_tree(model)
    if not tree:
        return "{}\n"
    return "\n".join(_emit_map(tree, 0)) + "\n"


def from_canonical_yaml(text: str, model_type: type[M]) -> M:
    """Parse a canonical YAML document into ``model_type``.

    Returns the validated model; produces no diagnostics. Raises
    ``yaml.YAMLError`` on malformed YAML and ``pydantic.ValidationError`` on
    schema violations — the config store (Session S3+) converts both into
    catalogue diagnostics at the SDK boundary.
    """
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    return model_type.model_validate(data)
