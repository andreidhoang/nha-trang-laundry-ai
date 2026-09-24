#!/bin/sh
# A daily base backup, taken over the network from a sidecar rather than inside the database
# container, so PGDATA is never shared out of it.
#
# `laundry_backup` is a fourth role with REPLICATION and nothing else -- the three roles the
# deploy-day runbook creates are the migration, API and worker identities, and none of them may
# stream. Encrypted here for the same reason as the WAL segments: the key that writes it cannot
# read it back.
#
# **Staged to disk before upload, deliberately.** The first version streamed
# `pg_basebackup | gzip | age | upload` in one pipeline. POSIX `sh` has no `pipefail`, so `set -e`
# saw only the upload's status: with `pg_basebackup` absent entirely the script printed
# "base backup ... archived", exited 0, and stored a 220-byte object that decrypted to zero bytes
# of tar. `restore.sh` then took `sort | tail -1` and selected exactly that one, in preference to
# the good backup from nine seconds earlier.
#
# Streaming cannot be made safe here: by the time the failure is visible the partial object has
# already been written, and `DEC-026` requires a credential that cannot delete, so nobody on this
# host can remove it. Staging costs disk -- a single shop's database is megabytes -- and buys the
# property that only a complete, verified artifact is ever uploaded. `archive-wal.sh` reached the
# same conclusion for segments and this script simply had not.

set -eu

recipients="${BACKUP_RECIPIENTS_FILE:-/run/secrets/backup_encryption_recipients}"

# `pg_basebackup --no-password` never prompts, and `pg_hba` requires scram for the replication
# connection, so without this the backup fails authentication with no way to supply one. The value
# is read from a mounted secret into the environment of this process only -- it is not in the
# compose file, not in `docker inspect`, and never on a command line where `ps` would show it.
source_password_file="${BACKUP_SOURCE_PASSWORD_FILE:-/run/secrets/backup_source_password}"
if [ -r "$source_password_file" ]; then
    PGPASSWORD="$(cat "$source_password_file")"
    export PGPASSWORD
fi
repository="${BACKUP_REPOSITORY_PREFIX:?BACKUP_REPOSITORY_PREFIX is required}"
upload="${BACKUP_UPLOAD_COMMAND:?BACKUP_UPLOAD_COMMAND is required}"
label="base-$(date -u +%Y%m%dT%H%M%SZ)"

staging="${BACKUP_STAGING_DIR:-/var/lib/postgresql/backup-staging}"
mkdir -p "$staging"
work="$(mktemp -d "${staging}/${label}.XXXXXX")"
trap 'rm -rf "$work"' EXIT INT TERM

# `-X none`: the WAL this backup needs is already being archived continuously, and including it
# here would duplicate segments the restore has to reconcile.
if ! pg_basebackup \
    --host "${BACKUP_SOURCE_HOST:-postgres}" \
    --username "${BACKUP_SOURCE_USER:-laundry_backup}" \
    --format=tar --wal-method=none --checkpoint=fast --no-password \
    --pgdata="$work"; then
    echo "base-backup: pg_basebackup failed for ${label}; nothing was uploaded" >&2
    exit 1
fi

archive="${work}/base.tar"
[ -s "$archive" ] || {
    echo "base-backup: ${label} produced no base.tar; nothing was uploaded" >&2
    exit 1
}

# Two stages, two statuses -- the defect `archive-wal.sh` already documents and fixed, which this
# file kept. `gzip | age` in one pipeline reports only `age`'s status in POSIX sh, so a gzip that
# died part-way (a read error, a full staging volume) fed `age` a prefix, `age` encrypted it and
# exited 0, the `-s` guard below passed because a truncated artifact is not empty, and the success
# marker was written for a backup that cannot be restored. `gzip -t` then proves the stream is whole
# before anything is encrypted, let alone uploaded.
compressed="${work}/${label}.tar.gz"
encrypted="${work}/${label}.tar.gz.age"
if ! gzip -c "$archive" > "$compressed"; then
    echo "base-backup: compression failed for ${label}; nothing was uploaded" >&2
    exit 1
fi
if ! gzip -t "$compressed"; then
    echo "base-backup: compressed stream for ${label} failed verification; nothing was uploaded" >&2
    exit 1
fi
if ! age -R "$recipients" -o "$encrypted" "$compressed"; then
    echo "base-backup: encryption failed for ${label}; nothing was uploaded" >&2
    exit 1
fi
rm -f "$compressed"
[ -s "$encrypted" ] || {
    echo "base-backup: ${label} encrypted to an empty artifact; nothing was uploaded" >&2
    exit 1
}

# Only now, with a complete artifact on disk, does anything leave the host.
if ! "$upload" --if-not-exists "$encrypted" "${repository}/base/${label}.tar.gz.age"; then
    echo "base-backup: upload failed for ${label}" >&2
    exit 1
fi

# A marker with the size, written only on success, so `check_shop_operations.py --check base` can
# answer "when did a base backup last complete?" without a repository credential of its own. WAL
# archiving with no base backup restores nothing, and until now nothing anywhere noticed.
printf '%s %s\n' "$label" "$(wc -c < "$encrypted" | tr -d ' ')" > "${staging}/last-success"

echo "base backup ${label} archived ($(wc -c < "$encrypted") bytes)"
