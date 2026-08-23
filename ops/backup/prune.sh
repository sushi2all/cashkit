#!/bin/bash
# Backup retention, and the proof SPEC §9 needs from it.
#
# Two jobs in one, and the second is the interesting one.
#
# 1. Delete snapshots older than BACKUP_RETENTION_DAYS (30, SPEC §9). Only
#    snapshots that carry a COMPLETE marker are eligible: a run that died
#    half-way is not evidence of anything, and deleting the last good snapshot
#    because a broken one looked newer is how a backup system loses data.
#
# 2. Write the timestamp of the **oldest snapshot still in the bucket** to
#    BACKUP_MARKER_FILE. `cashkit_service.retention.close_backup_windows()`
#    reads it and closes a deletion's 30-day window only when the account was
#    deleted before that instant — because every retained backup was written
#    after that, so none of them can be holding the account. That is a
#    statement about the bucket rather than about the calendar, and it is the
#    only honest way to mark a §9 backup purge done (D-MLP-99).
#
# The two jobs share no client and no credential: one ISO timestamp in a file
# on a shared volume is the whole interface.
set -euo pipefail

: "${S3_BUCKET:?S3_BUCKET is required}"
S3_PREFIX="${S3_PREFIX:-cashkit}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
MARKER="${BACKUP_MARKER_FILE:-/var/lib/cashkit/backup-oldest.txt}"

aws_s3() {
  if [ -n "${S3_ENDPOINT:-}" ]; then
    aws --endpoint-url "$S3_ENDPOINT" s3 "$@"
  else
    aws s3 "$@"
  fi
}

# `<prefix>/2026-08-23T03-15-00Z/` -> `2026-08-23T03-15-00Z`
snapshots() {
  aws_s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/" \
    | awk '{print $2}' | sed 's#/$##' | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' | sort
}

iso_of() {  # 2026-08-23T03-15-00Z -> 2026-08-23T03:15:00+00:00
  printf '%s\n' "$1" | sed -E 's/^([0-9-]{10})T([0-9]{2})-([0-9]{2})-([0-9]{2})Z$/\1T\2:\3:\4+00:00/'
}

CUTOFF_EPOCH=$(( $(date -u +%s) - RETENTION_DAYS * 86400 ))
kept=0
deleted=0
oldest_kept=""

for snap in $(snapshots); do
  # `date -d` on busybox wants a parseable form; the ISO one works.
  snap_epoch=$(date -u -d "$(iso_of "$snap")" +%s 2>/dev/null || echo 0)
  complete=1
  aws_s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/${snap}/COMPLETE" >/dev/null 2>&1 || complete=0

  if [ "$snap_epoch" -lt "$CUTOFF_EPOCH" ] && [ "$complete" -eq 1 ]; then
    aws_s3 rm "s3://${S3_BUCKET}/${S3_PREFIX}/${snap}" --recursive --only-show-errors
    deleted=$((deleted + 1))
    echo "[prune] deleted $snap"
    continue
  fi
  if [ "$snap_epoch" -lt "$CUTOFF_EPOCH" ] && [ "$complete" -eq 0 ]; then
    echo "[prune] KEEPING incomplete snapshot $snap past retention — a half-written run is not evidence; investigate" >&2
  fi
  kept=$((kept + 1))
  [ -z "$oldest_kept" ] && oldest_kept="$snap"
done

mkdir -p "$(dirname "$MARKER")"
if [ -n "$oldest_kept" ]; then
  iso_of "$oldest_kept" > "$MARKER"
else
  # An empty bucket holds nothing, so every open deletion window may close.
  # The marker is removed rather than emptied: an unreadable marker and an
  # empty bucket must not look the same to the retention sweep.
  rm -f "$MARKER"
fi

echo "[prune] kept $kept, deleted $deleted, oldest kept ${oldest_kept:-none}"
