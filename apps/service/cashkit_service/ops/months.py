"""A month with no day, and the book's answer for which day it means.

The engine has no such thing as "March": `Segment.start` is a date, and the day
it carries is load-bearing — it sets the recurrence phase, the escalation
anniversary, and, through settlement, which month the cash lands in. So a day
has to come from somewhere when the user says "rent is 950 a month" and names
none.

Before this module the assistant chose one, silently, and its choice was
invisible. Now it may write the month alone (`"2026-03"`) and the host fills the
day from the book's `accounting_day` — a setting the user can see and change
(SPEC §6-S15). The model states what it was told; the book states the
convention; neither guesses on behalf of the other.

**Two kinds of answer, and only one of them is a day.** `"1"`..`"28"` is a fixed
day. `"eom"` is the last day of whichever month the occurrence falls in — 28,
29, 30 or 31 — which is not a day at all but the engine's own recurrence anchor
(`Recurrence.anchor = "eom"`, `engine/calendars.py`). Expanding `eom` to a date
alone would be wrong for every later occurrence: a line anchored on 30 April
steps to 30 May, not to the 31st. So `eom` does two things — it dates the first
occurrence on that month's last day, **and** it stamps `anchor: "eom"` on the
operation so the engine keeps landing on month ends.

**An exclusive end is expanded to the first of the month, never to the
accounting day.** `end` means "the line stops here" — a line ending "2026-07"
runs through June, so the boundary is 1 July whatever day the book counts from.
Expanding it to the 27th would keep July's occurrence and silently add a period.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as _dt
import re
from typing import Any

__all__ = ["EOM", "MONTH_ONLY", "expand_months", "is_month", "month_only_slots", "parse_accounting_day"]

MONTH_ONLY = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

#: The stored token for "the last day of the month, whichever day that is".
EOM = "eom"

#: Per operation, the slots naming the day a line happens. They take the
#: book's accounting day. Only a line's `start` qualifies: every other date is
#: either a boundary or the day of a real record.
_DAY_SLOTS: dict[str, tuple[str, ...]] = {"add_item": ("start",)}
#: Per operation, the slots naming a boundary. They take the first: `end` is a
#: boundary the line does not reach, `from_date` splits a line so the new amount
#: covers that whole month, and a cutover drops nothing inside its own month.
_BOUNDARY_SLOTS: dict[str, tuple[str, ...]] = {
    "add_item": ("end",),
    "set_amount": ("from_date",),
    "set_cutover": ("date",),
}
#: Recurrence units the end-of-month anchor makes sense for. A weekly line
#: anchored to month end would pay four times on the 31st.
_NO_EOM_UNITS = ("d", "w")


def is_month(value: Any) -> bool:
    """True for `"2026-03"`, false for a date, a number or anything else."""
    return isinstance(value, str) and MONTH_ONLY.match(value) is not None


def parse_accounting_day(stored: Any) -> int | str:
    """The book's setting as either a day in 1..28 or :data:`EOM`.

    Anything unreadable falls back to the first of the month rather than
    raising: a preference that cannot be parsed must not cost the user a turn.
    """
    if isinstance(stored, str) and stored.strip().lower() == EOM:
        return EOM
    try:
        day = int(stored)
    except (TypeError, ValueError):
        return 1
    return min(max(day, 1), 28)


def expand_months(operation: dict[str, Any], accounting_day: int | str) -> dict[str, Any]:
    """Return ``operation`` with any month-only date slot resolved to a date.

    Anything that is not a month-only string is passed through untouched, so
    this is safe to run over every operation before the typed grammar sees it:
    an operation that already carries real dates is returned unchanged — and in
    particular keeps whatever anchor it already had.
    """
    day = parse_accounting_day(accounting_day)
    resolved = dict(operation)
    expanded_a_start = False
    op = operation.get("op")

    for slot in _DAY_SLOTS.get(op, ()):
        match = MONTH_ONLY.match(resolved[slot]) if is_month(resolved.get(slot)) else None
        if match is None:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        if day == EOM:
            resolved[slot] = _dt.date(year, month, _calendar.monthrange(year, month)[1]).isoformat()
        else:
            resolved[slot] = _dt.date(year, month, day).isoformat()
        expanded_a_start = expanded_a_start or slot == "start"

    for slot in _BOUNDARY_SLOTS.get(op, ()):
        match = MONTH_ONLY.match(resolved[slot]) if is_month(resolved.get(slot)) else None
        if match:
            resolved[slot] = _dt.date(int(match.group(1)), int(match.group(2)), 1).isoformat()

    # Only a line the host dated itself gets the anchor. A start the user named
    # ("from 31 January") is theirs, and turning it into "every month end" would
    # be the host deciding what they meant.
    recurrence = str(resolved.get("recurrence") or "1m").strip().lower()
    if day == EOM and expanded_a_start and recurrence[-1:] not in _NO_EOM_UNITS:
        resolved["anchor"] = EOM
    return resolved


def month_only_slots(operation: dict[str, Any]) -> list[str]:
    """The slots still holding a month with no day after :func:`expand_months`.

    Those are dates of real records (an event, an actual): the host will not
    invent a day for something that happened, so the caller refuses them.
    """
    return [slot for slot, value in operation.items() if is_month(value)]
