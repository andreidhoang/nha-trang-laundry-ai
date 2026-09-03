#!/bin/sh
# A daily base backup, taken over the network from a sidecar rather than inside the database
# container, so PGDATA is never shared out of it.
#
# `laundry_backup` is a fourth role with REPLICATION and nothing else -- the three roles the
# deploy-day runbook creates are the migration, API and worker identities, and none of them may
# stream. Encrypted here for the same reason as the WAL segments: the key that writes it cannot
# read it back.

set -eu

recipients="${BACKUP_RECIPIENTS_FILE:-/run/secrets/backup_encryption_recipients}"
repository="${BACKUP_REPOSITORY_PREFIX:?BACKUP_REPOSITORY_PREFIX is required}"
label="base-$(date -u +%Y%m%dT%H%M%SZ)"

# `-X none`: the WAL this backup needs is already being archived continuously, and including it
# here would duplicate segments the restore has to reconcile.
pg_basebackup \
    --host "${BACKUP_SOURCE_HOST:-postgres}" \
    --username "${BACKUP_SOURCE_USER:-laundry_backup}" \
    --format=tar --wal-method=none --checkpoint=fast --no-password --pgdata=- \
  | age -R "$recipients" \
  | "${BACKUP_UPLOAD_COMMAND:?BACKUP_UPLOAD_COMMAND is required}" \
        --if-not-exists - "${repository}/base/${label}.tar.age"

echo "base backup ${label} archived"
