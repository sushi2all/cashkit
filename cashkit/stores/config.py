"""The config store: the §3.3 on-disk layout, and migrations for it (PRD §8.5).

This module owns the shape of a book on disk and nothing else. It turns a
:class:`ConfigState` — a Book, its scenarios, its committed summaries and the
engine settings — into a
:class:`~cashkit.stores.revisions.RevisionState` (path -> canonical text) and
back. **It never learns what a revision is**, exactly as the revision store
never learns what a Book is (ADR-0018).

The layout, verbatim from PRD §3.3::

    <book_root>/
      .cashkit/version              schema generation, for migration on at()
      .cashkit/config.toml          engine settings
      book.yaml                     grain, calendar, horizon, opening balance, cutover
      params.yaml                   named scalars, sorted keys
      items/<id>.yaml               ONE FILE PER ITEM
      scenarios/<id>.yaml
      snapshots/<id>.summary.yaml   computed; committed; outcome diff lives here
      ledger.sqlite                 git-ignored
      frames.duckdb                 git-ignored

Everything above ``ledger.sqlite`` is tracked; everything derived or
high-volume is not.

**``.cashkit/config.toml`` is tracked, and holds engine settings only.**
This closes DECISIONS D-P2-01. The rounding policy changes every number in the
book, so it cannot live in a file that does not travel with the history —
``at("HEAD~5")`` reproducing the wrong numbers because the machine's local
settings changed would be precisely the silent numerical error the design
forbids. Store *backends* (paths, connection strings) are genuinely
machine-local and are therefore constructor arguments, not settings, so nothing
machine-specific ends up tracked.

**Migrations are forward-only** (PRD §8.5). ``.cashkit/version`` names the
generation a state was written in; reading an older one applies each step in
turn before validation, so ``at(ref)`` keeps working across a refactor and the
historical-reproducibility argument survives. A state from a *newer* generation
is refused with ``CK-E026`` rather than read optimistically.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml
from pydantic import Field, ValidationError

from cashkit.engine.numeric import RoundingPolicy
from cashkit.model import (
    Book,
    CalendarSpec,
    CashKitModel,
    Diagnostic,
    Grain,
    Item,
    ItemId,
    Money,
    PeriodRange,
    RunSummary,
    Scenario,
    TaxRegime,
    Watermark,
    make_diagnostic,
    to_canonical_yaml,
)
from cashkit.model.primitives import BookId, FiniteDecimal, ParamKey, ScenarioId

from .revisions import RevisionState

__all__ = [
    "BOOK_FILE",
    "CONFIG_FILE",
    "CommittedSummary",
    "ConfigState",
    "EngineSettings",
    "GITIGNORE_FILE",
    "IGNORED_PATHS",
    "ITEMS_DIR",
    "PARAMS_FILE",
    "SCENARIOS_DIR",
    "SCHEMA_VERSION",
    "SNAPSHOTS_DIR",
    "VERSION_FILE",
    "build_state",
    "load_state",
    "read_working_tree",
    "write_working_tree",
]

#: Config schema generation. Bumped only alongside a migration step.
SCHEMA_VERSION = 3

VERSION_FILE = ".cashkit/version"
CONFIG_FILE = ".cashkit/config.toml"
GITIGNORE_FILE = ".gitignore"
BOOK_FILE = "book.yaml"
PARAMS_FILE = "params.yaml"
ITEMS_DIR = "items"
SCENARIOS_DIR = "scenarios"
SNAPSHOTS_DIR = "snapshots"

#: Derived or high-volume paths, never tracked (PRD §3.3).
IGNORED_PATHS = (
    "ledger.sqlite",
    "ledger.sqlite-journal",
    "ledger.sqlite-wal",
    "frames.duckdb",
    "frames.duckdb.wal",
    "exports/",
    ".cashkit/lock",
)

_GITIGNORE_BODY = (
    "# Written by CashKit (PRD §3.3): everything derived or high-volume.\n"
    + "".join(f"{path}\n" for path in IGNORED_PATHS)
)


# --------------------------------------------------------------------------- #
# Models stored on disk
# --------------------------------------------------------------------------- #


class BookHeader(CashKitModel):
    """``book.yaml``: every Book field except ``params`` and ``items``.

    Those two live in ``params.yaml`` and ``items/`` for diff legibility — one
    file per item is what makes a review of "what changed in the plan" readable
    (PRD §3.3). The split is asserted exhaustive by
    ``tests/test_revision_store.py``: a new Book field that nobody added here
    would be silently dropped on every commit, which is the worst kind of data
    loss because the file it vanished from still looks complete.
    """

    id: BookId
    base_grain: Grain = Grain.DAY
    calendar: CalendarSpec
    horizon: PeriodRange
    opening_balance: Money
    cutover: date
    ledger_watermark: Watermark | None = None
    tax_regimes: list[TaxRegime] = Field(default_factory=list)


class ParamsFile(CashKitModel):
    """``params.yaml``: the named scalars, keys sorted by the canonical emitter."""

    params: dict[ParamKey, FiniteDecimal] = Field(default_factory=dict)


class CommittedSummary(CashKitModel):
    """``snapshots/<scenario>.summary.yaml``: an outcome, committed with its config.

    Recording ``engine_version`` and the ledger watermark alongside the numbers
    is what makes ADR-0006's guarantee checkable: reproduction is exact at
    matching engine version, and a mismatch is *reported* rather than
    discovered as a wrong number.
    """

    scenario: ScenarioId
    engine_version: str
    schema_version: int
    watermark: Watermark | None = None
    summary: RunSummary


class EngineSettings(CashKitModel):
    """``.cashkit/config.toml``: settings that change the numbers.

    Tracked with the history, because a run is identified by
    ``(revision, scenario, engine_version, watermark)`` and every one of those
    must be recoverable from the revision alone.
    """

    rounding_policy: RoundingPolicy = RoundingPolicy.HALF_UP

    def render(self) -> str:
        """The exact ``config.toml`` text. Deterministic; no diagnostics."""
        return (
            "# CashKit engine settings. Tracked with the history: these change\n"
            "# the numbers, so at(ref) must be able to recover them (D-P9-04).\n"
            "# Store backends are machine-local and are NOT settings — they are\n"
            "# constructor arguments and CLI flags.\n"
            "\n"
            "[engine]\n"
            f'rounding_policy = "{self.rounding_policy.value}"\n'
        )

    @classmethod
    def parse(cls, text: str) -> tuple["EngineSettings | None", str | None]:
        """Read ``config.toml``. Returns ``(settings, None)`` or ``(None, reason)``."""
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            return None, f"not valid TOML ({exc})"
        engine = data.get("engine", {})
        if not isinstance(engine, dict):
            return None, "the [engine] table is not a table"
        policy = engine.get("rounding_policy", RoundingPolicy.HALF_UP.value)
        try:
            return cls(rounding_policy=RoundingPolicy(policy)), None
        except ValueError:
            return None, (
                f"rounding_policy {policy!r} is not one of "
                + ", ".join(sorted(p.value for p in RoundingPolicy))
            )


@dataclass(frozen=True)
class ConfigState:
    """Everything a book keeps in git, in memory.

    ``book`` is the **authored** book: never the engine's augmented one. The
    engine synthesizes ``_tax:*`` and ``_event:*`` items on every compile
    (D-P5-09, D-P5-10) and serializing one would commit a value the next run
    recomputes.
    """

    book: Book
    scenarios: dict[ScenarioId, Scenario] = field(default_factory=dict)
    summaries: dict[ScenarioId, CommittedSummary] = field(default_factory=dict)
    settings: EngineSettings = field(default_factory=EngineSettings)
    schema_version: int = SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #


def build_state(config: ConfigState) -> RevisionState:
    """Serialize a :class:`ConfigState` into a revision state.

    Every file goes through the canonical emitter, so the same state always
    produces the same bytes — which is what makes "a reformat-only change
    produces an empty diff" true by construction rather than by a comparison
    that has to be clever. Produces no diagnostics; raises ``TypeError`` only on
    a value outside the model vocabulary (programmer error).
    """
    book = config.book
    header = BookHeader(
        **{name: getattr(book, name) for name in BookHeader.model_fields}
    )
    files: dict[str, str] = {
        VERSION_FILE: f"{config.schema_version}\n",
        CONFIG_FILE: config.settings.render(),
        GITIGNORE_FILE: _GITIGNORE_BODY,
        BOOK_FILE: to_canonical_yaml(header),
        PARAMS_FILE: to_canonical_yaml(ParamsFile(params=dict(book.params))),
    }
    for item_id, item in book.items.items():
        files[f"{ITEMS_DIR}/{item_id}.yaml"] = to_canonical_yaml(item)
    for scenario_id, scenario in config.scenarios.items():
        files[f"{SCENARIOS_DIR}/{scenario_id}.yaml"] = to_canonical_yaml(scenario)
    for scenario_id, summary in config.summaries.items():
        files[f"{SNAPSHOTS_DIR}/{scenario_id}.summary.yaml"] = to_canonical_yaml(summary)
    return RevisionState(files=files)


def load_state(
    state: RevisionState,
) -> tuple[ConfigState | None, tuple[Diagnostic, ...]]:
    """Parse a revision state into a :class:`ConfigState`, migrating if needed.

    Applies every forward migration between the state's recorded
    ``.cashkit/version`` and :data:`SCHEMA_VERSION` before validating, so a
    revision from an older generation still loads (PRD §8.5). Returns
    ``(config, diagnostics)`` with ``config=None`` when the state cannot be
    read at all. Diagnostics: ``CK-E026`` (state is from a newer generation —
    migrations are forward-only), ``CK-E025`` (a file is malformed or fails
    validation). Never raises on stored content.
    """
    diagnostics: list[Diagnostic] = []
    documents, problems = _parse_documents(state)
    diagnostics.extend(problems)
    if problems:
        return None, tuple(diagnostics)

    found = _read_version(state)
    if found is None:
        diagnostics.append(
            make_diagnostic(
                "CK-E025",
                field=VERSION_FILE,
                path=VERSION_FILE,
                reason="the schema version file is missing or not an integer",
            )
        )
        return None, tuple(diagnostics)
    if found > SCHEMA_VERSION:
        diagnostics.append(
            make_diagnostic(
                "CK-E026", field=VERSION_FILE, found=found, supported=SCHEMA_VERSION
            )
        )
        return None, tuple(diagnostics)

    for step in range(found, SCHEMA_VERSION):
        documents = MIGRATIONS[step](documents)

    return _validate_documents(documents, diagnostics)


def _read_version(state: RevisionState) -> int | None:
    text = state.get(VERSION_FILE)
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _parse_documents(
    state: RevisionState,
) -> tuple[dict[str, Any], tuple[Diagnostic, ...]]:
    """YAML-parse every ``.yaml`` path; other paths pass through as raw text."""
    documents: dict[str, Any] = {}
    problems: list[Diagnostic] = []
    for path in state.paths():
        text = state.files[path]
        if not path.endswith(".yaml"):
            documents[path] = text
            continue
        try:
            documents[path] = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            problems.append(
                make_diagnostic(
                    "CK-E025", field=path, path=path, reason=f"malformed YAML ({exc})"
                )
            )
    return documents, tuple(problems)


def _validate_documents(
    documents: Mapping[str, Any], diagnostics: list[Diagnostic]
) -> tuple[ConfigState | None, tuple[Diagnostic, ...]]:
    header_data = documents.get(BOOK_FILE)
    if not isinstance(header_data, dict):
        diagnostics.append(
            make_diagnostic(
                "CK-E025",
                field=BOOK_FILE,
                path=BOOK_FILE,
                reason="book.yaml is missing or is not a mapping",
            )
        )
        return None, tuple(diagnostics)

    params_data = documents.get(PARAMS_FILE) or {}
    params = params_data.get("params", {}) if isinstance(params_data, dict) else {}

    items: dict[ItemId, Item] = {}
    for path in sorted(documents):
        if not path.startswith(f"{ITEMS_DIR}/") or not path.endswith(".yaml"):
            continue
        parsed = _validate(documents[path], Item, path, diagnostics)
        if parsed is not None:
            items[parsed.id] = parsed

    book_data = dict(header_data)
    book_data["params"] = params
    book_data["items"] = items
    book = _validate(book_data, Book, BOOK_FILE, diagnostics)
    if book is None:
        return None, tuple(diagnostics)

    scenarios: dict[ScenarioId, Scenario] = {}
    for path in sorted(documents):
        if not path.startswith(f"{SCENARIOS_DIR}/") or not path.endswith(".yaml"):
            continue
        parsed = _validate(documents[path], Scenario, path, diagnostics)
        if parsed is not None:
            scenarios[parsed.id] = parsed

    summaries: dict[ScenarioId, CommittedSummary] = {}
    for path in sorted(documents):
        if not path.startswith(f"{SNAPSHOTS_DIR}/") or not path.endswith(".summary.yaml"):
            continue
        parsed = _validate(documents[path], CommittedSummary, path, diagnostics)
        if parsed is not None:
            summaries[parsed.scenario] = parsed

    settings = EngineSettings()
    raw_settings = documents.get(CONFIG_FILE)
    if isinstance(raw_settings, str):
        parsed_settings, reason = EngineSettings.parse(raw_settings)
        if parsed_settings is None:
            diagnostics.append(
                make_diagnostic(
                    "CK-E025", field=CONFIG_FILE, path=CONFIG_FILE, reason=reason
                )
            )
        else:
            settings = parsed_settings

    if any(d.severity == "error" for d in diagnostics):
        return None, tuple(diagnostics)
    return (
        ConfigState(
            book=book,
            scenarios=scenarios,
            summaries=summaries,
            settings=settings,
            schema_version=SCHEMA_VERSION,
        ),
        tuple(diagnostics),
    )


def _validate(data: Any, model_type: type, path: str, diagnostics: list[Diagnostic]):
    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        diagnostics.append(
            make_diagnostic(
                "CK-E025",
                field=path,
                path=path,
                reason=_first_error(exc),
            )
        )
        return None


def _first_error(exc: ValidationError) -> str:
    problems = exc.errors()
    if not problems:  # pragma: no cover - pydantic always reports at least one
        return str(exc)
    first = problems[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
    return f"{location}: {first.get('msg', 'invalid')}"


# --------------------------------------------------------------------------- #
# Migrations — forward only (PRD §8.5)
# --------------------------------------------------------------------------- #


def _migrate_1_to_2(documents: Mapping[str, Any]) -> dict[str, Any]:
    """Generation 1 -> 2: items move out of ``book.yaml`` into ``items/``.

    Generation 1 kept the whole Book in one document, which made every review of
    "what changed in the plan" a diff of one enormous file. One file per item is
    the §3.3 layout.
    """
    out = dict(documents)
    header = dict(out.get(BOOK_FILE) or {})
    for item_id, item in sorted((header.pop("items", None) or {}).items()):
        out[f"{ITEMS_DIR}/{item_id}.yaml"] = item
    out[BOOK_FILE] = header
    return out


def _migrate_2_to_3(documents: Mapping[str, Any]) -> dict[str, Any]:
    """Generation 2 -> 3: params move to ``params.yaml``; settings file appears.

    Generation 2 still carried ``params`` inline in ``book.yaml``. Splitting
    them out gives the lever surface its own reviewable file, and generation 3
    is the first to record the engine settings that change the numbers, so a
    state without one is read at the documented default.
    """
    out = dict(documents)
    header = dict(out.get(BOOK_FILE) or {})
    params = header.pop("params", None) or {}
    out[BOOK_FILE] = header
    out[PARAMS_FILE] = {"params": params}
    out.setdefault(CONFIG_FILE, EngineSettings().render())
    return out


#: ``MIGRATIONS[n]`` upgrades a generation-``n`` document set to ``n+1``.
MIGRATIONS: Mapping[int, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
}


# --------------------------------------------------------------------------- #
# The working tree
# --------------------------------------------------------------------------- #


def write_working_tree(root: str | Path, state: RevisionState) -> None:
    """Write a revision state onto disk under ``root``.

    Paths present on disk but absent from the state are removed, so the working
    tree is the state and not a superset of it — otherwise an item deleted in
    memory would keep contributing to the next load. Files outside the tracked
    layout (the ledger, the frame store, exports) are left alone. Produces no
    diagnostics.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    wanted = set(state.paths())
    for path in sorted(wanted):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(state.files[path], encoding="utf-8", newline="\n")
    for path in _tracked_paths_on_disk(root):
        if path not in wanted:
            (root / path).unlink()


def read_working_tree(root: str | Path) -> RevisionState:
    """Read the tracked layout from disk into a revision state.

    Only the paths the layout defines are read, so an editor's stray file never
    becomes part of a revision. Produces no diagnostics; raises ``OSError`` for
    an unreadable file (programmer error / broken store).
    """
    root = Path(root)
    files: dict[str, str] = {}
    for path in _tracked_paths_on_disk(root):
        files[path] = (root / path).read_text(encoding="utf-8")
    return RevisionState(files=files)


def _tracked_paths_on_disk(root: Path) -> list[str]:
    found: list[str] = []
    for path in (VERSION_FILE, CONFIG_FILE, GITIGNORE_FILE, BOOK_FILE, PARAMS_FILE):
        if (root / path).is_file():
            found.append(path)
    for directory, suffix in (
        (ITEMS_DIR, ".yaml"),
        (SCENARIOS_DIR, ".yaml"),
        (SNAPSHOTS_DIR, ".yaml"),
    ):
        base = root / directory
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_file() and child.name.endswith(suffix):
                found.append(f"{directory}/{child.name}")
    return sorted(found)


def is_book_root(root: str | Path) -> bool:
    """True when ``root`` holds a CashKit book. No diagnostics."""
    return (Path(root) / VERSION_FILE).is_file()


def decimal_params(raw: Mapping[str, Any]) -> dict[str, Decimal]:
    """Coerce a raw param mapping to ``Decimal``. Programmer-error on bad input."""
    return {key: Decimal(str(value)) for key, value in raw.items()}
