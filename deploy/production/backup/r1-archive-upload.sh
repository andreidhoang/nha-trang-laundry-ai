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

# Two modes, because the two callers mean different things by "it is already there".
#
#   --if-not-exists   strict. An existing object is an error. Base backups: the label carries a
#                     timestamp, so two cannot legitimately share a name and a collision is the
#                     administrator error the PostgreSQL manual has in mind.
#   --idempotent      an existing, non-empty object means this segment is already archived, which
#                     is success. WAL: PostgreSQL re-offers a segment whenever it stops between the
#                     archive succeeding and the `.ready` file being renamed -- an unclean shutdown,
#                     which on a shop's own Mac means a closed lid or a power cut.
#
# **The strict mode used to serve both, and that wedges the archiver.** `archive_command` returning
# non-zero makes PostgreSQL retry that same segment indefinitely: it never advances, WAL is never
# recycled, the volume fills and the server stops accepting writes -- the failure `deploy-today.md`
# names as the one that stops the counter taking orders. `archive-wal.sh` justified the strictness
# with "a segment name is unique by construction, so a collision means something is wrong rather
# than something is repeated", and that premise is false: a name is unique per timeline, and the
# same name being offered twice is ordinary.
#
# Reporting success does not weaken `DEC-026`. Nothing is overwritten -- the upload is skipped --
# and the segment genuinely is archived; only what PostgreSQL is told about an already-true fact
# changes. An *empty* object is the exception and stays a hard refusal in both modes: that is a
# broken earlier write, and waving it through would report a segment safe when what is stored
# cannot be replayed.
mode="strict"
case "${1:-}" in
    --if-not-exists) shift ;;
    --idempotent) mode="idempotent"; shift ;;
    *)
        echo "r1-archive-upload: expected --if-not-exists or --idempotent as the first argument," \
             "got '${1:-}'" >&2
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
    if [ "$mode" = "idempotent" ]; then
        # `lsf --format s` prints the size. A zero-length object is a broken earlier write rather
        # than an archived segment, and is refused in both modes.
        stored_size="$(rclone --config "$config" lsf --format s "$destination" 2>/dev/null | head -n 1)"
        if [ "${stored_size:-0}" -gt 0 ] 2>/dev/null; then
            echo "r1-archive-upload: already archived, nothing to do: $destination" >&2
            exit 0
        fi
        echo "r1-archive-upload: an empty object is already stored at $destination;" \
             "that is a broken earlier write and needs a human" >&2
        exit 65
    fi
    echo "r1-archive-upload: refusing to overwrite an existing object: $destination" >&2
    exit 65
fi

# `--immutable` makes rclone itself fail rather than replace, so a race that beats the check above
# still cannot destroy history.
exec rclone --config "$config" --immutable copyto "$source_path" "$destination"
