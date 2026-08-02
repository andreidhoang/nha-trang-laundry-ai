# Private staging deployment and rollback

This topology is engineering evidence only. It does not authorize Public OpenClaw, customer ingress,
provider/channel credentials, automated sends, or any capability in `delivery/CAPABILITY_STATUS.yaml`.
The only host listener is TLS on `127.0.0.1:8443`; operators reach it through an approved VPN/IAP or
an explicitly managed local tunnel.

## Provision once on the Linux staging host

Use a dedicated host identity with no Docker socket exposed to any service. Create the database
network as an internal network and connect only the managed PostgreSQL private endpoint or its scoped
proxy:

```text
docker network create --internal nha-trang-laundry-staging-database-private
docker network inspect nha-trang-laundry-staging-database-private
```

The inspection must show `Internal: true`. Provision the following external secrets through the host
secret manager/Compose integration; never place their values in `.env`, shell history, Git, Markdown,
screenshots, image layers, or Compose environment entries:

```text
staging_migration_database_url
staging_api_database_url
staging_worker_database_url
staging_oidc_issuer
staging_oidc_audience
staging_oidc_jwks_url
staging_oidc_mfa_claim
staging_oidc_mfa_value
staging_tls_certificate
staging_tls_private_key
```

The migration, API, and worker URLs use separate database roles. The migration role alone has DDL;
the API role receives only its application-table operations; the worker role receives only queue,
audit, and state-transition operations declared by its repositories. None is a PostgreSQL superuser,
role administrator, database owner, or replication identity. The TLS certificate SAN is
`staging.internal`; its private CA is distributed separately to operator browsers and the smoke host.

## Deploy

Set `STAGING_API_IMAGE`, `STAGING_WORKER_IMAGE`, and `STAGING_TLS_IMAGE` to reviewed immutable
references containing `@sha256:<64 lowercase hex>`. Local tags are allowed only while building the
initial staging evidence; they are never acceptable deployment or rollback evidence.

```text
docker compose -f compose.yaml -f compose.production.yaml config
docker compose -f compose.yaml -f compose.production.yaml pull
docker compose -f compose.yaml -f compose.production.yaml up -d --wait
uv run python scripts/staging_smoke.py --base-url https://staging.internal:8443 --ca-file <PRIVATE_CA_FILE>
```

`migrate` is a one-shot UID `10003` job and must complete before the API or worker starts. API, worker,
and TLS run as separate numeric identities with read-only roots, all capabilities dropped,
`no-new-privileges`, bounded PIDs, and bounded `noexec` temporary storage. The worker queue modes and
all public/agent/send flags remain false. Validate that no service except `tls` publishes a port and
that the published address remains `127.0.0.1`.

## Roll back application images

Before deployment, record the preceding reviewed API and worker digests in the restricted change
record. Rollback never reverses a migration, deletes state, restores a database, or enables a
capability. Confirm the preceding applications are compatible with every already-applied forward
migration; if compatibility is unknown, stop the services and require a human recovery decision.

```text
export STAGING_API_IMAGE=<PREVIOUS_API_NAME@sha256:DIGEST>
export STAGING_WORKER_IMAGE=<PREVIOUS_WORKER_NAME@sha256:DIGEST>
docker compose -f compose.yaml -f compose.production.yaml config
docker compose -f compose.yaml -f compose.production.yaml up -d --no-deps api worker
uv run python scripts/staging_smoke.py --base-url https://staging.internal:8443 --ca-file <PRIVATE_CA_FILE>
```

Keep the TLS proxy on the known-good configuration, preserve database and audit evidence, and leave
all capability flags false. Clear the two image variables from the operator shell after the change.
If smoke fails, stop `api` and `worker`; do not route around TLS or expose their container ports.
