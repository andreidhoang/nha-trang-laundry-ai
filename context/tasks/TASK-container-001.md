# Task packet: CONTAINER-001

```text
Task ID: CONTAINER-001
Goal: Build reproducible, minimal, non-root production images for the internal services.
Domain(s): platform, runtime_architecture, privacy_consent
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 3.3, 7, 10, 11, and 13
- `specs/IMPLEMENTATION_ROADMAP_V1.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `specs/contracts/container-scan-evidence-v1.schema.json`
- existing Python package/entrypoint manifests and `uv.lock`
- `runtime/openclaw/public-cell/` isolation requirements

## Files in scope

- `apps/api/Dockerfile` (create)
- `apps/worker/Dockerfile` (create)
- `apps/public-agent-tools/Dockerfile` (create)
- `.dockerignore` (create)
- `compose.production.yaml` (create)
- `packages/evals/tests/test_container_build_contract.py` (create)
- deployment/build runbook documentation

Do not containerize or mount the private owner OpenClaw workspace. Do not place the public cell in the
same trust boundary as owner tools, channel credentials, or the database administration identity.

## Required behavior

1. Pin the Python base image by immutable digest at release time; an explicit non-release placeholder
   may remain only when the release verifier rejects it.
2. Use multi-stage builds where they materially reduce runtime contents.
3. Run every service as a numeric non-root user with a read-only-compatible filesystem layout.
4. Install dependencies from `uv.lock` without development/test packages in runtime layers.
5. Copy only required application packages and runtime files.
6. Define explicit entrypoints, health checks, resource/config boundaries, and graceful termination.
7. Inject secrets only at runtime; never use build arguments or copied `.env` files for secrets.
8. Keep external/public automation disabled by default.

## Tests first

Add contract tests that reject:

- `USER root` or a missing runtime `USER`;
- `COPY . .` without a verified minimal context;
- copied `.env`, `.git`, evidence, owner workspace, private memory, or raw data;
- unpinned package installation;
- missing health check/entrypoint;
- privileged containers, host networking, Docker socket, or owner-workspace mounts.

## Done when

- contract tests and all declared Docker build/config commands pass;
- synthetic health checks succeed without real credentials or customer data;
- each image reports a non-root runtime identity;
- exact built digests are captured as local engineering evidence only;
- no scan/SBOM or release authorization is fabricated;
- rollback removes the deployment profile/images without database mutation.
