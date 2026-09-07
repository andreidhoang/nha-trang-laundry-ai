#!/bin/sh
# Let the backup role open a replication connection, and nothing else.
#
# **`host all all all scram-sha-256` does not cover this.** PostgreSQL treats `all` in the database
# column as "every database" and deliberately *not* as "replication" -- a replication connection
# matches only a line carrying the literal keyword. The official image's defaults grant it over the
# Unix socket and loopback, which is no use to `base-backup.sh`: it connects over the network so
# PGDATA is never shared out of the database container.
#
# Measured on the first bring-up of the pilot stack:
#
#     pg_basebackup: error: no pg_hba.conf entry for replication connection from host
#     "172.23.0.2", user "laundry_backup", no encryption
#
# Scoped to the one role that has REPLICATION. The three application roles must never match this
# line: a replication connection reads the whole cluster byte for byte, which is every privilege
# separation in this system defeated at once.

set -eu

printf 'host replication laundry_backup all scram-sha-256\n' >> "$PGDATA/pg_hba.conf"
echo "pg_hba: replication allowed for laundry_backup"
