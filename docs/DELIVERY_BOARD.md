# Delivery board

This is the human planning projection. [`delivery/WORK_QUEUE.yaml`](../delivery/WORK_QUEUE.yaml) is
the execution source, [`delivery/PROGRAM_PLAN.yaml`](../delivery/PROGRAM_PLAN.yaml) defines stable
phases, and [`delivery/CAPABILITY_STATUS.yaml`](../delivery/CAPABILITY_STATUS.yaml) is the production-
authorization source. A code status never authorizes a release.

| ID | Phase | Task | Dependency | Status |
|---|---|---|---|---|
| `FOUNDATION-001` | FOUNDATION | Python workspace, contracts, context and delivery foundation | — | Complete |
| `DB-001` | FOUNDATION | PostgreSQL migration and atomic transaction foundation | FOUNDATION-001 | Complete |
| `CONFIG-001` | IDENTITY_CONTROL | Immutable generic configuration publication | DB-001 | Complete |
| `IDENTITY-001` | IDENTITY_CONTROL | Staff identity, RBAC, MFA boundaries and audit | DB-001 | Complete |
| `DOMAIN-001` | DOMAIN_CORE | Canonical enums, service normalization and aliases | CONFIG-001 | Complete |
| `DOMAIN-002` | DOMAIN_CORE | Pricebook import manifest and canonical counts | DOMAIN-001 | Complete |
| `DOMAIN-003` | DOMAIN_CORE | Deterministic pricing and estimate engine | DOMAIN-002 | Complete |
| `DOMAIN-004` | DOMAIN_CORE | Promotion, delivery, SLA and fail-closed policy boundaries | DOMAIN-003 | Complete |
| `DOMAIN-005` | DOMAIN_CORE | Immutable quote snapshots and calculation traces | DOMAIN-003 | Complete |
| `OPERATIONS-001` | OPERATIONS_CONTROL | Orders, approvals, inbox/outbox, audit and Staff PWA | identity + domain | Complete |
| `AGENT-001` | AGENT_SHADOW | Isolated OpenClaw Concierge, Tool Facade and evals | OPERATIONS-001 | Blocked externally - all 32 local degraded preflights, release/status cryptographic enforcement, offline audit, sandbox scan gate, and context coverage checks are complete; integrated provider proof, scanned image/SBOM, and approvals remain missing |
| `SECURITY-001` | REAL_SHADOW_READINESS | Privacy, PITR, incident, kill switch and G1 readiness | AGENT-001 | Pending / DEC-006 |
| `SHADOW-001` | REAL_SHADOW_READINESS | Internal real-order Shadow pilot | SECURITY-001 | Pending |
| `CHANNEL-001` | PUBLIC_ASSISTED | Official channel and isolated public-cell entry | SHADOW-001 | Pending / DEC-005, DEC-006 |
| `AUTONOMY-001` | BOUNDED_AUTONOMY | Capability-specific bounded canary | CHANNEL-001 | Pending / commercial decisions |

Every item uses the [implementation task packet](../context/task-templates/IMPLEMENTATION_TASK.md) and
must attach its declared checks, rollback impact and unresolved assumptions before completion. Unknown
policy remains fail-closed; public capability flags remain disabled without a valid signed manifest.
