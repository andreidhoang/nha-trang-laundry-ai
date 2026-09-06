#!/bin/sh
# The upload client `archive_command` and `base-backup.sh` invoke, as one executable.
#
# **This exists because the contract had no implementation.** Both scripts call
# `"$BACKUP_UPLOAD_COMMAND" --if-not-exists <local> <destination>` and the runbooks said
# `export R1_BACKUP_UPLOAD_COMMAND=<your rclone/aws wrapper>` with no step that put a wrapper
# anywhere. The image had no object-store client at all -- not rclone, aws, s3cmd, mc, curl, gsutil
# or az -- `--no-cache` left no apk index, and `read_only: true` with `cap_drop: ALL` meant one
# could not be installed at runtime. So `archive_command` failed on every segment, which
# `deploy-today.md` itself warns is the failure that fills the disk and stops the shop taking
# orders. The thing SHOP-PILOT-001 calls "the single point on which the pilot's safety rests" could
# not run as shipped.
#
# **`--if-not-exists` is checked here, not hoped for.** A client that does not know the flag
# receives it as a positional argument and exits 0, so the guard failed open and the callers could
# not tell. An unknown first argument is a hard error.
#
# **Overwrite protection is the object store's job, and this is the second line.** The `lsf` check
# below races with a concurrent writer; a repository configured for versioning and object lock is
# what makes it true, which `DEC-026` requires and the hosting decision records.

set -eu

case "${1:-}" in
    --if-not-exists) shift ;;
    *)
        echo "r1-archive-upload: expected --if-not-exists as the first argument, got '${1:-}'" >&2
        exit 64
        ;;
esac

source_path="${1:?r1-archive-upload: a local file is required}"
destination="${2:?r1-archive-upload: a destination is required}"

[ -s "$source_path" ] || {
    echo "r1-archive-upload: refusing to upload an empty or missing file: $source_path" >&2
    exit 66
}

# `RCLONE_CONFIG` points at the mounted credential. rclone reads it and nothing else: `--config`
# with an explicit path stops it searching a home directory that does not exist on a read-only
# root filesystem.
config="${RCLONE_CONFIG:-/run/secrets/backup_repository_credential}"
[ -r "$config" ] || {
    echo "r1-archive-upload: no readable rclone configuration at $config" >&2
    exit 67
}

if [ -n "$(rclone --config "$config" lsf "$destination" 2>/dev/null)" ]; then
    echo "r1-archive-upload: refusing to overwrite an existing object: $destination" >&2
    exit 65
fi

# `--immutable` makes rclone itself fail rather than replace, so a race that beats the check above
# still cannot destroy history.
exec rclone --config "$config" --immutable copyto "$source_path" "$destination"
