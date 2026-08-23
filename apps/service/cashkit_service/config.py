"""Service configuration.

Everything the service needs from its environment, in one typed place. No
module reads ``os.environ`` directly; tests build a :class:`Settings` and
override the dependency instead.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
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

    # --- magic-link deep links (SPEC §3) ---------------------------------- #

    #: Where the web app serves the verify route. The link a browser receives
    #: is an ordinary HTTPS URL throughout (SPEC §3), so this is the web app's
    #: origin, not the service's — they are different hosts in every
    #: environment past development.
    web_app_url: str = "http://localhost:8081"
    #: The custom URL scheme a development build registers. Universal links
    #: need the associated-domains entitlement and therefore the paid Apple
    #: enrolment, so they arrive with the TestFlight track (SPEC §3); until
    #: then a development build is reached through its own scheme. Matches
    #: `expo.scheme` in `apps/client/app.json`.
    mobile_scheme: str = "cashkit"
    #: The path both link shapes land on. One constant, so the client route and
    #: the mailed link cannot drift apart.
    verify_path: str = "/auth/verify"

    # --- agent layer (SPEC §2.3, §8) -------------------------------------- #

    #: The pinned model. ADR-0028: every turn runs flash-class, and there is no
    #: pre-interpretation routing. A model change reruns the trial suite first.
    llm_model: str = "google/gemini-3.7-flash"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    #: Accepts the repo-root `OPENROUTER_API_KEY` as well as the prefixed name,
    #: so a developer needs one key in one place.
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("CASHKIT_LLM_API_KEY", "OPENROUTER_API_KEY"),
    )
    llm_timeout_seconds: float = 120.0
    llm_max_tokens: int = 16000
    #: SPEC §2.3 step 5: the Q&A read loop is bounded at four read-only calls.
    llm_qa_max_calls: int = 4
    #: How many times a turn may re-ask after unparseable JSON, warmer each
    #: time (proto T08/T09: a temperature-0 retry reproduces the same bytes).
    llm_json_retries: int = 2
    #: SPEC §2.3 step 4 / ADR-0030 stage 2: at most one repair round from
    #: diagnostics, and one bounded verification call.
    llm_diagnostic_repair_rounds: int = 1
    #: The ceiling on model calls in one turn. Every loop is bounded on its own,
    #: but the bounds multiply; this is the single number that bounds a turn, so
    #: the §8 daily budget — checked once, before the first call — cannot be
    #: outrun from inside one turn. A healthy turn makes one to three calls.
    llm_max_calls_per_turn: int = 15

    # --- the import loop (SPEC §7, ADR-0030 stage 4) ---------------------- #

    #: SPEC §7.2: *Cap: 20 model calls, then present partial result honestly.*
    #: Import is the one free-running loop in the product, and this is what
    #: bounds it. Every call it makes counts, retries included.
    import_max_llm_calls: int = 20
    #: How many times one section may be re-authored after a reconciliation
    #: mismatch, with the engine's own receipts as the evidence (SPEC §7.2).
    import_revise_rounds: int = 2
    #: The largest upload the service will read. A household budget workbook is
    #: two orders of magnitude smaller.
    import_max_bytes: int = 8 * 1024 * 1024

    # SPEC §8 guardrails, enforced server-side (see agent/budget.py).
    daily_model_budget_usd: Decimal = Decimal("0.50")
    turns_per_hour: int = 30
    imports_per_day: int = 5

    # --- retention (SPEC §9) ---------------------------------------------- #
    # Every one of these numbers is also a sentence in the privacy policy
    # (`compliance/privacy-policy.md`). They are settings and not constants so
    # that widening one is a visible change to an operator's environment; a
    # test asserts the policy and these defaults agree, so neither can move
    # without the other.

    #: `llm_calls.request` / `.response` carry the user's financial data
    #: verbatim (SPEC §4) and are blanked after this many days. The numeric
    #: columns survive, so the cost and repair-rate history does too.
    llm_payload_retention_days: int = 30
    #: The structured JSON access log of SPEC §11.
    request_log_retention_days: int = 90
    #: How long a magic-link token row (an email plus a hash) survives after it
    #: expires. Its TTL is fifteen minutes; a day is a generous sweep window.
    login_token_retention_days: int = 1
    #: SPEC §9: account deletion must reach backups within 30 days. This is
    #: both the backup bucket's own lifecycle and the window the `deletions`
    #: receipt is measured against — one number, so they cannot disagree.
    backup_retention_days: int = 30

    #: Where the rotating request log lives. Empty in tests and in a bare
    #: development run: the line goes to stdout like every other log.
    request_log_dir: Path = Path("/var/log/cashkit")
    #: Written by the backup prune with the timestamp of the oldest object it
    #: left in the bucket. The retention sweep reads it to close deletion
    #: windows against what is actually retained rather than against elapsed
    #: time (see `retention.close_backup_windows`).
    backup_marker_file: Path = Path("/var/lib/cashkit/backup-oldest.txt")

    # --- connection pool (D-MLP-29) --------------------------------------- #
    # A turn holds its request connection for the whole turn and the journal
    # opens a second, short one per write, so concurrent turns consume two
    # connections each at their peak. SQLAlchemy's defaults (5 + 10) put the
    # ceiling at roughly seven turns in flight before one waits — a latency
    # cliff SPEC §8's budgets would show. Size these against the VM's
    # Postgres `max_connections`, which `docker-compose.prod.yml` sets.
    db_pool_size: int = 20
    db_max_overflow: int = 20
    #: Refuse rather than hang when the pool is exhausted: a turn that waits
    #: forever is a turn that has already missed its budget.
    db_pool_timeout_seconds: float = 10.0

    # --- observability (SPEC §11) ----------------------------------------- #

    #: Unhandled exceptions go here with the request_id attached. Empty
    #: disables Sentry entirely — no network call, no client installed.
    sentry_dsn: str = Field(
        default="", validation_alias=AliasChoices("CASHKIT_SENTRY_DSN", "SENTRY_DSN")
    )
    #: Names the deployment in Sentry and in the metric `cashkit_build_info`.
    environment: str = "development"
    #: `GET /metrics` for the Prometheus agent. It is unauthenticated on the
    #: container network and never published by Caddy (see `ops/Caddyfile`).
    metrics_enabled: bool = True
    #: The structured request log middleware. Off in tests, so a suite does not
    #: write a hundred thousand lines nobody reads.
    request_log_enabled: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings singleton (FastAPI dependency)."""
    return Settings()
