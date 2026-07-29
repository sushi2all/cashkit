"""CashKit data model (PRD §4) and canonical serialization.

Nothing in this package reads the wall clock, uses ``float``, or touches a
store. Models are frozen Pydantic v2 classes; serialization is the canonical
YAML form defined in :mod:`cashkit.model.canonical`.
"""

from .book import Book
from .canonical import from_canonical_yaml, to_canonical_yaml
from .diagnostics import CATALOGUE, DiagnosticSpec, make_diagnostic
from .event import Event
from .item import Item, Recurrence, Segment
from .primitives import (
    Amount,
    CalendarSpec,
    CashKitModel,
    Diagnostic,
    Duration,
    Escalation,
    EventId,
    Grain,
    ItemId,
    Money,
    PeriodRange,
    PeriodRef,
    SparseOverlay,
    Watermark,
)
from .scenario import EventOverlay, ItemOverlay, Scenario
from .settlement import DueTerm, Settlement
from .tax import TaxRegime, VatSpec

__all__ = [
    "Amount",
    "Book",
    "CATALOGUE",
    "CalendarSpec",
    "CashKitModel",
    "Diagnostic",
    "DiagnosticSpec",
    "DueTerm",
    "Duration",
    "Escalation",
    "Event",
    "EventId",
    "EventOverlay",
    "Grain",
    "Item",
    "ItemId",
    "ItemOverlay",
    "Money",
    "PeriodRange",
    "PeriodRef",
    "Recurrence",
    "Scenario",
    "Segment",
    "Settlement",
    "SparseOverlay",
    "TaxRegime",
    "VatSpec",
    "Watermark",
    "from_canonical_yaml",
    "make_diagnostic",
    "to_canonical_yaml",
]
