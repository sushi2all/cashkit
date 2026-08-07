"""The construction surface (PRD §6.1): how a book comes into existence.

Everything here writes the **authored** book — `book.yaml`, `params.yaml`,
`items/` — which is base's content at top level (ADR-0007). The scenario surface
(`set_item`, `set_param` on a scenario) writes overlays into `scenarios/`. The
API split maps 1:1 onto the §3.3 storage split, and both go through the one
owner of those structures, :class:`~cashkit.sdk.scenarios.ScenarioSet`:
construction is not a second write path, it is the other half of the same one.

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
requirement for ``add_derived`` ("formula parsed + DAG-checked NOW") and the
reason an agent can loop on the result instead of discovering the problem in a
run three steps later. ``validate()`` still runs the same checks before a
commit; this surface only moves the news forward.

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
from cashkit.engine.graph import DERIVED_KINDS, compile_book
from cashkit.model import (
    Book,
    CalendarSpec,
    ChangeReport,
    Diagnostic,
    Grain,
    Item,
    ItemId,
    ItemRef,
    Money,
    PeriodRange,
    TaxRegime,
)
from cashkit.model.diagnostics import make_diagnostic
from cashkit.model.primitives import ScenarioId

from .macros import RetagItems, resolve_selector
from .validation import _sign_conflict, cutover_problem

if TYPE_CHECKING:  # pragma: no cover - import cycle: kit imports this module
    from cashkit.stores.config import EngineSettings
    from cashkit.stores.ledger import LedgerStore
    from cashkit.stores.revisions import RevisionStore

    from .kit import CashKit

__all__ = [
    "AffectedCount",
    "BookRef",
    "add_derived",
    "add_item",
    "add_tax_regime",
    "create_book",
    "resolve_holidays",
    "retag",
    "set_cutover",
    "set_param",
]


# --------------------------------------------------------------------------- #
# Return types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BookRef:
    """A created book and the kit it is opened as (PRD §6.1 ``-> BookRef``).

    ``kit`` is ``None`` exactly when creation was refused, in which case
    ``diagnostics`` says why and nothing was written to disk. It is a live
    object rather than a serializable model for the same reason
    :class:`~cashkit.sdk.kit.RunRef` is: a reference an agent cannot act through
    is a receipt, not a handle.
    """

    kit: "CashKit | None"
    book_id: str
    root: Path
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the book was created. No diagnostics."""
        return self.kit is not None


class AffectedCount(int):
    """A count of affected items that can also say what went wrong.

    PRD §6.1 types ``retag`` as ``-> int`` and §6.5 requires every fallible
    operation to return diagnostics; a selector is fallible, so an ``int``
    alone would have to swallow ``CK-E003`` and report a malformed selector as
    "nothing matched". That is the silent-failure class this project ranks
    worst — a typo in a selector and a selector that genuinely matches nothing
    are the same number and must not be the same answer.

    This **is** the integer (``retag(...) == 3``, ``isinstance(…, int)``), with
    the diagnostics attached. A selector matching nothing is ``0`` with no
    diagnostics; a malformed one is ``0`` with ``CK-E003``.
    """

    diagnostics: tuple[Diagnostic, ...]

    def __new__(
        cls, value: int, diagnostics: tuple[Diagnostic, ...] = ()
    ) -> "AffectedCount":
        count = super().__new__(cls, value)
        count.diagnostics = diagnostics
        return count

    @property
    def ok(self) -> bool:
        """True when no error-severity diagnostic was produced. No diagnostics."""
        return not any(d.severity == "error" for d in self.diagnostics)


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
) -> BookRef:
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

    Returns a :class:`BookRef` whose ``kit`` is ``None`` when creation was
    refused. Diagnostics: ``CK-E031`` when a book already exists at ``root``
    (PRD §9.6 rule 2 — open it, do not create a second one), ``CK-E032`` when an
    argument cannot make a Book (a malformed id, a horizon that is not
    ``start < end``, money past 4 decimal places).
    """
    from cashkit.stores.config import EngineSettings, is_book_root

    from .kit import CashKit

    root = Path(root)
    if is_book_root(root):
        return BookRef(
            kit=None,
            book_id=id,
            root=root,
            diagnostics=(make_diagnostic("CK-E031", path=str(root)),),
        )
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
        return BookRef(
            kit=None,
            book_id=id,
            root=root,
            diagnostics=(make_diagnostic("CK-E032", reason=_reason(exc)),),
        )
    kit = CashKit.init(
        root,
        book,
        settings=settings or EngineSettings(),
        ledger=ledger,
        revisions=revisions,
        base_id=base_id,
    )
    return BookRef(kit=kit, book_id=book.id, root=root)


def _reason(exc: Exception) -> str:
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


def _isolated_problems(item: Item) -> tuple[Diagnostic, ...]:
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

    wrong = _sign_conflict(item)
    if wrong is not None:
        found.append(
            make_diagnostic(
                "CK-E011", item_id=item.id, field=wrong, direction=item.direction
            )
        )
    return tuple(found)


def _key(diagnostic: Diagnostic) -> tuple[str, str | None, str | None, str]:
    return (diagnostic.code, diagnostic.item_id, diagnostic.field, diagnostic.message)


def _new_compile_problems(before: Book, after: Book) -> tuple[Diagnostic, ...]:
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


def _write_item(kit: "CashKit", item: Item) -> ItemRef:
    """Record ``item`` in the authored book and report what moved."""
    existing = kit.book.items.get(item.id)
    if existing == item:
        return ItemRef(
            target=item.id,
            item_id=item.id,
            diagnostics=(make_diagnostic("CK-I002", field=item.id),),
        )
    kit.scenarios.set_book(items={**kit.book.items, item.id: item})
    kit.save()
    if existing is None:
        return ItemRef(target=item.id, item_id=item.id, created=(item.id,))
    changed = tuple(
        sorted(
            name
            for name in type(item).model_fields
            if getattr(existing, name) != getattr(item, name)
        )
    )
    return ItemRef(target=item.id, item_id=item.id, changed=changed)


def _admit(kit: "CashKit", item: Item) -> ItemRef:
    """Validate ``item``, write it unless it is wrong in isolation, and report.

    The two-tier rule of this module's docstring, in one place so ``add_item``
    and ``add_derived`` cannot drift apart.
    """
    refusing = _isolated_problems(item)
    if refusing:
        return ItemRef(target=item.id, item_id=item.id, diagnostics=refusing)
    candidate = kit.book.model_copy(update={"items": {**kit.book.items, item.id: item}})
    reporting = _new_compile_problems(kit.book, candidate)
    report = _write_item(kit, item)
    if not reporting:
        return report
    return report.model_copy(
        update={"diagnostics": tuple(report.diagnostics) + reporting}
    )


# --------------------------------------------------------------------------- #
# The verbs
# --------------------------------------------------------------------------- #


def add_item(book: "CashKit", spec: Item) -> ItemRef:
    """Add (or re-author) one generative item in the book (PRD §6.1).

    ``spec`` is the whole item as you want it: an id already in the book is
    **re-authored**, and ``ItemRef.changed`` names the fields whose value moved,
    so a construction script is idempotent and a second identical call reports
    ``CK-I002`` rather than pretending to have changed something.

    Refuses, writing nothing: ``CK-E003`` (a formula on a flow item, a missing
    or unparseable formula on a derived one, segments on a derived one),
    ``CK-E004`` / ``CK-E005`` (a settlement term list that cannot mean
    anything), ``CK-E011`` (an amount whose sign contradicts ``direction`` — an
    agent authoring rent as positive "out" would otherwise silently create an
    inflow), ``CK-E012`` (a generative ``kind="stock"``).

    Writes and reports: ``CK-E001`` (an unknown reference or a selector matching
    nothing), ``CK-E002`` (a cycle with no ``prev()`` edge), ``CK-E008`` (an
    unknown param), ``CK-E019``, ``CK-E020`` (aggregation across currencies) —
    each of them a statement about the book as a whole, which a later write can
    settle.

    Returns an :class:`~cashkit.model.ItemRef`. On a revision-bound kit the
    write is refused with ``CK-E030`` and nothing is recorded.
    """
    refusal = book._authored_write()
    if refusal is not None:
        return ItemRef(target=spec.id, item_id=spec.id, diagnostics=(refusal,))
    return _admit(book, spec)


def add_derived(
    book: "CashKit",
    id: ItemId,
    formula: str,
    tags: Mapping[str, str] | None = None,
    *,
    name: str = "",
    kind: str = "derived",
    currency: str = "EUR",
    flags: set[str] | None = None,
    agg_rule: str = "sum",
) -> ItemRef:
    """Add a derived item, parsing and DAG-checking the formula **now** (PRD §6.1).

    "Now" is the point of this call: a formula that does not parse never enters
    the book, and one that does but cannot resolve — an unknown ``it()`` target,
    an ``agg()`` selector matching nothing, a cycle with no ``prev()`` edge —
    comes back as a diagnostic from *this* call rather than as a zero column in
    a run three steps later.

    ``kind="stock"`` is legal here and only here: a stock takes its value from a
    formula in v1 (``CK-E012`` is the generative case).

    Refuses, writing nothing: ``CK-E003`` for a formula that does not parse (or
    is empty, or is past the length limit), ``CK-E007`` for a malformed param
    key inside one. Writes and reports: ``CK-E001``, ``CK-E002``, ``CK-E008``,
    ``CK-E020``.

    Returns an :class:`~cashkit.model.ItemRef`. On a revision-bound kit the
    write is refused with ``CK-E030``, before the formula is even parsed: a kit
    that will not record the item has no opinion to offer about it.
    """
    refusal = book._authored_write()
    if refusal is not None:
        return ItemRef(target=id, item_id=id, diagnostics=(refusal,))
    try:
        item = Item(
            id=id,
            name=name or id,
            kind=kind,  # type: ignore[arg-type]
            formula=formula,
            tags=dict(tags or {}),
            flags=set(flags or ()),
            currency=currency,
            agg_rule=agg_rule,  # type: ignore[arg-type]
        )
    except (ValidationError, ValueError) as exc:
        return ItemRef(
            target=id,
            item_id=id,
            diagnostics=(
                make_diagnostic(
                    "CK-E003", item_id=id, field="formula", reason=_reason(exc)
                ),
            ),
        )
    return _admit(book, item)


def set_param(
    book: "CashKit", key: str, value: Decimal, note: str = ""
) -> ChangeReport:
    """Set a named scalar on the authored book (PRD §6.1).

    ``params`` is the lever surface: anything an agent might sweep must be a
    param rather than a literal inside a formula, and this is where the book's
    own value for one is written. The scenario-level ``set_param`` (§6.3)
    overrides it sparsely; this sets what the overrides fall through to.

    ``note`` is accepted for signature parity and, as everywhere else on this
    surface, is not stored — the revision message is where a change's reason
    lives, and a note that only ever went into memory would be a promise the
    history does not keep.

    Returns a :class:`~cashkit.model.ChangeReport` whose ``changed`` is
    ``("params.<key>",)``. Diagnostics: ``CK-E007`` for a key formulas could not
    address as ``p.<key>``, ``CK-E024`` when the reserved ``opening_balance`` key
    is not valid money (the diagnostic names the book, because the authored
    book's params *are* base's — ADR-0007), ``CK-I002`` when the value was
    already this, ``CK-E030`` on a revision-bound kit.
    """
    from cashkit.model.primitives import _require_money

    from .scenarios import OPENING_BALANCE_PARAM

    target = f"params.{key}"
    refusal = book._authored_write()
    if refusal is not None:
        return ChangeReport(target=target, diagnostics=(refusal,))
    if key == OPENING_BALANCE_PARAM:
        try:
            _require_money(value)
        except ValueError as exc:
            return ChangeReport(
                target=target,
                diagnostics=(
                    make_diagnostic(
                        "CK-E024",
                        field=key,
                        key=key,
                        scenario_id=book.book.id,
                        reason=str(exc),
                    ),
                ),
            )
    if book.book.params.get(key) == value:
        return ChangeReport(
            target=target, diagnostics=(make_diagnostic("CK-I002", field=target),)
        )
    try:
        params = _validated_params({**book.book.params, key: value})
    except (ValidationError, ValueError) as exc:
        return ChangeReport(
            target=target,
            diagnostics=(make_diagnostic("CK-E007", field=key, key=key, reason=_reason(exc)),),
        )
    book.scenarios.set_book(params=params)
    book.save()
    return ChangeReport(target=target, changed=(target,))


def _validated_params(params: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Run a param map through the Book field's own validation.

    ``model_copy`` skips validation, so a key that formulas could not address as
    ``p.<key>`` would otherwise reach the store and fail at load time, far from
    the call that wrote it.
    """
    from cashkit.stores.config import ParamsFile

    return dict(ParamsFile(params=dict(params)).params)


def retag(
    book: "CashKit", selector: str, tags: Mapping[str, str]
) -> AffectedCount:
    """Merge ``tags`` into every item the selector matches (PRD §6.1).

    Uses the one §5.4 selector grammar — space-separated ``key:value`` /
    ``flag:name`` terms, ANDed — shared with ``agg()``, ``frame(where=…)`` and
    ``TaxRegime.accumulates``, through the same
    :func:`~cashkit.sdk.macros.resolve_selector` the scenario macros use. There
    is no second grammar.

    Tags are dimensional, so retagging moves ``agg()`` membership and every
    tag-sliced view; that is the point. The change is written as concrete tag
    values, never as a rule, so an item added later is untouched.

    Returns the number of items whose tags actually moved — an
    :class:`AffectedCount`, which is that integer and also carries diagnostics.
    A selector matching nothing is ``0`` and no error. A malformed selector is
    ``0`` carrying ``CK-E003``, because "you typed the selector wrong" and
    "nothing matched" must not be the same answer. A revision-bound kit is ``0``
    carrying ``CK-E030``.
    """
    refusal = book._authored_write()
    if refusal is not None:
        return AffectedCount(0, (refusal,))
    matched, problem = resolve_selector(selector, dict(book.book.items))
    if problem is not None:
        return AffectedCount(0, (problem,))
    macro = RetagItems(selector=selector, tags=dict(tags))
    updated = dict(book.book.items)
    affected: list[ItemId] = []
    for item in matched:
        rewritten = macro.rewrite(item)
        if rewritten == item:
            continue
        updated[item.id] = rewritten
        affected.append(item.id)
    if not affected:
        return AffectedCount(0)
    book.scenarios.set_book(items=updated)
    book.save()
    return AffectedCount(len(affected))


def add_tax_regime(book: "CashKit", regime: TaxRegime) -> ChangeReport:
    """Add (or replace) a tax regime on the book (PRD §6.1).

    PRD §6.1 types this ``-> None``; §6.5 requires every fallible operation to
    return diagnostics, and a regime is fallible — an ``accumulates`` selector
    that matches nothing produces no schedule at all, which is a zero an agent
    must be told about. The report is the §6.5 reading of the same operation
    (the ``commit() -> Revision | None`` precedent, D-P9-09).

    A regime whose id is already present is **replaced**, so re-running a
    construction script is idempotent.

    Refuses, writing nothing: ``CK-E019`` when the regime is unusable on its own
    terms — ``credit_handling="refund_annual"`` with no
    ``annual_adjustment_month``, or an ``accumulates`` selector that does not
    parse. Writes and reports: ``CK-E019`` when the selector parses but matches
    no item in this book yet, which a later ``add_item`` can settle.

    Returns a :class:`~cashkit.model.ChangeReport` whose ``created`` names the
    regime when it is new. ``CK-E030`` on a revision-bound kit.
    """
    from cashkit.engine.tax import _configuration_problem

    target = f"tax_regimes.{regime.id}"
    refusal = book._authored_write()
    if refusal is not None:
        return ChangeReport(target=target, diagnostics=(refusal,))
    problem = _configuration_problem(regime)
    if problem is None:
        parsed, reason = _parse_accumulates(regime)
        if not parsed:
            problem = make_diagnostic(
                "CK-E019",
                field=f"tax_regimes[{regime.id}].accumulates",
                regime_id=regime.id,
                reason=reason or "unparseable selector",
            )
    if problem is not None:
        return ChangeReport(target=target, diagnostics=(problem,))

    existing = {item.id: item for item in book.book.tax_regimes}
    if existing.get(regime.id) == regime:
        return ChangeReport(
            target=target, diagnostics=(make_diagnostic("CK-I002", field=target),)
        )
    created = () if regime.id in existing else (regime.id,)
    merged = {**existing, regime.id: regime}
    candidate = book.book.model_copy(
        update={"tax_regimes": [merged[key] for key in sorted(merged)]}
    )
    reporting = _new_compile_problems(book.book, candidate)
    book.scenarios.set_book(tax_regimes=candidate.tax_regimes)
    book.save()
    return ChangeReport(
        target=target,
        created=created,
        changed=() if created else (target,),
        diagnostics=reporting,
    )


def _parse_accumulates(regime: TaxRegime) -> tuple[bool, str | None]:
    """Whether the regime's selector parses at all. Empty means the VAT default."""
    from cashkit.engine.formula import parse_selector

    if not regime.accumulates.strip():
        return True, None
    selector, reason = parse_selector(regime.accumulates)
    return selector is not None, reason


def set_cutover(book: "CashKit", day: date, note: str = "") -> ChangeReport:
    """Move the book's cutover — the last reconciled boundary (PRD §6.1).

    Before ``cutover`` the ledger is the complete record and generation is
    suppressed entirely, cash legs included (ADR-0004); from ``cutover`` forward
    generation resumes and events apply alongside it. Advancing it is therefore
    the act that turns a reconciled window from forecast into history, and
    :func:`~cashkit.sdk.events.reconcile` computes the day to advance it to.

    It is authored, never ``today()`` — nothing in this package reads the clock,
    which is what makes a run at any later date reproduce (ADR-0010).

    ``note`` is accepted for signature parity and not stored; the revision
    message carries the reason.

    A cutover outside the horizon is **recorded and warned about**, never
    refused: ``CK-W006`` names which direction and what it does to the model —
    past ``horizon.end`` suppresses every occurrence there is, before
    ``horizon.start`` suppresses none. Both are states an agent can plausibly
    mean and neither is legible from the numbers afterwards, which is why the
    warning exists at all. The same check runs in ``validate()``, from the same
    function, so a book already in that state is not silent either.

    Returns a :class:`~cashkit.model.ChangeReport` whose ``changed`` is
    ``("cutover",)``, or ``CK-I002`` when the cutover was already this day.
    ``CK-E030`` on a revision-bound kit.
    """
    refusal = book._authored_write()
    if refusal is not None:
        return ChangeReport(target="cutover", diagnostics=(refusal,))
    problem = cutover_problem(day, book.book)
    warnings = () if problem is None else (problem,)
    if book.book.cutover == day:
        return ChangeReport(
            target="cutover",
            diagnostics=(make_diagnostic("CK-I002", field="cutover"), *warnings),
        )
    book.scenarios.set_book(cutover=day)
    book.save()
    return ChangeReport(target="cutover", changed=("cutover",), diagnostics=warnings)
