ARG CADDY_BASE="caddy:2.10.2-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d"
FROM ${CADDY_BASE}

USER root
RUN apk add --no-cache --virtual .cap-tools libcap \
    && setcap -r /usr/bin/caddy \
    && apk del --no-network .cap-tools \
    && addgroup -g 10004 -S staging-tls \
    && adduser -u 10004 -S -D -H -s /sbin/nologin -G staging-tls staging-tls

USER 10004:10004
