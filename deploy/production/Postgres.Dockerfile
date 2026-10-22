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

ARG POSTGRES_BASE="postgres:16.15-alpine3.23@sha256:621a761097839bdb50207afd6b87a72f38e2d718dd46c3d744828d8917c4f1e0"
ARG GO_BASE="golang:1.26.9-alpine3.23@sha256:6750308561953d451210ed1a8086d36c8c8bdcbf672d38d9d0d424b8a7a0216c"
ARG RCLONE_BASE="rclone/rclone:1.75.2@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5"

# PostgreSQL's upstream image still ships gosu 1.19 compiled with Go 1.24.6.
# Rebuild the same gosu release/commit with the patched compiler, retaining the
# entrypoint's privilege drop instead of deleting or bypassing it to satisfy a scan.
FROM ${GO_BASE} AS gosu-build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=2
RUN go install -trimpath github.com/tianon/gosu@6456aaa0f3c854d199d0f037f068eb97515b7513 \
    && go version -m /go/bin/gosu

# Alpine's packaged backup clients embed older Go libraries that precise image scans
# omit. Keep the encryption/upload capabilities using patched, versioned binaries;
# the release gate also scans embedded libraries with comprehensive detection.
FROM ${GO_BASE} AS age-build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=2
RUN go install -trimpath filippo.io/age/cmd/age@v1.3.2 filippo.io/age/cmd/age-keygen@v1.3.2 \
    && go version -m /go/bin/age /go/bin/age-keygen

FROM ${RCLONE_BASE} AS rclone-binary

# Development/demo and local verification use this target; the self-managed deployment adds its
# existing backup clients below. Both retain upstream POSTGRES_* and PGDATA behavior.
FROM ${POSTGRES_BASE} AS postgres-runtime
COPY --from=gosu-build /go/bin/gosu /usr/local/bin/gosu
RUN gosu --version && gosu nobody true

FROM postgres-runtime AS postgres-backup

# `archive-wal.sh` and `base-backup.sh` require all three clients. Installing only
# compression/encryption leaves every upload failing and pins WAL until disk exhaustion.
# The rclone image is used only as a verified binary source, not as the database base.
RUN apk add --no-cache gzip=1.14-r3
COPY --from=age-build /go/bin/age /go/bin/age-keygen /usr/local/bin/
COPY --from=rclone-binary /usr/local/bin/rclone /usr/local/bin/rclone

# The archive command is mounted as a config at runtime rather than baked in, so it can be reviewed
# in a diff and changed without rebuilding the database image. The directory has to exist for the
# read-only mount to land.

# The base backup stages here before anything leaves the host, and the root filesystem is read-only,
# so this has to be a volume. **It has to exist in the image, owned by 70:70, or it does not work.**
# Docker seeds an empty named volume from the image's directory at that path -- content *and*
# ownership -- and when the path is absent it creates the volume `root:root 0755` instead. Measured
# on the first bring-up of the pilot stack: every `archive_command` failed with
# `mkdir /var/lib/postgresql/backup-staging/objectstore: permission denied`, twelve times, while
# every service read healthy. The same would have happened on the cloud host.
RUN mkdir -p /var/lib/postgresql/backup-staging \
    && chown 70:70 /var/lib/postgresql/backup-staging \
    && chmod 0700 /var/lib/postgresql/backup-staging

USER 70:70
