"""Service configuration.

Everything the service needs from its environment, in one typed place. No
module reads ``os.environ`` directly; tests build a :class:`Settings` and
override the dependency instead.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEV_DATABASE_URL = (
    "postgresql+asyncpg://cashkit:cashkit@localhost:55432/cashkit"
)


class Settings(BaseSettings):
    """Runtime configuration, read from ``CASHKIT_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="CASHKIT_", extra="ignore")

    database_url: str = DEFAULT_DEV_DATABASE_URL
    books_root: Path = Path("/var/lib/cashkit/books")

    # SPEC §3 token and session policy.
    link_token_ttl_minutes: int = 15
    session_ttl_days_mobile: int = 30
    session_ttl_days_web: int = 7
    # Sliding renewal writes to Postgres at most once per this interval, so an
    # authenticated request is not a database write in the common case.
    session_renewal_interval_minutes: int = 60

    # SPEC §2.5.
    proposal_ttl_minutes: int = 15

    # Response-invariant middleware (SPEC §3) is a test-mode check: it walks
    # every JSON response and fails the request when a money figure travels
    # without its provenance envelope. Never enabled in production — an
    # assertion that turns a served payload into a 500 is not a production
    # behaviour.
    check_response_invariants: bool = False

    # Engine runs happen inline on the event-loop thread (SPEC §2.2 thread
    # confinement). This bounds how long one book operation may hold its lock.
    book_lock_timeout_seconds: float = 30.0

    default_currency: str = Field(default="EUR", frozen=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings singleton (FastAPI dependency)."""
    return Settings()
