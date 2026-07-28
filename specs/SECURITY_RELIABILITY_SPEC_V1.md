# Security & Reliability Specification v1

**Version:** 1.2-runtime-decision  
**Date:** 2026-07-27  
**Applies to:** R1 Shadow Mode through R3 Bounded Autonomy

## 1. Security objective

Hệ thống phải bảo vệ đồng thời:

1. dữ liệu khách hàng;
2. quyền quyết định của chủ/nhân viên;
3. tính đúng của giá và trạng thái đơn;
4. khả năng vận hành khi AI hoặc channel lỗi;
5. môi trường OpenClaw cá nhân của chủ tiệm.

OpenClaw public cell được xem là một **untrusted reasoning client** của Business Control Plane. Nó không phải security boundary, database hay authority.

## 2. Trust zones

### TZ-0 — Internet and customer content

Bao gồm:

- customer messages;
- attachments;
- channel webhook payloads;
- customer URLs;
- public lead data.

Mọi nội dung ở đây là untrusted, có thể chứa:

- prompt injection;
- malware;
- forged identifiers;
- replay;
- oversized payload;
- PII;
- abusive/cost-amplifying content.

### TZ-1 — Edge and channel adapters

Responsibilities:

- TLS termination;
- webhook verification;
- body/attachment size limits;
- request normalization;
- replay/deduplication;
- rate limit;
- quick provider acknowledgement;
- persist every accepted event to the durable inbox before agent processing;
- enqueue only a reference to that persisted inbox event.

TZ-1 không được chứa business pricing logic.

### TZ-2 — Public OpenClaw cell

Requirements:

- separate OpenClaw profile, config, state and workspace;
- a separate VM/VPS is mandatory before any public or otherwise untrusted inbound is connected;
- same-host isolation is permitted only for development and internal pre-channel Shadow;
- no access to personal workspace or memory;
- no `exec`, browser, node, canvas, generic filesystem mutation, cron mutation, session-control or generic messaging tools;
- no channel-provider credentials, provider webhook secret or outbound-send capability;
- no owner workspace mount, Docker socket, host administration interface or shared runtime directory;
- runtime writes are limited to dedicated OpenClaw state/log directories owned by the public-cell OS identity; no filesystem-mutation tool is exposed to the model;
- elevated mode disabled;
- no arbitrary web fetch from customer-provided URLs;
- egress allowlist only to the approved model provider and Agent Tool Facade;
- inbound agent runs only from the authenticated control-plane `AGENT_RUNNER`; it dispatches a
  signed, contact-bound job over a private queue/pull transport, and a minimal public-cell executor
  invokes the loopback Gateway. The public Internet and channel adapter cannot invoke the Gateway
  control plane directly;
- disposable/rebuildable.

### TZ-3 — Business control plane

Contains:

- API and Agent Tool Facade;
- Policy Decision Point (`PDP`);
- durable inbox, dispatcher and authenticated `AGENT_RUNNER` job controller;
- outbox and `OUTBOX_WORKER`;
- pricing/policy engine;
- approval service;
- PostgreSQL;
- private object storage;
- operator UI.

Đây là authority cho business truth và state transitions.

Required processing path:

```text
channel provider
  -> edge adapter
  -> durable inbox
  -> authenticated AGENT_RUNNER
  -> public OpenClaw draft/tool calls
  -> approval or allowlisted deterministic-template decision
  -> transactional outbox
  -> OUTBOX_WORKER
  -> channel provider
```

OpenClaw never receives a channel credential and never sends directly.

### TZ-4 — Private owner environment

Existing personal OpenClaw Gateway remains here.

Rules:

- owner-only;
- no direct inbound public customer channel;
- no shared public channel credentials;
- no direct database superuser credentials;
- analytics access qua scoped API hoặc sanitized export;
- read-only/scoped analytics only; it cannot execute an approval, outbound message or business mutation;
- never used as fallback public gateway.

### External processor boundaries

Channel providers and the model provider remain external processors, outside TZ-1–TZ-4:

- only the edge adapter and `OUTBOX_WORKER` may communicate with a channel provider;
- only the public OpenClaw cell may call the approved model endpoint;
- prompts use the minimum necessary data and exclude secrets, bank configuration and unnecessary addresses;
- provider data-use, training, regional processing and retention terms require documented review before public launch;
- private object storage is in TZ-3, has no public bucket, and is accessed only through scoped service identities or signed expiring URLs.

## 3. Network policy

| Source | Destination | Allow | Notes |
|---|---|---:|---|
| Internet | Edge HTTPS | Yes | 443 only |
| Internet | Operator UI | No by default | VPN/IAP/private network preferred |
| Internet | PostgreSQL | Never | no public listener |
| Internet | Public OpenClaw Gateway/control UI | Never | no reverse-proxy route |
| Edge | Durable inbox/API | Yes | authenticated private hop; persist before dispatch |
| Control-plane `AGENT_RUNNER` | Public-cell job transport | Yes | authenticated private queue/pull; signed, expiring, contact-bound job |
| Public-cell executor | Public OpenClaw loopback | Yes | same public VM; no network-exposed Gateway |
| Public OpenClaw | Agent Tool Facade | Yes | narrow audience/scopes |
| Public OpenClaw | Approved model provider | Yes | dedicated credential; minimum data |
| Public OpenClaw | Channel APIs | Never | no route or credential |
| Public OpenClaw | PostgreSQL | Never | no route/credential |
| Public OpenClaw | Private owner Gateway | Never | separate trust boundary |
| Business API | PostgreSQL | Yes | least-privilege DB role |
| Business services | Private object storage | Yes | scoped identity; private endpoint/bucket |
| Worker | Channel APIs | Yes | approved egress only |
| Worker | Model provider | No by default | only agent runtime calls model |
| Operator browser | API | Yes | authenticated HTTPS |
| Private owner Gateway | Analytics API/export | Optional | read-only/scoped |

Production firewall must deny by default.

## 4. OpenClaw hardening profile

Before any public or otherwise untrusted inbound:

- `allowInsecureAuth = false`;
- OpenClaw Gateway control protocol and control UI bind to loopback only;
- the Internet-facing reverse proxy exposes only the channel adapter HTTP routes, never the OpenClaw Gateway control plane;
- the control-plane `AGENT_RUNNER` creates authenticated, contact-bound job references; a minimal
  public-cell executor receives them through a private pull/queue mechanism and invokes OpenClaw
  over loopback;
- strong randomly generated Gateway token;
- public control UI disabled/unreachable;
- session isolation per server-bound contact/conversation supplied by `AGENT_RUNNER`;
- tool allowlist contains only named business tools;
- deny runtime/fs/browser/nodes/automation/session-control groups;
- deny elevated execution;
- no channel plugin/credential is installed in the public cell; channel access policy is enforced by the edge adapter and business control plane;
- no secrets inside workspace/prompt;
- security audit has no unresolved high/critical finding;
- plugins pinned to reviewed versions;
- plugin inventory and hashes recorded;
- model/provider credentials unique to public cell;
- channel credentials are unique to the business channel and exist only in the edge adapter/`OUTBOX_WORKER` secret scope;
- credential rotation runbook tested.
- provider/model configuration pins the explicit OpenClaw agent runtime; runtime `auto` is prohibited
  for the public release identity;
- a dedicated production provider service/API credential is used; owner interactive credentials are
  absent from the public cell;
- the deployed OpenClaw version, image digest, config hash, plugin inventory, tool policy, model route
  and provider request/storage behavior match the signed release artifacts;
- a pre-real-data integration test verifies effective provider storage/retention behavior. Any
  mismatch, undocumented forced storage or inability to meet the approved retention policy disables
  real-customer model processing.

These requirements apply before any untrusted inbound, including a “private beta” channel. Container-only isolation on the same personal host is insufficient as the final public boundary.

## 5. Authentication

### 5.1 Human accounts

- Named accounts; no shared `admin`.
- Password, passkey or OIDC implementation must come from a maintained auth component.
- MFA is required for `OWNER_ADMIN` before the first real Shadow order.
- Before any public channel, MFA is required for every role that can view PII, approve, export, publish price/policy, access finance, manage credentials or see a delivery address.
- MFA is strongly recommended for all remaining staff accounts; no SMS-only MFA for privileged roles where a passkey/TOTP alternative is available.
- Session cookie: `HttpOnly`, `Secure`, appropriate `SameSite`.
- CSRF protection for browser mutations.
- Session idle timeout and absolute timeout.
- Device/session list and revoke capability.
- Disable user → revoke all sessions/tokens.
- Rate-limit login and recovery.
- Recovery events audited.

### 5.2 Service identities

Separate identities:

- `PUBLIC_AGENT`;
- `AGENT_RUNNER`;
- `OUTBOX_WORKER`;
- `channel-adapter-<provider>`;
- `PRIVATE_AGENT`;
- `backup-job`.

Every token:

- has explicit audience;
- has capability scopes;
- has expiry;
- can be independently revoked;
- is rotated without redeploying unrelated services;
- is never accepted from browser local storage.

### 5.3 Database identities

At minimum:

- migration owner;
- application runtime;
- append-only audit writer;
- read-only reporting;
- backup;
- break-glass admin.

Runtime account cannot create roles, extensions or arbitrary schema.
The application path may insert an audit event through the append-only audit-writer contract but cannot update or delete audit rows. Reporting and agent identities have no audit-write permission.

### 5.4 Public order-status authorization

An order-status lookup code must contain at least 80 bits of cryptographic entropy, but the code is an identifier—not authentication.

The public status flow must:

1. receive an opaque code without exposing sequential order/customer IDs;
2. derive the expected contact and channel binding server-side;
3. verify ownership through the already bound provider session or an explicit challenge to the stored contact;
4. issue a short-lived, single-purpose token scoped to one order/status view;
5. return the same generic denial for unknown, expired, mismatched and unauthorized requests;
6. rate-limit by source, channel account and opaque-code hash;
7. redact codes, tokens, phone numbers, addresses and provider identities from access/application logs.

A possession-only code URL must never reveal customer, price, address or order state. Automated enumeration and cross-contact IDOR tests are release-blocking.

## 6. Authorization and RBAC

### 6.1 Roles

| Capability | `OWNER_ADMIN` | `OPS_APPROVER` | `OPERATOR` | `DRIVER` | `ACCOUNTANT` | `AUDITOR` | `PUBLIC_AGENT` |
|---|---:|---:|---:|---:|---:|---:|---:|
| View assigned customers/orders | Yes | Yes | Yes | Assigned only | Limited | Read | Contact-scoped |
| Create draft quote | Yes | Yes | Yes | No | No | No | Yes |
| Approve standard quote | Yes | Yes | No | No | No | No | No |
| Approve special/range price | Yes | Policy | No | No | No | No | No |
| Confirm capacity/SLA | Yes | Yes | No | No | No | No | No |
| Update delivery leg | Yes | Yes | Yes | Assigned only | No | Read | No |
| Record payment | Yes | Limited | Limited | No | Yes | Read | No |
| Issue refund/credit | Yes | No by default | No | No | Policy | Read | No |
| Publish price/policy | Yes | No | No | No | No | Read | No |
| Export PII | Yes + reauth | No | No | No | Policy | No by default | No |
| Manage users/secrets | Yes | No | No | No | No | No | No |

Machine roles `PRIVATE_AGENT`, `AGENT_RUNNER` and `OUTBOX_WORKER` are not human roles and never inherit human UI permissions. `AGENT_RUNNER` may invoke one contact-bound agent run; `OUTBOX_WORKER` may execute only a valid outbox envelope; `PRIVATE_AGENT` is read-only/scoped analytics.

### 6.2 Server-side enforcement

- UI visibility is not authorization.
- Every command checks actor, capability, object scope and current version.
- List/search endpoints apply row/object filters.
- Direct object reference tests must prove no IDOR.
- Agent cannot enumerate contacts/orders.
- Driver sees only assigned route data and minimum necessary contact/address.
- Analyst/reporting views should be aggregated or pseudonymized.

## 7. Approval security

Material actions use an immutable action envelope:

```json
{
  "action_request_id": "uuid",
  "capability": "quote.send",
  "resource_type": "quote",
  "resource_id": "uuid",
  "resource_version": 7,
  "policy_bundle_id": "uuid",
  "input_hash": "sha256",
  "rendered_action_hash": "sha256",
  "requested_by_actor": "PUBLIC_AGENT",
  "expires_at": "2026-07-27T10:00:00Z",
  "idempotency_key": "opaque"
}
```

Rules:

- public/agent input is limited to an allowlisted action enum, resource ID/version, canonical input/rendered hash and idempotency key;
- the server derives the reason codes, required approver role, policy obligations, execution capability and maximum TTL;
- approval is one-time;
- approval expires;
- approval covers exact rendered message and exact resource revision;
- any edit invalidates approval;
- worker executes; requester cannot self-execute;
- immediately before execution, worker rechecks consent, suppression, object version and policy;
- financial/policy actions require owner-level approval;
- maker-checker for high-value actions when staffing allows.

Default maximum TTLs unless a stricter policy applies:

| Action | Maximum TTL |
|---|---:|
| Customer-facing quote/message send | 30 minutes |
| Capacity/slot confirmation | 15 minutes |
| Payment/refund/credit execution | 10 minutes |
| Pricebook/promotion/policy publish | 10 minutes |

Expiry never extends implicitly. A retry after expiry creates a new request and re-runs policy.

Approval states:

```text
REQUESTED
  -> APPROVED -> EXECUTING -> EXECUTED
  -> APPROVED -> EXECUTING -> FAILED -> MANUAL_REVIEW/DLQ
  -> REJECTED
  -> EXPIRED
  -> CANCELLED
```

### 7.1 Pre-channel manual-send protocol

Manual copy/send is permitted only in internal pre-channel `SHADOW`, with this state protocol:

```text
DRAFT
  -> APPROVAL_REQUESTED
  -> APPROVED_FOR_MANUAL_SEND
  -> MANUAL_SEND_RECORDED
```

`APPROVED_FOR_MANUAL_SEND` covers one exact draft revision and one exact JCS/SHA-256 rendered-content hash. Before content becomes visible/copyable, the server rechecks the current revision, approval TTL, purpose-specific consent and suppression. For marketing, failed/unknown suppression state means the content is not rendered for copying.

After the human sends outside the system, the UI requires a `manual_send_attestation`:

```json
{
  "draft_revision_id": "uuid",
  "exact_rendered_content_hash": "sha256",
  "actor_id": "uuid",
  "channel": "phone_or_provider_enum",
  "recipient_binding_id": "uuid",
  "sent_at": "2026-07-27T10:00:00Z"
}
```

Rules:

- the server derives the resource/contact binding; arbitrary recipient text is not accepted as evidence;
- editing any character creates a new draft revision and invalidates the approval;
- `MANUAL_SEND_RECORDED` means human-attested manual send, not provider delivery or provider acknowledgement;
- recording manual send consumes the approved envelope and prevents later `OUTBOX_WORKER` execution of that logical send;
- manual attestation does not count as integrated exactly-once provider evidence;
- every transition and rejected attestation is audited.

### 7.2 Fail-closed autonomy and kill switches

Every agent processing or automated execution decision evaluates:

```text
automation_allowed =
  global_automation_enabled
  AND agent_processing_enabled
  AND agent_outbound_enabled
  AND capability_enabled
  AND stage_policy_allows
  AND PDP_allows
```

All six inputs are server-side, versioned and audited. A missing, unreadable, expired or stale flag/policy result evaluates to `false`.

- `agent_processing_enabled=false` prevents new model runs and creates/retains human tasks.
- `agent_outbound_enabled=false` places every unexecuted automated outbox item on hold or cancels it according to its immutable policy; it cannot proceed on retry.
- `capability_enabled=false` affects that capability only; capability flags never widen stage policy.
- `global_automation_enabled=false` disables new agent runs and all unexecuted automated outbound, while deterministic staff workflows remain available.
- approved-but-unexecuted envelopes are re-evaluated immediately before execution.
- service/model/channel credentials each have an independent revoke path in case configuration control is unavailable.
- kill-switch state is read from an authoritative store on every execution decision; caches have a bounded TTL and stale cache fails closed.

## 8. Webhook security

### 8.1 Inbound sequence

1. Read raw bytes with maximum size.
2. Validate media type.
3. Verify provider signature/secret before JSON parsing where supported.
4. Validate timestamp freshness.
5. Validate nonce/provider event ID.
6. Check replay cache/unique constraint.
7. Parse into provider schema.
8. Normalize into internal event schema.
9. Run deterministic consent-withdrawal/STOP evaluation before any model dispatch.
10. Persist the inbox event and any withdrawal/suppression event transactionally.
11. Return acknowledgement quickly.
12. Enqueue a persisted event reference for asynchronous dispatcher/`AGENT_RUNNER` processing only if policy allows.

Unique key:

```text
(provider, channel_account_id, provider_event_id)
```

### 8.2 Replay and duplicate behavior

- Same provider event with same payload → return prior success/no-op.
- Same event ID with different payload → security alert and reject.
- Out-of-order message → store, serialize per conversation and reconcile.
- Provider retry after timeout must not produce duplicate agent run or send.

### 8.3 Outbound

- Only outbox worker sends.
- Every send references approved action or explicitly allowlisted auto capability.
- Suppression check occurs immediately before provider call.
- Store provider message ID and acknowledgement.
- Retry only according to error class.

### 8.4 Deterministic STOP/withdrawal path

Consent withdrawal is an ingress safety control, not an LLM intent:

- normalize Unicode, whitespace and provider command syntax deterministically;
- evaluate the published exact-command/phrase registry before creating an agent run;
- an exact withdrawal immediately appends the withdrawal/suppression event and blocks marketing;
- a message matching the separately published deterministic ambiguous-opt-out phrase/regex registry—but not an exact registered command—immediately sets `marketing_suppression=PENDING_REVIEW_BLOCKED`, preventing marketing while a human reviews it;
- service/transactional messages remain governed by their separate purpose and policy;
- no model output may reverse, narrow or delay a deterministic block.

A consent grant is valid only when the server can bind an affirmative provider event to a pending consent request with exact wording version, purpose, channel, contact and expiry. The model cannot choose the evidence, contact, scope, wording version or consent status. Every grant, withdrawal, ambiguous block and review decision is audited.

## 9. Inbox, outbox, retry and DLQ

### 9.1 Transactional invariant

Every material command commits, in one PostgreSQL transaction:

1. domain aggregate mutation;
2. domain event;
3. append-only audit event;
4. every required outbox event.

If any required audit/event/outbox insert fails, the domain mutation rolls back. An unavailable audit path must not degrade into “log later”. Failure-injection tests must prove this invariant.

The runtime path has insert-only access to the audit ledger through a constrained database role/function. It cannot update/delete an existing audit row or bypass audit creation for a mutating command. Audit corrections are new append-only events.

### 9.2 Retry

Suggested transient schedule:

```text
30 seconds -> 2 minutes -> 10 minutes -> 1 hour -> 6 hours
```

Use:

- exponential backoff;
- jitter;
- provider `Retry-After`;
- maximum attempts;
- maximum event age;
- retry classification.

Do not retry:

- invalid auth without rotation/recovery;
- policy rejection;
- invalid recipient;
- invalid payload;
- denied consent/suppression;
- stale approval.

### 9.3 DLQ

DLQ record contains:

- original event;
- normalized payload hash;
- last error class;
- attempts;
- first/last attempted time;
- correlation IDs;
- replay eligibility;
- operator decision.

Any DLQ item alerts within five minutes during business hours.

Manual replay preserves the logical idempotency key.

### 9.4 Raw inbox and DLQ protection

- raw webhook bodies, normalized inbox payloads and DLQ bodies are encrypted at rest;
- object-store spillover uses a private bucket, a per-object integrity hash and scoped service identity;
- access, export, replay and deletion are audited;
- routine operator UI shows a redacted projection, not raw secrets/signatures/PII by default;
- raw/DLQ data is excluded from ordinary application logs and model prompts;
- retention is policy-driven by event class; the initial maximum for rejected/raw webhook data is 30 days unless an active incident/legal hold requires restricted retention;
- purge jobs delete database and object copies consistently and emit a sanitized audit event;
- DLQ replay runs the current authorization, consent/suppression, approval, idempotency and kill-switch checks—it is not a bypass.

## 10. Prompt injection and agent abuse defenses

- Customer text is always tagged untrusted.
- System/developer policy cannot be supplied from customer content.
- Customer URLs are not fetched by public agent.
- Attachments are not executed or used as instructions.
- Model cannot choose arbitrary tool names/endpoints.
- Tool schemas reject unknown properties.
- Tool output is revalidated server-side.
- Tool results are authoritative only for their declared fields.
- Public agent cannot read other contacts/orders by free-form search.
- Max turns, max tokens, max tool calls and spend budget per conversation.
- Repeated malicious requests switch to canned safe response/human handoff.
- Any attempt to access denied capability emits security metric.

## 11. Attachment/media security

R1 may disable customer attachments entirely. If enabled:

1. fetch only from allowlisted provider CDN;
2. enforce content-length and actual decoded size;
3. verify MIME by content;
4. store in quarantine;
5. antivirus/malware scan;
6. strip metadata where appropriate;
7. generate derivative preview in isolated process;
8. do not parse active documents automatically;
9. require human review before using media to make commercial promise;
10. use signed, expiring object URLs.

Never expose private object-storage bucket publicly.

## 12. Data classification

| Class | Examples | Model access | Log access |
|---|---|---|---|
| `PUBLIC` | published prices, hours | Allowed | Allowed |
| `INTERNAL` | capacity, costs, machine data | Only private approved workflows | Redacted/limited |
| `PII` | phone, channel ID, address, messages | Minimum necessary | Redacted |
| `RESTRICTED` | bank configuration, incident evidence, auth, secrets | Denied by default | Never plaintext |

## 13. PII minimization

- Store exact address only for active delivery/business need.
- Model receives zone/distance, not full address, unless strictly required.
- Do not collect CCCD or card details.
- Separate public phone display from internal contact identifiers.
- Hash normalized identifiers for dedupe/suppression lookup where practical.
- Do not send bank details to model; insert approved template data after generation.
- Remove PII from traces, exception telemetry and analytics.
- Restrict bulk export and require reauthentication.

## 14. Consent and suppression

Consent event fields:

- subject/contact;
- channel;
- purpose;
- wording version;
- affirmative action/evidence;
- granted/withdrawn timestamp;
- collector actor;
- source interaction.

Grant validation additionally requires a non-expired pending consent request with exact purpose, channel, wording version and contact binding. Evidence is the server-verified provider event—not an agent summary or staff free-text note.

Suppression:

- append-only source event;
- enforcement cache/view;
- always checked immediately before marketing send;
- remains effective after ordinary record cleanup using minimum protected identifier;
- service messages and marketing purposes remain distinct.

## 15. Retention proposal

Pending legal/accounting review:

| Data | Initial engineering retention |
|---|---|
| Rejected/raw webhook payload | 7–30 days |
| Routine conversation body | 90–180 days, then redact/summarize |
| Agent run/tool payload | 90 days; keep sanitized audit longer |
| Exact delivery location | remove/coarsen after operational/legal need |
| Consent/suppression evidence | according to legal requirement |
| Orders/payments/invoices | accounting/legal schedule |
| Incident evidence | restricted policy/legal schedule |
| Debug logs | 14–30 days |
| Security/audit events | minimum 12 months; final policy to approve |

Retention jobs must be testable, auditable and reversible only through backup policy—not hidden soft-delete forever.

## 16. Audit ledger

Minimum fields:

- `audit_event_id`;
- `occurred_at_utc`;
- `actor_type`, `actor_id`;
- `session/service_identity_id`;
- `action`;
- `result`;
- `resource_type`, `resource_id`, `resource_version`;
- structured diff or before/after hashes;
- `reason_code`;
- `approval_request_id`;
- `pricebook_version_id`;
- `policy_bundle_id`;
- `trace_id`;
- model/prompt/agent/tool versions when applicable;
- source network/device metadata where appropriate.

Mandatory audited events:

- auth/login/recovery;
- PII view/export;
- quote calculate/override/send/accept;
- price/policy publish/rollback;
- approval lifecycle;
- order transition;
- delivery proof;
- payment/invoice/credit changes;
- consent/suppression;
- incident evidence access;
- user/role/token changes;
- DLQ replay;
- kill switch.

Audit events are append-only. Corrections create new events.

## 17. Secrets

- No secrets in Git, CSV, Markdown, prompt, logs or screenshots.
- Use environment/secret references appropriate to deployment.
- Separate dev/staging/prod.
- Separate public/private OpenClaw Gateway, model and tool credentials; the public cell has no channel credential.
- Rotate model/channel/service tokens independently.
- Record owner, purpose, created/rotated/expires dates.
- Secret access is audited.
- Break-glass secrets are offline/protected and tested.
- Revoking public cell credentials must not disrupt private owner Gateway.
- Owner/administrator screens manage secret references, status and rotation—not reveal stored secret values.

## 18. Supply-chain security

- Dedicated project repository; never `git add .` from the personal OpenClaw workspace.
- Lockfiles committed.
- Versions pinned for runtime and OpenClaw plugins.
- Dependency license/SCA scan in CI.
- Secret scan in pre-commit and CI.
- Container image scan.
- Generate SBOM for release.
- Base images pinned by digest for production.
- No unreviewed install scripts.
- Review transitive dependencies of channel bridges/plugins.
- Critical vulnerability blocks release unless documented risk acceptance.

## 19. Observability

### 19.1 Correlation

Propagate:

- `trace_id`;
- `provider_event_id`;
- `conversation_id`;
- `agent_run_id`;
- `contact_id`;
- `quote_id`;
- `order_id`;
- `approval_id`;
- `outbox_event_id`.

PII must not be used as metric label.

### 19.2 Metrics

Ingress:

- webhook accepted/rejected;
- signature failure;
- duplicate/replay;
- payload size;
- rate-limit.

Business:

- quote preview latency/error;
- quote mismatch;
- order transitions;
- SLA adherence;
- delivery fee/manual override;
- incident/rewash;
- margin completeness.

Agent:

- inbox-to-run dispatch latency;
- model latency;
- token/cost;
- tool-call count;
- schema failure;
- denied capability;
- handoff;
- human correction.

Worker:

- queue depth;
- oldest event age;
- attempts;
- outbound acknowledgement;
- DLQ.

Control:

- manual-send attestation accept/reject;
- each kill-switch/feature-flag state and age;
- automation decisions by capability/stage/PDP result;
- held/cancelled automated outbox count;
- consent withdrawal-to-enforcement latency.

Security/ops:

- auth failure;
- PII export;
- backup age;
- restore-test age;
- secret expiry;
- policy rollback;
- spend anomaly.

## 20. SLOs

| SLO | Shadow target | Assisted target |
|---|---:|---:|
| Internal control-plane availability | 99.5% business hours | 99.5% business hours until HA is implemented/tested |
| Deterministic quote p95 | <500ms | <500ms |
| Agent draft p95 | <15s | <15s |
| Agent run hard timeout | 20s | 20s |
| Webhook acknowledgement p95 | N/A | <2s |
| Ready-queue dispatch latency p95 | <15s | <15s |
| Outbound dispatch success | Manual | >=99.5% excluding provider rejection |
| Material mutation audit coverage | 100% | 100% |
| Unauthorized agent action | 0 | 0 |
| Cross-customer disclosure | 0 | 0 |
| Deterministic quote mismatch | 0 | 0 |

Definitions:

- business hours are 08:00–20:00 Asia/Saigon on published operating days;
- “agent draft” runs from authenticated `AGENT_RUNNER` invocation to validated draft result, excluding human wait;
- at 20 seconds the run is cancelled/abandoned, no tool result is treated as final, and a human task is created;
- ready-queue dispatch latency measures an eligible ready item until the first worker/runner attempt; retry backoff, dependency outage and human approval time are reported separately and excluded;
- 99.9% is not a launch commitment on the single-host control plane; it requires a separate HA design, failure-domain review and demonstrated failover.

## 21. Alerts

Immediate/high:

- cross-customer access;
- unauthorized mutation;
- audit write failure;
- database backup/PITR failure;
- provider credential compromise;
- public agent denied-capability spike;
- bad pricebook publish;
- unexpected PII export.

Business-hours urgent:

- any DLQ;
- queue oldest age >5 minutes;
- webhook signature failures spike;
- agent schema failures exceed threshold;
- channel rejection/quotas spike;
- model spend anomaly;
- database disk/connection saturation.

## 22. Backup and disaster recovery

### 22.1 Targets

- RPO <=15 minutes, effective from the first real customer order.
- RTO <=4 hours.
- Continuous WAL archiving plus encrypted base backups, or a managed PostgreSQL PITR equivalent; a daily snapshot alone cannot meet the RPO.
- Daily encrypted recovery copy retained 35 days in addition to PITR.
- Object storage versioning for evidence.
- If the primary database/application is single-host, an encrypted off-host recovery copy is mandatory.
- Restore proof must demonstrate a usable recovery point no older than 15 minutes.

### 22.2 Back up

- database;
- audit ledger;
- continuous WAL/PITR history and base backups;
- published price/policy bundles;
- object evidence;
- infrastructure configuration without secrets;
- migrations and release manifests.

Private object storage must be backed up/versioned in a failure domain separate from the primary host. OpenClaw session memory is not a backup.

### 22.3 Restore testing

- before the first real customer order;
- after major schema migration;
- quarterly thereafter.

Test:

- database restore;
- object access;
- selected recovery point age <=15 minutes;
- published rule version;
- one historical quote reconstruction;
- one audit timeline;
- worker/outbox recovery without duplicate send.

Every drill records backup identifiers, chosen recovery timestamp, achieved RPO/RTO, integrity checks, reviewer and remediation. A failed or overdue restore drill blocks launch/progression.

## 23. Degraded-mode runbooks

### Model down

- deterministic app continues;
- agent button shows unavailable;
- use approved canned acknowledgement;
- create human task;
- do not guess.

### Database down

- stop quote/order confirmation;
- display read-only cached public info only;
- manual paper intake with later reconciliation identifier;
- no parallel unofficial spreadsheet source without import protocol.

### Channel down

- retain outbox;
- notify operator;
- phone/manual channel fallback;
- reconcile provider IDs after recovery.

### Queue backlog

- disable auto-send;
- prioritize transactional service messages;
- move to manual;
- investigate dependency/provider.

### Public OpenClaw compromise

1. activate `PUBLIC_AGENT`/agent-processing kill switches;
2. revoke public-cell Gateway, tool and model credentials; rotate channel credentials only if evidence shows the edge/worker scope was affected;
3. block network identity;
4. preserve logs/audit;
5. rebuild public cell from clean image;
6. rotate affected secrets;
7. review customer impact.

### Bad pricebook/promotion release

1. disable version;
2. invalidate pending approvals;
3. publish prior valid version;
4. recompute unsent drafts;
5. do not mutate accepted order snapshots;
6. identify affected customers/orders.

### Security incident response

Severity:

| Level | Examples | Initial response target |
|---|---|---:|
| `SEV0` | active unauthorized mutation, cross-customer disclosure, compromised privileged/channel credential, widespread wrong automated money promise | acknowledge <=15 min; contain/disable affected automation immediately |
| `SEV1` | confirmed limited PII exposure, limited wrong customer-facing automated message, sustained production outage or backup/PITR failure | acknowledge <=30 min |
| `SEV2` | blocked exploit attempt, non-sensitive degradation, policy/test defect with no confirmed customer impact | next business-hour triage |

Minimum roles, even when one person holds multiple roles:

- incident commander (`OWNER_ADMIN` or delegated named person);
- technical containment/recovery owner;
- operations/customer-remediation owner;
- evidence/timeline scribe.

Required lifecycle:

1. **Detect and declare:** create immutable incident ID, severity, start time, reporter and affected capabilities.
2. **Contain:** evaluate the fail-closed switches; hold automated outbox; revoke affected identities independently; isolate the public VM/adapter/worker as needed; preserve manual deterministic operation if safe.
3. **Preserve evidence:** snapshot relevant audit/inbox/outbox/config/version hashes and provider IDs into restricted evidence storage; record chain of custody; never copy secrets or raw PII into general chat/tickets.
4. **Scope:** identify contacts/orders/messages/data fields/time range, model/tool/config versions, delivery status and whether fallback/manual paths were affected.
5. **Eradicate:** patch configuration/code, rotate credentials, rebuild a compromised public cell from a known-clean image and close the root cause.
6. **Recover:** restore/forward-fix, run security and deterministic money tests, reconcile inbox/outbox without duplicate send, canary the affected capability and monitor.
7. **Communicate/remediate:** owner plus legal/accounting advisor decides any provider, authority or customer notification requirement and timing; decisions/reasons are audited.
8. **Close:** written post-incident review within five business days for `SEV0/SEV1`, with root cause, impact, detection gap, corrective owners/dates and regression tests.

Customer-facing correction after a wrong automated send must reference the original provider message/order, state the corrected fact without silently rewriting history, require human approval, and be sent by the worker or recorded through the manual-send protocol. Financial remedy/refund/credit follows the domain approval policy; the agent cannot grant it.

Evidence access, severity changes, notifications, customer correction, credential rotation and incident closure are mandatory audit events. Incident drills cover at least: public-cell compromise, cross-contact disclosure, wrong confirmed price, lost channel credential, PITR restore and duplicate-send reconciliation.

## 24. Threat model

| Threat | Impact | Required defense |
|---|---|---|
| Prompt injection | unauthorized tool/data access | no generic tools; strict capability API |
| Cross-customer leak | privacy incident | per-contact sessions, scoped retrieval, auth tests |
| Webhook spoof/replay | fake/duplicate order | signature, freshness, nonce, unique event |
| Quote manipulation | revenue/customer harm | server recalculation from typed inputs |
| Approval tampering | unauthorized send/price | content hash, version, expiry |
| Capacity race | missed SLA | transaction/lock/reservation |
| Cost DoS | model spend/outage | rate/token/turn budget and circuit breaker |
| SSRF | internal compromise | no customer URL fetch/browser |
| Malicious attachment | malware/parser exploit | quarantine, scan, human review |
| PII leakage to model/log | privacy harm | minimization/redaction/provider policy |
| Insider export | data loss | RBAC, reauth, audit, anomaly alert |
| Supply-chain compromise | token/data theft | pin, SBOM, SCA, plugin isolation |
| Stale promotion | wrong quote | effective dates, server expiry, cache invalidation |
| Marketing without consent | legal/reputation risk | purpose-specific consent + pre-send gate |
| CSV/HTML injection | code/formula execution | output encoding, sanitization, formula neutralization |
| Public status enumeration | order/PII disclosure | >=80-bit opaque code plus contact-bound ownership auth, generic denial, rate limit |
| Audit bypass/failure | untraceable unauthorized state | atomic mutation/event/audit/outbox; insert-only audit role; fail closed |
| Stale/missing autonomy config | unintended automated action | six-input fail-closed decision; bounded cache TTL; execution-time recheck |
| Raw inbox/DLQ leakage | PII/secret disclosure | encryption, scoped/redacted access, retention and audited replay |

## 25. Security test suite

P0 tests:

- IDOR across contacts/orders.
- Role privilege escalation.
- CSRF and session fixation.
- Webhook invalid signature.
- Replay same event.
- Same event ID/different payload.
- Duplicate outbound retry.
- Stale approval execution.
- Edited message after approval.
- Manual-send attestation with edited hash/revision/recipient.
- Manual attestation then queued worker execution of the same logical send.
- Suppressed contact send.
- Exact STOP and ambiguous opt-out block before any model run.
- Forged/model-invented consent evidence or scope.
- Prompt asks agent to reveal other order.
- Prompt asks to run shell/browser/fetch URL.
- Tool call with unknown field/negative price.
- Agent attempts refund/credit.
- Cross-session memory leak.
- Public status code enumeration, cross-contact replay and generic-denial equivalence.
- Public status token/log redaction and >=80-bit generator property.
- Missing/stale kill-switch input, kill during approved queued send and independent credential revoke.
- External scan proves OpenClaw Gateway control protocol/UI is unreachable while adapter webhook remains reachable.
- Public-cell image/config scan proves no channel credential/plugin, owner mount, Docker socket or filesystem-mutation tool.
- Agent processing path proves edge persist -> runner -> draft -> approval -> outbox worker; no direct agent send.
- Failure injection at domain-event, audit and required-outbox insert proves aggregate mutation rollback.
- Formula injection in every exported text field.
- Stored/reflective XSS in customer notes.
- Backup restore and outbox reconciliation.
- PITR drill demonstrates a usable recovery point <=15 minutes old.
- Raw/DLQ unauthorized access, retention purge and replay-policy recheck.

P1:

- attachment malware/MIME spoof.
- token rotation under load.
- public-cell compromise drill.
- complete `SEV0/SEV1` incident and customer-correction drill.
- dependency/container scan gate.
- external surface scan and penetration test.

## 26. Release security gates

### Before Shadow Mode

- dedicated repository and `.gitignore`;
- auth/RBAC;
- `OWNER_ADMIN` MFA and recovery/session-revoke tests;
- atomic domain mutation + domain event + append-only audit + required outbox on every material command;
- audit-writer insert-only permission and failure-injection rollback test;
- deterministic golden tests;
- continuous WAL/PITR plus encrypted backup, off-host when single-host, and restore-point <=15-minute proof before the first real order;
- secrets separated by environment;
- all autonomy flags off;
- manual-send exact-hash/attestation flow tested if pre-channel copy/send is used;
- manual fallback documented.

### Before public channel

- inherits every Shadow gate;
- completed `SHADOW` pilot with at least 30 real orders, 10 cycle logs and 20 delivery logs;
- zero wrong confirmed/sent monetary values from any cause, including import, configuration, stale revision, manual edit, fallback and post-approval mutation;
- every Shadow outbound is either `OUTBOX_WORKER` execution of an exact approved envelope or a valid exact-hash manual-send attestation;
- zero unsupported customer commitment in the reviewed pilot sample;
- separate public VM/cell;
- OpenClaw Gateway/control UI loopback only; reverse proxy exposes the adapter only;
- public cell has no channel credential, channel plugin, generic mutation tool, owner mount or Docker socket;
- official channel credentials;
- channel credentials scoped to adapter/`OUTBOX_WORKER` only;
- webhook verification/replay defense;
- durable inbox -> authenticated `AGENT_RUNNER` -> public OpenClaw draft -> approval/policy -> outbox -> `OUTBOX_WORKER` path;
- encrypted/retained/audited inbox/outbox/DLQ;
- consent/suppression;
- deterministic STOP/ambiguous opt-out before model;
- per-contact session isolation;
- rate/token/spend budgets;
- MFA for every role with PII, approval, export, price/policy, finance, secret or delivery-address access;
- public order-status ownership/IDOR/enumeration tests;
- fail-closed six-input kill-switch and hold/cancel drill;
- model-provider data-use/training/retention review;
- OpenClaw audit clean of high/critical;
- prompt-injection and authorization suite pass.

### Before bounded autonomy

- inherits every Shadow/public-channel gate and the capability-specific Assisted gate;
- at least 100 eligible cases for the exact capability with zero critical safety, authorization or confirmed-price errors across primary and fallback paths;
- at least 30 consecutive observation days;
- verified business margin/capacity;
- published measurement policy, promotion eligibility event, relevant calendar/cutoff and customer policy for the capability;
- kill-switch drill;
- incident-response drill;
- restore, audit-replay and rollback drills;
- canary rollout with predeclared stop/rollback thresholds;
- metric contracts freeze eligibility, numerator, denominator, sampling and observation window before the gate is evaluated.
