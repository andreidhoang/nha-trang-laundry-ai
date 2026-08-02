# Internal container deployment

These images package only the private control-plane API, fail-closed worker host, and private Agent
Tool Facade. They do not package Public OpenClaw, an owner workspace, channel credentials, customer
data, release signatures, or a sender integration.

## Local engineering build

```text
docker compose -f compose.yaml -f compose.production.yaml config
docker build --file apps/api/Dockerfile --tag nha-trang-laundry-api:local .
docker build --file apps/worker/Dockerfile --tag nha-trang-laundry-worker:local .
docker build --file apps/public-agent-tools/Dockerfile --tag nha-trang-laundry-agent-tools:local .
```

The Python base is pinned to the exact multi-platform manifest digest captured in each Dockerfile.
Update it only through a reviewed dependency task, rebuild all images, and rerun scans and contract
tests. Local image IDs are engineering evidence, not signed release or vulnerability-scan evidence.

## Runtime boundaries

- Run as numeric UID/GID `10001:10001` with a read-only root filesystem, all Linux capabilities
  dropped, no-new-privileges, and a bounded temporary filesystem.
- Inject database and identity credentials through the deployment secret manager. Never copy an
  `.env` file or pass a secret as a build argument.
- All public-channel, agent-runtime, and automated-send flags remain false in the image and Compose
  defaults.
- The worker `/healthz` endpoint proves process liveness only. Its response explicitly states that
  automation is disabled; later runtime wiring must add a separate readiness check before it can
  claim jobs.
- The production control-plane profile never mounts a Docker socket, owner workspace, OpenClaw state,
  evidence directory, or raw operational data.

The base Compose PostgreSQL service is behind the `local-database` profile in this overlay. A real
deployment uses an externally managed PostgreSQL endpoint with PITR and a least-privilege runtime
identity.

The executable private TLS topology, external-secret inventory, migration ordering, synthetic smoke
drill, and forward-only rollback procedure are documented in `docs/runbooks/private-staging.md`.

## Rollback

Stop the three application services and redeploy the preceding immutable image digests. Container
rollback does not roll back migrations or delete PostgreSQL state. Keep capability and outbound flags
disabled throughout rollback.
