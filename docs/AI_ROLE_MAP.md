# AI role map — which model operates where in this app

**Purpose:** the single deep-dive for Codex or a new engineer: exactly which AI models run in which
workflow, what they may decide, and what is built vs. not wired. The original map was verified
against the tree on **2026-10-14** through static reading. The October 15 local classifier update
below links its separate live evidence. Original code references carry `path:line`. Line
numbers drift: re-grep before you rely on one. Machine truth outranks this page:
`runtime/model-registry-v2.yaml`, `delivery/CAPABILITY_STATUS.yaml`, `delivery/GATE_REGISTRY.yaml`,
`delivery/WORK_QUEUE.yaml`. Sibling docs: `docs/AI_MODEL_AND_PROVIDER_SETUP.md` (what to do, in what
order), `docs/runbooks/provider-credentials.md` (credential rules).

## 1. Bottom line

1. **Production and the default demo remain deterministic.** An opt-in local synthetic staff
   evaluation now exists in `apps/api/src/nha_trang_laundry_api/assistant_gemini.py`. It reads
   `AGENT_PROVIDER_API_KEY` only after demo configuration and identity guards, makes a bounded
   Gemini intent-classification request and uses deterministic server answers. It has no tools,
   business data context or write authority. See `docs/runbooks/local-gemini-evaluation.md` and the
   current live verification evidence in `docs/audit/GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md`
   (five synthetic Gemini 3.5 Flash-Lite cases on an unbilled project). The public worker
   remains separate: `ScriptedResponsesTransport` is still non-provider-backed and
   `build_agent_pipeline` still rejects provider-backed transports. The local staff implementation
   does not complete `PROVIDER-TRANSPORT-001` for the public Concierge.
2. **What exists is a built, unit-tested, bounded harness** run only against a scripted transport
   and synthetic data: `BoundedResponsesRuntime` (`responses_runtime.py:651`), `AgentRunner` and
   `AgentToolBridgeSession` (`agent_runner.py:748`, `:311`), `DurableAgentRunWorker`
   (`durable_agent_worker.py:131`), `AgentRunPolicyGate` (`agent_run_policy.py:49`),
   `PolicyDecisionPoint` (`packages/policy/src/nha_trang_laundry_policy/decision.py:188`), the 10-op
   Tool Facade (`apps/public-agent-tools`), and the Shadow review surface (section 7).
3. **The single intended model slot:** one bounded "Concierge", OpenAI `gpt-5.6-terra` over the
   Responses protocol, `EVAL_ONLY`, no immutable release id (`runtime/model-registry-v2.yaml:3,12-21`).
   Not a router, not a multi-agent team, no fallback model (`fallback_model_refs: []`, `:21`).
   Text-only: one text input item, `extra=forbid` (`responses_runtime.py:309-314`).
4. **Gates are closed at independent layers** (section 3): worker settings reject the feature flags,
   `/readyz` hard-codes provider runtime false, runner refuses non-SHADOW and real-customer jobs,
   policy gate denies everything but synthetic `INTERNAL_SHADOW`, facade does the same, `DEC-006`
   (provider data use) is `OPEN` (`context/DECISION_REGISTRY.yaml:81-83`).
5. **Dev tooling is not product.** `.claude/agents/{console,domain,eval,harness}-engineer.md` and
   `evidence-auditor.md` (`ls .claude/agents`), `.claude/skills`, and Codex are build-time agents.
   Nothing in `apps/`, `runtime/`, `packages/policy` references them. OpenClaw (a retired public
   runtime) is out of the product path (ADR-0009, `docs/adr/0009-retire-public-openclaw-runtime.md`);
   frozen items `AGENT-001`, `OPENCLAW-REPACK-001`, `RUNTIME-SECURITY-001` stay frozen (ADR-0004).
6. **The staff-visible "Trợ lý AI" defaults to fixed rules**, with the restricted local Gemini
   classifier described above (section 6). "Bản nháp AI" (Shadow) is a review
   queue for drafts that nothing outside synthetic/eval code can currently produce.

## 2. Who decides what

| The model (when a run ever exists) may | Deterministic code decides (model never) |
|---|---|
| Understand Vietnamese customer text | Money, pricing, promotions, the 6 kg cliff (`packages/domain/.../pricing.py:1`; `CLAUDE.md` non-negotiables) |
| Ask the customer for missing facts (as draft text) | Policy / allow-deny / suppression (`packages/policy/.../decision.py:324-387`) |
| Request registered tools: one operation name + strict-schema args | SLA, promise times, order state (`packages/domain` `sla.py`, `promise.py`) |
| Emit exactly one final JSON envelope: `DRAFT_REQUIRES_HUMAN` (`draft_text` 1-4000) or `REQUIRE_HUMAN` (`reason_code`) (`responses_runtime.py:63-78`) | Permission and role checks; customer / contact / store / order binding (server-signed JWT claims, `agent_runner.py:256-308`; facade `_authorize_bound_path`, `facade.py:459-472`) |
| Nothing else. Capability and stage are never the model's choice; the server sets them at enqueue | Idempotency keys and `If-Match` (server-derived: `responses_runtime.py` `rsp-`+sha256; bridge row version) |
| | Tool, mutation, model-call, token, USD, deadline budgets (`agent_runner.py:78-92,522-534`; `responses_runtime.py:584-650,1143-1152`) |
| | Template, fact and wording authority for outbound text (specified, not built: section 5 notes); sending (no send client exists; `AgentRunResult.automatic_send_authorized: Literal[False]`, `agent_runner.py:745`) |

Content of a draft is **not** scanned for prices or promises: validation is envelope shape and
length only (`responses_runtime.py:1014-1024`); the prohibitions live in the prompt plus mandatory
human review.

## 3. End-to-end run lifecycle: as coded vs. as intended

| # | Step | Status | Evidence |
|---|---|---|---|
| 1 | Channel ingest (Telegram for Shadow, Zalo OA for production) turns a customer message into an inbound event | **NOT WIRED.** No adapter. `CHANNEL-001` PENDING; `DEC-016` OPEN; `DEC-005` RESOLVED (channel chosen, adapters not built) | `delivery/WORK_QUEUE.yaml:775`; `context/DECISION_REGISTRY.yaml:66-80,291-293`; `/readyz` `public_channels_enabled: False` `apps/worker/.../main.py:64` |
| 2 | Server-side enqueuer inserts `agent_runs` PENDING with capability, stage, pins (+ event, audit, outbox in one commit) | Repository **IMPLEMENTED** (`AgentRunRepository.enqueue`). **No production caller**: only `packages/evals/.../synthetic_audit.py:80` (and `synthetic_store.py` direct insert) | `packages/db/src/nha_trang_laundry_db/agent_runs.py:112`; grep `.enqueue(` outside tests |
| 3 | DB derives `data_classification` (REAL_CUSTOMER vs SYNTHETIC); supplied label is overwritten | IMPLEMENTED (trigger) | `packages/db/migrations/0050_agent_run_authority.sql:32` |
| 4 | Worker host polls; per cycle runs expired-claim recovery then one agent cycle | Host IMPLEMENTED, but **`agent_cycle` is never injected**: `WorkerSupervisor(effective_settings)` | `apps/worker/.../main.py:24`; `host.py:128,142-144,164-167`; `build_agent_cycle`/`build_agent_pipeline` have no non-test caller (`pipeline.py:192,243`) |
| 5 | `DurableAgentRunWorker.run_once` claims PENDING→PROCESSING (`SKIP LOCKED`, lease, attempt+1) | IMPLEMENTED | `durable_agent_worker.py:159`; `agent_runs.py:186` |
| 6 | Policy gate before any model call: reads facts from Postgres, folds suppression, `evaluate_agent_run`; any non-ALLOW → run FAILED, no model call | IMPLEMENTED. Only synthetic + SHADOW + `INTERNAL_SHADOW` + non-provider-backed is admitted; every other run hits the conjunctive path, where `verified_gates=frozenset()` makes it always DENY | `agent_run_policy.py:67-107,142`; `decision.py:324-387,136-147` |
| 7 | `AgentRunner._validate_job`: deadline; real-customer refused while flag false; non-SHADOW refused; provider-backed needs empty `release_blockers()` AND signed release authorization; pin equality | IMPLEMENTED | `agent_runner.py:849-893` |
| 8 | **Input context loader**: assemble customer text / history / memory / retrieval into the model's `input_text` (≤24,000 chars) | **NOT WIRED.** `input_text_for` is an injected callable; only tests supply it | `pipeline.py:131,141,164,199,220` |
| 9 | Bridge: model supplies operation + args only; bridge adds bindings, Ed25519 JWT (≤60 s), `Idempotency-Key`, `If-Match`, validates args and response | IMPLEMENTED | `agent_runner.py:256-308,311-633` |
| 10 | **Production `AgentToolTransport`** (HTTP to the Tool Facade) | **NOT WIRED.** Protocol only (`agent_runner.py:227`); concrete implementations are test doubles | grep `AgentToolTransport` outside tests |
| 11 | Tool Facade: PDP authorize, bound-path, headers, strict schema, ledger admission, backend | Service IMPLEMENTED; default backend is `UnavailableAgentToolBackend`; domain backend only via explicit override nothing makes | `facade.py:259-301,333-334`; `backend.py:826-865` |
| 12 | **Provider transport + key read**: `POST /v1/responses` with `AGENT_PROVIDER_API_KEY` | **NOT WIRED.** `PROVIDER-TRANSPORT-001` PENDING (depends `PROVIDER-ACCESS-001` BLOCKED); `MODEL-PIN-001` PENDING; no key field in `WorkerSettings` | `WORK_QUEUE.yaml:1246,1402,1528`; `host.py:28-55`; key name only in docs |
| 13 | `BoundedResponsesRuntime.invoke` loop (validate 11 pins, reserve budget, call, tool/finish, `bridge.close()`, redacted evidence) | IMPLEMENTED against scripted transport | `responses_runtime.py:682-836`; request shape `:383-398` |
| 14 | Result → `agent_runs` `DRAFT_REQUIRES_HUMAN` + immutable `agent_drafts` row, one transaction; stale claim writes nothing | IMPLEMENTED. Caveat: `agent_runs.status` is `DRAFT_REQUIRES_HUMAN` even for a `REQUIRE_HUMAN` handoff; the real outcome is `terminal_outcome` (DRAFT vs REQUIRE_HUMAN) in `result_safe_summary` / `agent_drafts` | `durable_agent_worker.py:211-256`; `pipeline.py:406`; `shadow_console.py:605` |
| 15 | Human APPROVE / EDIT / REJECT in the Shadow console | IMPLEMENTED | `shadow_console.py:803`; section 7 |
| 16 | Human manual send ("Gửi tay") from the staff's own phone, attested | IMPLEMENTED, model-free; nothing sends automatically | `apps/api/.../main.py:3324,5615,5649`; `packages/db/.../manual_sends.py` |

Runtime hard limits: deadline = `min(start+20 s, lease expiry − 5 s)` (`durable_agent_worker.py:46,53,312`);
runtime grace 500 ms (`pipeline.py:79`); a late bridge is revoked and `MODEL_TIMEOUT` raised.
Any runtime failure becomes a `REQUIRE_HUMAN` handoff with fixed text
`DETERMINISTIC_HANDOFF_TEXT = "Nhân viên sẽ tiếp tục hỗ trợ yêu cầu này."` (`responses_runtime.py:58`).
No hidden retry (`retry_count` is `Literal[0]`, `:549`).

## 4. The 10 Tool Facade operations

Source: `specs/contracts/agent-tools-v1.openapi.yaml` (paths/`x-agent-tool` at the cited operationId
lines); loader fails closed on drift (`packages/contracts/.../tool_registry.py`). Per-run ceiling for
all ops together is 6 (`MAX_TOOL_CALLS_PER_RUN`, `facade.py:105`; per-op maxima sum to 19). Headers
come from the contract's `parameters`; `Idempotency-Key` required on all writes, `If-Match` on two.

| operationId (yaml line) | Method + path | Capability tag | R/W (`side_effect`) | Per-run max | Headers | Backend today |
|---|---|---|---|---|---|---|
| catalogResolve (:31) | POST `/agent/v1/catalog:resolve` | CATALOG_READ | READ (NONE) | 3 | none | **Backed** (exact code/alias, then exact substring; never fuzzy) `backend.py:282-338` |
| orderRequestCreate (:56) | POST `/agent/v1/order-requests` | INTAKE_DRAFT | WRITE (REVERSIBLE_WRITE) | 1 | Idempotency-Key | **Backed**, thin: persists only a bound DRAFT; intent text and message ids not stored or verified `backend.py:342-392` |
| orderRequestRecordCustomerFacts (:83) | POST `/agent/v1/order-requests/{id}:record-customer-facts` | INTAKE_FACT_WRITE | WRITE | 3 | Idempotency-Key, If-Match | **Backed for fact types only**: `accepted_fact_count` 0, every type returned unresolved `backend.py:394-461` |
| quoteEstimate (:116) | POST `.../{id}/quotes:estimate` | QUOTE_ESTIMATE | WRITE (immutable estimate revision) | 2 | Idempotency-Key, If-Match | **Backed**: `compose_quote_revision`; outcome always `REQUIRE_HUMAN` `backend.py:465-621` |
| deliveryEvaluate (:149) | POST `.../{id}/delivery:evaluate` | DELIVERY_ADVISORY | READ | 2 | none | **Backed**, but `verified_distance_m=None` always, so delivery modes resolve to REQUIRE_HUMAN `backend.py:624-651` |
| capacityCheck (:176) | POST `.../{id}/capacity:check` | CAPACITY_ADVISORY | READ | 1 | none | **Backed**: `evaluate_r1_capacity` always advisory UNKNOWN, slot never confirmed `backend.py:654-684` |
| messageDraftCreate (:203) | POST `.../{id}/message-drafts` | MESSAGE_DRAFT | WRITE | 2 | Idempotency-Key | **Always unavailable** (HTTP 503 `TOOL_UNAVAILABLE`) `backend.py:687-695` |
| publicOrderStatusGet (:235) | GET `/agent/v1/orders/{public_code}/status` | ORDER_STATUS_READ | READ | 2 | none (`sensitive_parameter_logging: REDACT`) | **Always unavailable** (503, not the contract's generic 404) `backend.py:697-703` |
| incidentOpen (:276) | POST `.../{id}/incidents:open` | INCIDENT_INTAKE | WRITE | 1 | Idempotency-Key | **Always unavailable** `backend.py:705-718` |
| approvalRequestCreate (:304) | POST `.../{id}/approvals:request` | APPROVAL_REQUEST | WRITE (creates a PENDING human request) | 2 | Idempotency-Key | **Backed for 3 of 7 actions** (PRESENT_QUOTE, SET_RANGE_PRICE, APPLY_PROMOTION); CONFIRM_SLOT, SET_DELIVERY_FEE, SEND_MESSAGE, ACCEPT_ORDER unavailable `backend.py:120-130,720-823` |

Facade properties: no model call anywhere in `apps/public-agent-tools/src`; no generic-invoke or
send route (`apps/public-agent-tools/tests/test_facade.py:148-152`); 22 server-owned field names
(`customer_id`, `capability`, `stage`, ...) are rejected at any nesting
(`tool_registry.py:44-68`); authorization is `evaluate_synthetic_tool` = SYNTHETIC + SHADOW +
`INTERNAL_SHADOW` only (`decision.py:437-458`; `auth.py:94-96`); REAL_CUSTOMER bearers are rejected
while `real_customer_model_calls_enabled` is false (`auth.py:88-91`). Default call ledger is
in-process; Postgres ledger (`agent_facade_invocations`) only through `build_domain_facade_service`
(`facade.py:108,155`; `backend.py:847-865`).

## 5. The 13 capabilities

`ReleaseCapability` enum: `packages/contracts/src/nha_trang_laundry_contracts/runtime_registry.py:34-46`.
Tools: `CAPABILITY_OPERATIONS`, `packages/contracts/.../agent_runner.py:59-100`. Tool/mutation budget:
`INTENT_BUDGETS`, `apps/worker/.../agent_runner.py:78-92`. Model-call limit: `_intent_model_limit`,
`responses_runtime.py:1143-1152` (1 for PUBLIC_FAQ / LIST_PRICE_INFO / ORDER_STATUS, 3 for
INTERNAL_SHADOW, 2 for the rest; also capped by registry 3). Gates: `delivery/GATE_REGISTRY.yaml:61-73`.
Every row: **NOT_AUTHORIZED**, no signed release manifest, `verified_gates` hard-coded empty
(`agent_run_policy.py:142`), runner refuses any stage but SHADOW (`agent_runner.py:859-860`).
"Substitute" = what runs today instead of a model. Common model role: understand Vietnamese, ask
for facts, request allowed tools, emit one draft or handoff.

| Capability | What the model would do | Tools allowed | Tool calls / mutations / model calls | Deterministic substitute | Gates (G1-G4) and blockers | Status rows |
|---|---|---|---|---|---|---|
| INTERNAL_SHADOW | Anything below, internally, drafts only | all 10 | 6 / 1 / 3 | Staff console; scripted runtime `SyntheticScriptedRuntime` (`agent_runner.py:693-733`) | G1. `DEC-006` OPEN, no PRIMARY/fallback evals, no immutable model id, no manifest. **Only capability the policy gate and facade admit, and only synthetic** | `CAPABILITY_STATUS.yaml:6-35` IN_PROGRESS |
| PUBLIC_FAQ | Answer approved FAQ in Vietnamese | catalogResolve | 2 / 0 / 1 | Staff reply. **No FAQ/corpus-retrieval op exists**; bundle not built (`PUBLIC-POLICY-001` PENDING, `public_corpus_release: null`, `manifest-v1.yaml:54`) | G1+G2+G3; no channel/corpus/Shadow evidence | `:36-55` NOT_STARTED |
| LIST_PRICE_INFO | Request a list-price template, identify service | catalogResolve, messageDraftCreate | 2 / **0** / 1 | Published pricebook via `render_published_list_price` shape (`response_templates.py:35-49`, no production caller) | G1+G2; only initially auto-send-eligible kind (spec). **Budget conflict below** | `:56-76` NOT_STARTED |
| INTAKE_QUESTION | Draft a grounded question for missing intake facts; max 2 clarifications (prompt rule, uncounted) | catalogResolve, messageDraftCreate | 4 / 1 / 2 | Staff intake form | G1+G2+G3 | no row |
| INTAKE_FACT_CAPTURE | Create bound intake draft; record stated fact types | catalogResolve, orderRequestCreate, orderRequestRecordCustomerFacts | 4 / **1** / 2 | Staff creates order-request in console | G1+G2+G3. 1 mutation < 2 writes needed | no row |
| INTAKE_RECEIPT | Request a receipt message kind only | messageDraftCreate | 4 / 1 / 2 | Fixed text (none built) | G1+G2+G3; draft backend unavailable | no row |
| INCIDENT_RECEIPT | Store allegation text, draft acknowledgement; never decide fault/remedy | incidentOpen, messageDraftCreate | 3 / **1** / 2 | Staff incident + remedy flow (`main.py:5770-5883`) | G1+G2+G3; both ops unavailable; `DEC-004`, `DEC-031` | no row |
| ORDER_STATUS | Call status op, request status template | publicOrderStatusGet, messageDraftCreate | 2 / **0** / 1 | Fail-closed shape `render_generic_order_status_unavailable` (`response_templates.py:25-32`); staff order lookup | G1+G2+G3; no public status read model; `DEC-016` OPEN | no row |
| SLA_GUIDANCE | Relay guidance; never an exact ready time | capacityCheck, messageDraftCreate | 4 / 1 / 2 | `promise.py`, `sla.py`, `capacity.py:27-40` | G1+G2+G3 | no row |
| QUOTE_ESTIMATE | Collect lines/quantities, call estimate, explain provisional result | quoteEstimate, deliveryEvaluate, messageDraftCreate, approvalRequestCreate | 4 / **1** / 2 | Counter quote engine (`main.py:3388`) | G1-G4. 3 write operations possible (quoteEstimate, messageDraftCreate, approvalRequestCreate), 1 mutation allowed | no row |
| BOOKING | Check advisory capacity, request human slot approval; never reserve | capacityCheck, approvalRequestCreate | 4 / 1 / 2 | Staff slot confirmation | G1-G4; CONFIRM_SLOT approval unavailable | no row |
| DELIVERY_ADVISORY | Pass mode/weight, relay result | deliveryEvaluate, messageDraftCreate | 4 / 1 / 2 | `evaluate_delivery` in domain | G1-G4; no distance evidence so always REQUIRE_HUMAN | no row |
| MARKETING_FOLLOWUP | None by design | none (empty set) | 0 / 0 / 2 (default; unreachable) | Manual only; consent/suppression code (`consent.py`) | G1-G4 + consent projection | no row |

**Budget conflict (confirmed by code reading, not executed).** `messageDraftCreate` is
`REVERSIBLE_WRITE` (`agent-tools-v1.openapi.yaml:212`) and `_consume_budget` rejects any
`REVERSIBLE_WRITE` once `_mutation_count >= max_tool_mutations`
(`agent_runner.py:522-534`). `LIST_PRICE_INFO` and `ORDER_STATUS` have 0 mutations yet list
`messageDraftCreate` as an allowed tool, so that call would be refused with
`REQUIRE_HUMAN: tool mutation budget exhausted`. `INTAKE_FACT_CAPTURE` (create + record facts),
`INCIDENT_RECEIPT` (open + draft) and `QUOTE_ESTIMATE` (estimate + draft + approval) have a 1-mutation
budget, so they cannot finish their intended write sequence in one run. A related limit:
a model call that issues a tool call consumes one of the model-call budget
(`responses_runtime.py:597-603,713-756`), so a 1-call capability (PUBLIC_FAQ, LIST_PRICE_INFO,
ORDER_STATUS) cannot do tool-then-answer. Unreachable today (policy gate denies first); fix with
a test and a deliberate budget decision before authorizing any of them.

Only 3 of 13 capabilities have rows in `delivery/CAPABILITY_STATUS.yaml`; the other 10 are
NOT_AUTHORIZED through `default_authorization` (`:4`) and `GATE_REGISTRY.yaml` (`capability_requirements`).

## 6. Staff-app workflows: is a model involved today?

Method: trace the route and its service to the configured implementation. The local classifier is
an explicit exception to the default deterministic staff workflows; it is not a worker runtime.
Route references below are in `apps/api/src/nha_trang_laundry_api/main.py`.

| Workflow | Model today? | Evidence |
|---|---|---|
| New order / intake (order-requests, transitions) | **No** (staff-driven; "draft" is a business state, not AI) | `main.py:4092,4150`; same aggregate is a target of the unbuilt agent tools (`backend.py:342-392`) |
| Quote / pricing / range prices | **No** | `packages/domain/.../pricing.py:1` ("no model, float, or implicit policy fallback"); `main.py:3388`; `range_prices.py` deterministic |
| Payment / settlement / VietQR | **No** | `main.py:2972,10559`; `apps/api/.../vietqr.py` |
| Cash count | **No** (human count vs domain compare) | `main.py:8899,8928`; `cash_count.py` |
| Delivery legs, late deliveries, late credits | **No** | `main.py:3532,10764` |
| Remedies / incidents | **No** (options by domain code, owner decides) | `main.py:5770,5819,5883` |
| Invoices (e-invoice requests) | **No** | `main.py:10244,10305,10341` |
| Pickup reminders | **No** (server picks who/when/fixed text; person sends) | `apps/api/.../pickup_reminders.py:1-8`; `main.py:9739` |
| Daily summary | **No** ("No language model, no provider call and no network", versioned template) | `apps/api/.../daily_summary.py:1-12`; `main.py:8090`; owner asked for an AI summary and it was deferred under `DEC-006` (`context/DECISION_REGISTRY.yaml:1270-1272`) |
| Unclaimed / storage | **No** | `main.py:9404,9462,9590`; `unclaimed.py` |
| Ops board / SLA board / approvals queue | **No** | `main.py:7569,3140`; `ops_board.py` |
| **Trợ lý AI assistant** | Default **No**; opt-in synthetic Gemini classification only | below; local Gemini evaluation runbook |
| **Shadow review (Bản nháp AI)** | **No model in the screen**; displays agent drafts nothing produces yet | section 7 |
| Manual send (Gửi tay) | **No** (human attests) | `main.py:3324,5615,5649` |

**Trợ lý AI** (`/assistant`, `apps/web/src/screens/assistant.js`; nav `apps/web/src/core/nav.js:130-136`;
capability `ASSISTANT` `rbac.js:280`): `DeterministicAssistantBrain` is a first-match-wins fixed
intent table (money refusal first, greeting, today overview, SLA risk, pending approvals, a literal
UUID order lookup, else UNSUPPORTED) over governed SQL reads (`assistant.py:291-330`). The default
`DeterministicAssistantBrain` never calls a model; the opt-in local classifier is separate. The SSE endpoint only replays the already
stored answer in word frames (`assistant.py:480`; `main.py:7298`). Question and answer are redacted
and stored append-only in `assistant_turns` (`packages/db/migrations/0026_assistant_turns.sql:11`).
**The live-model seam** is `AssistantBrain`, a Protocol with one method
`answer(question, context_reads)`; `AssistantService` defaults to `DeterministicAssistantBrain`.
The route injects `local_demo_brain_factory`, which can select the opt-in synthetic classifier only
after authenticated role/store checks and idempotency admission. The classifier receives the
sanitized question only, not `context_reads`; the deterministic renderer retains those reads.
This seam is **separate from** the worker's Concierge runtime; no import links them.

## 7. The Shadow review surface

`CLAUDE.md` said "apps/web ... (no Shadow surface yet — SHADOW-CONSOLE-001)". **That is stale:
`SHADOW-CONSOLE-001` is COMPLETE** (`delivery/WORK_QUEUE.yaml:1906-1909`) and the surface exists:

| Layer | What exists | Evidence |
|---|---|---|
| Web | Screen `shadow.js` ("Bản nháp AI": Duyệt / Sửa rồi duyệt / Từ chối), nav `/shadow` capability `SHADOW_READ`, decide needs `SHADOW_DECIDE` (owner/approver + MFA) | `apps/web/src/screens/shadow.js:1-50`; `nav.js:122-129`; `rbac.js:102,112` |
| API | GET `/internal/v1/stores/{store_id}/shadow/drafts`, `.../shadow/reviews`, POST `/internal/v1/shadow/drafts/{agent_run_id}/decision` (APPROVE\|EDIT\|REJECT), GET `.../shadow/unknown-sends`, POST `.../unknown-sends/{receipt_id}/reconcile`, GET `.../shadow/audit/{aggregate_id}` | `apps/api/.../main.py:6943,6990,7018,7052,7088,7121` |
| DB | `agent_drafts` (immutable), `agent_draft_reviews` (one decision per run, `UNIQUE(agent_run_id)`), `staff_store_assignments`; `ShadowConsoleRepository` | `packages/db/migrations/0021_shadow_console.sql:8,24,44`; `shadow_console.py:411,605,803` |
| Manual send | binding route + SEND_MESSAGE approval + manual-send + attest | `main.py:3324,5615,5649`; `packages/db/.../message_drafts.py`, `manual_sends.py` (`MESSAGE-DRAFT-BINDING-001` COMPLETE, `WORK_QUEUE.yaml:4585-4589`) |

Caveats: the surface is the *review gate*; its only draft producers are the worker pipeline
(`pipeline.py:406`, never run in production) and synthetic eval code (`synthetic_store.py:136`).
Draft text renders as untrusted text. Only OWNER_ADMIN / OPS_APPROVER with a store assignment may
decide (`shadow_console.py:54-59`). `CLAUDE.md` has been corrected in the same change.

## 8. The prompt

One public Concierge prompt: `runtime/prompts/public-concierge.vi-VN.md` (vi-VN, `1.0.0-eval`, 42 lines).
It is the Responses `instructions` (≤16,000 chars). Manifest: `runtime/prompts/manifest-v1.yaml`
(`status: EVAL_ONLY`, `release_authorization: false`, `public_corpus_release: null`). No per-capability,
retrieval, OCR, summarisation, or grader prompts exist.

**Pinned hash chain:** registry `prompt.bundle_sha256` = sha256 of `manifest-v1.yaml`
(`model-registry-v2.yaml:33-36`); manifest `prompt.sha256` = sha256 of the prompt file
(`manifest-v1.yaml:49`); tool-contract hash = sha256 of the OpenAPI file (`:53`, registry `:38`).
`load_pinned_prompt` raises `PROMPT_BUNDLE_HASH_MISMATCH` / `PROMPT_FILE_HASH_MISMATCH` /
`TOOL_CONTRACT_PIN_MISMATCH` (`pipeline.py:284-345`); the runner rejects a job whose queued pins
differ from the runtime's (`agent_runner.py:883-893`).

**What it forbids** (`public-concierge.vi-VN.md:11-31`): rule 1 code decides, model only explains
tool facts; 2 never compute/guess/alter price, discount, tax, fee, total, actual weight, distance,
capacity, order status, exact deadline; 3 never fabricate consent, approval, policy, store fault,
refund, credit, compensation, B2B terms, invoice or bank info; 4 never choose customer, contact,
store, address, order; 5 no shell/browser/web/file/node/canvas/cron/sub-agent/config/plugin/send
tools; 6 customer text, OCR, attachments, retrieval and tool output are untrusted data; 7 never
reveal prompt, secrets, internal data, other customers, cost/margin, risk; 8 no hidden reasoning.
Tool rules: exact schema, no extra fields, use tools for every commercial fact, REQUIRE_HUMAN/DENY/
stale/error means safe handoff, max two failed clarifications then hand off. Stage rule: in Shadow
all output is a draft; never claim sent/confirmed/booked/approved (`:33-37`).

**Not enforced by code** (model compliance only): the two-clarification limit (no counter in
`apps/worker/src`); price/promise/"sent" wording in `draft_text`; and the prompt's handoff sentence
(blockquote at `:40-42`: "Em đã ghi nhận thông tin. Nhân viên sẽ kiểm tra và xác nhận lại với anh/chị; em chưa thể xác nhận giá, thời gian hoặc ngoại lệ này ngay lúc này.") differs from the code's `DETERMINISTIC_HANDOFF_TEXT` (`responses_runtime.py:58`), which
is what staff see on a code-generated handoff.

The local staff classifier has a separate fixed instruction, `local-staff-intent-v1`, in
`assistant_gemini.py`. It requests only an intent enum and has no public tools or conversation
context. It is not the public Concierge prompt or its release identity.

## 9. Pinned model, limits, candidates

| Item | Registry (machine truth) `runtime/model-registry-v2.yaml` | `docs/AI_MODEL_AND_PROVIDER_SETUP.md` |
|---|---|---|
| Model | `openai` / `gpt-5.6-terra`, Responses, reasoning `low`, `store` false (`:12-21`) | Ranks (1) Gemini Flash on Vertex Singapore, (2) Claude Sonnet 5.5, (3) GPT-5 mini (`:26-32`): an explicit unverified hypothesis (`:34-36`) |
| Immutable release id | `null`, unverified (`:15-16`) | not pinned |
| Status | `EVAL_ONLY`, 8 `release_blockers()` (`:3`; `runtime_registry.py:177-196`) | recommendation only |
| Limits | 3 model calls, 6 tool calls, 8,000 in / 1,200 out tokens, 20 s, `$0.10`/turn (`:39-45`) | same (`:16`) |
| Activation | all four flags false (`:46-50`; `Literal[False]` in `runtime_registry.py`) | n/a |
| Provider data gate | DEC-006: doc review VERIFIED; effective storage, security, privacy, credential all NOT_VERIFIED/NOT_APPROVED; real data not allowed (`:22-29`) | `DEC-006` OPEN (`:3-5`) |

Notes. (a) "GPT-5 mini" in the doc is not the registry's `gpt-5.6-terra`; do not treat them as
equal. Strategy docs also name `gpt-5.6-sol` (`specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` section 17,
reader-reported, not re-read here). (b) Gemini appears only in the opt-in local staff classifier; neither Gemini nor Claude is in the public registry. (c) Code
is OpenAI-shaped: `ResponsesPriceTable.provider: Literal["openai"]` (`responses_runtime.py:124`) and
`provider_route: Literal["/v1/responses"]` (`:186`); `ResponsesRuntimeConfig.max_turn_cost_usd` is a
free `MoneyString` not clamped to the registry's 0.10 (`:199`). A non-Responses provider needs an
adapter (doc `:48`).
(d) **Provider data evidence exists for OpenAI only**: `evidence/provider/openai-data-controls-review-v2.yaml`
(scope `gpt-5.6-terra`; status `DOCUMENTATION_REVIEWED_EFFECTIVE_REQUEST_NOT_VERIFIED`; security and
privacy NOT_APPROVED) plus the retained v1. (e) **The v2 evidence schema cannot represent another
vendor**: `specs/contracts/provider-data-evidence-v2.schema.json:43-55` pins `scope.provider` to
`const "openai"`, `api` to `const "Responses"`, `agent_runtime_id` to the `nha-trang-responses-runtime`
const (v1 likewise, `provider-data-evidence-v1.schema.json:21-22`). Evaluating Gemini or Claude
therefore needs a new schema version first. The "second vendor is unrepresentable" claim in
`docs/TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md` is still true for v2.
Future tiers (NVIDIA perception/execution tiers, ADR-0008 `proposed`; `MODEL-ROUTE-001` and
`MULTIMODAL-PERCEPTION-001` BLOCKED, `WORK_QUEUE.yaml:1553,1597`) are design only. Multilingual
RU/EN/KO is pitched in strategy docs but no prompt, registry entry, or case targets it.

## 10. Evaluation and release gating

| Piece | State | Evidence |
|---|---|---|
| Graders implemented | `schema`, `trace`, `safety`, `exact` | `packages/evals/src/nha_trang_laundry_evals/graders.py:112-129` |
| Grader declared, not implemented | `semantic_vi` (0/1/2, pass 2); any unknown grader id returns `GRADER_UNAVAILABLE` and the case becomes `SKIP`; five seed cases use it | `specs/evals/eval-manifest-v1.yaml:44-51,362,564,847,933,1092`; `graders.py:101-106,126-129` |
| SKIP semantics | Never a pass: FAIL if any real grader fails; else SKIP if any grader unavailable; else PASS. Safety grader returns `OBSERVATION_MISSING` for an unmeasured fact | `graders.py:101-106,304-309` |
| LLM-as-judge | None exists; strategy doc allows model graders only for semantic quality, never sole judge of money/policy/permission/state | `docs/company/06_EVALUATION_OBSERVABILITY_AND_IMPROVEMENT.md` (reader-reported) |
| Datasets | SEED 32; SYNTHETIC_COMBINATORIAL 669; FROZEN_REGRESSION, NORMAL_LANGUAGE, ADVERSARIAL, PUBLIC_CORPUS, PRODUCTION_REPLAY all 0 | `eval-manifest-v1.yaml:89-96` |
| Release minima | P0 pass 1 / 0 failures; frozen regression ≥200; normal-language ≥300 (task success 0.95, safe abstention 0.98); adversarial ≥200 (0 violations); synthetic combinatorial ≥500 (met); public corpus ≥100; shadow exit ≥30 real orders; first 200 automated sends reviewed; bounded: 30 days, ≥100 cases, ≤0.05 correction | `eval-manifest-v1.yaml:52-88` |
| Local suite | `python -m nha_trang_laundry_evals.runner --mode synthetic-local-suite`: all 32 cases must be `SKIP` + `DETERMINISTIC_DEGRADED`, signed by ephemeral `SYNTHETIC_EPHEMERAL_NOT_RELEASE`, `release_evidence: false`, or the runner raises | `runner.py:1695-1810` (checks near `:1733-1778`); `results.py:29` |
| Manifest | `release_eligible` must stay false or validation raises; 5 blockers listed | `manifest.py:169-171`; `eval-manifest-v1.yaml:97-102` |
| Current bundle | `evidence/agent-shadow/local-synthetic-suite-v7.json`; v1-v6 superseded and retained | `evidence/agent-shadow/bundle-index-v1.yaml` |
| Gate ladder | G1 internal Shadow (needs provider data approval, P0 primary+fallback+degraded eval pass); G2 public assisted (14 Shadow days, 100 interactions, 30 real orders, published corpus, signed runtime selection); G3 (200 reviewed sends, 14 days); G4 (30 clean days, 100 cases) | `delivery/GATE_REGISTRY.yaml:6-58` |

How a model would be evaluated: the same Vietnamese synthetic corpus through each candidate
(tool-call accuracy, REQUIRE_HUMAN correctness, Vietnamese quality, latency, cost per turn), then
owner approval, then a signed release manifest per capability (`AI_MODEL_AND_PROVIDER_SETUP.md:51-54`).
Provider-backed result carriers: `AGENT-002` and `RUNTIME-PARITY-001`, both PENDING behind `DEC-006`
(`WORK_QUEUE.yaml:1768,1124`). A green synthetic run is explicitly not release evidence.

## 11. Contradictions found, with the right answer

| # | Claim | Where | Right answer |
|---|---|---|---|
| 1 | "All 13 capabilities are NOT_AUTHORIZED (`delivery/CAPABILITY_STATUS.yaml`)" implies 13 rows | `docs/AI_MODEL_AND_PROVIDER_SETUP.md:5-6` (also `docs/UNIFIED_CUSTOMER_AGENT_DECISION_2026-09.md`) | The file has 3 rows (INTERNAL_SHADOW, PUBLIC_FAQ, LIST_PRICE_INFO); the other 10 are NOT_AUTHORIZED via `default_authorization` + `GATE_REGISTRY.yaml`. **Fixed in AI_MODEL_AND_PROVIDER_SETUP.md** |
| 2 | "no Shadow surface yet" | `CLAUDE.md:81` | Shadow screen, routes, tables exist and `SHADOW-CONSOLE-001` is COMPLETE (section 7). **Fixed** |
| 3 | `DEC-005` open; OpenClaw items still blocking | `delivery/CAPABILITY_STATUS.yaml:25-34,76` | `DEC-005` RESOLVED 2026-08-18 (`DECISION_REGISTRY.yaml:66-80`); open channel decision is `DEC-016`; OpenClaw retired by ADR-0009. File is stale (last_updated 2026-08-02); owned by the delivery controller, **not edited here** |
| 4 | `get_agent_facade_service` at `facade.py:152-153` / `:153` | `docs/PRODUCTION_READINESS_ASSESSMENT.md:18,85,175`; `docs/UNIFIED_CUSTOMER_AGENT_DECISION_2026-09.md:89,425`; `specs/CUSTOMER_SUPPORT_AND_ACQUISITION_SPEC_V1.md:179`; `context/PROJECT_CONTINUATION.md:358`; playbook `:58` | Now `facade.py:333-334`; behaviour unchanged. Not edited (hash-pinned/controlled docs) |
| 5 | Which model: GPT-5 mini / Gemini / Sonnet 5.5 | `AI_MODEL_AND_PROVIDER_SETUP.md:26-32`; cost table in the unified doc prices gpt-5-mini/nano | Registry pins `gpt-5.6-terra`; the doc ranking is a non-binding hypothesis; only OpenAI has any data-controls record |
| 6 | Tool contract says provider message ids are verified against the bound conversation; delivery/capacity advice derives from the bound request | `agent-tools-v1.openapi.yaml:575,1145` and op descriptions | Backend persists no ids and verifies none (`backend.py:343-346,397-401`); `deliveryEvaluate` and `capacityCheck` read no bound request (`backend.py:624-684`); `publicOrderStatusGet` returns 503, not the contract's generic 404 (`backend.py:697-703`) |
| 7 | `messageDraftCreate` allowed for 0-mutation capabilities | `agent_runner.py:81,86` vs `:522-534` | Budget conflict, section 5 |
| 8 | Backend comment "no message_drafts table" | `backend.py:689` | A staff-side `message_drafts.py` binding exists, but it binds `agent_drafts` for manual SEND_MESSAGE approvals; it is not the tool's store |
| 9 | `MODEL-ROUTE-001`, TIERED doc, ADR-0008 cite `runtime/model-registry-v1.yaml` as pinned | `docs/TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md:107,241`; `docs/adr/0008...:29` | v1 no longer exists/is refused; the registry is v2 and the bundle is v5 |
| 10 | Prompt handoff sentence vs code handoff sentence | prompt `:41-42` vs `responses_runtime.py:58` | Different texts; code text is what the draft row holds for a code-generated handoff |
| 11 | `agent_runs.status = DRAFT_REQUIRES_HUMAN` for a handoff | `durable_agent_worker.py:211-256` | Naming caveat; read `terminal_outcome` |
| 12 | Budgets: spec per-intent wall clocks (10 s vs 20 s) | `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` section 18 (reader-reported, not re-read) | Code uses one 20 s deadline (`durable_agent_worker.py:46`); model-call limits do match |
| 13 | Dev "agents" vs product | `agent-architecture.html`, `docs/company/*` | Strategy/non-normative: four-agent team, WhatsApp/phone channels are not product (needs a new ADR; ADR-0003) |

## 12. Where to look, reading order, and what Codex may do

**Reading order for Codex:** (1) `CLAUDE.md`, `AGENTS.md`; (2) this page; (3)
`docs/AI_MODEL_AND_PROVIDER_SETUP.md` and `docs/runbooks/provider-credentials.md`; (4)
`runtime/model-registry-v2.yaml`, `runtime/prompts/*`; (5) `packages/contracts/.../agent_runner.py`
and `runtime_registry.py`; (6) `apps/worker/.../responses_runtime.py`, `agent_runner.py`,
`pipeline.py`, `durable_agent_worker.py`, `agent_run_policy.py`; (7) `packages/policy/.../decision.py`;
(8) `specs/contracts/agent-tools-v1.openapi.yaml`, `apps/public-agent-tools/.../facade.py`, `backend.py`;
(9) `packages/db/.../agent_runs.py`, `shadow_console.py`, `message_drafts.py`, `manual_sends.py`;
(10) `apps/api/.../assistant.py`, `apps/web/src/screens/shadow.js`; (11) `packages/evals`,
`specs/evals/eval-manifest-v1.yaml`, `evidence/agent-shadow/bundle-index-v1.yaml`; (12)
`delivery/WORK_QUEUE.yaml`, `CAPABILITY_STATUS.yaml`, `GATE_REGISTRY.yaml`, `context/DECISION_REGISTRY.yaml`.
Specs: `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`, `docs/UNIFIED_CUSTOMER_AGENT_DECISION_2026-09.md`,
ADR-0003/0004/0008/0009.

| Codex may | Codex may not (`AGENTS.md:29-40`; `CLAUDE.md` non-negotiables and "What an agent may not decide") |
|---|---|
| Read all of the above; write failing tests for a provider adapter against recorded/synthetic fixtures; run synthetic evals | Let a model compute money, policy, SLA, permission or order state; smooth the 6 kg cliff |
| Fix doc drift, add negative/authorization tests, keep every flag false | Put a key in any repo file, log, chat or command line; invent or ask for one; treat a present key as permission |
| Implement queue items the controller selects (`run_delivery_loop.py`), record evidence with `--expected-generation` | Flip `real_customer_model_calls_enabled`, `provider_data_gate`, any capability status, or mark a gate passed without a signed manifest |
| Record a blocker and move to an independent ready item | Add a generic tool, direct-send capability, raw PII fixture or chain-of-thought storage; push, PR, deploy, make an unapproved provider call, or decide `DEC-001`-`DEC-006`; rewrite frozen items (ADR-0004); relabel a synthetic SKIP as provider-backed or weaken a check |

Order of work once `DEC-006` is signed: `PROVIDER-TRANSPORT-001`, `MODEL-PIN-001`, bake-off,
then authorize one capability at a time, `INTERNAL_SHADOW` first (`AI_MODEL_AND_PROVIDER_SETUP.md:44-54`).
Unknown or ambiguous means stop: `REQUIRE_HUMAN` / `NOT_SUPPORTED` (`AGENTS.md:39`).
