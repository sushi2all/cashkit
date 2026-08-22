-- CashKit MLP service schema (SPEC-mlp-consumer.md §4).
--
-- App data only. Book content NEVER lives here: a book is a directory on the
-- volume, managed by the cashkit file stores (SPEC §2.2, D-MLP-01).
--
-- Two tables extend the §4 list; both are recorded in DECISIONS.md:
--   * login_tokens — §3 specifies single-use magic-link tokens with a 15-minute
--     TTL, and §4 gives them no table. Single-use is not enforceable without
--     one (D-MLP-07).
--   * proposals.book_id — supersession in §2.5 is per book, so the row must
--     name its book (D-MLP-08).

CREATE TABLE users (
    id          uuid PRIMARY KEY,
    email       text NOT NULL,
    created_at  timestamptz NOT NULL,
    deleted_at  timestamptz
);
-- Email is stored already lower-cased by the application; the index makes the
-- one-account-per-address rule structural rather than a convention.
CREATE UNIQUE INDEX users_email_key ON users (email);

CREATE TABLE login_tokens (
    id          uuid PRIMARY KEY,
    email       text NOT NULL,
    token_hash  text NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at  timestamptz NOT NULL
);
CREATE INDEX login_tokens_email_idx ON login_tokens (email);

CREATE TABLE sessions (
    id           uuid PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash   text NOT NULL UNIQUE,
    platform     text NOT NULL,
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

CREATE TABLE books (
    id              uuid PRIMARY KEY,
    user_id         uuid NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    storage_path    text NOT NULL,
    active_scenario text NOT NULL DEFAULT 'base',
    created_at      timestamptz NOT NULL
);

CREATE TABLE turns (
    id                uuid PRIMARY KEY,
    user_id           uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    book_id           uuid NOT NULL REFERENCES books (id) ON DELETE CASCADE,
    request_id        text NOT NULL,
    input_text        text NOT NULL,
    kind              text,
    context           text,
    intents           jsonb,
    model             text,
    prompt_tokens     integer,
    completion_tokens integer,
    cost              numeric(12, 6),
    latency_ms        integer,
    outcome           text,
    diagnostics       jsonb,
    created_at        timestamptz NOT NULL
);
CREATE INDEX turns_book_idx ON turns (book_id, created_at);
CREATE INDEX turns_request_idx ON turns (request_id);

CREATE TABLE llm_calls (
    id                uuid PRIMARY KEY,
    turn_id           uuid NOT NULL REFERENCES turns (id) ON DELETE CASCADE,
    seq               integer NOT NULL,
    purpose           text NOT NULL,
    request           jsonb,
    response          jsonb,
    prompt_tokens     integer,
    completion_tokens integer,
    cost              numeric(12, 6),
    latency_ms        integer,
    error             text,
    created_at        timestamptz NOT NULL
);
CREATE INDEX llm_calls_turn_idx ON llm_calls (turn_id, seq);
-- §4/§9: request and response carry user financial data and purge after 30
-- days; the numeric columns survive. The purge job is S6's.
CREATE INDEX llm_calls_created_idx ON llm_calls (created_at);

CREATE TABLE proposals (
    id                  uuid PRIMARY KEY,
    book_id             uuid NOT NULL REFERENCES books (id) ON DELETE CASCADE,
    turn_id             uuid REFERENCES turns (id) ON DELETE SET NULL,
    origin              text NOT NULL,
    context             text,
    scenario            text NOT NULL,
    ops                 jsonb NOT NULL,
    deltas              jsonb NOT NULL,
    base_revision       text,
    overlay_fingerprint text NOT NULL,
    status              text NOT NULL,
    supersedes          uuid REFERENCES proposals (id) ON DELETE SET NULL,
    expires_at          timestamptz NOT NULL,
    created_at          timestamptz NOT NULL,
    resolved_at         timestamptz
);
CREATE INDEX proposals_book_status_idx ON proposals (book_id, status);

CREATE TABLE import_jobs (
    id         uuid PRIMARY KEY,
    book_id    uuid NOT NULL REFERENCES books (id) ON DELETE CASCADE,
    status     text NOT NULL,
    report     jsonb,
    created_at timestamptz NOT NULL
);
CREATE INDEX import_jobs_book_idx ON import_jobs (book_id);
