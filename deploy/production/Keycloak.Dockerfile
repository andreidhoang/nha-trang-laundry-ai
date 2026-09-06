# The production staff identity provider, per DEC-011 (ratified 2026-08-18).
#
# Self-hosted in Zone C so staff PII never leaves the owner-controlled host: no new counterparty,
# no cross-border data question for staff identity, and no external dependency on the login path.
# The cost the decision accepted is that the business now operates one more stateful service.
#
# Built with `--optimized` in mind: an unoptimized start re-augments /opt/keycloak/lib/quarkus at
# boot, which a read-only root filesystem forbids. Doing the augmentation here is what lets the
# runtime container keep `read_only: true` like every other service in the topology.

ARG KEYCLOAK_BASE="quay.io/keycloak/keycloak:26.0@sha256:09a381c715ab0b111835b70f2905955274843a219c6f27efb348e4d9f4086858"

FROM ${KEYCLOAK_BASE} AS builder
ENV KC_DB=postgres
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=false
ENV KC_HTTP_RELATIVE_PATH=/idp
RUN /opt/keycloak/bin/kc.sh build

FROM ${KEYCLOAK_BASE}
COPY --from=builder /opt/keycloak/ /opt/keycloak/

USER root
# Its own numeric identity, alongside 10001 api / 10002 worker / 10003 migrate / 10004 tls.
RUN echo 'keycloak:x:10005:10005::/opt/keycloak:/sbin/nologin' >> /etc/passwd \
    && echo 'keycloak:x:10005:' >> /etc/group \
    && chown -R 10005:10005 /opt/keycloak

COPY --chmod=0555 deploy/production/keycloak-entrypoint.sh /opt/keycloak/bin/entrypoint.sh

USER 10005:10005
ENTRYPOINT ["/opt/keycloak/bin/entrypoint.sh"]
