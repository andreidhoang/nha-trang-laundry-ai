#!/bin/sh
# PostgreSQL's `archive_command`. Exits non-zero unless the segment is durably off-host.
#
# **Why this does the upload rather than spooling.** The alternative -- write the segment to a local
# spool and let a sidecar ship it -- keeps the database container off the network, and makes this
# command lie. PostgreSQL is free to recycle a segment the moment `archive_command` returns 0, so a
# spool-and-ship design reports success while the only copy is still on the failing host. That
# converts a recovery guarantee into a hope, silently. The cost of doing it honestly is that this
# container needs egress to the archive repository, restricted by host firewall rules that compose
# cannot express -- stated in the runbook rather than pretended away.
#
# **Why public-key encryption.** ADR-0007 §3 and `PRODUCTION_OPERATIONS_SPEC_V1` §3.1 both require
# encryption "before leaving Host A with a key that is not stored on Host A". Taken literally that
# excludes a symmetric passphrase: a value present here to write the archive also decrypts every
# archive ever taken, so a host compromise reads all of history -- which is the exact failure the
# sentence exists to exclude. `age -R` encrypts to a recipient public key. Nothing on this host can
# read back what it wrote.
#
# **Why compress, and why before encrypting.** A WAL segment is a fixed 16MB file however little
# is in it, and `archive_timeout` deliberately ships partly-empty ones so an idle shop cannot drift
# behind its recovery point. Uncompressed that is real money: a twelve-hour trading day at a 300s
# timeout is ~144 segments, ~2.3GB/day, ~80GB a month before the 35-day retention is even counted --
# for a laundry whose actual daily writes are a few megabytes. A mostly-empty segment gzips to tens
# of kilobytes, so the same day costs single-digit megabytes.
#
# Compression has to come first because ciphertext does not compress. Doing it the other way round
# is a common and silent mistake: the pipeline still works, the archive is still correct, and it
# costs a hundred times more forever.
#
# Arguments are PostgreSQL's: %p is the path to the segment, %f is its name.

set -eu

source_path="$1"
segment_name="$2"

recipients="${BACKUP_RECIPIENTS_FILE:-/run/secrets/backup_encryption_recipients}"
repository="${BACKUP_REPOSITORY_PREFIX:?BACKUP_REPOSITORY_PREFIX is required}"

encrypted="/tmp/${segment_name}.gz.age"
trap 'rm -f "$encrypted"' EXIT INT TERM

# `set -o pipefail` is not in POSIX sh, so the pipeline's success is asserted rather than assumed:
# without this a gzip failure would be masked by age exiting 0 on empty input, and the command would
# report a segment archived that is not.
if ! gzip -c "$source_path" | age -R "$recipients" -o "$encrypted"; then
    echo "archive-wal: compress/encrypt failed for ${segment_name}" >&2
    exit 1
fi
[ -s "$encrypted" ] || { echo "archive-wal: produced an empty artifact for ${segment_name}" >&2; exit 1; }

# `--if-not-exists`: the repository is versioned and append-only, and an archive command that
# overwrites is an archive command that can destroy history on a retry. A segment name is unique
# by construction, so a collision means something is wrong rather than something is repeated.
"${BACKUP_UPLOAD_COMMAND:?BACKUP_UPLOAD_COMMAND is required}" \
    --if-not-exists "$encrypted" "${repository}/wal/${segment_name}.gz.age"
