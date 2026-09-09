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

compressed="/tmp/${segment_name}.gz"
encrypted="/tmp/${segment_name}.gz.age"
trap 'rm -f "$compressed" "$encrypted"' EXIT INT TERM

# **The two stages do not share a pipeline, and that is the whole point.** This used to read
# `if ! gzip -c "$source_path" | age -R ... ; then`, above a comment claiming the pipeline's success
# was asserted rather than assumed. It was not: `set -o pipefail` is not POSIX, and the exit status
# of a pipeline in POSIX sh is the status of its LAST command. A gzip that died part-way -- a read
# error on the segment, a full filesystem -- still fed `age` whatever it had managed to write, and
# `age` encrypted that happily and exited 0. The `-s` guard below catches an empty artifact and a
# truncated one is not empty, so the command returned 0 and PostgreSQL was free to recycle a segment
# whose only archived copy was short. Nothing would notice until a restore reached that segment,
# which is the one moment nobody can afford to find out.
#
# Staging to a file makes each status its own, and buys two checks a pipeline cannot have.
if ! gzip -c "$source_path" > "$compressed"; then
    echo "archive-wal: compression failed for ${segment_name}" >&2
    exit 1
fi

# `gzip -t` walks the stream and verifies its CRC and length trailer, so a truncated member is
# refused here rather than shipped.
if ! gzip -t "$compressed"; then
    echo "archive-wal: compressed segment ${segment_name} is corrupt" >&2
    exit 1
fi

# And the decisive one: gzip records the uncompressed length, so this compares what was read against
# what the segment actually is. A short read that happened to end on a member boundary passes the
# CRC and fails this.
source_bytes=$(wc -c < "$source_path")
stored_bytes=$(gzip -l "$compressed" | awk 'NR==2 {print $2}')
if [ "$source_bytes" -ne "$stored_bytes" ]; then
    echo "archive-wal: ${segment_name} is ${stored_bytes} bytes compressed from ${source_bytes}" >&2
    exit 1
fi

if ! age -R "$recipients" -o "$encrypted" < "$compressed"; then
    echo "archive-wal: encryption failed for ${segment_name}" >&2
    exit 1
fi
[ -s "$encrypted" ] || { echo "archive-wal: produced an empty artifact for ${segment_name}" >&2; exit 1; }

# `--if-not-exists`: the repository is versioned and append-only, and an archive command that
# overwrites is an archive command that can destroy history on a retry. A segment name is unique
# by construction, so a collision means something is wrong rather than something is repeated.
"${BACKUP_UPLOAD_COMMAND:?BACKUP_UPLOAD_COMMAND is required}" \
    --if-not-exists "$encrypted" "${repository}/wal/${segment_name}.gz.age"
