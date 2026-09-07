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

# The recovery target in UTC, as YYYYMMDDHHMMSS, so it can be compared to the labels the base
# backups carry. `busybox date -d` parses "YYYY-MM-DD hh:mm:ss" as UTC and rejects an offset, so the
# offset is applied by hand.
_target_stamp() {
    _dt=$(printf '%s' "$target_time" | sed -E 's/([+-][0-9]{2}:?[0-9]{2}?|Z)$//' | sed -E 's/\.[0-9]+$//')
    _off=$(printf '%s' "$target_time" | sed -nE 's/.*([+-][0-9]{2}:?[0-9]{2}?|Z)$/\1/p')
    _epoch=$(date -u -d "$_dt" +%s 2>/dev/null) || {
        echo "refusing: RECOVERY_TARGET_TIME is not a timestamp this can read: $target_time" >&2
        echo "use e.g. '2026-09-03 14:05:00+07'" >&2
        exit 6
    }
    case "$_off" in
        ""|Z) _shift=0 ;;
        *)
            _sign=$(printf '%s' "$_off" | cut -c1)
            _hh=$(printf '%s' "$_off" | cut -c2-3)
            _mm=$(printf '%s' "$_off" | tr -d ':' | cut -c4-5)
            [ -n "$_mm" ] || _mm=0
            _shift=$(( 10#$_hh * 3600 + 10#$_mm * 60 ))
            [ "$_sign" = "-" ] && _shift=$(( -_shift ))
            ;;
    esac
    date -u -d "@$(( _epoch - _shift ))" +%Y%m%d%H%M%S
}
target_stamp=$(_target_stamp)

# **Newest at or before the target, not newest overall.** A base backup taken *after* the recovery
# target cannot be recovered from -- PostgreSQL stops with `could not locate required checkpoint
# record`, hours into the drill -- and that is the ordinary case rather than an edge one. Backups
# run nightly; recovering from something that went wrong yesterday afternoon means reaching for the
# backup from before it, not the one taken since. Measured: restoring to 05:31:50 picked the 05:31:57
# backup and the server refused to start.
#
# Still newest-first among the eligible, and still not newest-only: a truncated artifact sorts
# newest and used to be the sole candidate, so a good backup from seconds earlier was never tried.
all_candidates="$("$list_command" "${repository}/base/" | sort -r)"
[ -n "$all_candidates" ] || { echo "no base backup found in ${repository}/base/" >&2; exit 3; }

candidates=""
skipped=""
# Splitting on newlines only, so an object path containing a space stays one candidate.
IFS='
'
for candidate in $all_candidates; do
    # `base-20260907T053157Z.tar.gz.age` -> `20260907053157`
    stamp=$(printf '%s' "${candidate##*/}" | sed -nE 's/^base-([0-9]{8})T([0-9]{6})Z.*/\1\2/p')
    if [ -z "$stamp" ]; then
        echo "ignoring an object whose name is not a base-backup label: $candidate" >&2
        continue
    fi
    if [ "$stamp" -le "$target_stamp" ]; then
        candidates="${candidates}${candidate}
"
    else
        skipped="${skipped}  $candidate (taken after the target)
"
    fi
done
unset IFS

[ -z "$skipped" ] || { echo "not eligible for this recovery target:" >&2; printf '%s' "$skipped" >&2; }
[ -n "$candidates" ] || {
    echo "no base backup was taken at or before ${target_time}; the earliest recovery point is" >&2
    echo "the oldest backup in ${repository}/base/. Nothing was changed." >&2
    exit 7
}

# One line at a time, not `for candidate in $candidates`: that word-splits, so a repository prefix
# or an object name containing a space would be torn into fragments and every fetch would miss.
# A space in these paths is expected rather than exotic -- the identity arrives on removable media
# and "/Volumes/USB DRIVE/" is the ordinary shape of that.
restored=""
while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
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
done <<CANDIDATES
$candidates
CANDIDATES

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
