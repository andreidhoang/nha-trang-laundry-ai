# Build Engineering Specification — Production-Constrained AI Operations System

**System:** Nha Trang Laundry AI / Giặt Là Sạch Cộng  
**Status:** implementation baseline  
**Audience:** engineers, Codex coding agents, reviewers, operations owner  
**Language:** English execution specification; Vietnamese business specifications remain normative.  
**Last reviewed:** 2026-08-10

## 1. Purpose and decision

Build a production-quality *operations system with a constrained AI concierge*, not an autonomous
laundry business.

The system must remain safe and operable when the model, OpenClaw, a channel provider, or a worker
is unavailable. AI may understand language, gather missing facts, and draft responses. Deterministic
domain code, authenticated staff, and versioned policy decide commercial facts and all side effects.

The initial deliverable is an internal, human-approved Shadow Mode MVP. It is deliberately not a
public chatbot launch.

```text
Customer event
  -> authenticated channel adapter
  -> durable inbox
  -> agent runner
  -> constrained LLM concierge
  -> typed Tool Facade
  -> deterministic domain + policy decision point
  -> approval when required
  -> transactional outbox
  -> sole sender worker
```

The model never becomes the system of record, a security boundary, or the sender of record.

## 2. Normative authority and conflict handling

This file translates the approved architecture into build instructions. It must not silently change
business policy.

Priority order when sources conflict:

1. an owner-approved, published, versioned production configuration;
2. immutable structured snapshots in PostgreSQL;
3. `specs/contracts/*` and `specs/evals/*` machine-readable contracts;
4. approved specifications in `specs/`;
5. this build specification;
6. CSV/templates, research notes, and prose examples.

Rules for an ambiguity:

- Do not guess or encode a business rule from a conversational example.
- Return `REQUIRE_HUMAN`, `HUMAN_INPUT_REQUIRED`, or `NOT_SUPPORTED` as appropriate.
- Add a decision record naming the missing owner decision, affected capability, temporary
  fail-closed behavior, and test to update when the decision is approved.
- Structured contracts win over prose for names, schemas, enums, and fields.

`POLICY_RISK_REVIEW.md`, internal prompts, security findings, credentials, private memory, and raw
operational exports are never public retrieval content.

## 3. Product boundary

### 3.1 In scope for R1 / Shadow Mode

- staff authentication and RBAC;
- versioned catalog, pricebook, promotions, calendar, SLA, and business profile;
- contacts/accounts, consent and suppression events;
- multi-line quotes, immutable quote snapshots, deterministic monetary calculation;
- orders, chain-of-custody pilot fields, state transitions, events, and audit;
- delivery and SLA proposal logic with human confirmation where required;
- staff mobile PWA, approvals, and controlled manual-send attestation;
- durable inbox/outbox, idempotency, retries, dead-letter handling, and traceability;
- agent drafting and typed tool calls with no direct send;
- offline, integration, adversarial, and Shadow evaluation harnesses.

### 3.2 Explicitly out of scope for R1

- public customer channel connection;
- fully autonomous customer service;
- LLM-generated prices, discounts, fees, capacity, or SLA promises;
- dynamic pricing, autonomous dispatch, refunds, credits, compensation, or invoices;
- production Zalo Personal automation;
- vector database, Kubernetes, Kafka, microservice mesh, generic workflow engine, or a multi-agent
  swarm.

### 3.3 Technology decision

Build a Python-first modular monolith: Python 3.12, FastAPI, Pydantic, PostgreSQL, a
PostgreSQL-backed worker, `uv` workspace/lockfile, Docker Compose, and OpenTelemetry. Use a Linux
production host. React/Vite with TypeScript is retained only for the browser-based Staff PWA; browser
code has no business authority.

No agent framework is required to begin M0–M3. From M4 onward, the Python `ConstrainedAgentRuntime`
contract is the stable boundary and a custom OpenAI Responses adapter is the preferred production
target. OpenClaw remains an `EVAL_ONLY` comparison/rollback implementation until runtime-parity and
retirement evidence pass. Neither runtime is the CRM, ledger, workflow authority, database, policy
decision point, channel adapter, or delivery sender. ADR-0003 supersedes only the mandatory OpenClaw
selection in ADR-0002; its isolation, provider-data, authority, and egress constraints remain binding.

This is a smallest-sufficient-system decision for the current workload: one bounded Concierge, ten
fixed tools, at most three model calls and six tool calls, and no browser, shell, filesystem, channel
send, generic plugin or multi-agent requirement. It is not a general rejection of OpenClaw. The custom
target remains an unproven candidate until equivalent comparative evidence exists, and it must not
grow into a project-owned generic agent framework. Revisit the decision through an ADR if the required
capability set materially expands.

## 4. Non-negotiable system invariants

Every implementation and code review must preserve all of these invariants.

1. PostgreSQL is the transactional source of truth.
2. Money is integer VND. Never use binary floating point for monetary values.
3. Only deterministic domain code calculates price, eligibility, discount, delivery fee, SLA, state,
   permission, capacity eligibility, and margin.
4. A published configuration is immutable. Historical quotes/orders retain rule snapshots and hashes.
5. Every mutation, domain event, required outbox item, and audit event commits atomically or none
   commits.
6. Every inbound provider event is persisted and deduplicated before any model invocation.
7. Every external send has a stable logical idempotency key. Only the outbox worker may execute it.
8. A content edit invalidates its approval. The exact rendered content hash and revision must match at
   execution time.
9. Server-derived identity, authorization, role, stage, approval obligations, contact binding, and
   capability may never be supplied or overridden by model tool arguments.
10. STOP/withdrawal/suppression is recognized deterministically at ingress before model invocation and
    before every marketing send.
11. Missing, stale, unpublished, malformed, or conflicting policy/configuration disables the affected
    automated capability.
12. No hidden retry can create a duplicate order, quote acceptance, approval, or send.
13. The system records traces and structured evidence, never chain-of-thought.
14. A public/untrusted customer cannot reach the owner workspace, private memory, shell, browser,
    database, gateway control plane, channel credential, or direct-send API.

## 5. Agent architecture and responsibility split

### 5.1 One concierge in the customer hot path

Use one constrained Customer Concierge per inbound interaction. Logical roles such as quote composer
and incident intake are prompt/tool profiles, not independent autonomous agents that converse with
each other. Introduce another runtime only when it has a distinct trust boundary, separately measured
value, explicit tool contract, owner, and eval suite.

The concierge may:

- classify intent and map a candidate service;
- extract candidate fields and ask for missing information;
- call allowlisted typed tools;
- explain returned, verified facts;
- draft a reply or incident summary in Vietnamese;
- abstain and hand off.

The concierge may not originate, infer, select, or mutate:

- price, discount, tax, subtotal, total, margin, fee, actual weight, distance, capacity, or exact SLA;
- order state/timestamps, consent proof, approval state, policy interpretation, or bank information;
- refunds, credits, remedies, fault, B2B credit/invoice terms, or final range prices;
- arbitrary customer/contact/order/address identifiers.

### 5.2 Typed Tool Facade

The public agent only calls operations present in
`specs/contracts/agent-tools-v1.openapi.yaml`. Generate TypeScript schemas/validators/SDKs from that
file or prove contract equivalence in CI. Do not create a generic SQL, REST, browser, search, file, or
"execute action" tool.

For each tool, enforce:

- schema validation and unknown-field rejection;
- server-derived actor, tenant, contact/conversation binding, stage, and policy context;
- least-privilege read projection;
- authorization and policy decision before domain call;
- timeout, rate/cost budget, idempotency, audit and trace correlation;
- structured error codes that cause a safe handoff rather than a model guess.

Tool output is untrusted data for display and must be encoded/sanitized in its rendering context. It is
not executable instructions.

### 5.3 Public and private runtime cells

Private Owner OpenClaw may assist trusted owner workflows such as research, analytics, and engineering.
It never receives public untrusted messages directly and never bypasses policy, approval, or consent.

Before accepting any public inbound traffic, deploy the selected public agent runtime on a separate
VM/VPS and OS identity with separate state, secrets, model credential, network policy, and logs. It has:

- no channel-provider credential, SDK, or network route;
- no owner workspace, personal memory, raw database route, filesystem mutation, shell, browser, nodes,
  generic web fetch, plugin installation, or direct messaging tool;
- only model-provider and Tool Facade outbound routes;
- private/loopback-only control plane; no public runtime UI/protocol.

The separate-cell boundary applies to every runtime. For the retained OpenClaw comparator, OpenClaw
documentation explicitly treats a Gateway as one trusted-operator boundary, not hostile multi-tenant
isolation; never expose the owner's Gateway. See
[OpenClaw security guidance](https://docs.openclaw.ai/gateway/security).

The public release must pin the exact provider/model, explicit runtime implementation/route, prompt
bundle, package/plugin inventory and public-cell configuration. Runtime `auto`, moving model aliases
and interactive personal credentials are not release identities. The custom Responses adapter is the
preferred target; the existing OpenClaw route remains an `EVAL_ONLY` comparator. Neither candidate
grants production authorization until integrated parity, provider-data, security and release gates pass.

Before any real customer data reaches the model provider, an integration test must verify the
effective request's storage/retention behavior and Security/Privacy must approve provider training,
retention, region, deletion, subprocessors and incident terms. A runtime/provider combination that
cannot enforce the approved data policy remains disabled for real-customer processing.

### 5.4 Minimum public runtime state machine

The preferred adapter owns only the bounded provider/tool loop:

1. validate the server-created job, signed context packet, runtime/model pin and remaining deadline;
2. reserve the worst-case registered cost before each model call;
3. send the exact prompt/context and strict allowlisted functions with explicit non-storage, provider
   built-in tools disabled and parallel public tool calls disabled;
4. reject unknown/malformed/duplicate/out-of-budget calls and execute an accepted call only through the
   already contact-bound `AgentToolBridgeSession`;
5. append the typed tool result and continue within the same absolute call/deadline budget;
6. validate and persist a draft or deterministic handoff plus structured evidence;
7. revoke the bridge and settle/release reservations on success, cancellation, timeout or failure.

Provider/framework conversation state is never durable business state. The adapter must not implement
channels, plugins, arbitrary tools, business workflows, policy calculation, database access, generic
memory or multi-agent scheduling.

## 6. Domain and data engineering requirements

### 6.1 Data model rules

- Use SQL-first migrations and a typed query/repository layer. Migrations are forward-only once
  deployed; destructive migrations need an approved, reversible rollout plan.
- Use UUID/opaque external references with at least 80 bits of entropy; public references are not
  authorization.
- Store timestamps as UTC instants, display and business-rule calculations in the declared timezone.
- Normalize inputs at boundaries and preserve display input separately where required.
- Use append-only domain/audit/consent events. Do not delete orders or overwrite evidence.
- Record actor, service identity, reason code, correlation ID, occurred-at time, object version, and
  snapshot hash for every material decision.
- Encrypt sensitive data in transit and at rest according to the deployment platform. Minimize PII
  fields and retain only data needed for the operational purpose.

### 6.2 Quote/order behavior

- Separate estimate from final quote; range-priced items cannot become final without a staff-selected,
  reasoned exact amount and approval.
- Reprice/reapprove after an edit or expired quote. Never mutate an approved historical revision.
- Route all order transitions through command handlers. UI endpoints do not update status directly.
- Treat pickup and return as separate delivery legs. Address/distance/weight changes invalidate the
  relevant fee/vehicle/approval decision.
- Set R1 auto-confirmable capacity to zero. Exact promise remains a human decision.

## 7. Security, privacy, and abuse resistance

This system has adversarial inputs: customer messages, attachments, quoted messages, tool outputs,
provider payloads, web content, CSV imports, and staff-entered text. Prompts are not authorization.

### 7.1 Required controls

- RBAC enforced server-side for every read and mutation; named staff accounts only; no shared admin.
- MFA for the owner before real-customer Shadow. MFA for every PII, approval, export, policy, finance,
  or address role before public access.
- Authenticated webhooks with provider signature validation, replay protection, payload limits, rate
  limits, and durable dedupe.
- Object-level authorization tests for every public identifier; never trust an ID from the client/model.
- Secret manager or environment injection per environment; secrets never enter code, prompts, logs,
  test fixtures, browser captures, or git.
- Data redaction in logs/traces. Export must be role-scoped, sanitized, auditable, and revocable.
- Content Security Policy, secure cookies, CSRF protections where applicable, input validation, output
  encoding, dependency pinning, and vulnerability scanning.
- Attachment/media intake uses size/type limits, isolated processing, malware policy, and explicit
  retention; never give an attachment arbitrary tool authority.
- No production PII in development, demos, or generic LLM evaluation datasets.

### 7.2 Threat-driven tests

The test suite must include prompt injection, prompt/system leakage attempts, cross-customer access,
ID enumeration, tool argument substitution, fake/stale approval, edited-after-approval, duplicate
webhook/send, STOP race, malicious attachment, CSV/HTML/SQL injection, URL/SSRF, stale config, model
timeout, provider outage, and fallback regression.

The OWASP LLM/GenAI Top 10 identifies prompt injection, sensitive-information disclosure, improper
output handling, excessive agency, misinformation, and unbounded consumption as relevant application
risks. Use it as a threat-catalogue input, not a substitute for tests.
[OWASP Top 10 for LLM and GenAI applications](https://genai.owasp.org/llm-top-10/).

## 8. Reliability and operational design

### 8.1 Inbox/outbox protocol

1. Validate and normalize inbound event.
2. In one transaction, deduplicate and persist raw/minimized event, normalized envelope, inbox record,
   and audit evidence.
3. A worker claims the inbox item with a lease and invokes the agent exactly as a recoverable job.
4. Persist agent draft/tool result and policy decision. Agent memory never represents durable state.
5. Create approval/outbox envelope transactionally when policy permits.
6. Re-check current policy, revision/hash, suppression, feature flag, and idempotency immediately before
   send.
7. The sender worker records provider attempt/receipt. Retry only a known-safe, idempotent envelope.

Use bounded exponential backoff, jitter, max attempts, dead-letter queue, operator visibility, and a
replay process that preserves original correlation IDs. Treat unknown provider outcome as reconciliation,
not permission to blindly resend.

### 8.2 Availability and recovery

- Health checks must distinguish API, database, worker, queue age, model provider, and channel status.
- Define SLOs only after baseline measurement; alert on queue age/depth, failed/delayed jobs, duplicate
  sends, approval backlog, data backup freshness, policy/config staleness, error rate, latency, and
  spend.
- For first real orders, configure continuous WAL/PITR plus off-host base backups. Demonstrate point-in-
  time restore at or below the 15-minute RPO requirement before declaring readiness.
- Implement kill switches at capability and outbox-execution levels. Missing/stale flags fail closed.
- Every external system has a documented manual fallback and an exercised incident/rollback runbook.

## 9. AI quality, evaluation, and release evidence

### 9.1 Evaluation principle

Offline demonstration is not production authorization. Measure the complete integrated path: primary
model, every fallback, deterministic/template degradation, tool facade, policy, approval, outbox, and
provider simulation. A fallback is in the same safety denominator as the primary model.

Use the existing `specs/evals/eval-manifest-v1.yaml`, case/result schemas, and assertion registry as
the baseline. CI must fail if it detects contract drift among OpenAPI, database, prompts, fixtures,
domain code, policy, and evals.

Required datasets:

- frozen regression cases for known contracts;
- rotating unseen cases to detect overfitting;
- synthetic combinatorial boundary cases;
- adversarial/P0 security and policy cases;
- sanitized production replay only after owner-approved data handling exists.

Required zero-tolerance results:

- zero wrong confirmed/sent monetary fact;
- zero unauthorized side effect or direct send;
- zero cross-customer disclosure;
- zero suppression miss;
- 100% high-risk handoff recall;
- 100% deterministic price/promotion/delivery/SLA policy cases;
- 100% P0 pass across model and fallback/degraded paths.

### 9.2 Trace contract

For each run record only necessary data: trace, inbox/conversation/contact/order/quote/approval IDs;
model and prompt version; tool schema/version; policy/config/snapshot hashes; latency/tokens/cost;
decision reason; human edits/approval; outbox logical key; and provider receipt. Do not store chain of
thought.

Every release produces a signed gate manifest conforming to
`specs/contracts/release-gate-manifest-v1.schema.json`. It names the exact model, prompt, tool,
policy, configuration, dataset, test/eval hashes, evidence window, approvers, canary percentage, expiry,
and rollback state.

### 9.3 Autonomy progression

- `MANUAL_TRUTH`: staff runs business manually; software records truth.
- `SHADOW`: AI drafts only; human approves all external communication and commercial commitments.
- `ASSISTED`: capability-specific, low-risk automation only after the exact cumulative gates in
  `delivery/GATE_REGISTRY.yaml`. `LIST_PRICE_INFO` is the first G1/G2 envelope; wider Assisted
  capabilities additionally require G3. Monetary and
  personalized transactional content stays human-approved as specified in the agent spec.
- `BOUNDED`: one narrow capability at a time after its own evidence, canary, signed manifest, rollback,
  and monitoring gates.

Never promote a whole agent because an FAQ capability performed well. Each capability has its own
eligible population, harm model, tests, owner, and launch evidence.

NIST's AI RMF frames this as continuous governance: Govern, Map, Measure, and Manage should occur
throughout the lifecycle. This specification operationalizes that by tying each capability to a risk
envelope, measurable evidence, accountable approval, monitoring, and rollback.
[NIST AI RMF core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/),
[NIST Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence).

## 10. Repository and delivery structure

Create the following as a dedicated repository; never initialize or publish a personal agent-runtime
workspace.

```text
apps/
  web/                 # React/Vite mobile-first staff PWA
  api/                 # FastAPI HTTP boundary / command handlers
  worker/              # inbox, outbox, jobs, reconciliation
  channel-adapters/    # official provider ingress/egress normalization; no AI authority
  public-agent-tools/  # separate facade deployment/module and auth audience
packages/
  domain/              # pure deterministic money, state, delivery, SLA rules
  contracts/           # generated/validated OpenAPI, JSON Schema, Zod types
  db/                  # migrations, repositories, transaction helpers
  policy/              # typed policy decision point and capability flags
  observability/       # structured logs, tracing, redaction
  evals/               # runners, graders, fixtures, reports
  test-fixtures/       # non-production deterministic test data
config/
  seeds/ public-knowledge/
docs/
  adr/ runbooks/
infra/
  compose/ caddy/ backup/
scripts/
  import/ verify/
```

Dependencies must be pinned through `uv.lock`. Upgrading runtime, model, prompt, framework/plugin,
SDK, provider adapter, or tool schema is a versioned change with regression, security, and rollback
review.

Planning uses stable work-item and phase IDs from `delivery/WORK_QUEUE.yaml` and
`delivery/PROGRAM_PLAN.yaml`; milestone numbers in older prose are descriptive only. Release gate IDs
and capability dependencies come from `delivery/GATE_REGISTRY.yaml` and must remain identical to the
release-gate JSON Schema.

## 11. Implementation sequence and acceptance criteria

### M0 — Engineering foundation

Deliver monorepo, strict TypeScript, package manager lockfile, lint/format/typecheck/test commands,
CI, `.gitignore`, local Compose, environment templates, ADR template, contribution guide, and a concise
repo `AGENTS.md`.

**Exit:** fresh clone can run documented checks; CI blocks lint/type/contract failures; secrets and
build artefacts cannot be committed; no production credentials are required locally.

### M1 — Canonical configuration and identity

Implement staff identity/RBAC, configuration publication, immutable versioning, canonical enum
validation, snapshot hashing, and migration pipeline.

**Exit:** unpublished config cannot influence a quote; historical snapshot remains stable after a new
publication; unauthorized user/object access is rejected and audited; contract parsers and
cross-references pass.

### M2 — Deterministic domain core

Implement quotes, order state machine, price/promotion/delivery/SLA engines, and property/golden tests.
Do not integrate an LLM yet.

**Exit:** all boundary vectors pass; money/policy outputs are exact; invalid inputs fail closed; edits,
expiry, and range-price rules require correct reapproval; no endpoint bypasses command handlers.

### M3 — Operations control plane

Implement staff PWA, unified internal inbox, approval revisions/content hashes, order/exception boards,
versioned metric projections with freshness, audit timeline, inbox/outbox, idempotency, manual-send
attestation, feature flags, channel-health views, and pilot instrumentation. AI may explain verified
dashboard facts but never calculate a metric or execute a dashboard action.

**Exit:** failure injection proves atomic mutation/event/audit/outbox behavior; duplicate/replayed input
produces one logical send; edited draft cannot send under an old approval; an operator can reconstruct
the full decision chain.

### M4 — Constrained Shadow concierge

Implement Agent Runner and Tool Facade, then invoke an isolated runtime through
`ConstrainedAgentRuntime`. Build the bounded custom Responses adapter with strict allowlisted function
tools, no provider built-in tools, no parallel public tool execution, and explicit non-storage; retain
OpenClaw only as an `EVAL_ONLY` parity/rollback candidate. Pin the runtime implementation, provider
route, and evaluated model release. Add prompt/model/public-cell config registries, context packet
schema/version/hash, cost/timeout ceilings, provider-storage verification, redaction, evaluation
runners, and human draft review.

**Exit:** the agent has only registered typed tools; it cannot choose cross-customer IDs, call provider
send, mutate configuration, browse/exec, or make unverified commercial claims; P0 integrated evals
pass for all enabled paths.

Execute M4 as three separately reviewable slices:

- **M4A — Responses runtime:** implement and locally verify the finite state machine behind the existing
  runtime protocol using fake/injectable transport; no provider credential or public route is required.
- **M4B — parity and selection:** compare the custom adapter and retained OpenClaw candidate with the
  same pinned model/prompt/context/tools/budgets/datasets; provider-backed and synthetic results remain
  visibly distinct.
- **M4C — OpenClaw retirement:** only after M4B, provider-data, security, rollback and signed release
  gates pass, remove OpenClaw from the public dependency/deployment path while preserving immutable
  evidence and the separately isolated Private Owner OpenClaw environment.

### M5 — Real-customer Shadow readiness

Complete owner MFA, staff RBAC/session revocation, privacy/retention baseline, incident ownership,
off-host PITR/restore drill, controlled sending, and real pilot instrumentation.

**Exit:** all G1 evidence in the approved specs is attached to a reviewable release manifest. This is
the first point where real customer interactions may be processed under human approval; it is still not
public autonomous operation.

### M6+ — Assisted/public capability gates

Do not schedule as automatic feature work. Each proposed capability needs an ADR, threat-model delta,
public policy/corpus review, official channel decision, separate public host, new/updated evals, owner
and security sign-off, canary plan, customer-correction workflow, and successful drill evidence.

## 12. Codex operating contract

Codex is a force multiplier, not an authority to invent product or policy. Maintain a short root
`AGENTS.md` that points to this file, the normative specs, contracts, commands, and local rules.

Every Codex implementation prompt must state:

```text
Goal: [one bounded deliverable]
Context: [relevant spec, contracts, package, existing code]
Constraints: [invariants, interfaces that cannot change, security limits]
Done when: [specific tests/checks and observable behavior]
```

For any task involving money, identity, consent, outbound messages, policy, data migration, public
input, or external integration, Codex must:

1. inspect the applicable contract/spec and existing tests before editing;
2. write or update deterministic tests before/with the implementation;
3. preserve backwards compatibility or create a migration/rollback plan;
4. run targeted tests plus lint/typecheck/contract validation;
5. review the diff for authorization, idempotency, logging/redaction, error handling, and policy drift;
6. report assumptions and unresolved policy as fail-closed behavior, never conceal them;
7. avoid production deployment, credential changes, destructive migrations, or public sends without an
   explicit authorized task and release gate.

Do not ask Codex to "build the agent" as one task. Give it vertical, reviewable slices such as
"implement published pricebook snapshots with golden tests". Use plan-first work for cross-package,
security-sensitive, or architectural tasks; use direct bounded implementation for a well-specified
slice.

Current Codex guidance recommends durable repo guidance in `AGENTS.md`, clear goal/context/
constraints/done-when prompts, and testing/review as part of the coding loop.
[Codex best practices](https://learn.chatgpt.com/guides/best-practices),
[AGENTS.md guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## 13. Definition of done for every production change

A change is not done because it compiles. The pull request/task handoff includes:

- linked requirement, ADR, and contract/eval case IDs;
- implementation and migration notes, including rollback/compatibility impact;
- unit, property, integration, contract, and UI/e2e tests proportional to risk;
- negative authorization and failure-path test for a sensitive change;
- test/lint/typecheck/format/eval results;
- structured observability and redaction review;
- feature flag/default-off behavior where rollout risk exists;
- updated runbook/dashboard/alert where operational behavior changes;
- review of the diff against the invariants in Section 4;
- no secrets, raw PII, chain-of-thought, unpinned dependency, or unapproved public corpus added.

## 14. Build start checklist

Start coding now only after the team records these kickoff choices in an ADR or environment template:

- Node LTS/package-manager version and naming conventions;
- local Postgres/Compose developer workflow;
- authentication provider/interface for internal staff;
- secret and backup provider for staging/production;
- CI provider and required checks;
- owner/reviewer for pricebook and policy publication;
- initial feature flags, all defaulting to disabled for external automation.

These are engineering selections, not permission to decide unresolved commercial policy. Any missing
commercial rule continues to fail closed.

## 15. References

- `specs/README.md` — specification index and normative hierarchy.
- `specs/ENGINEERING_SPEC_V1.md` — domain and platform requirements.
- `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` — agent topology, tool limits, evaluation, and gates.
- `specs/SECURITY_RELIABILITY_SPEC_V1.md` — threat model, hardening, recovery, and incidents.
- `specs/IMPLEMENTATION_ROADMAP_V1.md` — stack, milestones, and rollout sequence.
- `specs/TEAM_REVIEW_REPORT_V1.md` — approved implementation decision and P0 evidence requirements.
- `specs/contracts/agent-tools-v1.openapi.yaml` — only public-agent tool registry.
- `specs/evals/eval-manifest-v1.yaml` — launch/evaluation manifest.
