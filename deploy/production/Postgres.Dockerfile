# PostgreSQL for the self-managed branch, with the two binaries `archive_command` needs.
#
# `SHOP-RECOVERY-001` wrote the archiving against `age` and `gzip` and recorded that neither was in
# the image -- so the self-managed branch did not work, stated then rather than discovered at
# cutover. This is that gap closed.
#
# **Why `age` and not a symmetric passphrase.** ADR-0007 §3 requires encryption "before leaving
# Host A with a key that is not stored on Host A". A passphrase present here to write the archive
# also decrypts every archive ever taken, so a host compromise reads all of the shop's history --
# the exact failure that sentence excludes. `age -R` encrypts to a recipient public key and nothing
# on this host can read back what it wrote.
#
# **Why `gzip` matters as much as `age`.** A WAL segment is a fixed 16MB file however little is in
# it, and `archive_timeout` deliberately ships partly-empty ones so an idle shop cannot drift behind
# its recovery point. Compressed first -- ciphertext does not compress -- a mostly-empty segment
# costs tens of kilobytes instead of sixteen megabytes.

ARG POSTGRES_BASE="postgres:16.10-alpine"
FROM ${POSTGRES_BASE}

# Pinned exactly, like every other dependency in this repository. `--no-cache` leaves no index
# behind, so the running image cannot install anything else, and the read-only root filesystem in
# `compose.r1.yaml` means it could not persist it if it tried.
# `rclone` is here because the archiving contract had no implementation without it. Both
# `archive-wal.sh` and `base-backup.sh` exec `$BACKUP_UPLOAD_COMMAND`, and this image contained no
# object-store client of any kind -- so `archive_command` failed on every segment, WAL pinned
# forever, and the disk filled until the shop could not take an order. `deploy-today.md` describes
# that failure precisely while shipping the image that guarantees it.
RUN apk add --no-cache \
        age=1.2.1-r10 \
        gzip=1.14-r2 \
        rclone=1.69.3-r5

# The archive command is mounted as a config at runtime rather than baked in, so it can be reviewed
# in a diff and changed without rebuilding the database image. The directory has to exist for the
# read-only mount to land.
USER 70:70
