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

list_command="${BACKUP_LIST_COMMAND:?BACKUP_LIST_COMMAND is required}"
fetch_command="${BACKUP_FETCH_COMMAND:?BACKUP_FETCH_COMMAND is required}"

# Everything below is interpolated into a `restore_command` that PostgreSQL parses for `%`
# placeholders and then hands to a shell. Three separate failures came out of not checking this,
# and all three reported success and then failed hours later when PostgreSQL was started -- inside
# the four-hour clock, after the visible step said "data directory prepared":
#
#   a space in the identity path  -> FATAL: could not locate required checkpoint record
#   a `%` in the repository path  -> FATAL: invalid value for parameter "restore_command"
#   a `'` in the identity path    -> FATAL: configuration file contains errors
#
# The runbook has the key handed over on removable media at drill time, so "/Volumes/USB DRIVE/"
# is the expected shape, not an exotic one. Paths are quoted inside the command below, which makes
# spaces safe; the characters that quoting cannot save are refused here, loudly, before anything
# is written.
for candidate_name in identity repository fetch_command; do
    eval "candidate_value=\${$candidate_name}"
    case "$candidate_value" in
        *%*|*\'*|*\"*|*\$*|*\`*|*\\*)
            echo "refusing: \$$candidate_name contains a character that cannot appear in a" >&2
            echo "restore_command (one of % ' \" \$ \` \\): $candidate_value" >&2
            exit 4
            ;;
    esac
done

# Newest first, but not newest only. A truncated or empty base backup sorts newest and used to be
# the one and only candidate, so a good backup from seconds earlier was never tried.
candidates="$("$list_command" "${repository}/base/" | sort -r)"
[ -n "$candidates" ] || { echo "no base backup found in ${repository}/base/" >&2; exit 3; }

restored=""
for candidate in $candidates; do
    echo "trying $candidate"
    if "$fetch_command" "$candidate" \
      | age -d -i "$identity" \
      | gzip -dc \
      | tar -x -C "$data_directory"; then
        # `tar` succeeding is not enough on its own: a truncated stream can extract a prefix. A
        # base backup without a control file is not one.
        if [ -s "$data_directory/global/pg_control" ]; then
            restored="$candidate"
            break
        fi
        echo "  $candidate extracted without a control file; discarding it" >&2
    else
        echo "  $candidate could not be extracted" >&2
    fi
    # A partial extraction must not be mistaken for the next candidate's work.
    rm -rf "${data_directory:?}/"* "${data_directory:?}/".[!.]* 2>/dev/null || true
done

[ -n "$restored" ] || {
    echo "no base backup in ${repository}/base/ could be restored; tried:" >&2
    echo "$candidates" >&2
    exit 5
}
echo "restored from $restored to $target_time"

# `restore_command` fetches and decrypts one segment at a time, so the private key is needed for
# the whole of recovery and not only for the base backup. The inner double quotes are what make a
# path with a space work; the outer single quotes are PostgreSQL's.
cat > "$data_directory/postgresql.auto.conf" <<CONF
restore_command = '"${fetch_command}" "${repository}/wal/%f.gz.age" | age -d -i "${identity}" | gzip -dc > %p'
recovery_target_time = '${target_time}'
recovery_target_action = 'promote'
CONF
touch "$data_directory/recovery.signal"

echo "data directory prepared. Start PostgreSQL against it and wait for promotion, then run:"
echo "  uv run python scripts/validate_restore_drill.py --evidence <drill.json> \\"
echo "      --database-url-file <path to a file holding the DSN>"
