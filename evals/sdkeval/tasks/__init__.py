"""Task families and suite generation.

A suite is ``variants`` seeded draws of every selected family. Seeds are the
string ``"<family>:<variant>"``, so a given ``(family, variant)`` is the same
task on every machine and every run — a model's score on ``escalating_rent#3``
today and next month is a comparison of the model, not of the draw.

**Splits.** Variants ``0 … HELDOUT_FROM-1`` are ``dev``; variants from
``HELDOUT_FROM`` up are ``heldout``. Iterate on prompts, docs or fine-tuning
data against ``dev`` only, and report ``heldout`` — a score on tasks you tuned
against measures memory, not ability.
"""

from __future__ import annotations

import random
from dataclasses import replace

from ..schema import TaskInstance
from ._common import FAMILIES, Family
from . import advanced, construction, formula, ledger, query, repair, scenario, settlement, tax, workflow  # noqa: F401  (registration)

HELDOUT_FROM = 1000


def instance(name: str, variant: int) -> TaskInstance:
    fam = FAMILIES[name]
    rng = random.Random(f"{name}:{variant}")
    inst = fam.generate(rng, variant)
    split = "heldout" if variant >= HELDOUT_FROM else "dev"
    return replace(inst, split=split, difficulty=fam.difficulty)


def suite(
    *,
    families: list[str] | None = None,
    categories: list[str] | None = None,
    variants: int = 3,
    split: str = "dev",
    max_difficulty: int | None = None,
) -> list[TaskInstance]:
    """Instances of every selected family. ``split`` is ``dev``, ``heldout`` or ``all``."""
    names = sorted(FAMILIES)
    if families:
        unknown = sorted(set(families) - set(FAMILIES))
        if unknown:
            raise KeyError(f"unknown families {unknown}; have {names}")
        names = [n for n in names if n in families]
    if categories:
        names = [n for n in names if FAMILIES[n].category in categories]
    if max_difficulty is not None:
        names = [n for n in names if FAMILIES[n].difficulty <= max_difficulty]
    if split == "dev":
        indices = list(range(variants))
    elif split == "heldout":
        indices = list(range(HELDOUT_FROM, HELDOUT_FROM + variants))
    elif split == "all":
        indices = list(range(variants)) + list(range(HELDOUT_FROM, HELDOUT_FROM + variants))
    else:
        raise ValueError(f"split must be dev, heldout or all, not {split!r}")
    return [instance(name, v) for name in names for v in indices]


__all__ = ["FAMILIES", "Family", "HELDOUT_FROM", "instance", "suite"]
