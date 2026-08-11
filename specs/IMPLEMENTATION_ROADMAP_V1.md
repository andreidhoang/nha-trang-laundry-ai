# Implementation Roadmap v1

**Version:** 1.2-first-principles-runtime-transition
**Date:** 2026-08-10
**Goal:** Production-quality Shadow Mode MVP, then evidence-gated autonomy

## 0. Authorization boundary

This roadmap currently authorizes only:

- deterministic internal domain/control-plane implementation;
- an authenticated staff application;
- internal agent extraction/drafting with no direct send capability;
- human-approved Shadow Mode after the real-data launch gates pass.

It does **not** authorize public-channel automation, public Zalo automation, autonomous quotes, autonomous bookings or autonomous delivery decisions merely because the corresponding code exists. Those capabilities remain disabled until their cumulative evidence, security, policy-publication and owner-approval gates pass.

Zalo rules are explicit:

- Zalo Personal automation is prohibited in every production stage;
- no Zalo channel is connected during internal Shadow Mode;
- an official Zalo OA path may be considered only in M7 after connector, webhook, consent, public-policy and separate-VM gates pass.

Telegram Bot API may be used as the first provider sandbox for adapter engineering, but no Telegram
public ingress or send is authorized before DEC-005 and the same cumulative public-channel gates.

## 1. Build-vs-buy preflight

### 1.0 Runtime decision function

The public runtime is selected only after filtering candidates through all authority, isolation, data
and release invariants. For valid candidates, minimize total lifecycle risk and cost rather than
maximize framework features. The current one-agent, ten-tool, three-model-call/six-tool-call workload
makes a bounded Responses adapter the preferred target; OpenClaw remains the comparison/rollback
candidate. This is falsifiable through parity evidence and must be revisited if browser/host tools,
dynamic plugins, broad channel orchestration or multi-agent workflows become real requirements.

## 1.1 Laundry-specific repositories

GitHub preflight on 2026-07-27 found several laundry management projects, but the leading candidates were small, old or weakly governed:

| Project | Signal | Decision |
|---|---|---|
| `mohaiminur/laundry` | ~70 stars; last push 2020; no declared license | Reject |
| `HashJProgramming/LMS-Laundry-Management-System-with-QRCode` | ~23 stars; MIT; last push 2024 | Reference only |
| `abhishekbvs/Laundry-Management-System` | ~19 stars; last push 2018; no declared license | Reject |

Reasons:

- no demonstrated production security posture;
- no versioned policy/approval model;
- no agent boundary;
- no reliable maintenance signal;
- likely more expensive to audit/fork than build the narrow domain core.

## 1.2 General platforms

| Platform | Strength | Why not core V1 |
|---|---|---|
| ERPNext | mature ERP/accounting workflows | too heavy before quote/order truth and invoice policy stabilize |
| NocoDB | fast data/admin prototype | cannot be authority for typed pricing/state/approval invariants |
| Appsmith | strong internal UI builder | useful optional admin shell; domain logic must remain in API |
| Chatwoot | multi-channel human inbox | valuable later when channel handoff becomes painful |
| Activepieces | integration automation | not safe authority for core order/financial transitions |
| Google Sheets/Forms | fast manual pilot | insufficient concurrency, RBAC, audit and invariants for production SoT |
| Custom Responses adapter | narrow public agent loop | preferred target; not business authority or sender |
| OpenClaw | current EVAL_ONLY comparison/rollback runtime | not a mandatory production dependency |

## 1.3 Decision

Build only the business-specific thin core:

- deterministic domain/control plane;
- minimal mobile operator UI;
- narrow agent tools;
- audit/approval/outbox.

Reuse mature components:

- PostgreSQL;
- standard auth/OIDC component;
- official OpenAI Responses API/SDK behind the project runtime contract;
- OpenClaw only as a temporary `EVAL_ONLY` parity/rollback comparator;
- OpenTelemetry;
- maintained PostgreSQL job library;
- official channel APIs;
- S3-compatible storage if media is enabled.

Do not build:

- database engine;
- auth crypto;
- message broker;
- generic omnichannel inbox;
- workflow engine;
- vector database;
- generic agent framework, generic workflow engine or multi-agent runtime. The project does build the
  small bounded adapter required by `ConstrainedAgentRuntime`.

## 2. Recommended technical stack

Final versions are pinned in lockfiles at implementation kickoff.

### Monorepo

**Superseded for implementation by [`ADR-0001`](../docs/adr/0001-python-control-plane.md).**

- Python 3.12 baseline, managed by `uv`.
- Python strict typing with Ruff, mypy and pytest.
- `uv` workspaces and a single `uv.lock`.
- TypeScript remains limited to the future React/Vite browser PWA; no browser code owns business logic.

### Applications

- `apps/web`: React + Vite PWA, mobile-first.
- `apps/api`: FastAPI/Python REST API.
- `apps/worker`: Python PostgreSQL-backed jobs/outbox worker.
- `apps/public-agent-tools`: optional facade deployment or module inside API with separate auth audience.

### Packages

- `packages/domain`: pure Python rules/state machines.
- `packages/contracts`: Pydantic/JSON Schema/OpenAPI contracts.
- `packages/db`: schema, migrations, repositories.
- `packages/policy`: capability and approval decisions.
- `packages/observability`: logging/tracing/redaction.
- `packages/evals`: golden cases, graders, replay harness.
- `packages/test-fixtures`: deterministic seeds.

### Data

- PostgreSQL 16+.
- SQL-first migrations; typed query layer.
- PostgreSQL transactional inbox/outbox.
- PostgreSQL-backed worker library after maintenance review.
- Private S3-compatible object storage only when evidence/media scope is enabled.

### Runtime

- Docker Compose for local/staging and initial single-host control plane.
- Linux production host.
- Caddy or equivalent reverse proxy.
- Separate public agent-runtime VM/VPS security cell is mandatory before any public channel; a same-host
  profile/OS user is development-only isolation.
- `ConstrainedAgentRuntime` is the stable boundary. Custom Responses is the preferred production target;
  OpenClaw remains the current `EVAL_ONLY` comparison/rollback candidate until parity and retirement
  evidence pass. Python remains the business/security authority and PostgreSQL remains the source of truth.
- Stable phase names and dependencies are machine-readable in `../delivery/PROGRAM_PLAN.yaml`; legacy
  M-number headings in this roadmap remain explanatory, not work-item identity.

### Runtime transition baseline

- Existing comparator: audited/repackaged **OpenClaw 2026.7.1-2**, still `EVAL_ONLY`.
- OpenClaw supports isolated named Gateway profiles/state/workspaces, but public production traffic still requires a stronger separate VM/OS security cell.
- Bundled Zalo Bot support is experimental and is not the same product surface as Zalo OA.
- Zalo Personal support is unofficial and carries suspension/ban risk; it is excluded from production.
- Tool policy, sandbox and elevated mode are separate controls; the public cell must deny the capability at every applicable layer.
- Model, prompt, runtime package/plugin and adapter upgrades are versioned releases with regression/security review.
- Pin an explicit runtime implementation and provider route. Do not release a customer cell with runtime `auto`, a
  moving model alias or an interactive owner credential.
- Initial public eval candidate: `openai/gpt-5.6-terra` at low reasoning effort. Record and evaluate an
  exact model release before deployment; `openai/gpt-5.6-sol` is a quality-first comparison/escalation
  candidate and receives no additional permissions.
- Before real PII, verify the effective provider request and storage/retention behavior end to end. A
  route that cannot enforce the approved provider-data policy remains disabled.
- Custom Responses implementation uses strict functions generated from the agent-tool OpenAPI, disables
  built-in provider tools and parallel public tool calls, and routes every call through the existing
  server-bound bridge. OpenClaw removal occurs only after equivalent P0, timeout, budget, isolation,
  provider-data and rollback evidence exists.

### Testing

- Unit/property tests for domain.
- Integration tests with real PostgreSQL container.
- Contract tests for tool/API schema.
- Playwright for critical operator journeys.
- Security/authorization negative tests.
- Agent offline eval harness.

## 3. Repository layout

Create a dedicated repository outside the personal OpenClaw workspace root:

```text
nha-trang-laundry-ai/
  apps/
    web/
    api/
    worker/
  packages/
    domain/
    contracts/
    db/
    policy/
    observability/
    evals/
    test-fixtures/
  config/
    seeds/
    public-knowledge/
  docs/
    specs/
    adr/
    runbooks/
  infra/
    compose/
    caddy/
    backup/
  scripts/
    import/
    verify/
  .github/
    workflows/
  package.json
  pnpm-lock.yaml
  README.md
```

Do not initialize/push the entire `C:\Users\DELL\.openclaw\workspace` as the project repository.

## 4. Environments

### Development

- synthetic customers only;
- local PostgreSQL;
- no production channel credentials;
- model calls optional/mocked;
- seed versions clearly labeled dev.

### Staging

- production-like schema;
- fake/masked PII;
- real agent/tool integration;
- sandbox/test channel;
- lower model/spend cap;
- restore/replay drills.

### Production

- named staff accounts;
- real business configuration;
- separate secrets;
- encrypted backups;
- public agent cell separate;
- feature flags default closed.

Never copy production raw customer database into development.

## 5. Engineering milestones

Effort ranges are planning estimates for one primary engineer with automated review support, not delivery promises.

The stable execution vocabulary is `FOUNDATION`, `IDENTITY_CONTROL`, `DOMAIN_CORE`,
`OPERATIONS_CONTROL`, `AGENT_SHADOW`, `PRODUCTION_HARDENING`, `REAL_SHADOW_READINESS`,
`PUBLIC_ASSISTED` and `BOUNDED_AUTONOMY` in `delivery/PROGRAM_PLAN.yaml`. `PRODUCTION_HARDENING`
is a local engineering track that may proceed while external `AGENT_SHADOW` evidence is blocked; both
tracks are required before real-customer Shadow readiness. The M0–M8 labels below are retained as a
readable historical decomposition only. Queue items use stable domain IDs such as `IDENTITY-001` and
`HARDEN-CI-001`.

## M0 — Repository, ADR and CI foundation

**Estimate:** 2–3 engineering days

Deliver:

- dedicated repository;
- monorepo skeleton;
- pinned runtime/lockfile;
- `.gitignore`;
- secret scanning;
- lint/typecheck/test CI;
- ADR directory;
- environment config schema;
- release/version convention;
- canonical artifact convention backed by `contracts/canonical-enums-v1.json`,
  `contracts/agent-tools-v1.openapi.yaml`, `evals/eval-manifest-v1.yaml` and
  `contracts/release-gate-manifest-v1.schema.json`;
- CI rule that generated canonical artifacts cannot drift from their source.

Exit:

- clean checkout builds;
- CI passes;
- no secret in history;
- project can run with one documented command;
- every release artifact can record source commit and SHA-256.

## M1 — Domain kernel

**Estimate:** 4–7 engineering days

Deliver:

- money/quantity/time value objects;
- exact price migration:
  - 44 source CSV price rows;
  - 43 canonical services;
  - 43 canonical `price_rules`;
  - 42 non-tier rules;
  - 1 `AGGREGATE_TIER_PER_UNIT` rule for `STANDARD_WASH_DRY`;
  - 2 child price tiers for `<6kg` and `>=6kg`;
  - legacy `STD_WASH_DRY_LT6` and `STD_WASH_DRY_GE6` retained as aliases only;
- standard aggregate tier engine;
- fixed/range price rules;
- promotion engine;
- delivery zone engine;
- permission model;
- quote snapshots;
- canonical enum registry for units, money/rate types, rule types, lifecycle states, policy outcomes and error codes;
- seed/import manifest containing source filenames, row counts, mapping counts and SHA-256 hashes;
- golden deterministic tests.

Exit:

- 100% price/promo/delivery boundary tests;
- no float money;
- calculation trace/hash reproducible;
- old CSV IDs map correctly;
- validation asserts exactly `44 source rows -> 43 services + 43 price_rules (42 non-tier + 1 aggregate) + 2 price_tiers`;
- canonical enum and import manifests are generated, versioned and hash-stable.

## M2 — Database and command model

**Estimate:** 7–12 engineering days

**Depends on:** M0 and M1 exit gates.

Deliver:

- migrations;
- party/contact/address;
- catalog/versioning;
- quotes/revisions/lines;
- order orthogonal states/events;
- approvals;
- consent/suppression;
- delivery bundles/legs;
- payments;
- incidents;
- audit/inbox/outbox/idempotency;
- pilot instrumentation required before the first real order:
  - asset/machine registry;
  - batches, operations and order-to-batch allocations;
  - cycle timestamps and measured load;
  - staff time entries/staff-minutes by operation;
  - delivery bundles, route/leg cost events and order cost allocations;
  - processing/delivery/other cost events with completeness status;
- command API and agent-tool API contracts;
- generated OpenAPI 3.1 document plus canonical JSON Schema bundle and contract SHA-256.

Exit:

- constraints/transactions tested;
- illegal transitions fail;
- stale versions fail;
- retry same key returns same result;
- every material mutation, audit event and required outbox event commit atomically;
- audit-write failure rolls back the material command;
- OpenAPI/JSON Schema contract tests pass and generated contract hash is recorded;
- an end-to-end fixture can record a machine operation, batch allocation, staff-minutes, delivery cost allocation and cost-completeness state before pilot starts.

## M3 — Operator PWA

**Estimate:** 5–8 engineering days

**Depends on:** stable M2 command/OpenAPI contracts.

Deliver critical screens:

1. login;
2. new inquiry/customer;
3. quote builder;
4. approval queue;
5. order board;
6. order timeline;
7. intake/measurement;
8. production/ready;
9. delivery legs;
10. payment;
11. incident;
12. machine/batch/cycle and staff-minute capture;
13. delivery-cost/cost-event capture;
14. daily operations dashboard/export: unified inbox, approvals, orders, exceptions, SLA risk,
    deterministic revenue/operations metrics, channel/queue health, AI quality and audit freshness.

Exit:

- mobile usable at store;
- 10 scripted operator journeys pass;
- range/human blockers obvious;
- no hidden unsupported default;
- pilot operator can capture all mandatory instrumentation without editing CSV manually.

## M4 — Shadow agent integration

**Estimate:** 4–7 engineering days

**Depends on:** M1 deterministic engine, M2 tool contracts and M3 approval UI.

Deliver:

- isolated agent dev profile;
- explicit `ConstrainedAgentRuntime` implementation and provider route;
- bounded custom Responses tool loop with strict OpenAPI-derived functions, explicit non-storage,
  no provider built-in tools and no parallel public tool execution;
- OpenClaw comparator retained only for parity/rollback evidence;
- provider storage/retention request verification;
- future customer Concierge prompt evaluated internally only;
- tool facade;
- intake/quote/incident tools;
- fact references;
- message draft revisions;
- approval hash;
- no-send policy;
- model/tool trace;
- frozen eval dataset and graders;
- deterministic context compiler with packet schema/version/hash, fact provenance and exclusion tests;
- eval manifest containing dataset hashes, prompt/model/runtime/tool versions, policy/contract hashes, thresholds and results.

Exit:

- agent cannot call prohibited capability;
- every draft grounded;
- no direct send tool;
- offline eval thresholds pass;
- eval manifest is reproducible from the release commit and blocks release on missing/mismatched hashes.
- custom runtime matches or improves the retained comparator on every P0/safety threshold before
  OpenClaw retirement; retirement is reversible and preserves historical evidence.

Execution slices and dependency order:

1. **`RESPONSES-RUNTIME-001` — local bounded implementation.** Preserve the existing protocol/bridge and
   implement validate → reserve → request → serial tool → validate draft/handoff → revoke/settle.
   Use an injectable scripted transport; add all negative, timeout, cancellation, ambiguity and budget
   tests. No provider credential, public channel or release authority is part of this slice.
2. **`RUNTIME-PARITY-001` — evidence-backed selection.** Run custom and retained OpenClaw candidates
   against identical pinned model/prompt/context/tools/budgets/datasets/graders. Record P0, quality,
   handoff, tool correctness, latency/cost, failure recovery, effective provider request, deployed
   inventories and rollback rehearsal. Synthetic and provider-backed evidence stay separate.
3. **`OPENCLAW-RETIRE-001` — reversible cleanup.** Only after parity, DEC-006, security, provider-data
   and signed release gates pass: remove OpenClaw from public routing/deployment dependencies, rehearse
   rollback, then remove mutable runtime/build inputs. Preserve immutable manifests, hashes, evals,
   audit and rollback documentation. Private Owner OpenClaw is out of scope.

The custom candidate loses selection if any zero-tolerance gate fails, a critical regression exists,
registered latency/cost budgets fail, provider-data behavior is unapproved, or deployed operational
surface is not actually simpler. In that case OpenClaw remains `EVAL_ONLY` while the candidate is fixed
or a new ADR changes direction.

## M5A — Identity, application security and privacy

**Estimate:** 4–7 engineering days

**Depends on:** M2 authorization model and M3 login/operator journeys.

Deliver:

- maintained authentication/OIDC integration;
- named users and server-enforced RBAC/object scope;
- MFA enrollment, recovery and revocation;
- CSRF/CSP/session hardening;
- secrets and environment separation;
- PII/log/trace redaction;
- export formula neutralization;
- IDOR/RBAC/session/CSRF/XSS negative tests.

Exit:

- no shared admin account;
- `OWNER_ADMIN` MFA is enforced before any real Shadow data;
- disable-user/session-revocation and recovery tests pass;
- PII export requires explicit permission and reauthentication;
- no real customer data is allowed before this exit gate.

## M5B — Transactional reliability, recovery and manual-send integrity

**Estimate:** 5–8 engineering days

**Depends on:** M2 audit/inbox/outbox schema and worker contracts.

Deliver:

- transactional audit/domain-event/outbox invariant;
- inbox/outbox worker, idempotency, retry classification and DLQ;
- manual Shadow send attestation bound to exact approved content hash;
- deterministic consent/suppression final-send checks;
- fail-closed capability and global kill switches;
- managed PITR or continuous WAL archiving, encrypted off-host backups and restore automation;
- duplicate-event/send and reconciliation tooling;
- database/model/channel degraded-mode runbooks.

Exit:

- audit failure-injection proves no unaudited material mutation commits;
- same inbound/provider event creates at most one logical agent run/send;
- manual edit after approval invalidates approval and cannot retain the old attestation;
- restore drill demonstrates RPO `<=15 minutes` and RTO `<=4 hours`;
- outbox recovery produces no duplicate business effect;
- kill-switch test holds unexecuted automated actions and leaves manual operation available.

## M5C — Observability, release and incident readiness

**Estimate:** 3–6 engineering days

**Depends on:** M4 eval artifacts plus M5A and M5B.

Deliver:

- OpenTelemetry traces, structured logs, redaction and SLO dashboards;
- alerts for audit failure, backup/WAL freshness, queue age, DLQ, auth/security, spend and policy drift;
- rate/token/turn/spend limits;
- dependency/license/secret/container scans and SBOM;
- signed or hashed release manifest linking commit, migrations, OpenAPI hash, canonical enum/config hashes and eval manifest;
- security incident-response, public-agent compromise and degraded-mode runbooks;
- capability kill-switch and restore/incident drills.

Exit:

- all P0 security tests pass;
- alert delivery and ownership are verified;
- release manifest and eval manifest verify against deployed artifacts;
- restore, incident and kill-switch drills pass;
- M5A, M5B and M5C are all mandatory dependencies for real Shadow; they are not optional parallel polish.

## M6 — Internal Shadow pilot

**Estimate:** 7–14 calendar days

**Starts only after:** M0–M5C exit gates and explicit owner go/no-go.

Operate:

- all outbound human-approved;
- 30+ completed or operationally closed **real orders**, not quote records, leads or synthetic fixtures;
- 10+ cycle logs;
- 20+ delivery cost logs;
- corrections reason-coded;
- daily issue review.

Exit:

- zero critical deterministic errors;
- 100% audit/approval coverage;
- every counted order has a quote snapshot, order events and applicable instrumentation;
- required machine/batch/staff-minute/delivery-cost/cost-event data meets the published completeness threshold;
- no systematic UX or agent failure;
- no public channel, public Zalo or Zalo Personal automation is connected.

## M7 — Official channel Assisted pilot

**Estimate:** 5–10 engineering days after provider access, excluding external OA/provider approval lead time.

**Start only after:** all M0–M6 gates remain green and the exact capability has passed its own Assisted entry gate.

Deliver:

- official channel credentials/adapter;
- verified webhook;
- channel dedupe/receipts;
- safe intent auto-send flags;
- consent/suppression;
- mandatory separate public agent-runtime VM/VPS security cell;
- MFA enforced for every role that can access PII, approve, export, publish policy or handle finance;
- approved `PUBLIC_CUSTOMER` policy/knowledge bundle with version, audience, effective period and hash;
- public-policy correction workflow: disable bad version, invalidate pending actions, identify affected messages/orders, approve correction text, notify affected customers when required and preserve audit;
- canary and rollback thresholds.

Rules:

- Zalo Personal automation remains prohibited.
- Telegram Bot API is the preferred adapter sandbox candidate, not an implicit production choice;
  its secret-header, update dedupe, sender receipt, rate-limit and unknown-outcome tests must pass.
- No public Zalo automation is authorized merely by completing the adapter.
- Official Zalo OA may be enabled only after its connector/webhook/security review, public-policy publication gate, consent test, separate-VM gate and explicit owner approval pass.
- All providers normalize to one canonical inbound envelope and share domain behavior, while keeping
  provider-specific credentials, webhook verification, rate limits, receipts and reconciliation.

## M8 — Bounded autonomy

Bounded autonomy is cumulative and capability-specific. Enabling one capability does not authorize the next.

Every capability must satisfy all of the following before activation:

1. all M0–M7 foundational/security/public-policy gates remain green;
2. exact capability contract, eval suite, metric, owner and rollback trigger exist;
3. at least **100 eligible cases for that exact capability** have been observed in Shadow/Assisted replay or production review;
4. at least **30 calendar days** of observation for that capability;
5. zero critical price, authorization, privacy, consent, duplicate-action or unsupported-promise errors;
6. human correction/handoff thresholds are defined and passed;
7. kill switch and rollback are tested;
8. owner explicitly approves the capability envelope, traffic percentage and exclusions.

Capability sequence and additional gates:

1. **FAQ auto-send** — only published `PUBLIC_CUSTOMER` facts; correction/rollback drill passed.
2. **Intake questions** — contact binding, minimization, consent/suppression and required-field accuracy gates passed.
3. **Incident acknowledgement** — no fault/remedy language; escalation delivery/response SLO passed.
4. **Standard quote estimate** — deterministic fixed-price/promotion snapshot; no range/special/B2B; estimate disclosure enforced.
5. **Standard final quote** — verified staff measurement, exact approved rule version and customer reconfirmation behavior validated.
6. **Capacity reservation/booking** — measured cycle/staff data, concrete calendar/cutoff, transactional reservation and oversell tests passed.
7. **Delivery `<=6km`** — pickup pre-weight, one-leg, route/staff capacity, cost evidence and delivery rollback gates passed; enabled last.

Special, B2B, refund, credit, compensation and >6km remain human.

## 6. Work breakdown structure

## Epic E01 — Foundation

- `E01-T01` dedicated repository;
- `E01-T02` package/workspace layout;
- `E01-T03` config validation;
- `E01-T04` logging/redaction;
- `E01-T05` CI quality gate;
- `E01-T06` secret/SCA scan;
- `E01-T07` ADR templates;
- `E01-T08` canonical artifact/release manifest schema;
- `E01-T09` source/contract hash verification.

## Epic E02 — Catalog and pricing

- `E02-T01` service normalization;
- `E02-T02` legacy aliases;
- `E02-T03` pricebook version model;
- `E02-T04` fixed/range/tier types;
- `E02-T05` aggregate 6kg rule;
- `E02-T06` calculation trace;
- `E02-T07` money allocation/rounding;
- `E02-T08` golden cases;
- `E02-T09` publication validation;
- `E02-T10` exact 44-row import manifest and `43 services / 43 rules / 2 tiers` count assertion;
- `E02-T11` canonical enum registry and legacy alias map.

## Epic E03 — Promotions

- `E03-T01` half-open effective range;
- `E03-T02` explicit target resolution;
- `E03-T03` eligibility event type;
- `E03-T04` stacking policy;
- `E03-T05` discount allocation;
- `E03-T06` expiry/repricing tests;
- `E03-T07` promotion snapshot.

## Epic E04 — Customers and consent

- `E04-T01` party/account/contact;
- `E04-T02` phone normalization;
- `E04-T03` address tokenization/encryption;
- `E04-T04` consent events;
- `E04-T05` suppression projection;
- `E04-T06` duplicate suggestions;
- `E04-T07` privacy/export permissions.

## Epic E05 — Quote and order

- `E05-T01` quote/revision/line;
- `E05-T02` estimate/range/final states;
- `E05-T03` customer/store acceptance separation;
- `E05-T04` order state machines;
- `E05-T05` command handlers;
- `E05-T06` optimistic concurrency;
- `E05-T07` customer reconfirmation;
- `E05-T08` order timeline.

## Epic E06 — SLA and capacity

- `E06-T01` SLA policy types;
- `E06-T02` concrete SLA instances;
- `E06-T03` 8h clock;
- `E06-T04` 24–48 guidance;
- `E06-T05` annual calendar;
- `E06-T06` promises;
- `E06-T07` cycle logs;
- `E06-T08` operation staff-minute/time entries;
- `E06-T09` future reservation interface.

## Epic E07 — Custody and batches

- `E07-T01` bag/item tags;
- `E07-T02` custody events;
- `E07-T03` batch/operation/allocation;
- `E07-T04` machine references;
- `E07-T05` QC;
- `E07-T06` measured cycle import/export;
- `E07-T07` pilot instrumentation completeness validator.

## Epic E08 — Delivery

- `E08-T01` distance evidence;
- `E08-T02` zone engine;
- `E08-T03` delivery bundle/legs;
- `E08-T04` vehicle plan;
- `E08-T05` customer fee vs internal cost;
- `E08-T06` extra trip charge;
- `E08-T07` delivery proof;
- `E08-T08` route/leg cost events and order allocations;
- `E08-T09` cost log/report.

## Epic E09 — Finance/incidents

- `E09-T01` charges;
- `E09-T02` partial payments;
- `E09-T03` invoice request tracking;
- `E09-T04` incident/evidence;
- `E09-T05` remedy approval;
- `E09-T06` credit ledger;
- `E09-T07` processing/other cost events and completeness.

## Epic E10 — Approval/audit/reliability

- `E10-T01` approval envelope/hash;
- `E10-T02` decision/execution;
- `E10-T03` immutable audit in the material-command transaction;
- `E10-T04` idempotency;
- `E10-T05` inbox/outbox;
- `E10-T06` retry/DLQ;
- `E10-T07` kill switches;
- `E10-T08` reconciliation;
- `E10-T09` manual-send attestation and approval invalidation;
- `E10-T10` WAL/PITR backup and duplicate-free restore.

## Epic E11 — Agent

- `E11-T01` public prompt;
- `E11-T02` context assembler;
- `E11-T03` tool schemas;
- `E11-T04` policy decision integration;
- `E11-T05` fact-backed drafts;
- `E11-T06` no-send approval path;
- `E11-T07` model budgets;
- `E11-T08` fallback/handoff;
- `E11-T09` eval manifest generation and verification.
- `E11-T10` custom Responses runtime adapter and bounded tool loop;
- `E11-T11` runtime parity, provider-data capture and OpenClaw retirement gate;
- `E11-T12` context packet schema/version/hash and provenance tests.
- `E11-T13` custom-runtime timeout/cancel/late-call/provider-ambiguity negative suite;
- `E11-T14` signed runtime-selection evidence and reversible OpenClaw public-path retirement.

## Epic E12 — Evals and contract gates

- `E12-T01` deterministic test vectors;
- `E12-T02` Vietnamese conversation set;
- `E12-T03` handoff grader;
- `E12-T04` fact grounding grader;
- `E12-T05` prompt injection set;
- `E12-T06` OpenAPI/JSON Schema generation and diff gate;
- `E12-T07` canonical enum/import hash gate;
- `E12-T08` duplicate/replay suite.

## Epic E13 — Identity, application and privacy security

- `E13-T01` maintained auth/OIDC integration;
- `E13-T02` named-user RBAC/object scope;
- `E13-T03` owner MFA and privileged/PII-role MFA gates;
- `E13-T04` CSRF/CSP/session/recovery hardening;
- `E13-T05` IDOR/RBAC/XSS/security suite;
- `E13-T06` PII/log/trace redaction;
- `E13-T07` export permission/reauth/formula neutralization;
- `E13-T08` secret/environment separation.

## Epic E14 — Recovery, observability and release safety

- `E14-T01` OpenTelemetry/SLO dashboards;
- `E14-T02` alert routing and ownership;
- `E14-T03` RPO/RTO restore drill;
- `E14-T04` incident/public-cell-compromise drill;
- `E14-T05` kill-switch/in-flight test;
- `E14-T06` container scan/SBOM;
- `E14-T07` signed/hashed release manifest;
- `E14-T08` public-policy publish/correction/affected-customer workflow.

## Epic E15 — Official channels and daily operations intelligence

- `E15-T01` canonical inbound envelope and provider-adapter contract;
- `E15-T02` Telegram Bot API sandbox adapter, webhook authentication and dedupe;
- `E15-T03` Telegram outbox sender, receipts and unknown-outcome reconciliation;
- `E15-T04` official Zalo OA adapter contract and credential lifecycle;
- `E15-T05` Zalo OA sender, receipts and reconciliation;
- `E15-T06` unified Staff PWA inbox, channel health and exception recovery;
- `E15-T07` versioned daily metrics/read models with numerator, denominator and freshness;
- `E15-T08` read-only AI operations brief grounded only in typed metric facts;
- `E15-T09` dashboard RBAC/IDOR, AI-grounding and no-action-authority tests.

## 7. CI pipeline

For every pull request:

1. dependency install from lockfile;
2. formatting/lint;
3. TypeScript check;
4. unit/property tests;
5. schema migration validation;
6. PostgreSQL integration tests;
7. regenerate OpenAPI/JSON Schema and fail on uncommitted contract drift;
8. regenerate canonical enums/import manifest and verify hashes/counts;
9. API/tool contract tests;
10. deterministic golden tests;
11. agent frozen eval subset and eval-manifest validation;
12. secret scan;
13. dependency/license scan;
14. build artifacts.

Release pipeline adds:

- full agent eval;
- canonical enum/config/OpenAPI/eval-manifest hash verification;
- container scan/SBOM;
- staging migration;
- smoke tests;
- backup checkpoint;
- hashed release manifest;
- required domain/security/owner approval;
- canary/feature flag;
- post-deploy checks.

## 8. Branching and review

- protected `main`;
- short-lived branches;
- pull request required;
- no direct production edits;
- migration/schema changes require data review;
- auth/network/public-agent changes require security review;
- pricing/promotion changes require domain/business review;
- prompt/model/tool changes require eval report.

For the first small team, one human may hold several review roles, but the checklist remains explicit.

## 9. Test pyramid

### Unit/property

- value objects;
- formulas;
- state machines;
- policy composition;
- calendar boundaries;
- permission logic.

### Integration

- PostgreSQL constraints;
- idempotency;
- outbox transaction;
- approval invalidation;
- consent final-send check;
- migrations.

### Contract

- OpenAPI;
- agent tool schemas;
- channel normalized events;
- domain event envelopes.

### E2E

- inquiry → quote → approval → intake → ready → delivery/payment;
- range item;
- far delivery;
- complaint;
- duplicate webhook;
- model outage/manual operation.

### Security

- auth/RBAC/IDOR;
- CSRF/XSS;
- prompt injection;
- replay;
- approval tampering;
- CSV injection;
- secret/log redaction.

## 10. Operator UX specification

Mobile-first requirements:

- one primary action per screen;
- large tap targets;
- Vietnamese labels;
- price/permission state visible;
- no hidden automatic rounding;
- explicit `ƯỚC TÍNH`, `KHOẢNG GIÁ`, `ĐÃ DUYỆT`;
- owner-confirmed price cliff notice near 6kg;
- visible SLA clock start reason;
- human-required fields grouped;
- offline/network failure clearly shown;
- every commit action confirms result once.
- channel identity and provider delivery state are visible without exposing provider credentials;
- every AI-generated operations summary links to metric IDs/versions and freshness timestamps;
- dashboard AI cannot issue SQL, choose arbitrary object IDs, mutate state or send.

Critical warning colors/labels:

- `HUMAN REQUIRED`;
- `PROMOTION PROVISIONAL`;
- `TAX UNVERIFIED`;
- `CAPACITY NOT CONFIRMED`;
- `DELIVERY FEE MISSING`;
- `CUSTOMER RECONFIRMATION`;
- `INCIDENT`.

Never show `0` for unknown cost/fee/tax.

## 11. Seed and configuration publication

Flow:

```text
CSV/media evidence
-> staging import
-> validation report
-> draft config version
-> deterministic dry run
-> owner review
-> publish approval
-> immutable active version
```

The runtime cannot read a draft version.

Publish invalidates:

- caches;
- unapproved drafts based on incompatible old version;
- approvals whose hash includes old policy where action not yet executed.

It does not mutate historical accepted quotes/orders.

### Public customer policy publication gate

Customer-facing policy/knowledge uses a separate audience-qualified flow:

```text
approved structured business truth
-> PUBLIC_CUSTOMER draft bundle
-> prohibited/internal-content scan
-> business review
-> legal review where the policy requires it
-> deterministic fact/eval dry run
-> owner publish approval
-> immutable version + effective period + SHA-256
-> public-agent allowlist
```

Before M7:

- at least one complete `PUBLIC_CUSTOMER` bundle is published;
- public runtime is technically unable to retrieve draft/internal/risk-review documents;
- correction and rollback are tested.

Correction workflow:

1. disable the faulty public version;
2. block new sends and invalidate unexecuted approvals/actions using it;
3. publish the last valid version or an owner-approved correction;
4. identify affected messages, quotes and orders;
5. create an owner-approved customer correction/notification task where required;
6. preserve source version, affected-set query, decisions and sends in audit.

## 12. Feature flags and kill switches

Global:

- `agent_processing_enabled`;
- `agent_outbound_enabled`;
- `channel_ingress_enabled`;
- `all_automation_enabled`.

Capability:

- `faq_auto_send`;
- `intake_auto_send`;
- `incident_ack_auto_send`;
- `quote_estimate_auto_send`;
- `quote_final_auto_send`;
- `slot_auto_confirm`;
- `delivery_auto_confirm`;
- `marketing_auto_send`.

Effective automation is conjunctive and fail closed:

```text
automation_allowed =
  all_automation_enabled
  AND agent_processing_enabled
  AND agent_outbound_enabled
  AND channel_ingress_enabled
  AND exact_capability_enabled
  AND cumulative_stage_gates_pass
  AND current_PDP_decision_is_ALLOW
```

- A global flag can never override a disabled capability.
- Missing, stale or unavailable flag/policy state means automation is disabled.
- Public/provider-specific ingress also needs an explicit provider flag; no Zalo Personal provider flag or production adapter may exist.
- Disabling outbound holds unexecuted automated actions while preserving manual operation and audit.

Kill switch changes:

- audited;
- owner/admin authorized;
- immediate;
- safe for in-flight work;
- tested in staging and pilot.

## 13. Pilot operating cadence

Daily during Shadow:

1. review wrong/edited drafts;
2. classify edit reasons;
3. review SLA/late orders;
4. verify outbox/audit;
5. review cost completeness;
6. inspect any security/policy denial;
7. add approved failure cases to eval set.

Weekly:

- regression report;
- domain/policy drift;
- price/promo expiry;
- backup status;
- delivery contribution;
- capacity evidence;
- stage gate score.

## 14. Decision log needed before Assisted/Bounded

Not required to start R1 coding, but must be recorded:

- promotion eligibility event;
- scale precision/weight rounding;
- tax inclusion;
- invoice process;
- exact annual closures and cutoff;
- one-leg delivery;
- pickup vehicle pre-weight method;
- rewash window;
- late credit base/cap/expiry;
- compensation;
- storage/unclaimed goods;
- B2B terms;
- official Zalo OA/channel path;
- public-policy reviewer/owner, legal-review triggers and customer-correction workflow.

## 15. Definition of Ready

A task is ready when:

- user/business behavior defined;
- typed inputs/outputs;
- permissions;
- state transition;
- errors;
- audit event;
- metrics;
- tests;
- human/failure path;
- source policy/version known;
- affected canonical enum/OpenAPI/eval/release artifacts identified.

## 16. Definition of Done

- code reviewed;
- tests pass;
- negative authorization tests;
- deterministic fixtures updated;
- no PII/secret leak;
- telemetry/audit present;
- migration tested;
- failure/retry path tested;
- docs/runbook updated;
- feature flag/rollback verified;
- owner-facing behavior demonstrated;
- generated OpenAPI, canonical enum/config hashes and eval manifest match the release manifest where applicable.

## 17. Recommended first implementation slice

Build the smallest vertical slice before broad schema completion:

```text
Staff login
-> create B2C contact
-> STANDARD_WASH_DRY estimate
-> applicable published promotion snapshot
-> <=6km delivery rule
-> human approval
-> create order
-> intake actual weight
-> new exact quote revision
-> production accepted
-> ready at store
-> payment
-> full audit/export
```

Then add:

1. range-priced service;
2. special 24–48 guidance;
3. delivery legs;
4. incident;
5. agent draft;
6. official channel only after pilot.

This vertical slice is a development demonstration, not authorization to start a real-data pilot. M2 pilot instrumentation and every M5A–M5C real-data gate must be complete before the first real order is counted.
