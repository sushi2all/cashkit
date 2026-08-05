"""The one place in CashKit that reads the wall clock — and why it is allowed.

Non-negotiable constraint 3 and ADR-0010 ban ``date.today()``,
``datetime.now()``, ``datetime.utcnow()``, ``datetime.today()`` and
``time.time()`` everywhere, because reading the clock during evaluation
destroys reproducibility: a run must depend on ``(revision, scenario,
engine_version, watermark)`` and on nothing else.

Two operational artifacts genuinely need a timestamp, and neither is an
evaluation:

* a **commit** — an authored artifact whose "when" is part of the audit trail;
* a **writer lock** — whose staleness is judged from the holder's pid, with the
  timestamp carried only so a human can read who has been holding it.

Rather than let the ban decay into "except where it was inconvenient", this
module is the single exemption: the lint in ``tests/test_wall_clock_lint.py``
covers the *whole* package and allowlists exactly this file, and a companion
test asserts that nothing under ``engine/``, ``model/``, ``reference/`` or
``sdk/`` imports it. If a second file ever needs a clock, the lint fails and the
question gets asked again — which is the point.

Every caller here takes an **injectable** timestamp: the default reads the
clock, a test or a fixture builder passes one in, and a fixture repository is
therefore byte-reproducible.
"""

from __future__ import annotations

import datetime as _datetime

__all__ = ["Timestamp", "wall_clock"]

#: A commit / lock timestamp: timezone-aware UTC. Never an evaluation input.
Timestamp = _datetime.datetime


_EPOCH = _datetime.datetime(1970, 1, 1, tzinfo=_datetime.timezone.utc)


def wall_clock() -> Timestamp:
    """Return the current UTC time, timezone-aware.

    The only wall-clock read in the package. Used to stamp commits and writer
    locks; never reached from any evaluation path. Produces no diagnostics.

    Built from integer nanoseconds rather than ``time.time()``'s float, so the
    no-float audit (``tests/test_no_float_money.py``) stays absolute: the
    identifier ``float`` does not appear anywhere under ``cashkit/`` except the
    boundary guard that rejects it. A timestamp is not money, but an exception
    carved for one non-money value is an exception that gets cited for the next
    one.
    """
    return _EPOCH + _datetime.timedelta(microseconds=_nanoseconds() // 1000)


def _nanoseconds() -> int:
    # Indirected through a local import so the module's single clock read is
    # trivially greppable, and so the banned identifier never sits at module
    # scope where a future reader could copy it elsewhere by habit.
    import time as _time

    return _time.time_ns()
