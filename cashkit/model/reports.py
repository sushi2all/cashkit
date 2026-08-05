"""Structured results returned by fallible SDK operations (PRD §6.2, §6.3).

Errors are data, not exceptions: every operation an agent can get wrong returns
one of these, carrying :class:`~cashkit.model.Diagnostic` objects with a
``suggested_fix`` rather than raising. Exceptions stay reserved for programmer
error (wrong type, missing store, corrupt file).

These models live with the rest of the data model because both the stores and
the SDK return them and neither may depend on the other.
"""

from __future__ import annotations

from pydantic import Field

from .primitives import CashKitModel, Diagnostic, EventId

__all__ = ["ChangeReport", "EventRef", "ImportReport"]


class EventRef(CashKitModel):
    """A handle on one ledger row: its id and its append-only sequence number.

    ``seq`` is the ledger's monotonic entry number — the basis of the ADR-0006
    watermark, and the reason the ledger may never delete or update a row.
    """

    id: EventId
    seq: int = Field(ge=1)


class ChangeReport(CashKitModel):
    """What a write actually recorded (PRD §6.3).

    ``changed`` lists the field paths recorded as *different*, so an agent that
    writes an unchanged value is told so rather than silently bloating the
    store; ``created`` names rows the operation appended. An operation that
    recorded nothing reports ``CK-I002``.
    """

    target: str | None = None
    changed: tuple[str, ...] = ()
    created: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no error-severity diagnostic was produced. No diagnostics."""
        return not any(d.severity == "error" for d in self.diagnostics)

    @property
    def empty(self) -> bool:
        """True when nothing was recorded or created. No diagnostics."""
        return not self.changed and not self.created


class ImportReport(ChangeReport):
    """The outcome of :meth:`import_events` (PRD §6.2).

    Idempotent on ``(source, ext_id)``: a row whose key exists with an identical
    payload is *skipped*; a row whose key exists with a different payload is a
    *conflict*, and any conflict aborts the whole batch (ADR-0008) with per-row
    ``CK-E010`` diagnostics. ``aborted`` says whether anything was written at
    all — on an aborted batch ``inserted`` is the count that *would* have been
    inserted, and the ledger is untouched.
    """

    source: str
    considered: int = Field(default=0, ge=0)
    inserted: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    conflicted: int = Field(default=0, ge=0)
    aborted: bool = False
