#!/bin/sh
# List objects under one prefix in the archive repository, one absolute path per line.
#
# `restore.sh` calls this as `"$BACKUP_LIST_COMMAND" "<prefix>/base/"` and sorts the result, so the
# output must be complete paths that `BACKUP_FETCH_COMMAND` can be handed back verbatim -- not the
# bare filenames `rclone lsf` prints.
#
# The read side of the same rclone configuration `r1-archive-upload.sh` writes with. A drill runs on
# a clean machine, so this is copied there along with the fetch script and the key.

set -eu

prefix="${1:?r1-archive-list: a repository prefix is required}"
config="${RCLONE_CONFIG:?RCLONE_CONFIG is required (the rclone configuration naming the archive)}"

# `--files-only`: a directory entry is not an object, and `sort | tail` would happily pick one.
rclone --config "$config" lsf --files-only "$prefix" | while IFS= read -r name; do
    [ -n "$name" ] || continue
    printf '%s%s\n' "$prefix" "$name"
done
