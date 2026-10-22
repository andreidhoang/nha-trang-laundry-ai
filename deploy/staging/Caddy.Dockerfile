ARG CADDY_BASE="caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f"
ARG GO_BASE="golang:1.26.9-alpine3.23@sha256:6750308561953d451210ed1a8086d36c8c8bdcbf672d38d9d0d424b8a7a0216c"

# The upstream 2.11.7 binary predates the October Go and HTTP/2 security fixes.
# Build its unchanged standard entrypoint as a dependency (preserving version metadata),
# with only the affected x/net module raised to its patched version. Go's checksum database
# verifies the versioned modules; no compiler or module cache enters the runtime image.
FROM ${GO_BASE} AS caddy-build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=2
WORKDIR /src
RUN go mod init laundry.local/caddy-build \
    && go mod download github.com/caddyserver/caddy/v2@v2.11.7 \
    && cp /go/pkg/mod/github.com/caddyserver/caddy/v2@v2.11.7/cmd/caddy/main.go . \
    && go get github.com/caddyserver/caddy/v2@v2.11.7 golang.org/x/net@v0.60.0 \
    && go mod tidy \
    && go build -p 2 -trimpath -o /out/caddy . \
    && go version -m /out/caddy \
    && /out/caddy version

FROM ${CADDY_BASE}

USER root
RUN apk add --no-cache --virtual .cap-tools libcap \
    && setcap -r /usr/bin/caddy \
    && apk del --no-network .cap-tools \
    && addgroup -g 10004 -S staging-tls \
    && adduser -u 10004 -S -D -H -s /sbin/nologin -G staging-tls staging-tls

COPY --from=caddy-build /out/caddy /usr/bin/caddy
USER 10004:10004
