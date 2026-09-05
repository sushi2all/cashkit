"""Construction (PRD §6.1): how a book comes into existence, and what a write admits.

:func:`create_book` is the one verb here. Authoring after that is
:meth:`~cashkit.sdk.kit.CashKit.set_item` and friends, addressed by scenario
(ADR-0031): base's content is the top-level book and every other scenario is an
overlay (ADR-0007), and the one write router that knows the difference is
:class:`~cashkit.sdk.scenarios.ScenarioSet`. This module keeps the **admission
rule** those writes share.

**What refuses and what reports.** Every operation returns diagnostics rather
than raising, but they divide in two, and the line is where the problem lives:

* A write is **refused** when the thing being written is wrong *in isolation* —
  a formula that does not parse, a settlement term list that cannot mean
  anything, an amount whose sign contradicts ``direction``, a generative stock.
  Nothing a later write can do makes these right, so recording them would only
  put a known-broken value in the book.
* A write is **recorded, with diagnostics**, when the problem is about the book
  as a whole — an unknown reference, a cycle, a selector matching nothing, an
  unknown param. These resolve as the book grows, and refusing them would make
  legal books unconstructible: two items in a genuine ``prev()`` feedback set
  each reference the other, so under a refusing rule neither could ever be added
  first.

Either way the diagnostic arrives **at call time**, which is the PRD §6.1
requirement ("formula parsed + DAG-checked NOW") and the reason an agent can
loop on the result instead of discovering the problem in a run three steps
later. ``validate()`` still runs the same checks before a commit; this surface
only moves the news forward.

**No wall clock, no content.** ``cutover`` is authored, never ``today()``
(ADR-0010), and nothing here knows what a tax is called anywhere (ADR-0021).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from pydantic import ValidationError

from cashkit.engine.calendars import PeriodIndex
from cashkit.engine.expand import INVALID, classify_settlement
from cashkit.engine.formula import parse_formula
from cashkit.engine.graph import DERIVED_KINDS, compile_book, sign_conflict
from cashkit.model import (
    Book,
    CalendarSpec,
    Diagnostic,
    Grain,
    Item,
    Money,
    PeriodRange,
    TaxRegime,
)
from cashkit.model.diagnostics import make_diagnostic
from cashkit.model.primitives import ScenarioId

if TYPE_CHECKING:  # pragma: no cover - import cycle: kit imports this module
    from cashkit.stores.config import EngineSettings
    from cashkit.stores.ledger import LedgerStore
    from cashkit.stores.revisions import RevisionStore

    from .kit import CashKit

__all__ = [
    "create_book",
    "isolated_problems",
    "new_compile_problems",
    "regime_problem",
    "resolve_holidays",
    "validated_params",
]


# --------------------------------------------------------------------------- #
# Calendar resolution (ADR-0010)
# --------------------------------------------------------------------------- #


def resolve_holidays(country: str | None, horizon: PeriodRange) -> list[date]:
    """Resolve a country's holidays for the whole horizon, once, at creation.

    ADR-0010: ``CalendarSpec.holidays`` is a **resolved and committed** list and
    the ``holidays`` package is a seed the runtime never consults, so a version
    bump of that package cannot change a historical run. Only the horizon's own
    years are resolved, and only days inside ``[start, end)`` are kept.

    Returns an empty list when no country is given or the package does not know
    it — an unknown country code is not a reason to refuse a book, and the
    absence is visible in the committed calendar. Produces no diagnostics.
    """
    if not country:
        return []
    try:
        import holidays as holidays_package
    except ImportError:  # pragma: no cover - the package is a core dependency
        return []
    years = range(horizon.start.year, horizon.end.year + 1)
    try:
        found = holidays_package.country_holidays(country, years=list(years))
    except (KeyError, NotImplementedError):
        return []
    return sorted(day for day in found if horizon.start <= day < horizon.end)


def _calendar_for(
    calendar: CalendarSpec | str | None, horizon: PeriodRange
) -> CalendarSpec:
    """The calendar a new book gets, with its holiday set resolved and frozen.

    A bare country code becomes a ``CalendarSpec`` for it; a ``CalendarSpec``
    that names a country but carries no holidays has them resolved here, so
    every path into book creation produces the same committed calendar.
    """
    if calendar is None:
        return CalendarSpec()
    if isinstance(calendar, str):
        return CalendarSpec(
            country=calendar, holidays=resolve_holidays(calendar, horizon)
        )
    if calendar.country and not calendar.holidays:
        return calendar.model_copy(
            update={"holidays": resolve_holidays(calendar.country, horizon)}
        )
    return calendar


# --------------------------------------------------------------------------- #
# create_book
# --------------------------------------------------------------------------- #


def create_book(
    root: str | Path,
    *,
    id: str,
    horizon: PeriodRange,
    opening_balance: Money,
    grain: Grain = Grain.DAY,
    calendar: CalendarSpec | str | None = None,
    cutover: date | None = None,
    params: Mapping[str, Decimal] | None = None,
    settings: "EngineSettings | None" = None,
    ledger: "LedgerStore | None" = None,
    revisions: "RevisionStore | None" = None,
    base_id: ScenarioId = "base",
) -> tuple["CashKit | None", tuple[Diagnostic, ...]]:
    """Create a book and the §3.3 layout holding it (PRD §6.1).

    ``root`` is where the book lives; the PRD signature omits it because it
    describes the model, not its storage, and storage stays swappable —
    ``ledger`` and ``revisions`` are constructor arguments precisely so a second
    backend needs no change here.

    ``calendar`` accepts a country code or a whole ``CalendarSpec``; either way
    the holiday set is resolved for the horizon and **committed** (ADR-0010), so
    a run reproduces whatever the ``holidays`` package later decides.
    ``cutover`` defaults to the horizon start — never to today, which this
    package cannot read.

    Nothing is committed: the layout is written and the caller decides whether
    an empty book deserves a revision.

    Returns ``(kit, diagnostics)`` — the same shape as
    :meth:`~cashkit.sdk.kit.CashKit.open` and :meth:`~cashkit.sdk.kit.ReadOnlyKit.at`
    (ADR-0032) — with ``kit`` ``None`` when creation was refused. Diagnostics: ``CK-E031`` when a book already exists at ``root``
    (PRD §9.6 rule 2 — open it, do not create a second one), ``CK-E032`` when an
    argument cannot make a Book (a malformed id, a horizon that is not
    ``start < end``, money past 4 decimal places).
    """
    from cashkit.stores.config import EngineSettings, is_book_root

    from .kit import CashKit

    root = Path(root)
    if is_book_root(root):
        return None, (make_diagnostic("CK-E031", path=str(root)),)
    try:
        book = Book(
            id=id,
            base_grain=Grain(grain),
            calendar=_calendar_for(calendar, horizon),
            horizon=horizon,
            opening_balance=opening_balance,
            cutover=cutover if cutover is not None else horizon.start,
            params=dict(params or {}),
        )
    except (ValidationError, ValueError) as exc:
        return None, (make_diagnostic("CK-E032", reason=reason_of(exc)),)
    kit = CashKit.init(
        root,
        book,
        settings=settings or EngineSettings(),
        ledger=ledger,
        revisions=revisions,
        base_id=base_id,
    )
    return kit, ()


def reason_of(exc: Exception) -> str:
    """One line naming what an argument got wrong, without a stack trace."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'book'}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)


# --------------------------------------------------------------------------- #
# Admission: what refuses, what reports
# --------------------------------------------------------------------------- #


def isolated_problems(item: Item) -> tuple[Diagnostic, ...]:
    """Everything wrong with ``item`` that no other item could put right.

    Kind/formula/segments consistency and formula parsing are checked with the
    engine's own routines and reported with its own wording — a construction
    surface that disagreed with the compiler about whether a formula is a
    formula would be worse than one that did not check at all (the D-P10-01
    argument, applied one step earlier).
    """
    found: list[Diagnostic] = []

    if item.kind == "stock" and item.segments:
        # One modelling mistake, one code (D-P10-03): the compiler also calls
        # this a formula/segments inconsistency, and CK-E012 is the code that
        # names it.
        found.append(make_diagnostic("CK-E012", item_id=item.id, field="kind"))
    elif item.kind in DERIVED_KINDS and item.segments:
        found.append(
            make_diagnostic(
                "CK-E003",
                item_id=item.id,
                field="segments",
                reason=(
                    f"kind={item.kind!r} takes its value from a formula, so it "
                    "must have no segments"
                ),
            )
        )

    if item.kind in DERIVED_KINDS:
        if item.formula is None:
            found.append(
                make_diagnostic(
                    "CK-E003",
                    item_id=item.id,
                    field="formula",
                    reason=f"kind={item.kind!r} requires a formula",
                )
            )
        else:
            found.extend(parse_formula(item.formula, item_id=item.id).diagnostics)
    elif item.formula is not None:
        found.append(
            make_diagnostic(
                "CK-E003",
                item_id=item.id,
                field="formula",
                reason="formula is valid on derived and stock items only",
            )
        )

    kind, settlement_problems = classify_settlement(item)
    if kind == INVALID:
        found.extend(settlement_problems)

    wrong = sign_conflict(item)
    if wrong is not None:
        found.append(
            make_diagnostic(
                "CK-E011", item_id=item.id, field=wrong, direction=item.direction
            )
        )
    return tuple(found)


def _key(diagnostic: Diagnostic) -> tuple[str, str | None, str | None, str]:
    return (diagnostic.code, diagnostic.item_id, diagnostic.field, diagnostic.message)


def new_compile_problems(before: Book, after: Book) -> tuple[Diagnostic, ...]:
    """Compile diagnostics ``after`` produces that ``before`` did not.

    The delta, not the whole list: an add must never be blamed for a problem
    that was already there, and must never hide one it introduced somewhere
    else — an item whose currency breaks an existing ``agg()`` is reported on
    the aggregating item, which is where the mistake now lives.

    Compilation parses formulas, resolves references and condenses the graph;
    it evaluates nothing, so this is the cheap half of a run.
    """
    periods = PeriodIndex.build(
        after.horizon, after.base_grain, after.calendar.fiscal_year_start_month
    )
    known = {_key(d) for d in compile_book(before, periods).diagnostics}
    return tuple(
        d for d in compile_book(after, periods).diagnostics if _key(d) not in known
    )


def validated_params(params: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Run a param map through the Book field's own validation.

    ``model_copy`` skips validation, so a key that formulas could not address as
    ``p.<key>`` would otherwise reach the store and fail at load time, far from
    the call that wrote it. Raises ``ValidationError`` / ``ValueError``; the
    caller turns that into ``CK-E007``.
    """
    from cashkit.stores.config import ParamsFile

    return dict(ParamsFile(params=dict(params)).params)


def regime_problem(regime: TaxRegime) -> Diagnostic | None:
    """``CK-E019`` when ``regime`` is unusable on its own terms, else ``None``.

    ``credit_handling="refund_annual"`` with no ``annual_adjustment_month``, or
    an ``accumulates`` selector that does not parse. A selector that parses but
    matches nothing in *this* book is not this function's business — that is a
    statement about the book as a whole, reported by compilation and settled by
    a later write.
    """
    from cashkit.engine.formula import parse_selector
    from cashkit.engine.tax import _configuration_problem

    problem = _configuration_problem(regime)
    if problem is not None:
        return problem
    if not regime.accumulates.strip():
        return None
    selector, reason = parse_selector(regime.accumulates)
    if selector is not None:
        return None
    return make_diagnostic(
        "CK-E019",
        field=f"tax_regimes[{regime.id}].accumulates",
        regime_id=regime.id,
        reason=reason or "unparseable selector",
    )
