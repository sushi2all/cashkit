"""The trials reuse the integration fixtures, plus the live-model ones."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from conftest import *  # noqa: F401,F403,E402

# `trials` is a package (it has an `__init__.py`), so the live helpers import by
# package path. A bare `live` would shadow this file's own `conftest` lookup.
from trials.live import (  # noqa: F401,E402
    live_app,
    live_session,
    live_transport,
)
