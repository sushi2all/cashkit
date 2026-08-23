#!/bin/bash
# One backup run (SPEC §2.2, §9).
#
# What a CashKit backup has to contain, and why each piece is here:
#
#   pg.dump          the app database. Users, sessions, books, turns,
#                    llm_calls, proposals — everything that is not book content.
#   <book>.bundle    `git bundle --all` per book: every revision, every branch,
#                    one verifiable file. This is the revision store's own
#                    archive format, so a restore is a `git clone` and not a
#                    directory copy that might or might not have caught a
#                    half-written object.
#   <book>.tree.tar  the working tree without `.git`. A bundle carries commits
#                    only, and the working overlay is real user state — the
#                    dirty flag on the book header is exactly the uncommitted
#                    change SPEC §2.4 promises to keep. A backup that dropped
#                    it would silently lose whatever the user had not saved.
#   <book>.ledger    `sqlite3 .backup`, not `cp`. The ledger is append-only and
#                    open; copying its pages while a write is in flight yields
#                    a file that opens and is wrong, which is this project's
#                    worst failure mode wearing a backup's clothes.
#   MANIFEST         what is in the snapshot, so a restore verifies rather
#                    than hopes.
#
# Everything is encrypted with `age` **before** it leaves the container, to a
# public key (SPEC §9, encryption at rest for volume and backups). The backup
# container therefore holds no key that can read a backup: it can write and it
# cannot read. The private identity lives wherever the restore is run, which is
# not this machine.
set -euo pipefail

: "${BOOKS_ROOT:?BOOKS_ROOT is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is required — backups are encrypted at rest (SPEC §9)}"
S3_PREFIX="${S3_PREFIX:-cashkit}"
PGHOST="${PGHOST:-postgres}"
PGUSER="${PGUSER:-cashkit}"
PGDATABASE="${PGDATABASE:-cashkit}"
export PGPASSWORD="${PGPASSWORD:-}"

aws_s3() {
  if [ -n "${S3_ENDPOINT:-}" ]; then
    aws --endpoint-url "$S3_ENDPOINT" s3 "$@"
  else
    aws s3 "$@"
  fi
}

STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DEST="s3://${S3_BUCKET}/${S3_PREFIX}/${STAMP}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

seal() {  # seal <plaintext-path> -> <path>.age, and remove the plaintext
  age -r "$BACKUP_AGE_RECIPIENT" -o "$1.age" "$1"
  rm -f "$1"
}

echo "[backup] $STAMP -> $DEST"

# --- the app database ----------------------------------------------------- #
# Custom format: `pg_restore` can then rebuild into a differently named
# database, which is what the drill and any real recovery both need.
pg_dump --format=custom --no-owner --no-privileges \
  -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -f "$WORK/pg.dump"
seal "$WORK/pg.dump"

# --- one archive set per book --------------------------------------------- #
MANIFEST="$WORK/MANIFEST"
: > "$MANIFEST"
echo "snapshot $STAMP" >> "$MANIFEST"
echo "books_root $BOOKS_ROOT" >> "$MANIFEST"

book_count=0
for book in "$BOOKS_ROOT"/*; do
  [ -d "$book" ] || continue
  id="$(basename "$book")"
  book_count=$((book_count + 1))

  # `--all` takes every ref; a bundle with no ref is refused by git itself,
  # which is the failure we want rather than an empty file uploaded as a
  # backup. A book with no commit yet cannot be bundled, so it is reported.
  if git -C "$book" rev-parse --verify HEAD >/dev/null 2>&1; then
    git -C "$book" bundle create "$WORK/$id.bundle" --all >/dev/null 2>&1
    git -C "$book" bundle verify "$WORK/$id.bundle" >/dev/null 2>&1
    echo "book $id bundle ok" >> "$MANIFEST"
    seal "$WORK/$id.bundle"
  else
    echo "book $id bundle skipped no-commit" >> "$MANIFEST"
  fi

  # The working tree without `.git`: the uncommitted overlay a bundle cannot
  # carry. `.cashkit/lock` is transient and is excluded.
  tar -C "$book" --exclude=.git --exclude='.cashkit/lock' \
      -czf "$WORK/$id.tree.tar.gz" . 2>/dev/null
  echo "book $id tree ok" >> "$MANIFEST"
  seal "$WORK/$id.tree.tar.gz"

  if [ -f "$book/ledger.sqlite" ]; then
    sqlite3 "$book/ledger.sqlite" ".backup '$WORK/$id.ledger.sqlite'"
    # An integrity check here, not at restore time: a corrupt ledger uploaded
    # as a backup is worse than a backup run that failed loudly.
    if [ "$(sqlite3 "$WORK/$id.ledger.sqlite" 'PRAGMA integrity_check;')" != "ok" ]; then
      echo "[backup] FAILED: ledger integrity check failed for $id" >&2
      exit 1
    fi
    echo "book $id ledger ok" >> "$MANIFEST"
    seal "$WORK/$id.ledger.sqlite"
  else
    echo "book $id ledger absent" >> "$MANIFEST"
  fi
done

echo "books $book_count" >> "$MANIFEST"
echo "completed_at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$MANIFEST"

# The manifest travels in the clear: it names book ids and counts, no content
# and no personal data, and a restore has to be able to read it before it has
# proved it can decrypt anything.
aws_s3 cp "$WORK/MANIFEST" "$DEST/MANIFEST" --only-show-errors
rm -f "$MANIFEST"
aws_s3 cp "$WORK" "$DEST" --recursive --only-show-errors

# The last object written, so its presence is what "the run finished" means.
# `prune.sh` refuses to delete a snapshot that has no COMPLETE marker.
echo "$STAMP" > "$WORK/COMPLETE"
aws_s3 cp "$WORK/COMPLETE" "$DEST/COMPLETE" --only-show-errors

# The §11 backup alarm reads the age of this file, not a job's exit code: a
# cron that never ran produces no failure to notice, but it does stop touching
# this. It is written last and only on success.
SUCCESS_MARKER="${BACKUP_SUCCESS_FILE:-/var/lib/cashkit/backup-last-success.txt}"
mkdir -p "$(dirname "$SUCCESS_MARKER")"
date -u +%Y-%m-%dT%H:%M:%S+00:00 > "$SUCCESS_MARKER"

echo "[backup] done: $book_count book(s) -> $DEST"
