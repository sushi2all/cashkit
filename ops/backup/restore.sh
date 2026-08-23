#!/bin/bash
# Restore one snapshot (SPEC §8: "restore procedure documented and tested once
# before beta"). This is the tested half; `ops/DEPLOY.md` is the documented one.
#
#   restore.sh <snapshot|latest> <target-books-root> [target-database]
#
# It never writes to the live volume or the live database by default: the
# targets are arguments. A restore procedure whose first action is to overwrite
# production is a procedure nobody will run in the emergency it was written for.
#
# The book restore is deliberately two steps, in this order:
#
#   1. `git clone` the bundle. That rebuilds the revision store from the
#      revision store's own archive format, with git verifying every object.
#   2. unpack the working tree over it. That restores the uncommitted overlay
#      the bundle cannot carry — and it must come second, because the clone
#      writes a checkout of HEAD that would otherwise overwrite it.
#
# The ledger is copied last, so a partially restored book never has a ledger
# that looks complete.
set -euo pipefail

SNAPSHOT="${1:?usage: restore.sh <snapshot|latest> <target-books-root> [target-database]}"
TARGET_BOOKS="${2:?usage: restore.sh <snapshot|latest> <target-books-root> [target-database]}"
TARGET_DB="${3:-}"

: "${S3_BUCKET:?S3_BUCKET is required}"
: "${BACKUP_AGE_IDENTITY_FILE:?BACKUP_AGE_IDENTITY_FILE is required — backups are encrypted (SPEC §9)}"
S3_PREFIX="${S3_PREFIX:-cashkit}"
PGHOST="${PGHOST:-postgres}"
PGUSER="${PGUSER:-cashkit}"
export PGPASSWORD="${PGPASSWORD:-}"

aws_s3() {
  if [ -n "${S3_ENDPOINT:-}" ]; then
    aws --endpoint-url "$S3_ENDPOINT" s3 "$@"
  else
    aws s3 "$@"
  fi
}

if [ "$SNAPSHOT" = "latest" ]; then
  SNAPSHOT="$(aws_s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/" \
    | awk '{print $2}' | sed 's#/$##' | grep -E '^[0-9]{4}-' | sort | tail -1)"
  [ -n "$SNAPSHOT" ] || { echo "[restore] no snapshot in the bucket" >&2; exit 1; }
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[restore] snapshot $SNAPSHOT -> books:$TARGET_BOOKS db:${TARGET_DB:-<skipped>}"
aws_s3 cp "s3://${S3_BUCKET}/${S3_PREFIX}/${SNAPSHOT}" "$WORK" --recursive --only-show-errors

# The marker the backup writes last. Its absence means the run did not finish,
# and restoring from a half-written snapshot is how a recovery becomes an
# incident of its own.
[ -f "$WORK/COMPLETE" ] || { echo "[restore] $SNAPSHOT has no COMPLETE marker; refusing" >&2; exit 1; }
cat "$WORK/MANIFEST"

unseal() { age -d -i "$BACKUP_AGE_IDENTITY_FILE" -o "${1%.age}" "$1"; }

# --- the app database ----------------------------------------------------- #
if [ -n "$TARGET_DB" ]; then
  unseal "$WORK/pg.dump.age"
  createdb -h "$PGHOST" -U "$PGUSER" "$TARGET_DB" 2>/dev/null || true
  # `--clean --if-exists` so restoring twice into the same target is the same
  # as restoring once. A restore you can only run on a virgin database is a
  # restore you cannot rehearse.
  pg_restore -h "$PGHOST" -U "$PGUSER" -d "$TARGET_DB" \
    --no-owner --no-privileges --clean --if-exists "$WORK/pg.dump"
  echo "[restore] database restored into $TARGET_DB"
fi

# --- the books ------------------------------------------------------------ #
mkdir -p "$TARGET_BOOKS"
restored=0
for bundle in "$WORK"/*.bundle.age; do
  [ -e "$bundle" ] || continue
  id="$(basename "$bundle" .bundle.age)"
  unseal "$bundle"
  git clone --quiet "$WORK/$id.bundle" "$TARGET_BOOKS/$id"
  # A clone leaves the bundle as `origin`, which would be a dangling remote on
  # a restored book pointing at a temporary directory.
  git -C "$TARGET_BOOKS/$id" remote remove origin || true

  if [ -f "$WORK/$id.tree.tar.gz.age" ]; then
    unseal "$WORK/$id.tree.tar.gz.age"
    tar -C "$TARGET_BOOKS/$id" -xzf "$WORK/$id.tree.tar.gz"
  fi
  if [ -f "$WORK/$id.ledger.sqlite.age" ]; then
    unseal "$WORK/$id.ledger.sqlite.age"
    cp "$WORK/$id.ledger.sqlite" "$TARGET_BOOKS/$id/ledger.sqlite"
  fi
  restored=$((restored + 1))
  echo "[restore] book $id"
done

echo "[restore] done: $restored book(s) into $TARGET_BOOKS"
