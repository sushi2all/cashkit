"""Shared helpers for task families: registry, literals, setups, check builders."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

from ..oracle import iso, month_starts, series, text
from ..schema import Check, TaskInstance

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Family:
    name: str
    category: str
    difficulty: int
    description: str
    generate: Callable[[random.Random, int], TaskInstance]


FAMILIES: dict[str, Family] = {}


def family(name: str, category: str, difficulty: int, description: str):
    """Register ``fn(rng, variant) -> TaskInstance`` as a task family."""

    def register(fn: Callable[[random.Random, int], TaskInstance]):
        if name in FAMILIES:
            raise ValueError(f"duplicate family {name!r}")
        FAMILIES[name] = Family(name, category, difficulty, description, fn)
        return fn

    return register


def task(
    rng: random.Random,
    variant: int,
    *,
    family: str,
    category: str,
    prompt: str,
    reference: str,
    checks: list[Check],
    setup: str | None = None,
    difficulty: int = 1,
    **extra,
) -> TaskInstance:
    return TaskInstance(
        family=family,
        category=category,
        variant=variant,
        prompt=prompt.strip(),
        reference=reference.strip() + "\n",
        checks=tuple(checks),
        setup=None if setup is None else setup.strip() + "\n",
        difficulty=difficulty,
        **extra,
    )


# --------------------------------------------------------------------------- #
# Literals for generated code
# --------------------------------------------------------------------------- #


def D(value: Decimal | int | str) -> str:
    """``Decimal("1200")`` as source text — never a float literal."""
    return f'Decimal("{Decimal(value)}")'


def dt(day: date) -> str:
    return f"date({day.year}, {day.month}, {day.day})"


IMPORTS = """\
from datetime import date
from decimal import Decimal
from cashkit.sdk import CashKit, create_book, ScaleItems, ShiftItems, RetagItems
from cashkit.model import (
    Amount, CalendarSpec, DueTerm, Escalation, Event, Grain, Item, PeriodRange,
    Recurrence, Segment, Settlement, TaxRegime, VatSpec,
)
"""


def pick(rng: random.Random, *options):
    return options[rng.randrange(len(options))]


def money_draw(rng: random.Random, low: int, high: int, step: int = 50) -> Decimal:
    return Decimal(rng.randrange(low // step, high // step + 1) * step)


# --------------------------------------------------------------------------- #
# Setups
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BookSpec:
    id: str
    start: date
    end: date
    opening: Decimal
    grain: str = "MONTH"
    params: tuple[tuple[str, Decimal], ...] = ()

    @property
    def periods(self) -> list[date]:
        return month_starts(self.start, self.end)


def create_code(spec: BookSpec, root: str = "ROOT") -> str:
    params = ", ".join(f'"{k}": {D(v)}' for k, v in spec.params)
    return (
        f"kit, diagnostics = create_book(\n"
        f"    {root},\n"
        f'    id="{spec.id}",\n'
        f"    horizon=PeriodRange(start={dt(spec.start)}, end={dt(spec.end)}),\n"
        f"    opening_balance={D(spec.opening)},\n"
        f"    grain=Grain.{spec.grain},\n"
        f"    params={{{params}}},\n"
        f")\n"
        f"assert kit is not None, diagnostics\n"
    )


def setup_book(spec: BookSpec, body: str = "") -> str:
    """Setup source: create the book at ROOT, then run ``body`` against ``kit``."""
    return IMPORTS + "\n" + create_code(spec) + ("\n" + body.strip() + "\n" if body.strip() else "")


def monthly_item_code(
    item_id: str,
    name: str,
    amount: Decimal,
    start: date,
    *,
    end: date | None = None,
    direction: str | None = None,
    tags: dict[str, str] | None = None,
    extra: str = "",
    scenario: str | None = None,
) -> str:
    """Source for one constant monthly flow item written with ``kit.set_item``."""
    direction = direction or ("in" if amount > 0 else "out")
    end_src = "None" if end is None else dt(end)
    tags_src = repr(tags or {})
    scen = f', scenario="{scenario}"' if scenario else ""
    return (
        f"kit.set_item(Item(\n"
        f'    id="{item_id}", name="{name}", kind="flow", direction="{direction}", tags={tags_src},\n'
        f"    segments=[Segment(start={dt(start)}, end={end_src},\n"
        f"        recurrence=Recurrence(every=1, unit=Grain.MONTH), amount=Amount(constant={D(amount)}))],\n"
        f"    {extra}\n"
        f"){scen})\n"
    )


# --------------------------------------------------------------------------- #
# Check builders
# --------------------------------------------------------------------------- #


def c_series(item: str, periods: list[date], flows: dict[date, Decimal], *, scenario="base", measure="cash", label=None) -> Check:
    return Check(
        "series",
        {"item": item, "scenario": scenario, "measure": measure, "values": series(periods, flows)},
        label or f"{item} {measure} in every period of {scenario}",
    )


def c_total(item: str, value: Decimal, *, scenario="base", measure="cash", label=None) -> Check:
    return Check("item_total", {"item": item, "scenario": scenario, "measure": measure, "value": text(value)},
                 label or f"{item} total {measure} in {scenario}")


def c_closing(value: Decimal, scenario="base", label=None) -> Check:
    return Check("closing", {"scenario": scenario, "value": text(value)}, label or f"closing balance of {scenario}")


def c_balance(period: date, value: Decimal, scenario="base", label=None) -> Check:
    return Check("balance_at", {"scenario": scenario, "period": iso(period), "value": text(value)},
                 label or f"balance at {iso(period)} in {scenario}")


def c_no_errors(scenario="base") -> Check:
    return Check("no_errors", {"scenario": scenario}, f"{scenario} runs with no error diagnostics")


def c_field(item: str, path: str, value, *, op="eq", scenario="base", label=None, critical=True) -> Check:
    return Check("item_field", {"item": item, "path": path, "value": value, "op": op, "scenario": scenario},
                 label or f"{item}.{path} {op} {value!r}", critical=critical)


def c_exists(item: str, exists=True, scenario="base", label=None) -> Check:
    return Check("item_exists", {"item": item, "exists": exists, "scenario": scenario},
                 label or f"{item} {'exists' if exists else 'is absent'} in {scenario}")


def c_param(key: str, value: Decimal | None, scenario="base", label=None) -> Check:
    return Check("param", {"key": key, "scenario": scenario, "value": None if value is None else str(value)},
                 label or f"param {key} in {scenario}")


def c_answer(value, kind="decimal", label="the answer is right") -> Check:
    return Check("answer", {"value": text(value) if kind == "decimal" else value, "type": kind}, label)
