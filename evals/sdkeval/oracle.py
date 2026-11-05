"""Independent arithmetic for expected values — ``Decimal`` and ``date`` only.

Nothing here imports ``cashkit``. A family states its expected figures by
re-deriving them from the numbers it wrote into the prompt, so a check that
passes means the candidate's book *agrees with an independent computation*, not
with whatever the engine produced. The reference-solution self-test
(``tests/test_reference_solutions.py``) is what keeps this module and the
engine honest with each other: if they disagree, that test fails and someone
has to decide which one is wrong.

Conventions mirror the PRD where they matter: money at 4 dp, half-up
(PRD §5.3); durations are calendar-semantic with the day clamped to month end
(PRD §4.0); horizons are half-open ``[start, end)``.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

Q = Decimal("0.0001")
ZERO = Decimal("0")


def money(value: Decimal | int | str) -> Decimal:
    """Round to 4 dp half-up — the engine's declared default policy."""
    return Decimal(value).quantize(Q, rounding=ROUND_HALF_UP)


def text(value: Decimal) -> str:
    """Canonical string for a money figure inside a check."""
    return str(money(value))


def add_months(day: date, months: int) -> date:
    """Calendar months, day clamped to month end (``"2m"`` semantics)."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def add_duration(day: date, duration: str) -> date:
    """``"<n>d" | "<n>w" | "<n>m" | "<n>y"`` added to ``day``."""
    n, unit = int(duration[:-1]), duration[-1]
    if unit == "d":
        return day + timedelta(days=n)
    if unit == "w":
        return day + timedelta(weeks=n)
    if unit == "m":
        return add_months(day, n)
    if unit == "y":
        return add_months(day, 12 * n)
    raise ValueError(f"bad duration {duration!r}")


def month_starts(start: date, end: date) -> list[date]:
    """First days of every month in ``[start, end)``; ``start`` is a month start."""
    out, day = [], start
    while day < end:
        out.append(day)
        day = add_months(day, 1)
    return out


def month_of(day: date) -> date:
    return date(day.year, day.month, 1)


def iso(day: date) -> str:
    return day.isoformat()


def monthly_occurrences(start: date, end: date | None, horizon_end: date, every: int = 1) -> list[date]:
    """Occurrence dates of a period-start monthly recurrence in ``[start, end)``."""
    stop = horizon_end if end is None else min(end, horizon_end)
    out, k = [], 0
    while True:
        day = add_months(start, k * every)
        if day >= stop:
            return out
        out.append(day)
        k += 1


def escalated(base: Decimal, rate: Decimal, occurrence: date, segment_start: date, every_years: int = 1) -> Decimal:
    """``base × (1+rate)^n`` with ``n`` completed escalation steps since segment start.

    The factor is exact Decimal and the product rounds once to 4 dp — the
    ADR-0002 order (escalation is the first rounding boundary).
    """
    months = (occurrence.year - segment_start.year) * 12 + occurrence.month - segment_start.month
    if occurrence.day < segment_start.day:
        months -= 1
    steps = months // (12 * every_years)
    return money(base * (1 + rate) ** steps)


def series(periods: list[date], flows: dict[date, Decimal]) -> dict[str, str]:
    """Map every period to its money figure (zero where nothing lands)."""
    return {iso(p): text(flows.get(p, ZERO)) for p in periods}


def bucket(flows: list[tuple[date, Decimal]], periods: list[date], horizon_end: date) -> dict[date, Decimal]:
    """Sum dated flows into month periods, dropping what lands past the horizon."""
    out: dict[date, Decimal] = {}
    for day, amount in flows:
        if day >= horizon_end or day < periods[0]:
            continue
        key = month_of(day)
        out[key] = out.get(key, ZERO) + amount
    return out


def balances(opening: Decimal, periods: list[date], flows: dict[date, Decimal]) -> dict[date, Decimal]:
    """Closing balance of every period."""
    out, running = {}, Decimal(opening)
    for p in periods:
        running += flows.get(p, ZERO)
        out[p] = running
    return out


def total(flows: dict[date, Decimal]) -> Decimal:
    return sum(flows.values(), ZERO)


def add(*maps: dict[date, Decimal]) -> dict[date, Decimal]:
    out: dict[date, Decimal] = {}
    for m in maps:
        for k, v in m.items():
            out[k] = out.get(k, ZERO) + v
    return out


# -- prompt rendering ------------------------------------------------------ #

MONTH_NAMES = [calendar.month_name[i] for i in range(13)]


def eur(value: Decimal) -> str:
    """``€1,234.50`` — how a person writes money in a request."""
    value = Decimal(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value == value.to_integral_value():
        return f"{sign}€{int(value):,}"
    return f"{sign}€{value:,.2f}"


def pct(rate: Decimal) -> str:
    """``0.035`` → ``3.5%``."""
    value = (Decimal(rate) * 100).normalize()
    return f"{value:f}%"


def month_name(day: date, *, year: bool = True) -> str:
    return f"{MONTH_NAMES[day.month]} {day.year}" if year else MONTH_NAMES[day.month]


def long_date(day: date) -> str:
    return f"{day.day} {MONTH_NAMES[day.month]} {day.year}"
