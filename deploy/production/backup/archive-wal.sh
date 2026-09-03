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
# Arguments are PostgreSQL's: %p is the path to the segment, %f is its name.

set -eu

source_path="$1"
segment_name="$2"

recipients="${BACKUP_RECIPIENTS_FILE:-/run/secrets/backup_encryption_recipients}"
repository="${BACKUP_REPOSITORY_PREFIX:?BACKUP_REPOSITORY_PREFIX is required}"

encrypted="/tmp/${segment_name}.age"
trap 'rm -f "$encrypted"' EXIT INT TERM

age -R "$recipients" -o "$encrypted" "$source_path"

# `--if-not-exists`: the repository is versioned and append-only, and an archive command that
# overwrites is an archive command that can destroy history on a retry. A segment name is unique
# by construction, so a collision means something is wrong rather than something is repeated.
"${BACKUP_UPLOAD_COMMAND:?BACKUP_UPLOAD_COMMAND is required}" \
    --if-not-exists "$encrypted" "${repository}/wal/${segment_name}.age"
