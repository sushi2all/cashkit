"""Provenance envelope and the WHAT-IF rule (SPEC §2.4, §3).

SPEC §3, response invariants: *every payload that carries a computed number
also carries* ``as_of``, ``scenario``, ``revision``, ``engine_version`` *and the*
``what_if`` *field of §2.4*.

SPEC §2.4, the WHAT-IF rule, quoted verbatim because the PROMPT requires its
wording wherever it is restated:

    Base is the plan of record. Any figure NOT from the committed state of
    ``base`` — a non-base scenario (active or not), a throwaway overlay, or a
    dry-run including pending changes — carries the WHAT-IF stamp: payload
    field ``what_if: {stamped: true, reason: "scenario"|"overlay"|"pending",
    scenario?: id}``, and a rendered stamp element (ADR-0024).

The rendered stamp is S4's; the payload field is this module's, and it is
truthful from the first commit.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from cashkit.engine import ENGINE_VERSION
from pydantic import BaseModel

WhatIfReason = Literal["scenario", "overlay", "pending"]

BASE_SCENARIO = "base"


class WhatIf(BaseModel):
    """The §2.4 payload field."""

    stamped: bool = False
    reason: WhatIfReason | None = None
    scenario: str | None = None


class Envelope(BaseModel):
    """Provenance carried by every payload that holds a computed number."""

    as_of: date
    scenario: str
    revision: str | None
    engine_version: str = ENGINE_VERSION
    what_if: WhatIf
    request_id: str


def what_if_for(*, scenario: str, clean: bool, pending: bool = False) -> WhatIf:
    """Decide the §2.4 stamp for one payload.

    Precedence is the order the rule lists its causes. ``pending`` wins because
    a dry-run figure is hypothetical whatever it was computed over; a non-base
    scenario is next; an uncommitted working overlay on base is last.
    """
    if pending:
        return WhatIf(stamped=True, reason="pending", scenario=scenario)
    if scenario != BASE_SCENARIO:
        return WhatIf(stamped=True, reason="scenario", scenario=scenario)
    if not clean:
        return WhatIf(stamped=True, reason="overlay", scenario=scenario)
    return WhatIf(stamped=False)


def envelope(
    *,
    as_of: date,
    scenario: str,
    revision: str | None,
    clean: bool,
    request_id: str,
    pending: bool = False,
) -> Envelope:
    return Envelope(
        as_of=as_of,
        scenario=scenario,
        revision=revision,
        engine_version=ENGINE_VERSION,
        what_if=what_if_for(scenario=scenario, clean=clean, pending=pending),
        request_id=request_id,
    )


#: The envelope keys the invariant middleware looks for.
ENVELOPE_KEYS = frozenset({"as_of", "scenario", "revision", "engine_version", "what_if"})
