"""Fixture builders for the Phase 9 gates: a book, and a repo spanning three
schema generations.

The historical generations are built from the *current* models and then written
in the shape their generation used, so the fixture cannot drift away from what
the migrations claim to migrate. Generation 1 kept the whole Book in one
``book.yaml``; generation 2 split ``items/`` out; generation 3 split
``params.yaml`` out and added the tracked engine settings.

Historical files are emitted with ``yaml.safe_dump`` rather than the canonical
emitter, which is both realistic — a past generation had a past emitter — and
useful: it proves that reading an old revision does not depend on the bytes
having been written by today's emitter.
"""

from __future__ import annotations

import datetime
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

import yaml

from cashkit.engine import ENGINE_VERSION, Engine
from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    DueTerm,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Scenario,
    Segment,
    Settlement,
    to_canonical_yaml,
)
from cashkit.sdk.views import summary as summarize
from cashkit.stores.config import (
    BOOK_FILE,
    CONFIG_FILE,
    SCENARIOS_DIR,
    SNAPSHOTS_DIR,
    VERSION_FILE,
    CommittedSummary,
    EngineSettings,
    _migrate_1_to_2,
)
from cashkit.stores.git_store import GitRevisionStore
from cashkit.stores.revisions import RevisionState

#: A fixed commit time, so a fixture repository is byte-reproducible.
FIXED_TIME = datetime.datetime(2026, 8, 5, 9, 0, tzinfo=datetime.timezone.utc)


def _monthly(day: int) -> Recurrence:
    return Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=day)


def build_history_book(*, rent: str = "-4000.00", fee: str = "18000.00") -> Book:
    """A small, fully deterministic book with a settlement lag and a derived item."""
    return Book(
        id="history-book",
        base_grain=Grain.DAY,
        calendar=CalendarSpec(fiscal_year_start_month=1, country="IT"),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        opening_balance=Decimal("120000.00"),
        cutover=date(2026, 1, 1),
        params={"margin": Decimal("0.20")},
        items={
            "acme_fee": Item(
                id="acme_fee",
                name="Acme retainer",
                kind="flow",
                direction="in",
                tags={"cat": "revenue", "customer": "acme"},
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        recurrence=_monthly(1),
                        amount=Amount(constant=Decimal(fee)),
                    )
                ],
                settlement=Settlement(due=[DueTerm(share=Decimal(1), offset="30d")]),
            ),
            "rent": Item(
                id="rent",
                name="Office rent",
                kind="flow",
                direction="out",
                tags={"cat": "opex"},
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        recurrence=_monthly(5),
                        amount=Amount(constant=Decimal(rent)),
                    )
                ],
            ),
            "commission": Item(
                id="commission",
                name="Sales commission",
                kind="derived",
                direction="out",
                tags={"cat": "opex"},
                formula='-agg(tag="cat:revenue") * p.margin',
            ),
        },
    )


def summary_of(book: Book) -> CommittedSummary:
    """Run ``book`` and package the result the way ``commit()`` would."""
    result = Engine(book).run()
    return CommittedSummary(
        scenario="base",
        engine_version=ENGINE_VERSION,
        schema_version=0,  # replaced per generation by the caller
        watermark=None,
        summary=summarize(result, book),
    )


def _dump(document: Any) -> str:
    """Emit a raw document the way an earlier generation's emitter would."""
    return yaml.safe_dump(document, default_flow_style=False, sort_keys=True)


def generation_one_state(book: Book, snapshot: CommittedSummary) -> RevisionState:
    """The whole Book in one ``book.yaml``; no ``items/``, no ``params.yaml``."""
    return RevisionState(
        files={
            VERSION_FILE: "1\n",
            BOOK_FILE: to_canonical_yaml(book),
            f"{SCENARIOS_DIR}/base.yaml": to_canonical_yaml(Scenario(id="base")),
            f"{SNAPSHOTS_DIR}/base.summary.yaml": to_canonical_yaml(
                snapshot.model_copy(update={"schema_version": 1})
            ),
        }
    )


def generation_two_state(book: Book, snapshot: CommittedSummary) -> RevisionState:
    """Items split into ``items/``; ``params`` still inline in ``book.yaml``."""
    first = generation_one_state(book, snapshot)
    documents: dict[str, Any] = {}
    for path in first.paths():
        text = first.files[path]
        documents[path] = yaml.safe_load(text) if path.endswith(".yaml") else text
    migrated: Mapping[str, Any] = _migrate_1_to_2(documents)
    files = {
        path: (_dump(value) if path.endswith(".yaml") else value)
        for path, value in migrated.items()
    }
    files[VERSION_FILE] = "2\n"
    files[f"{SNAPSHOTS_DIR}/base.summary.yaml"] = to_canonical_yaml(
        snapshot.model_copy(update={"schema_version": 2})
    )
    return RevisionState(files=files)


def build_three_generation_repo(root) -> GitRevisionStore:
    """Write a repository whose three revisions span three schema generations.

    Each revision carries the snapshot computed from *its own* book content, so
    "reproduces all historical runs" is a claim about numbers and not about
    files parsing.
    """
    store = GitRevisionStore(root)
    from cashkit.stores.config import ConfigState, build_state

    gen1_book = build_history_book(rent="-4000.00", fee="18000.00")
    store.write_revision(
        generation_one_state(gen1_book, summary_of(gen1_book)),
        message="generation 1: one book.yaml",
        author="founder",
        metadata={"engine-version": ENGINE_VERSION, "schema-version": "1"},
        timestamp=FIXED_TIME,
    )

    gen2_book = build_history_book(rent="-4500.00", fee="18000.00")
    store.write_revision(
        generation_two_state(gen2_book, summary_of(gen2_book)),
        message="generation 2: items split out",
        author="founder",
        metadata={"engine-version": ENGINE_VERSION, "schema-version": "2"},
        timestamp=FIXED_TIME + datetime.timedelta(days=1),
    )

    gen3_book = build_history_book(rent="-4500.00", fee="19500.00")
    snapshot = summary_of(gen3_book)
    from cashkit.stores.config import SCHEMA_VERSION

    store.write_revision(
        build_state(
            ConfigState(
                book=gen3_book,
                scenarios={"base": Scenario(id="base")},
                summaries={
                    "base": snapshot.model_copy(update={"schema_version": SCHEMA_VERSION})
                },
                settings=EngineSettings(),
            )
        ),
        message="generation 3: params split out",
        author="founder",
        metadata={"engine-version": ENGINE_VERSION, "schema-version": str(SCHEMA_VERSION)},
        timestamp=FIXED_TIME + datetime.timedelta(days=2),
    )
    return store


__all__ = [
    "CONFIG_FILE",
    "FIXED_TIME",
    "build_history_book",
    "build_three_generation_repo",
    "generation_one_state",
    "generation_two_state",
    "summary_of",
]
