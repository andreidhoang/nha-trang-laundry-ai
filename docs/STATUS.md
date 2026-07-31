# Project status

**Last updated:** 2026-07-31
**Authoritative machine status:** [`delivery/CAPABILITY_STATUS.yaml`](../delivery/CAPABILITY_STATUS.yaml)

## Current decision

The project is authorized to build the internal deterministic control plane. No customer-facing agent,
public channel, automated send, autonomous quote, booking, delivery decision, or remedy is authorized.

## Stable phase position

| Phase | Status | Evidence |
|---|---|---|
| `FOUNDATION` | Complete locally | workspace, contracts, context/delivery harness, PostgreSQL transaction and configuration primitives |
| `IDENTITY_CONTROL` | Complete locally | named staff, DB-authoritative RBAC, MFA/session boundaries, audit/outbox, and negative authorization evidence |
| `DOMAIN_CORE` | Complete locally | canonical registry, exact pricebook import, pricing, promotion/delivery/SLA boundaries, immutable quote snapshots and calculation traces |
| `OPERATIONS_CONTROL` | Complete locally | Staff PWA slice, approvals, inbox/outbox, idempotency, audit and operational workflows |
| `AGENT_SHADOW` | Blocked externally | isolated EVAL_ONLY OpenClaw cell, fixed Tool Facade, short-lived Runner bridge and durable run/tool ledger exist; P0 integrated eval and provider-data evidence require external prerequisites |
| `PRODUCTION_HARDENING` | Pending | local CI, observability, policy, container and supply-chain work is queued independently of the external provider blocker |
| `REAL_SHADOW_READINESS` | Not authorized | `G1_INTERNAL_SHADOW_READY` evidence absent |
| `PUBLIC_ASSISTED` | Not authorized | G1/G2 and capability-specific evidence absent |
| `BOUNDED_AUTONOMY` | Not authorized | cumulative G1–G4 evidence absent |

`delivery/PROGRAM_PLAN.yaml` is the stable phase vocabulary. Older M-number headings are explanatory
only; queue items use stable domain IDs.

## Current production authorization

```text
INTERNAL_SHADOW: NOT AUTHORIZED
PUBLIC_FAQ:      NOT AUTHORIZED
LIST_PRICE_INFO: NOT AUTHORIZED
```

Run `uv run python scripts/report_delivery_status.py` for the current machine-readable report. The
release decision is driven by evidence and a signed gate manifest, never by this prose status page.

## Next controlled task

`HARDEN-CI-001` is the next dependency-ready local task: make PostgreSQL integration tests and the
OpenClaw TypeScript plugin non-skippable CI gates. `OBSERVABILITY-001`, `POLICY-001`, and
`CONTAINER-001` are independent local successors; `SUPPLYCHAIN-001` depends on the CI and container
work.

In parallel, `AGENT-001` remains blocked externally. Its local seed fixtures and assertions are
complete; external prerequisites are required before PRIMARY/fallback/degraded integration evidence.
Local boundary preflights
cover all 32 manifest cases in a hash-pinned synthetic `SKIP` bundle. Runtime enforcement now also
requires a schema-valid, JCS-bound, artifact-verified, unexpired three-party release authorization
before any provider-backed call. Capability status and reporting also revalidate that exact signed
deployment envelope before displaying `AUTHORIZED`. Checksum-pinned public-key trust-root loading and the sanitized
candidate verifier are available. The current incomplete provider review is schema-valid and
hash-pinned, and the version-bound offline OpenClaw audit passes with zero critical findings. No
approved signer registry, effective-request proof, or authorization exists. The structurally parsed
OpenClaw configuration still names a placeholder sandbox image; the typed scan gate therefore reports
a ninth release blocker until an immutable digest, passing scan evidence, and hash-pinned SBOM exist.
Context drift validation also guarantees that each work item's declared normative inputs and any
atomic task packet are present. Read
the [engineering continuation brief](../context/PROJECT_CONTINUATION.md) before resuming and run
`uv run python scripts/run_delivery_loop.py` for the authoritative work brief.
