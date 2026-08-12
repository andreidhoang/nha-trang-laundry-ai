# Production Operations Specification v1

**Ngày phát hành:** 2026-08-12
**Trạng thái:** `SPEC_APPROVED_NO_PRODUCTION_PROVISIONED`
**Nguồn quyết định:** ADR-0007 (topology), ADR-0006 (release authorization), ADR-0005 (channel)
**Work items:** `DEPLOY-TARGET-001`, `BACKUP-RESTORE-001`, `MONITORING-001`, `SECURITY-001`

Nothing here provisions infrastructure or authorizes a capability. `DECISION-HOSTING-001` selects the
provider; a signed gate manifest authorizes capabilities.

## 1. Deployment

### 1.1 Topology

As fixed by ADR-0007 §1: Zone P (public ingress, one path per provider), Zone C (control plane,
Host A), Zone A (agent cell, Host B). Zone A is never co-located with Zone C. If cost forces one
host, public ingress is delayed rather than the boundary collapsed.

### 1.2 Images

Production reuses the existing hardened image contract from `compose.production.yaml`: non-root
numeric users, `read_only: true` root filesystem, `cap_drop: ALL`, `no-new-privileges`, `pids_limit`,
`init: true`, sized `tmpfs` for writable paths, explicit entrypoints.

Images are referenced by **immutable digest**, never by a moving tag. A deployment that cannot state
the digest it is running is not a deployment, it is a guess.

### 1.3 Migrations

The existing `migration_job` entrypoint runs as a separate one-shot service that must complete
successfully before API or worker start, exactly as staging does. Migrations are forward-only.
Deployed migration files are never rewritten.

### 1.4 Feature flags

`FEATURE_PUBLIC_CHANNELS_ENABLED`, `FEATURE_AUTOMATED_SENDS_ENABLED`,
`FEATURE_AGENT_RUNTIME_ENABLED`, `WORKER_INTERNAL_OUTBOX_ENABLED` and `WORKER_AGENT_QUEUE_ENABLED`
default to `false` in the image. Enabling one requires a deployment change that names the signed gate
manifest authorizing it. **Deploying code never enables a capability.**

### 1.5 Rollback

Rollback is redeploying the previous image digest plus, where schema changed, a forward-compatible
path — never a backward migration against live data. Every release records its predecessor digest.
Rollback is rehearsed before G1, not first attempted during an incident.

## 2. Secrets

- External secret references only. Never an environment variable baked into an image, never a
  committed `.env`, never a value in a compose file.
- Mounted read-only, owned by the consuming service's numeric UID, mode `0400` — as staging already
  does.
- **Never** reachable in: an image layer, a log line, an OTel attribute, an exception message, a
  trace, evidence, or a backup archive. `packages/observability` redaction covers the first five;
  §3.3 covers the last.
- Zone A holds exactly one secret: the model provider credential. It holds no database URL and no
  channel credential.
- Rotation of the database credential and the provider API key is exercised with the system running,
  before G1, proving no dropped or duplicated effect.

## 3. Backup and recovery

The hardest requirement in the project and the easiest to fake.

### 3.1 Requirements

| Requirement | Value |
|---|---|
| Recovery point (RPO) | ≤ 15 minutes |
| Recovery time (RTO) | ≤ 4 hours |
| Repository location | separate failure domain (ADR-0007 §3) |
| Encryption | before leaving Host A, with a key not stored on Host A |

"Separate failure domain" means different physical and administrative failure. A second volume on
Host A does not qualify. Neither does the same provider's object storage in the same datacenter.

### 3.2 Method

Continuous WAL archiving plus periodic base backups. Archive success is a **monitored metric with an
alert**, not a cron job whose failures nobody reads. An archive gap longer than the RPO raises an
alert immediately.

### 3.3 What must not enter a backup

Signing private keys, provider credentials and channel credentials. Restoring a backup must never
restore the ability to sign a release manifest or send a message.

### 3.4 The drill is the deliverable

`BACKUP-RESTORE-001` completes on a **drill result**, never on configuration. The drill restores to a
clean host and proves:

1. recovery point within 15 minutes of the simulated failure;
2. restoration completed within 4 hours, wall-clock, by one engineer following the runbook;
3. quote snapshots and calculation traces hash-identical to pre-failure values;
4. the audit chain has no gap across the failure;
5. **no duplicate send** — outbox rows in flight at failure are reconciled, not blindly replayed;
6. object evidence referenced by restored rows is still fetchable.

A drill that required improvisation is a failed drill, and the runbook is corrected before retry.

## 4. Observability

`TELEMETRY-001` and `OBSERVABILITY-001` already define structured logs, correlation IDs, redaction
and OTel metric/trace contracts. Production adds the collector, retention and alert routing.

Never logged, never a span attribute, never a metric label: secrets, credentials, raw provider
payloads, prompt contents, chain-of-thought, customer phone numbers, addresses, or message bodies.
Correlation IDs and typed event fields carry the diagnostic load instead.

### 4.1 Alerts that page a human

| Condition | Why |
|---|---|
| WAL archive gap > RPO | recovery guarantee is silently gone |
| Outbox rows in `UNKNOWN` reconciliation | a customer may have been messaged twice or not at all |
| Suppression check failure or bypass | consent violation |
| Provider auth failure / token refresh failure | sending is down, or worse, about to retry wrongly |
| Policy decision point unavailable | fail-closed is active; every request is degrading |
| Agent cell reaching an unexpected destination | isolation boundary breach |
| Any capability flag enabled without a matching signed manifest | authorization bypass |

The last two are treated as security incidents, not operational noise.

### 4.2 Deliberately not paging

Model latency, token cost, eval score drift. These are reviewed on a cadence. Paging on quality
metrics trains the on-call to ignore the pager, which is how the real alerts get missed.

## 5. Kill switch

Every capability has an independent flag. The kill switch must:

1. stop new work immediately;
2. **hold in-flight outbound actions rather than deliver them** — the existing
   `P0-KILL-SWITCH-INFLIGHT` preflight covers this path;
3. leave the deterministic degraded path and the staff console fully operational;
4. require no redeploy to activate;
5. be exercised in a drill before G1, with the in-flight case explicitly included.

Degraded mode is the standing fallback per ADR-0004: deterministic domain responses, `REQUIRE_HUMAN`
handoff, staff console, manual send. There is no second agent runtime to fail over to, by design.

## 6. Incident response

Severity is defined by customer effect, not by component:

| Severity | Definition | Response |
|---|---|---|
| SEV-1 | wrong money, unauthorized action, PII disclosure, duplicate send, suppression miss | kill switch first, diagnose second |
| SEV-2 | capability unavailable, sending down, restore capability degraded | degrade to manual, fix within the day |
| SEV-3 | quality regression, latency or cost breach | scheduled fix |

Any SEV-1 is a zero-tolerance event under `GATE_REGISTRY.yaml` and **resets the clean-day counter to
zero**. It cannot be reclassified after the fact to preserve a gate timeline. That rule exists
precisely because the pressure to reclassify will be strongest when a launch date is close.

Every incident produces: timeline, customer impact set, root cause, the regression case added to the
frozen eval suite, and the control that would have prevented it.

## 7. Runbooks required before G1

Extending the existing `docs/runbooks/`:

- `production-deployment.md` — deploy, verify digests, verify flags closed, roll back
- `restore-drill.md` — the §3.4 procedure, timed, with the exact commands
- `incident-response.md` — severity table, kill switch, communication, evidence preservation
- `credential-rotation.md` — database and provider key rotation with the system running
- `channel-reconciliation.md` — resolving `UNKNOWN` send outcomes without creating duplicates

A runbook that has never been executed by the person who will execute it under pressure is a draft.

## 8. Required verification before `DEPLOY-TARGET-001` completes

- external port scan reaches only the webhook path;
- Zone A cannot connect to PostgreSQL, the channel API or the staff console — proven by attempt;
- no secret in any image layer, log, span attribute or archive;
- all capability flags read `false` on a fresh production instance;
- restore drill passes §3.4 end to end;
- kill-switch drill holds an in-flight send;
- credential rotation completes with no dropped or duplicated effect.
