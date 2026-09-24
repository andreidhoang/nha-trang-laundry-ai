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
# backups carry.
#
# **Pure POSIX arithmetic, no `date -d` and no `10#`.** The previous version used both and ran only
# under busybox, which is where it was measured. On the Debian/Ubuntu drill host the runbook sets up,
# `/bin/sh` is dash, which has no `10#` radix prefix: the runbook's own target '...+07' aborted with
# "arithmetic expression: expecting EOF" (exit 2) before anything was restored. On macOS, the
# owner's machines, BSD `date` has no `-d` at all. Days-from-civil and its inverse (Hinnant's
# algorithms) need only integer `$(( ))`, which every POSIX shell has, so the conversion is the same
# on every host that can run the rest of this script.
_decimal() {
    # "07" -> 7 without the `10#` prefix dash rejects; "08" would otherwise be read as octal.
    _v=$1
    while [ "${#_v}" -gt 1 ] && [ "${_v#0}" != "$_v" ]; do _v=${_v#0}; done
    printf '%s' "$_v"
}
_refuse_target() {
    echo "refusing: RECOVERY_TARGET_TIME is not a timestamp this can read: $target_time" >&2
    echo "use e.g. '2026-09-03 14:05:00+07'" >&2
    exit 6
}
_target_stamp() {
    _dt=$(printf '%s' "$target_time" | sed -E 's/([+-][0-9]{2}:?[0-9]{2}?|Z)$//' | sed -E 's/\.[0-9]+$//')
    _off=$(printf '%s' "$target_time" | sed -nE 's/.*([+-][0-9]{2}:?[0-9]{2}?|Z)$/\1/p')
    case "$_dt" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][\ T][0-9][0-9]:[0-9][0-9]:[0-9][0-9]) ;;
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][\ T][0-9][0-9]:[0-9][0-9]) _dt="${_dt}:00" ;;
        *) _refuse_target ;;
    esac
    _y=$(_decimal "$(printf '%s' "$_dt" | cut -c1-4)")
    _mo=$(_decimal "$(printf '%s' "$_dt" | cut -c6-7)")
    _d=$(_decimal "$(printf '%s' "$_dt" | cut -c9-10)")
    _h=$(_decimal "$(printf '%s' "$_dt" | cut -c12-13)")
    _mi=$(_decimal "$(printf '%s' "$_dt" | cut -c15-16)")
    _s=$(_decimal "$(printf '%s' "$_dt" | cut -c18-19)")
    [ "$_mo" -ge 1 ] && [ "$_mo" -le 12 ] && [ "$_d" -ge 1 ] && [ "$_d" -le 31 ] \
        && [ "$_h" -le 23 ] && [ "$_mi" -le 59 ] && [ "$_s" -le 59 ] || _refuse_target
    case "$_off" in
        ""|Z) _shift=0 ;;
        *)
            _sign=$(printf '%s' "$_off" | cut -c1)
            _hh=$(_decimal "$(printf '%s' "$_off" | cut -c2-3)")
            _mm=$(printf '%s' "$_off" | tr -d ':' | cut -c4-5)
            [ -n "$_mm" ] || _mm=0
            _mm=$(_decimal "$_mm")
            _shift=$(( _hh * 3600 + _mm * 60 ))
            [ "$_sign" = "-" ] && _shift=$(( -_shift ))
            ;;
    esac
    # days from 1970-01-01 to the civil date
    _yy=$(( _mo <= 2 ? _y - 1 : _y ))
    _era=$(( (_yy >= 0 ? _yy : _yy - 399) / 400 ))
    _yoe=$(( _yy - _era * 400 ))
    _doy=$(( (153 * (_mo > 2 ? _mo - 3 : _mo + 9) + 2) / 5 + _d - 1 ))
    _doe=$(( _yoe * 365 + _yoe / 4 - _yoe / 100 + _doy ))
    _days=$(( _era * 146097 + _doe - 719468 ))
    _epoch=$(( _days * 86400 + _h * 3600 + _mi * 60 + _s - _shift ))
    # and back, in UTC
    _z=$(( _epoch / 86400 ))
    _rem=$(( _epoch - _z * 86400 ))
    if [ "$_rem" -lt 0 ]; then _rem=$(( _rem + 86400 )); _z=$(( _z - 1 )); fi
    _z=$(( _z + 719468 ))
    _era=$(( (_z >= 0 ? _z : _z - 146096) / 146097 ))
    _doe=$(( _z - _era * 146097 ))
    _yoe=$(( (_doe - _doe / 1460 + _doe / 36524 - _doe / 146096) / 365 ))
    _doy=$(( _doe - (365 * _yoe + _yoe / 4 - _yoe / 100) ))
    _mp=$(( (5 * _doy + 2) / 153 ))
    _od=$(( _doy - (153 * _mp + 2) / 5 + 1 ))
    _om=$(( _mp < 10 ? _mp + 3 : _mp - 9 ))
    _oy=$(( _yoe + _era * 400 + (_om <= 2 ? 1 : 0) ))
    printf '%04d%02d%02d%02d%02d%02d\n' "$_oy" "$_om" "$_od" \
        $(( _rem / 3600 )) $(( (_rem % 3600) / 60 )) $(( _rem % 60 ))
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
