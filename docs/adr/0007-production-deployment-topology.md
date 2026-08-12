# ADR-0007: Production deployment topology and hosting decision function

**Status:** proposed — topology accepted, hosting provider **not selected**

**Date:** 2026-08-12
**Depends on:** ADR-0002 and ADR-0003 trust boundaries; ADR-0005 (Zalo OA introduces the first
public ingress this project has ever needed).
**Blocks:** `DEPLOY-TARGET-001`, `BACKUP-RESTORE-001`, `MONITORING-001`

This ADR fixes the topology now, because the topology is derived from invariants that are already
decided. It does **not** select a provider. `DECISION-HOSTING-001` fills in §4 and this ADR moves to
`accepted` with the chosen provider recorded. No infrastructure is provisioned before that.

## Context

`compose.production.yaml` describes a hardened *private staging* topology: non-root users, read-only
root filesystems, all capabilities dropped, external Docker secrets, an internal-only ingress network
and a loopback-published TLS endpoint. It has no public ingress, no agent runtime and no backup path,
which is correct for staging and insufficient for production.

Production adds three things staging never had:

1. a publicly reachable webhook endpoint, because Zalo OA delivers updates by HTTP POST;
2. a public agent runtime cell processing untrusted language;
3. durable recovery in a separate failure domain.

Each is a trust-boundary change, so the topology must be decided before a provider is chosen — not
discovered afterwards by whatever the provider makes convenient.

## Decision

### 1. Three network zones, two hosts minimum

```text
                    internet
                       │
                       │ HTTPS, one path only: POST /webhook/{provider}
                       ▼
        ┌──────────────────────────────┐
        │ ZONE P — public ingress      │   TLS termination, rate limit,
        │ (reverse proxy)              │   body-size cap, provider IP allowlist
        └──────────────┬───────────────┘   where the provider publishes ranges
                       │ loopback / private bridge
                       ▼
        ┌──────────────────────────────────────────────┐
        │ ZONE C — control plane          HOST A       │
        │  channel adapter → durable inbox             │
        │  FastAPI internal API · worker · PostgreSQL  │
        │  staff console: NOT internet-reachable       │
        └──────┬─────────────────────────────┬─────────┘
               │ Tool Facade (authenticated) │ WAL archive (egress only)
               ▼                             ▼
   ┌───────────────────────────┐   ┌──────────────────────────┐
   │ ZONE A — agent cell       │   │ separate failure domain  │
   │ HOST B, separate identity │   │ encrypted object storage │
   │ egress: model endpoint +  │   └──────────────────────────┘
   │ Tool Facade ONLY          │
   │ no inbound from internet  │
   └───────────────────────────┘
```

Binding rules:

- **Zone P exposes exactly one path per provider.** The staff console, the Tool Facade, the database
  and every control endpoint are unreachable from the internet. Staff reach the console over VPN or
  an operator-network allowlist, never a public hostname.
- **Zone A runs on a separate host** with its own service and OS identity. It cannot reach
  PostgreSQL, channel APIs, the owner workspace, a shell, a browser or host administration. Its only
  egress is the approved model endpoint and the authenticated Tool Facade. This is ADR-0003 §2
  restated as a network fact rather than a configuration intention.
- **No channel credential exists in Zone A.** Credentials live in Zone C, reachable only by the
  sender worker.
- **The adapter acknowledges only after durable inbox commit** and never waits on Zone A.

Co-locating Zone A on Host A is not an accepted variant. If cost forces one host, the correct
response is to delay public ingress, not to collapse the boundary.

### 2. Feature flags are closed at the image level

Production images ship with `FEATURE_PUBLIC_CHANNELS_ENABLED`, `FEATURE_AUTOMATED_SENDS_ENABLED` and
`FEATURE_AGENT_RUNTIME_ENABLED` defaulting to `false`, exactly as `compose.production.yaml` already
does through `x-disabled-capabilities`. A flag is enabled only by an explicit deployment change
referencing a signed gate manifest. Deploying the code never enables the capability.

### 3. Recovery requirements are inputs to provider selection

`BACKUP-RESTORE-001` requires continuous archiving with a recovery point no older than **15 minutes**,
restoration within **4 hours**, and a repository in a **separate failure domain**. "Separate failure
domain" means a different provider or a different region under different physical and administrative
failure — a second volume on the same host does not qualify, and neither does the same provider's
same-datacenter object storage.

Archives are encrypted before leaving Host A with a key that is not stored on Host A. A restore drill
that has not been executed does not count.

### 4. Hosting decision function — to be completed by `DECISION-HOSTING-001`

A candidate is **admissible** only if it satisfies all of:

- **A1** two isolated compute units with private networking between them;
- **A2** encrypted off-host archive in a separate failure domain, reachable by egress only;
- **A3** ability to keep the staff console off the public internet;
- **A4** a documented data-residency position sufficient for `DEC-006`;
- **A5** restoration of a full instance within the 4-hour RTO, demonstrable in a drill.

Among admissible candidates, minimize, in order: (1) customer-PII jurisdictional risk, (2) monthly
cost, (3) operational burden carried by one engineer, (4) restore-drill difficulty.

| Criterion | VN VPS ×2 + VN object storage | VN VPS + off-VN archive | Managed cloud (SG) |
|---|---|---|---|
| PII stays in Vietnam | yes | app yes, archive no | no |
| `DEC-006` cross-border assessment | not required | required for archive | required |
| Separate failure domain | verify: distinct region/provider | yes by construction | yes |
| Managed PITR | no — self-managed WAL archiving | no | yes |
| Indicative monthly cost | lowest | low | highest |
| Operational burden | highest | high | lowest |
| Restore drill | fully self-run | fully self-run | provider tooling |

Costs and residency claims are indicative only and **must be verified against current provider terms
at decision time**, not taken from this table.

Deliverable of `DECISION-HOSTING-001`: this table completed with verified figures, an A1–A5
admissibility verdict per candidate, and a recommendation the owner approves in writing. That
approval promotes this ADR to `accepted`.

### 5. Secrets

External secret references only, as `compose.production.yaml` already does — never environment
variables baked into an image, never a committed `.env`. The provider's secret store or a
file-based store with restrictive ownership both qualify; what does not qualify is any path where a
secret reaches the image layer, a log line, an OTel attribute or a backup archive.

Rotation is exercised before G1: rotate the database credential and the provider API key with the
system running, and prove no dropped or duplicated effect.

## Consequences

- Provider selection becomes a filtered decision against A1–A5 rather than a preference.
- The two-host requirement sets a hard cost floor. That floor is the price of processing untrusted
  language, and is small relative to one wrong-money incident.
- Self-managed WAL archiving on a VPS is real work and the most likely source of a false sense of
  safety. `BACKUP-RESTORE-001` therefore completes on a **drill result**, never on configuration.
- Public webhook ingress is the first internet-reachable surface this system has had. It gets a
  single path, its own rate limits, and its own tests before anything else is exposed.

## Required verification before `DEPLOY-TARGET-001` completes

- a port scan from outside proves only the webhook path is reachable;
- Zone A cannot open a connection to PostgreSQL, the channel API or the staff console — proven by
  attempt, not by configuration review;
- no channel credential or provider key is present in any image layer or any archive;
- all three feature flags read `false` on a freshly deployed production instance;
- `scripts/staging_smoke.py` equivalents pass against the production topology with synthetic data.
