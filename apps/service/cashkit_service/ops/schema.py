"""The operation grammar: 21 model intents plus five host ops.

Two surfaces, one pipeline (SPEC §2.5):

* **Model intents** — the 21 of `km/notes/intent-schema-draft.md`, R1–R12 read
  and M1–M9 mutation. This is the only surface a prompt ever sees.
* **Host ops** — `set_horizon`, `set_opening_balance`, `remove_event`,
  `edit_schedule_date`, and the M5 record-actual channel. Typed, enumerated,
  and NEVER in a model prompt (D-MLP-03). They exist only on the UI→service
  path, and they flow through the same proposal pipeline as intents.

Two conventions the whole module keeps:

* ``as_of`` is not a slot. It is host-filled and never model-filled
  (ADR-0019 rule 2), so no operation here can carry one.
* money arrives as a **string**. A JSON number is a float before Pydantic sees
  it, and no float enters the money path.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

MoneyStr = Annotated[str, Field(examples=["-912.50"])]


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Every operation may name a scenario; it defaults to the active one.
    scenario: str | None = None


def _decimal(value: str, field: str) -> str:
    try:
        Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal string, got {value!r}") from exc
    return value


# --- mutation intents M1–M9 ----------------------------------------------- #


class AddItem(_Op):
    """M1 — ``add_item``."""

    op: Literal["add_item"] = "add_item"
    id: str
    name: str | None = None
    direction: Literal["in", "out"]
    amount: MoneyStr
    recurrence: str = Field(default="1m", examples=["1m", "3m", "1y"])
    start: _dt.date
    end: _dt.date | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    settlement: str | None = Field(default=None, examples=["immediate", "net30"])

    _v = field_validator("amount")(lambda cls, v: _decimal(v, "amount"))


class SetAmount(_Op):
    """M2 — ``set_amount``; ``from_date`` splits the segment."""

    op: Literal["set_amount"] = "set_amount"
    item: str
    amount: MoneyStr
    from_date: _dt.date | None = None

    _v = field_validator("amount")(lambda cls, v: _decimal(v, "amount"))


class ShiftItems(_Op):
    """M3 — ``shift_items``, the ShiftItems macro."""

    op: Literal["shift_items"] = "shift_items"
    selector: str
    by: str = Field(examples=["2m", "30d"])


class ScaleItems(_Op):
    """M4 — ``scale_items``, the ScaleItems macro."""

    op: Literal["scale_items"] = "scale_items"
    selector: str
    factor: str = Field(examples=["0.8"])

    _v = field_validator("factor")(lambda cls, v: _decimal(v, "factor"))


class AddEvent(_Op):
    """M5 — ``add_event``.

    Status is NOT a slot. The model never chooses whether something happened:
    the record-actual discriminator decides it host-side (SPEC §5-F5).
    """

    op: Literal["add_event"] = "add_event"
    date: _dt.date | None = None
    amount: MoneyStr
    direction: Literal["in", "out"] | None = None
    item: str | None = None
    note: str | None = None
    id: str | None = None

    _v = field_validator("amount")(lambda cls, v: _decimal(v, "amount"))


class CorrectActual(_Op):
    """M6 — ``correct_actual``. The note is mandatory (ADR-0012)."""

    op: Literal["correct_actual"] = "correct_actual"
    event: str
    amount: MoneyStr
    note: str = Field(min_length=1)
    date: _dt.date | None = None

    _v = field_validator("amount")(lambda cls, v: _decimal(v, "amount"))


class ForkScenario(_Op):
    """M7 — ``fork_scenario``."""

    op: Literal["fork_scenario"] = "fork_scenario"
    name: str
    parent: str | None = None
    note: str = ""


class SetCutover(_Op):
    """M8 — ``set_cutover``."""

    op: Literal["set_cutover"] = "set_cutover"
    date: _dt.date


class Save(_Op):
    """M9 — ``save``, which is ``commit()``.

    It is an intent so a turn can express it, but it does not run through the
    dry-run applier: committing is not a change to the working overlay, it is
    the act of recording one. ``POST /book/save`` is its endpoint.
    """

    op: Literal["save"] = "save"
    message: str = Field(min_length=1)


# --- host ops (SPEC §2.5, D-MLP-03) --------------------------------------- #


class SetHorizon(_Op):
    """Host op — move the book's horizon."""

    op: Literal["set_horizon"] = "set_horizon"
    start: _dt.date
    end: _dt.date


class SetOpeningBalance(_Op):
    """Host op — restate the opening balance."""

    op: Literal["set_opening_balance"] = "set_opening_balance"
    amount: MoneyStr

    _v = field_validator("amount")(lambda cls, v: _decimal(v, "amount"))


class RemoveEvent(_Op):
    """Host op — remove one event. Refused on an actual (SPEC §2.5).

    An actual is a fact. Removing the record of a fact destroys it; correcting
    it is M6, which leaves a scar (ADR-0012). The applier refuses rather than
    choosing for the user.
    """

    op: Literal["remove_event"] = "remove_event"
    event: str
    note: str = Field(default="removed from the plan", min_length=1)


class EditScheduleDate(_Op):
    """Host op — add, change or remove one explicit date on a schedule item."""

    op: Literal["edit_schedule_date"] = "edit_schedule_date"
    item: str
    action: Literal["add", "change", "remove"]
    date: _dt.date
    amount: MoneyStr | None = None
    new_date: _dt.date | None = None

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: str | None) -> str | None:
        return None if value is None else _decimal(value, "amount")


class RecordActual(_Op):
    """Host op — the M5 record-actual channel (SPEC §5-F5).

    This op *is* the ``context: "actuals_record"`` flow in typed form. Whether
    it becomes an actual or a forecast is still the discriminator's decision,
    not the caller's: a future-dated entry on this flow stays ``forecast``, and
    a missing date is a clarification, never a guess.
    """

    op: Literal["record_actual"] = "record_actual"
    date: _dt.date | None = None
    amount: MoneyStr
    direction: Literal["in", "out"] | None = None
    item: str | None = None
    note: str | None = None
    id: str | None = None

    _v = field_validator("amount")(lambda cls, v: _decimal(v, "amount"))


MutationOp = Annotated[
    Union[
        AddItem, SetAmount, ShiftItems, ScaleItems, AddEvent, CorrectActual,
        ForkScenario, SetCutover, Save,
        SetHorizon, SetOpeningBalance, RemoveEvent, EditScheduleDate, RecordActual,
    ],
    Field(discriminator="op"),
]

#: The operations a proposal may carry. `save` is deliberately absent: it is an
#: endpoint, not a working-overlay change.
PROPOSABLE_OPS = frozenset(
    {
        "add_item", "set_amount", "shift_items", "scale_items", "add_event",
        "correct_actual", "fork_scenario", "set_cutover",
        "set_horizon", "set_opening_balance", "remove_event",
        "edit_schedule_date", "record_actual",
    }
)

#: The five host ops. Never exposed to a model (D-MLP-03).
HOST_OPS = frozenset(
    {"set_horizon", "set_opening_balance", "remove_event", "edit_schedule_date", "record_actual"}
)

#: The nine mutation intents of the v0 schema.
MUTATION_INTENTS = frozenset(
    {
        "add_item", "set_amount", "shift_items", "scale_items", "add_event",
        "correct_actual", "fork_scenario", "set_cutover", "save",
    }
)

#: The twelve read intents. S1 executes them deterministically; S2 wires the
#: model surface to them (`cashkit_service.intents.read`).
READ_INTENTS = (
    "project_balance", "runway", "min_cash", "breakeven", "top_categories",
    "item_total", "explain_cell", "explain_zero", "compare_scenarios",
    "coverage", "list_items", "history",
)

__all__ = [
    "AddEvent", "AddItem", "CorrectActual", "EditScheduleDate", "ForkScenario",
    "HOST_OPS", "MUTATION_INTENTS", "MutationOp", "PROPOSABLE_OPS", "READ_INTENTS",
    "RecordActual", "RemoveEvent", "Save", "ScaleItems", "SetAmount",
    "SetCutover", "SetHorizon", "SetOpeningBalance", "ShiftItems",
]
