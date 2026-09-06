#!/bin/sh
# Write one archived object to stdout.
#
# Called two ways, and both matter. `restore.sh` runs it directly for the base backup; PostgreSQL
# runs it once per WAL segment through the `restore_command` this repository generates, where it is
# quoted as a single executable. A multi-word value for `BACKUP_FETCH_COMMAND` -- "rclone cat" --
# does not work in either place, which is why this exists as a script rather than as a setting an
# operator is expected to compose correctly under time pressure.
#
# Exits non-zero when the object is absent, which is what tells PostgreSQL it has reached the end of
# the archive rather than that recovery has failed.

set -eu

object="${1:?r1-archive-fetch: an object path is required}"
config="${RCLONE_CONFIG:?RCLONE_CONFIG is required (the rclone configuration naming the archive)}"

exec rclone --config "$config" cat "$object"
