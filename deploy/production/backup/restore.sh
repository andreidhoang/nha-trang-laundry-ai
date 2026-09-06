#!/bin/sh
# Restore to a point in time, on a clean host. The executable core of
# `docs/runbooks/restore-drill.md`.
#
# **The decryption identity is not on this host and is not in this repository.** It is handed over
# by whoever holds it, at drill time, inside the four-hour clock -- which is why the runbook counts
# that handover as a timed step rather than a preamble. `DEC-026` decides who holds it.
#
# This script does not delete anything and does not touch the source host. A restore that can write
# to the thing it is recovering from is not a restore.

set -eu

identity="${BACKUP_IDENTITY_FILE:?BACKUP_IDENTITY_FILE is required (the age private key, supplied at drill time)}"
repository="${BACKUP_REPOSITORY_PREFIX:?BACKUP_REPOSITORY_PREFIX is required}"
target_time="${RECOVERY_TARGET_TIME:?RECOVERY_TARGET_TIME is required, e.g. 2026-09-03 14:05:00+07}"
data_directory="${PGDATA:?PGDATA is required and must be empty}"

if [ -n "$(ls -A "$data_directory" 2>/dev/null)" ]; then
    echo "refusing: $data_directory is not empty. Restore to a clean directory." >&2
    exit 2
fi

latest="$("${BACKUP_LIST_COMMAND:?BACKUP_LIST_COMMAND is required}" "${repository}/base/" | sort | tail -1)"
[ -n "$latest" ] || { echo "no base backup found in ${repository}/base/" >&2; exit 3; }
echo "restoring from $latest to $target_time"

"${BACKUP_FETCH_COMMAND:?BACKUP_FETCH_COMMAND is required}" "$latest" \
  | age -d -i "$identity" \
  | gzip -dc \
  | tar -x -C "$data_directory"

# `restore_command` fetches and decrypts one segment at a time, so the private key is needed for
# the whole of recovery and not only for the base backup.
cat > "$data_directory/postgresql.auto.conf" <<CONF
restore_command = '${BACKUP_FETCH_COMMAND} ${repository}/wal/%f.gz.age | age -d -i ${identity} | gzip -dc > %p'
recovery_target_time = '${target_time}'
recovery_target_action = 'promote'
CONF
touch "$data_directory/recovery.signal"

echo "data directory prepared. Start PostgreSQL against it and wait for promotion, then run:"
echo "  uv run python scripts/validate_restore_drill.py --evidence <drill.json> --database-url ..."
