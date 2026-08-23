-- Retention and deletion (SPEC §9, S6).
--
-- Two changes, both about the same obligation: SPEC §9 requires that account
-- deletion erase the Postgres rows, the book directory, **and its backups
-- within 30 days**, and that log retention be stated and honoured.
--
-- 1. `users.deleted_at` is dropped. It has been unused since S1, and D-MLP-22
--    explains why: `DELETE /me` hard-deletes, because a retained `users` row
--    would keep the email — the one genuinely identifying column — for ever,
--    and would block the address from ever signing up again. A column that
--    can never be set is a false promise to whoever reads the schema next.
--
-- 2. `deletions` replaces it with the thing §9 actually needs and a hard
--    delete destroys: something to hang the 30-day backup obligation on.
--    The row carries NO personal data — the account's uuid (now referencing
--    nothing), the instant of deletion, the date by which every backup
--    holding that account must be gone, and the instant the prune closed it.
--    An operator can prove the window was met without the address ever being
--    retained to prove it with.

ALTER TABLE users DROP COLUMN deleted_at;

CREATE TABLE deletions (
    user_id             uuid PRIMARY KEY,
    deleted_at          timestamptz NOT NULL,
    backup_purge_due_at timestamptz NOT NULL,
    backups_purged_at   timestamptz
);
-- The prune job asks "which windows are still open"; this is that query.
CREATE INDEX deletions_open_idx ON deletions (backups_purged_at, backup_purge_due_at);
