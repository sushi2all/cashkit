"""The service clock — the one place the wall clock is read.

ADR-0019 rule 2 and SPEC §2.4: ``as_of`` is host-filled and the engine never
reads the clock. That guarantee is only worth something if the host reads the
clock in exactly one, replaceable place. Handlers therefore never call
``date.today()`` or ``datetime.now()``; they depend on a :class:`Clock`.

Every integration test and every trial installs :class:`FixedClock`, so no test
outcome depends on the day it runs.
"""

from __future__ import annotations

import datetime as _dt
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A source of the current instant and of the host-filled ``as_of`` date."""

    def now(self) -> _dt.datetime:
        """The current instant, timezone-aware in UTC."""

    def today(self) -> _dt.date:
        """The ``as_of`` date the host stamps on every computed payload."""


class SystemClock:
    """The production clock. The only wall-clock reader in the service."""

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(tz=_dt.timezone.utc)

    def today(self) -> _dt.date:
        return self.now().date()


class FixedClock:
    """A clock frozen at one instant, and movable by hand.

    Tests use it so that ``as_of``, session expiry and proposal expiry are all
    deterministic. Moving it is explicit: nothing advances on its own.
    """

    def __init__(self, instant: _dt.datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=_dt.timezone.utc)
        self._instant = instant

    def now(self) -> _dt.datetime:
        return self._instant

    def today(self) -> _dt.date:
        return self._instant.date()

    def advance(self, **delta: float) -> None:
        """Move the clock forward by a :class:`datetime.timedelta` keyword."""
        self._instant = self._instant + _dt.timedelta(**delta)

    def set(self, instant: _dt.datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=_dt.timezone.utc)
        self._instant = instant


_clock: Clock = SystemClock()


def get_clock() -> Clock:
    """FastAPI dependency returning the active clock."""
    return _clock
