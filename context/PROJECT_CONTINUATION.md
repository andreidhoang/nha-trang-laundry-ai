# Production continuation brief

**Last reconciled:** 2026-07-28 (Asia/Ho_Chi_Minh)  
**Active work item:** `AGENT-001`  
**Code stage:** `AGENT_SHADOW` / `IN_PROGRESS`  
**Production authorization:** `NOT_AUTHORIZED` for every capability

This brief is the human-readable entry point for an engineer or coding agent resuming implementation.
It is navigation only; machine-readable contracts, the work queue, capability status, and immutable
PostgreSQL state remain authoritative in the order defined by `context/CONTINUATION_PROTOCOL.md`.

## Resume safely

```text
1. Read AGENTS.md and BUILD_ENGINEERING_SPEC.md.
2. Read delivery/LOOP_STATE.yaml, delivery/WORK_QUEUE.yaml, delivery/CAPABILITY_STATUS.yaml.
3. Assemble the active packet:
   uv run python scripts/assemble_context.py --task-id AGENT-001 \
     --domain runtime_architecture --domain agent_tools \
     --domain evaluation_release --domain privacy_consent
4. Read context/tasks/TASK-agent-001.md and this brief.
5. Implement one bounded slice, then run its targeted tests and the full verification suite.
```

`continue execute` authorizes only safe repository engineering. It does not authorize a provider call,
real-customer data, public ingress, public automation, a credential, a deployment, or a release gate.

## Architecture that must remain true

```text
untrusted language
      |
      v
Public OpenClaw (reasoning/draft only; EVAL_ONLY)
      |
      v
typed Tool Facade -> deterministic domain + policy -> approval
      |                                            |
      +---------------- no direct send ------------+
                                                   v
                                   transactional outbox / controlled manual attestation
                                                   |
                                                   v
                                      sole sender worker (not yet provider-integrated)
```

- PostgreSQL is the source of truth; every material mutation requires atomic mutation + domain event +
  audit + outbox semantics.
- The model never calculates money, decides policy/SLA/state/permission, selects a customer/contact,
  or sends a message.
- No generic tool, raw database route, shell, browser, web fetch, channel credential, or direct-send
  capability may be added to the public runtime.
- Unknown/stale business policy, configuration, feature flag, provider state, or authorization fails
  closed to `REQUIRE_HUMAN`, `DENY`, or `NOT_SUPPORTED`.
- Never record secrets, raw PII fixtures, raw provider payloads, or chain-of-thought.

## Reconciled implementation state

The following is local engineering evidence only, not release evidence:

- Full verification on 2026-07-28: **243 tests passed** with local PostgreSQL; Ruff, formatting,
  strict mypy, contract validation, and context-drift checks passed.
- Migrations through `0011_manual_send_integrity.sql` are forward-only and applied by the local test
  database. Do not rewrite deployed migration files.
- The fixed Tool Facade has ten contract-defined tools, strict unknown-field rejection, Ed25519 Runner
  claims, and server-bound contact/order/public-code preflights.
- The runner has a hard 20-second runtime deadline, bridge revocation, hash-only tool ledger, and durable
  `MODEL_TIMEOUT` recovery record.
- Implemented P0 local paths are prompt/tool injection, model timeout, bound-request IDOR,
  public-status IDOR, approval-field tamper, post-approval edit, and manual-attestation/worker mutual
  exclusion. Each synthetic result stays `SKIP` because it is not a PRIMARY provider evaluation.
- Manual-send storage is restricted to `SHADOW` plus synthetic `INTERNAL_TEST`; marketing and any
  unconfigured real channel remain blocked until the owner policy/channel decision exists.

## Next implementation sequence

Work in this order unless a higher-authority contract changes it. After each slice, update
`TASK-agent-001.md`, this brief, the relevant machine status, and tests; do not mark `AGENT-001`
complete.

1. **`P0-KILL-SWITCH-INFLIGHT` — current bounded slice.**
   - Fixture: `fixture:outbound_disabled_after_draft_before_worker_send:v1`.
   - Required assertions: an unexecuted automated outbox action is held/cancelled; human operation
     remains available; no global flag can override the disabled capability.
   - Build a server-owned, fail-closed execution gate at the outbox boundary. A draft created before a
     disable event must not be sent afterwards. Preserve an auditable, atomic state transition and do
     not add a provider sender merely to test it.
   - Add a pinned PII-free fixture, negative PostgreSQL test, hash-only synthetic runner result, safety
     grader mapping, and CLI test. The synthetic result must remain `SKIP`.

2. **Finish remaining local P0 authorization/reliability slices.** Prioritize stale/unavailable flag
   storage, STOP/outbox race, audit-write failure, generic unavailable public renderer, and any P0 case
   whose fixture or required assertion remains `NOT_IMPLEMENTED`. Use the eval manifest and assertion
   registry as the precise backlog; do not change a registry status to `IMPLEMENTED` without executable
   evidence and a passing negative test.

3. **Integrated runtime evaluation.** Only after the local path is complete, add a separately
   controlled non-production integration harness for PRIMARY, fallback, and deterministic-degraded
   paths. It must pin the runtime/model/prompt/tool/config artifacts, validate result schema, and retain
   only permitted hash-safe evidence. No synthetic result may be relabeled as PRIMARY or release-ready.

4. **External/provider prerequisites — blocked, not improvable by code alone.**
   - `DEC-006`: obtain Security/Privacy decisions for training, retention, region, deletion,
     subprocessors, incident terms, and dedicated credential use.
   - Prove a supported OpenClaw Responses `store:false` route with a non-production dedicated API
     credential, then capture/assert the effective request without PII or secrets.
   - Pin an immutable model release ID. Moving aliases remain EVAL_ONLY.

5. **`SECURITY-001` only after `AGENT-001` declared acceptance is evidenced.** It requires real
   security, OIDC, PITR/restore, incident, and kill-switch drills. It is not authorized by passing unit
   tests. `SHADOW-001`, `CHANNEL-001`, and automation work remain downstream and may not be started as
   release work before their dependencies and owner decisions are satisfied.

## Acceptance and verification

For every code slice, run the targeted tests first. Before handoff, run:

```text
uv sync --all-packages --all-groups
uv run ruff check .
uv run ruff format --check .
$env:DATABASE_URL='postgresql://app:app@localhost:5432/nha_trang_laundry'
uv run pytest
uv run mypy apps packages
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
```

`AGENT-001` is complete only when its queue-declared evidence exists: P0 evaluated across required
runtime paths, tool-escape evidence, immutable runtime pin, provider-storage verification, rollback
assessment, and the declared acceptance commands. A signed release manifest and gate evidence are still
separate requirements.

## Known blockers and decisions

| ID / blocker | Effect | Required owner/external action |
|---|---|---|
| `DEC-006` provider data governance | No real-customer model use, public ingress, or automated send | Security/Privacy approval and verified provider configuration |
| immutable model release unset | Cannot identify a release candidate | Provider/runtime release selection and verification |
| OpenClaw `store:false` unproven | Cannot satisfy data policy | Supported route plus effective-request integration evidence |
| dedicated service credential unverified | No production provider integration | Create and verify dedicated non-personal credential |
| `DEC-005` official channel | No public channel/manual real channel | Business owner selects supported official channel and policy |
| P0 fixtures/datasets incomplete | No G1 P0 pass | Complete executable eval paths and calibrated grading |
| PITR, incident, kill-switch drills | No G1 readiness | `SECURITY-001` controlled operations work |

## Handoff template

Every continuation response or PR handoff states:

1. requirement/contract touched;
2. code and test evidence, including command results;
3. migration/rollback impact;
4. unresolved assumptions and decision IDs;
5. confirmation that authorization remains `NOT_AUTHORIZED` unless a signed gate manifest proves
   otherwise.
