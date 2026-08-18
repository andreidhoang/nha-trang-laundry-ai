# Agentic production harness — how to drive this repository to a system that serves a real customer

**Authored:** 2026-08-18
**Base commit:** `b9cac36` on `main`
**Method:** direct measurement of the harness (`.claude/`, `context/`, `delivery/`, `scripts/`) and of
the seams it drives, plus the operating practice of teams shipping agents into production
**Status of this document:** analysis and operating practice. It is **not normative**, resolves no
decision, authorizes no capability, and completes no delivery item. `delivery/` remains machine truth;
`context/DECISION_REGISTRY.yaml` remains the decision authority.

**Read first:** `AGENTS.md` (the durable contract), `docs/PRODUCTION_READINESS_ASSESSMENT.md` (measured
distance), `docs/STRATEGIC_DIRECTION_MEMO_2026-08.md` (why the shape is right commercially). This
document deliberately does not repeat any of them. It answers the one question they leave open:
**given all of that, what do you actually type, and what should the harness around you look like?**

---

## 0. The answer, before the argument

This repository does not have a velocity problem. It has 972 passing tests, 27 migrations, 37 routes,
a bounded FSM runtime, a typed 10-operation facade, a decision registry with 17 entries, and a
delivery controller with lease-and-generation compare-and-swap. Most production systems have less
governance than this at Series B.

It has an **evidence problem**, and the architecture is built to have exactly that problem on purpose.
Every gate in `delivery/GATE_REGISTRY.yaml` spends evidence. Every capability in
`delivery/CAPABILITY_STATUS.yaml` is `NOT_AUTHORIZED` until evidence is spent on it. So evidence
production is the only accelerator this architecture recognizes — and the evidence line currently
reads: **0 of 1,300 eval cases, 0 provider runs, 0 deployed agent processes, 0 customers.**

Which means the correct instruction to give an agent working here is almost never "build the next
feature." It is **"produce the next unit of evidence."** Everything below — the prompts, the subagents,
the hooks, the context doctrine, the frontend spec — is downstream of that single reframing.

---

## 1. The frame: the gates spend evidence, so evidence is the throttle

Four numbers set the entire schedule. All four are measured, none is a judgement call.

| The line | Now | What moves it | Who can move it |
|---|---:|---|---|
| Eval cases in the five gated suites | **0 / 1,300** | `EVAL-PUBLISH-001` publishes the 669 combinatorial cases that are already built, priced by the deterministic engine and content-hashed at `specs/evals/synthetic-combinatorial-v1.json` | **An agent, today**, once the owner enqueues it |
| Provider-backed model runs | **0** | `DEC-006` + a dedicated credential in the environment | **Owner only** — `docs/runbooks/provider-credentials.md` |
| Agent processes reachable in a running system | **0** | `apps/worker/.../main.py:19` constructs `WorkerSupervisor(effective_settings)` and passes no `agent_cycle=`; `host.py` has accepted one since before 08-14 | **An agent**, behind a closed flag |
| Customers the system can record | **0** | `CreateOrderCommand.bound_contact_id` (`packages/db/.../orders.py:43`) is required, and the only writer of `contact_channel_bindings` is keyed on a provider identity from a channel that is not connected | **Owner** signs `DEC-013` / `DEC-015`, then an agent builds |

Two of the four are agent-movable right now. Two are not, and no amount of prompting changes that —
which is itself the most useful thing to know before opening a session, because it tells you that a
session spent "making progress on the agent path" without touching evidence has, by this
architecture's own accounting, produced nothing.

### 1.1 The pattern the repo invented twice

Worth naming because it is the single most important structural fact for anyone driving this codebase.

```python
# apps/public-agent-tools/.../facade.py:152-153
def get_agent_facade_service() -> AgentFacadeService:
    return AgentFacadeService(UnavailableAgentToolBackend())
```

```python
# apps/api/.../assistant.py:376-382
def __init__(self, settings: AuthSettings, connection_factory=psycopg.connect,
             brain: AssistantBrain | None = None) -> None:
    ...
    self._brain: AssistantBrain = brain or DeterministicAssistantBrain()
```

Same shape, arrived at independently: **a typed seam, a safe default constructed at the production
factory, and a real implementation that exists but is not wired.** In both cases flipping the default
is one line of code and a capability authorization — and the authorization is the hard part by
design.

The operating consequence: **an agent working here must be able to tell the difference between "not
built" and "built and deliberately not wired," because the second one is never a bug to fix.** More
than one prompt below exists purely to make that distinction unmissable.

### 1.2 What this frame rules out

- Prompts that ask for throughput on the queue. Seven items completed in the last cycle moved
  `G1_INTERNAL_SHADOW_READY` from ~30% to ~35%, and every point of that came from two items.
- Prompts that ask to "finish the agent path." It cannot be finished; it can be *made reachable*
  behind a closed flag, which is a different and achievable thing.
- Any prompt that treats a green `uv run pytest` as progress. `AGENTS.md` says it plainly and the
  readiness assessment proves it: the suite has been green through every one of the four zeros above.

---

## 2. The harness as measured

What exists in `.claude/` today, and what is absent. Absence here is not criticism — most of it was
never needed until the work changed shape.

| Layer | Present | Gap |
|---|---|---|
| Always-loaded card | `CLAUDE.md`, 87 lines, explicitly "conventions and pointers only — never status" | None. This is correct and unusually disciplined; leave it alone. |
| Durable contract | `AGENTS.md`, 83 lines, including the continue-execution protocol | The protocol is prose that must be re-read and re-derived every session. It is procedural, repeated, and multi-step — the exact shape of a skill. |
| Subagents | `domain-engineer`, `harness-engineer`, `evidence-auditor` | **The two largest surfaces have no owner.** `apps/web` is 12,830 lines with no unit tests; `packages/evals` carries the binding constraint on G1. Neither has an agent whose system prompt encodes its rules. |
| Permissions | `.claude/settings.json` allow/deny, correctly denying `git push`, `gh pr create`, `.env` reads | Sound. Additive changes only. |
| Hooks | **none** | Several invariants are enforced only by prose and by a human remembering. `sw.js` regeneration after any `apps/web` byte change is the clearest: it is mandatory, mechanical, and currently depends on memory. |
| Context assembly | `scripts/assemble_context.py` renders a packet from `context/CONTEXT_MAP.yaml` | It emits a **bibliography, not a packet** — a list of 15+ paths. See §4.2. |
| Task packets | `context/tasks/TASK-*.md`, referenced by `task_packet:` on queue items | Working well. This is the best part of the existing context engineering and the model the rest should follow. |

One measured note on the permission list: it allows `Bash(uv run pytest:*)` without a prompt. That is
right for iteration speed, and it is also exactly why the "a green suite is not evidence" rule has to
be carried in the *prompt*, not in the permissions — the harness will never stop you from running the
thing that produces false confidence.

---

## 3. The prompts

These are meant to be pasted verbatim and then edited. They are written the way they are for reasons
that are stated after each one; the reasons matter more than the wording, because you will need to
write new ones.

Three properties every prompt here shares:

- **It names what would make the output a lie.** An agent that has been asked to state its own failure
  mode up front is measurably harder to talk into a false completion.
- **It routes unknowns to a named artifact**, never to a guess. In this repo the artifact is a decision
  request or a recorded blocker.
- **It ends with a verification step the agent did not choose.** Self-selected verification is how a
  weakened check gets past review.

### P0 — Opening a session

```text
Đọc trạng thái máy trước, kết luận sau.

Run these three, in order, and tell me nothing until all three have run:
  uv run python scripts/workspace_env.py --check
  uv run python scripts/check_context_drift.py
  uv run python scripts/run_delivery_loop.py --format controller-json

Then, in under 200 words:
1. Which item is IN_PROGRESS, or which one the controller selected.
2. For that item, which of its `blocked_by_decisions` are still OPEN — read
   context/DECISION_REGISTRY.yaml directly. Do NOT trust the queue's own field; it went
   stale on MULTIMODAL-PERCEPTION-001 and over-counted blockers by two.
3. What its task_packet declares as acceptance checks.
4. One sentence: what would make a completion claim on this item a lie?

Do not read PROJECT_CONTINUATION.md, STATUS.md or DELIVERY_BOARD.md to answer this. Those are
projections. If a projection disagrees with the queue, the queue wins and the projection is a
bug — tell me, don't reconcile it silently.
```

*Why:* the staleness in step 2 is a real defect found by measurement, not a hypothetical. Naming it
converts "read the queue" into "distrust the queue in one specific, verifiable way." Step 4 is the
lie-first framing. The last paragraph exists because prose status pages in this repo are written by
sessions like this one and are always at least slightly behind.

### P1 — Executing one queue item

```text
Take <ITEM-ID>. Before writing any code:

1. Read its task_packet in full. If it has none, stop and tell me — an item without a packet is
   not ready to be worked, and inventing the scope is how this repo gets a wrong feature built
   correctly.
2. Run: uv run python scripts/assemble_context.py --task-id <ITEM-ID> --domain <each context_domain>
   Read the CONTRACTS first. Read a spec only where a contract is ambiguous, and tell me which
   ambiguity sent you there.
3. State, in five bullets: the requirement, the seam you will touch, the invariant from
   context/INVARIANTS.md most at risk, what you will NOT touch, and the rollback.

Then implement ONE reviewable slice. Constraints that override anything the packet implies:

- Deterministic code decides money, policy, SLA, state, permission, capacity. Not a model. Not a
  default. Not a fallback.
- An unknown business fact is REQUIRE_HUMAN or NOT_SUPPORTED. Never a plausible value. If you find
  yourself picking a number the business has not confirmed, stop and draft a decision request.
- Every material mutation commits atomically with its domain event, audit row and required outbox
  row, through commit_material_change, or it does not commit.
- If the change touches apps/web, regenerate sw.js:
  uv run python scripts/generate_staff_console_manifest.py

Finally run every acceptance_check the item declares — all of them, unmodified — and paste the real
output. If one fails, do not adjust the check. Show me the failure.
```

*Why:* the "what you will NOT touch" bullet is the highest-value line in the prompt. Scope creep in
this repo is not a schedule risk, it is a correctness risk — the governance layer only holds if
changes stay inside the seam they declared. The final paragraph blocks the most common failure in
agentic coding, which is not writing bad code but *quietly relaxing the thing that would have caught
it*.

### P2 — Recording evidence

Never one prompt. Always two, and the second one is issued to a different agent.

```text
[to the working agent]
You believe <ITEM-ID> is complete. Do not record anything yet. Produce a claim:

- Which required_evidence entries do you assert are satisfied, and by which artifact — file path,
  test name, or command output. A sentence in a document is not an artifact.
- Which are NOT satisfied, and why.
- Which of your checks are synthetic. Name them. A SKIP is a SKIP; DETERMINISTIC_DEGRADED is not
  provider-backed.
```

```text
[to evidence-auditor, in a fresh context]
Here is a completion claim for <ITEM-ID>: <paste>.
Assume it is wrong and try to prove it. Open every artifact. Re-run every command yourself.
Report only what survives, and separately list what was asserted but not proven.
```

Only after the auditor's surviving list covers `required_evidence` do you record — and the generation
digest must be fetched fresh, because a stale one is how two sessions overwrite each other:

```bash
uv run python scripts/run_delivery_loop.py --format controller-json   # take the generation
uv run python scripts/record_delivery_evidence.py --expected-generation <SHA-256> ...
```

*Why:* separating the claim from the verification, across a context boundary, is the single highest-
leverage practice in this document. An agent that has spent forty tool calls building something is the
worst available judge of whether it works; the same model with none of that investment is a good one.
`evidence-auditor` already exists for exactly this and is, as far as the harness shows, underused.

### P3 — When you hit an unknown business fact

This is the prompt that keeps the model out of the business's decisions, and it is the one to reach for
most often.

```text
You have hit something the business has not decided. Do not choose it and do not code around it.

Draft docs/DECISION_REQUEST_<TOPIC>_<YYYY-MM>.md in the house style of the existing ones:

- What happens today, with file:line citations proving it, measured not remembered.
- What specifically stops — which motion, which screen, which order shape.
- What the owner is being asked, as 2-4 numbered questions a non-engineer can answer.
- A small options table with a recommendation and its cost.
- "Until it is signed" — the fail-closed behaviour that holds in the meantime.

Then append the entry to context/DECISION_REGISTRY.yaml with status OPEN and a
fail_closed_behavior field, and verify with:
  uv run python scripts/check_context_drift.py

Do not enqueue anything. Do not change any existing decision. Adding a row to
delivery/WORK_QUEUE.yaml is a scheduling act and it is the owner's.
```

*Why:* every one of `DEC-013` through `DEC-017` came out of exactly this move, and each converted a
place where an agent would otherwise have guessed into a question the owner can answer in a minute.
This is the mechanism by which an AI system stays inside its authority without stalling — it fails
closed *and hands back something actionable*, which is the whole difference between fail-closed and
stuck.

### P4 — Touching the staff console

```text
Work in apps/web only. This console has no framework, no build step and no npm tree by DEC-012.

Non-negotiable, all contract-tested in apps/api/tests/test_staff_console_contract.py:
- No raw-HTML sink. Every dynamic string is a text node through h()/render() in src/core/dom.js.
- No arithmetic on *_vnd. All money through format.money(). null money never renders as 0.
- Device persistence is the single localStorage key staff_store_id. Nothing else, ever.
- No auto-retry on writes. Idempotency-Key on every mutation; strong-quoted If-Match for CAS.
- A denied control renders disabled with its reason. It is never hidden.
- Truncation is disclosed. Never silently cut a list.
- Vietnamese first. Enum tokens render as Gloss (TOKEN) via enumLabel.

After ANY byte change under apps/web:
  uv run python scripts/generate_staff_console_manifest.py
  uv run python scripts/verify_console_interaction.py
  uv run pytest apps/api/tests/test_staff_console_contract.py

Read docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md §2 before you start. It lists ten invariants and
any slice that breaks one is rejected at review.
```

*Why:* 12,830 lines with no unit tests means the contract tests and the interaction verifier are the
entire safety net, and both are easy to forget because neither runs under a bare `uv run pytest`
mental model. This prompt exists mostly to make forgetting them impossible. The subagent added in §7
carries the same rules so the prompt can be shortened to one line.

### P5 — The two evidence-line moves, spelled out

These are the specific prompts for the two agent-movable rows in §1. Both assume the owner has
enqueued the item; neither should be run against an unenqueued proposal.

```text
[EVAL-PUBLISH-001 — 0/1,300 becomes 669/1,300 in one change]
The 669 synthetic combinatorial cases exist, are priced by the deterministic engine, and are
content-hashed at specs/evals/synthetic-combinatorial-v1.json. The manifest inventory reads 0
because publishing the count was reverted during the hash-pin freeze.

Establish, with file:line evidence, before changing anything:
1. Where the manifest inventory number is read from, and why it reads 0.
2. Whether publishing the count changes any hash that verify_contracts.py pins.
3. Whether 669 satisfies the synthetic_combinatorial_suite minimum of 500 in
   specs/evals/eval-manifest-v1.yaml release_policy — and whether satisfying one of five minima
   changes any gate's computed state.

Report those three before you write. If (2) is yes, stop — re-pinning a frozen hash is a separate
decision and not yours.
```

```text
[the worker wiring — the agent pipeline becomes reachable]
apps/worker/.../main.py:19 constructs WorkerSupervisor(effective_settings) with no agent_cycle.
host.py has accepted the parameter since before 2026-08-14. build_agent_cycle has no caller
outside tests, so the deployed process cannot run the pipeline.

Wire it, behind a flag that ships CLOSED. Requirements:
- With the flag off, behaviour is byte-identical to today. Prove it with a test.
- With the flag on and no provider credential present, the path returns a named terminal code.
  It must not attempt a model call, and holding a credential must not by itself enable anything —
  docs/runbooks/provider-credentials.md.
- Deploying this code enables no capability. delivery/CAPABILITY_STATUS.yaml is untouched.
- The rollback is turning the flag off, and you state that in the handoff.

Do not add a fallback that makes the path "work" when a dependency is missing. Fail closed with a
named code.
```

*Why:* both are written to establish facts before changing anything, because both sit next to
something frozen — a pinned hash in the first case, a capability boundary in the second. The last line
of the second prompt is the one that matters: "add a fallback so it doesn't fail" is the single most
dangerous instruction anyone can give in this repository, and an agent will invent it on its own if
the prompt does not forbid it.

### P6 — The anti-prompts

Things that read as normal engineering instructions and are actively unsafe here. Each is paired with
what to say instead.

| Do not say | Because | Say instead |
|---|---|---|
| "Make the tests pass" | The fastest way to make a test pass is to weaken it, and this repo's checks *are* the product | "Show me the failure output and diagnose it. Do not modify the check." |
| "Finish `<ITEM>`" | Invites marking complete over producing evidence | "Produce the completion claim for `<ITEM>`. I will have it audited before anything is recorded." |
| "Just wire up the facade / the assistant brain" | Both defaults are capability authorizations wearing the costume of a one-line change (§1.1) | "Tell me what authorization flipping that default would require, and who can grant it." |
| "Add a fallback so it doesn't fail" | Converts fail-closed into fail-open, silently, in the one architecture built to never do that | "Fail closed with a named terminal code, and tell me which code." |
| "Assume the price is roughly X" | `BUSINESS_TRUTH_INTAKE.md` marks unmeasured facts `CẦN ĐO`; a plausible number is worse than none because it is invisible | "That's unmeasured. Draft the decision request." |
| "Deploy it" / "Push this" | Outside agent authority per `CLAUDE.md`, and denied in `.claude/settings.json` | "Prepare it and tell me the exact command I should run myself." |
| "Clean up the queue" | `delivery/WORK_QUEUE.yaml` is machine truth with generation CAS; editing it by hand corrupts the controller's compare-and-swap | "Report the queue inconsistencies you found. I'll decide which to fix and how." |

---

## 4. Context engineering: progressive disclosure, and one proposed fix

### 4.1 The doctrine this repo already half-implements

`CLAUDE.md` opens by saying every token in it is paid on every turn of every session, and then
disciplines itself to 87 lines of conventions with no status. That is the correct instinct and it
generalises into four tiers:

| Tier | What | Cost | Rule |
|---|---|---|---|
| 1 — Card | `CLAUDE.md` | Every turn, every session | Conventions and pointers. **Never status.** If it changes weekly it does not belong here. |
| 2 — Procedure | Skills (§7) | Loaded when the task matches | Multi-step workflows currently living as `AGENTS.md` prose. Loaded on demand, so they can be long. |
| 3 — Packet | `context/tasks/TASK-*.md` | One item | The scope, the seam, the negative space, the reason it exists. Already the best-executed layer here. |
| 4 — Machine truth | `delivery/*.yaml`, `context/DECISION_REGISTRY.yaml` | Read by tool, never summarized | Read with a script, quoted with a citation. The moment machine truth is paraphrased into prose it starts rotting — §3's P0 exists because it already did. |

The failure mode this ordering prevents is the one every long-running agent project hits: status
migrates into the always-loaded layer, the card grows to 400 lines, every session pays for it, and it
is wrong within a week. This repo has explicitly refused that. Keep refusing it.

### 4.2 `assemble_context.py` emits a bibliography, not a packet

Measured: for a typical two-domain item the script emits ~15 paths and three fixed constraint lines.
An agent that dutifully reads all of them consumes a large fraction of its window before writing a
line, and the paths it most needs — the *seam*, the *decisions in effect* — are not distinguished from
the ones it needs least.

A proposed v2, offered as analysis and **not implemented here** because changing the context harness is
itself queue work:

```text
# Context packet: FULFILMENT-001
Domains: delivery_logistics, orders_audit

## Decisions in effect            <- resolved from the registry, not a link to it
- DEC-003  RESOLVED   staff-negotiated >6km is ratified policy
- DEC-015  OPEN       fail closed: no customer record is created

## The seam you are touching      <- file:line, the authority boundary itself
- packages/domain/.../delivery.py — decides cost; the model never does
- packages/db/.../orders.py:43 — bound_contact_id is required and has no producer

## Negative space                 <- what a correct change does not touch
- Do not modify delivery/WORK_QUEUE.yaml
- Do not add a provider credential path
- AGENT-001, OPENCLAW-REPACK-001, RUNTIME-SECURITY-001 are frozen (ADR-0004)

## Read in this order (budget)
1. specs/contracts/agent-tools-v1.openapi.yaml   (normative, 400 lines)
2. context/tasks/TASK-fulfilment-001.md          (the scope)
3. specs/DOMAIN_DATA_API_SPEC_V1.md §4.11        (only if the contract is ambiguous)
```

Three changes, all mechanical: **resolve** decisions instead of linking them, **name the seam** as
file:line instead of naming the file, and **order the reading with a budget** instead of listing paths
alphabetically. The prohibitions block already works and is unchanged.

---

## 5. The agentic frontend

The ask was a zero-friction agentic UI. The honest starting point is that this console already
contains the correct agentic primitive and calls it something else.

### 5.1 The primitive is already built, on one screen

`SHADOW-CONSOLE-001` persists a model proposal in `agent_drafts`, renders it for review, and takes
approve / edit / reject with attribution and a single-shot unique constraint. Approval binds an exact
rendered-content hash and revision, and editing invalidates the approval — that is invariant 8, and
`packages/db/.../approvals.py:44-45` carries `snapshot_hash` and `rendered_hash` to enforce it.

**That pattern is the product. It is currently one screen.** The design move is to generalize it into
the grammar of the whole console rather than to add a second chat surface.

### 5.2 The Proposal object

Every AI-originated thing in this system becomes one type, and the operator learns one interaction
once:

```text
Proposal
  kind          DRAFT_REPLY | INTAKE_FIELD | PRICE_EXPLANATION | RISK_FLAG | ROUTE_SUGGESTION
  target        the exact thing it would change (order id, thread id, field)
  rendered_hash approval binds this; editing invalidates it        (invariant 8)
  provenance    DETERMINISTIC | MODEL_DRAFTED | HUMAN_APPROVED
  basis         the governed reads it was derived from             (invariant 9)
  reason_codes  why it is proposing this, and why it might be wrong
  expires_at    a stale proposal is refused, not silently accepted
```

Three verbs, everywhere, forever: **Duyệt · Sửa · Bỏ**. `Sửa` produces a new proposal with a new hash,
which is invariant 8 rendered as a UI rule rather than written as a comment.

### 5.3 Seven rules for the surface

1. **Ambient, not destination.** A draft reply belongs on the order it answers; a risk flag belongs on
   Hôm nay. An operator who must *navigate somewhere* to use the AI will not use the AI. `#/assistant`
   stays for open questions — but if it is where the value lives, the design has failed.

2. **Deterministic first paint; the model may only enrich.** Never block a render on a model. The
   server already knows today's counts — paint them. A model contribution arrives as a *second* paint
   with a visible provenance chip. This is invariant 18 ("AI may explain a number, never originate or
   mutate it") expressed as a rendering rule, which is where it will actually be obeyed.

3. **One tap to accept, one tap to undo.** Reversibility is what makes acceptance rational. A bounded,
   server-enforced, audited undo window turns "should I approve this?" from a decision into a glance.
   Without it every approval is a small act of courage, and throughput dies at exactly the moment the
   system starts being useful.

4. **A refusal is a screen state, not an error.** `REQUIRE_HUMAN` and `NOT_SUPPORTED` render as named,
   explained, actionable states in Vietnamese with the next human step. The console already does this
   well at `#/gaps`; the agentic surface must inherit it. An agent that says *tôi không biết* clearly
   is more usable than one that guesses, and this is the rare product where that is also the law.

5. **Provenance on every rendered fact — three chips, no fourth.** *Máy tính* (deterministic) ·
   *AI soạn — chờ duyệt* (model-drafted) · *Người duyệt* (human-approved). If an operator cannot tell
   in one glance who is responsible for a number, the number should not be on screen.

6. **The composer is never the primary input.** Typing Vietnamese on a phone with wet hands between
   loads is the highest-friction input in the building. Priority order: tap a suggested action → tap a
   value from a governed list → photograph → speak → type. `assistant.js` already ships four suggestion
   chips; they are currently the empty state, and they should be the default interaction.

7. **Publish the latency budget.** Deterministic paint ≤ 200 ms. Model enrichment ≤ 3 s or it does not
   arrive and the screen says so. Never a spinner over a number the server already has.

### 5.4 The real zero-friction blocker is not the AI

`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md` §3.6 measures it: **UUID-paste workflows everywhere, no
pickers.** `orderRequests.js` and `staff.js` both carry comments describing screens that exist
specifically because an earlier screen demanded a pasted UUID. No amount of agentic polish survives an
operator being asked to paste `9f3a1c2e-...` on a phone. It is upstream-limited — it needs API work,
not console work — and it should be sequenced ahead of any new AI surface, because an AI proposal
attached to an object the operator cannot select is a proposal they cannot act on.

### 5.5 One finding, and it is a live one

`apps/web/src/screens/assistant.js:487` renders this to operators, in Vietnamese:

> "chữ chạy là nhịp hiển thị của câu trả lời đã lưu, **không phải mô hình đang sinh từ**"
> *(the running text is display pacing of an already-saved answer, not a model generating words)*

Today that is exactly true and admirably honest — `answer_sse_frames` replays a persisted string.

**It becomes false the moment a provider-backed brain is passed to `AssistantService(brain=...)`, and
nothing in the repository will catch it.** The string appears in no test: not
`test_staff_console_contract.py`, not `verify_console_interaction.py`. It is a factual claim about
system behaviour, rendered in the UI, with zero coverage — the same class of defect the whole
governance layer exists to prevent, sitting in the one layer the governance layer does not reach.

Two things follow, and both are cheap. **Contract-test the disclosure copy against the brain
implementation**, so that swapping the brain fails a test until the sentence changes. And more
generally: **UI disclosure strings are compliance surface in this system, and should be governed like
contracts, not like copy.** There are others — the assistant screen alone renders four factual claims
about what the system does not do.

---

## 6. Backend connection: the seams are right, the contracts have a gap

The frontend/backend boundary is in better shape than the frontend, because the seams were designed
before they were needed. `AssistantBrain` is a one-method protocol. `ConstrainedAgentRuntime` is a
replaceable implementation behind a bounded FSM. The Tool Facade is ten typed operations with a real
backend and a safe default. All three are the shape you want.

The measured gap is contract coverage, and it is small enough to fix in one item:

**Two routes the console calls in production appear nowhere under `specs/`.**

| Route | Defined | Called | In `specs/` |
|---|---|---|---|
| `GET /internal/v1/stores/{id}/settlements/today` | `apps/api/.../main.py:1074` | `today.js:234` | **no** |
| `GET /internal/v1/pricebook/services` | `apps/api/.../main.py:1118` | `quotes.js:535` | **no** |

This matters more here than in a normal codebase, because `AGENTS.md` makes the machine-readable
contracts normative and `CLAUDE.md` says contracts win over prose. A route with no contract is a route
with no authority — nothing can validate it, no eval case can be generated against it, and the console
depends on it. It is also the cleanest available example of the discipline: **when the contract is the
authority, an undocumented route is not a documentation debt, it is an ungoverned surface.**

The general rule for anything new on this boundary: the contract lands first, the route implements the
contract, `verify_contracts.py` proves the pair, and only then does a screen call it.

---

## 7. The harness additions — landed 2026-08-18

Everything in this section **exists in the tree** as of this document. All of it is inside `.claude/`.
None of it touches machine truth, none authorizes anything, and every piece is deletable with no
residue — `rm` the file and the harness is exactly what it was.

```text
.claude/agents/console-engineer.md      new
.claude/agents/eval-engineer.md         new
.claude/skills/delivery-item/SKILL.md   new
.claude/skills/decision-request/SKILL.md new
.claude/skills/record-evidence/SKILL.md new
.claude/settings.json                   modified — hooks added, permissions byte-identical
```

Gates re-run after the change: `check_context_drift.py` passed at 119 source references / 17
decisions / 77 work items; `verify_contracts.py` validated 19 JSON + 2 YAML contracts and 669
synthetic combinatorial cases; `ruff check .` clean; `test_staff_console_contract.py` 11 passed. The
full PostgreSQL-backed suite was **not** re-run and did not need to be — no Python, SQL, or `apps/web`
byte changed; the diff is five new Markdown files and one JSON key.

### 7.1 Two subagents for the two unowned surfaces

- **`console-engineer`** — owns `apps/web`. Carries the ten invariants from the UX spec, the
  `h()`/`render()` idiom, the no-raw-HTML rule, Vietnamese-first copy, the mandatory `sw.js`
  regeneration, and the two verification scripts. 12,830 lines with no unit tests is the largest
  unowned surface in the repository.
- **`eval-engineer`** — owns `packages/evals` and `specs/evals`. Carries the 1,300-case release policy,
  the five suite minima, the rule that a `SKIP` is never relabelled, and the hash-pin freeze. This is
  the binding constraint on `G1_INTERNAL_SHADOW_READY` and it currently has no agent.

### 7.2 Skills for the workflows that are currently prose

`AGENTS.md`'s continue-execution protocol, the evidence-recording sequence, the decision-request
format, and the console-change checklist are all procedural, repeated, and re-derived every session.
That is the definition of a skill: loaded on demand, so it can be as long as it needs to be, and paid
for only when it applies.

They **wrap** `scripts/run_delivery_loop.py` and `scripts/record_delivery_evidence.py`. They never
replace them. `CLAUDE.md` is explicit — one authoritative queue, one controller, no second
orchestration layer — and a skill that reimplemented the controller's lease-and-CAS logic would be
exactly that. Any scheduled or recurring run defers to `context/AUTOMATION_PROTOCOL.md`, and
**autonomous recurring execution remains not authorized** until an isolated runtime test proves
deterministic child lookup, reattachment, and no duplicate child across interruption.

### 7.3 One hook, and only one

A `PostToolUse` hook on `Edit|Write|MultiEdit|NotebookEdit`. It reads the tool payload, and **only**
when the edited path contains `apps/web/` does it run
`scripts/generate_staff_console_manifest.py`.

`sw.js` is generated. Regenerating it after any asset add, remove or rename is mandatory — UX spec
invariant 7 — and until now it depended entirely on someone remembering, with nothing in a bare
`uv run pytest` to remind them. This is the ideal hook: mechanical, idempotent, requires no
judgement, and converts a class of error from "caught at review if we are lucky" to "cannot happen."

Four safety properties, each exercised by running the hook command against synthetic tool payloads
rather than reasoned about:

- **It never blocks.** `exit 0` unconditionally; a failure of the generator cannot fail your edit.
- **It is a no-op off-path.** A payload naming `packages/domain/…` runs nothing.
- **It is idempotent.** Firing it with no real asset change leaves `sw.js` byte-identical
  (`sha256 aadb5e1e…` before and after).
- **It degrades safely without `jq`.** Path extraction falls back to matching the raw payload. Tested
  against a genuinely `jq`-free `PATH`: an `apps/web` payload still matches, a `packages/domain`
  payload still no-ops, and a payload whose *content* mentions `apps/web/` over-triggers — costing
  exactly one idempotent regeneration.

A note on how that last one was established, because it is the kind of mistake this document exists to
prevent. The first attempt tested it with `env PATH=/usr/bin:/bin`, which proved nothing: **macOS
ships `/usr/bin/jq`**, so `jq` was still resolvable and the fallback branch never ran. The property was
only genuinely verified after building a `PATH` containing nothing but `bash`, `cat` and `printf`. An
"I tested it" that did not exercise the branch is indistinguishable from an untested claim, and the
only thing that caught it here was checking what the test actually executed.

Resist adding more. A hook that *blocks* will eventually block something legitimate at the worst
possible moment; the entire value of this one is that it only ever regenerates a file that was
already supposed to be regenerated.

---

## 8. Sequencing, with an actor on every row

Nothing below is enqueued. Adding a row to `delivery/WORK_QUEUE.yaml` is a scheduling act and it is
the owner's. The `Actor` column is the point of the table.

| # | Move | Actor | What it changes | Blocked by |
|---:|---|---|---|---|
| 1 | Enqueue `EVAL-PUBLISH-001`, then publish the 669 cases | **Owner enqueues → agent builds** | 0/1,300 → 669/1,300; first of five suite minima satisfied | nothing |
| 2 | Contract the two undocumented routes (§6) | **Owner enqueues → agent builds** | Closes the ungoverned surface the console depends on | nothing |
| 3 | Wire `build_agent_cycle` into the worker behind a closed flag | **Owner enqueues → agent builds** | The pipeline becomes reachable in a running process for the first time | nothing |
| 4 | Contract-test the assistant's disclosure copy (§5.5) | **Owner enqueues → agent builds** | Makes a live compliance claim un-breakable in silence | nothing |
| 5 | Sign `DEC-013` (walk-in identity) | **Owner only** | The system can record its first customer | owner's answer |
| 6 | Sign `DEC-015` (what a customer record is) | **Owner only** | Sets the boundary the acquisition work runs into on success | owner's answer |
| 7 | Supply a Telegram bot token | **Owner only** | Proves inbound → envelope → runtime → draft → approval → outbound → receipt against a real provider | owner's account |
| 8 | Resolve `DEC-006` + provision a dedicated credential | **Owner only** | The first provider-backed model run; evidence base leaves zero | legal check + provider account |
| 9 | Start `SHOP-INSTRUMENT-001` measurement | **Owner only, 4–6 weeks** | Starts the calendar clock that G1 cannot compress | physical measurement |
| 10 | Build `PARTY-001` / `FULFILMENT-001` / `CATALOG-PRICEBOOK-001` | **Owner enqueues → agent builds** | First movement in the 14-of-63 aggregate count since measurement began | 5, 6 for the first |

Rows 1–4 are available today and none of them needs a decision, a credential, or a deployment. Rows
5–9 cannot be moved by any agent, by any prompt, in any amount of time. That division is the schedule.

---

## 9. What this document is not

It does not enqueue anything, resolve any decision, authorize any capability, or claim any gate. It
does not supersede `AGENTS.md`, `CLAUDE.md`, or `context/AUTOMATION_PROTOCOL.md`; where it appears to
conflict with any of them, they win and this is the bug.

It does not restate the commercial argument — `docs/STRATEGIC_DIRECTION_MEMO_2026-08.md` makes it, and
makes it better. It does not restate measured distance to production —
`docs/PRODUCTION_READINESS_ASSESSMENT.md` measures it, and this document's §1 is drawn from it rather
than re-deriving it.

The `.claude/` additions in §7 are the only part of this work that changed anything, and they are
reversible by deletion.
