# OpenClaw-Codex automation protocol

This protocol controls one recoverable, local-only engineering tick for
`nha-trang-laundry-ai`. OpenClaw supplies scheduling; the repository supplies
the delivery queue, transaction journal, Git evidence, and bounded retry state.

It does **not** authorize a push, PR, deployment, provider credential lookup,
production call, real customer data, public ingress, outbound message,
destructive migration, release signature, or capability enablement.

## Fixed repository

```text
C:\Users\DELL\OneDrive\Desktop\nha-trang-laundry-ai
```

Never substitute another repository or treat the personal OpenClaw workspace
as this project.

## Durable authorities

- `delivery/WORK_QUEUE.yaml`, `delivery/LOOP_STATE.yaml`, and
  `delivery/PROGRAM_PLAN.yaml` are one logical delivery generation.
- `scripts/delivery_state.py` protects that generation with a shared mutex and
  write-ahead journal. Every reader/writer must use
  `recover -> read -> validate -> mutate -> commit` under that mutex.
- `.openclaw/state.json` is schema v2 local execution state. Its non-terminal
  phases are:

```text
PREPARED -> CHILD_RUNNING -> VERIFYING -> TASK_COMMITTED -> MERGED
         -> DELIVERY_COMMITTED -> TERMINAL(PASSED)

PREPARED/CHILD_RUNNING/VERIFYING/TASK_COMMITTED
         -> RECOVERY_REQUIRED -> BLOCK_COMMITTED -> TERMINAL(BLOCKED)
```

- `FAILED`, `STOPPED`, and `TIMED_OUT` are retryable only before merge and only
  for attempts one and two. Attempt three must finish through the proven
  `BLOCK_COMMITTED` path.
- `scripts/run_automation_tick.py` is the executable fail-closed inspector. A
  cron prompt must follow its returned action; it must not improvise from this
  prose alone.

## One guarded tick

1. Change to the fixed repository. Read `AGENTS.md`, this protocol, and the
   selected work item's task packet.
2. Generate a unique owner such as `auto-dev:<random-UUID>` and acquire a lease:

```text
uv run python scripts/manage_automation_state.py acquire \
  --owner <unique-owner> \
  --ttl-seconds 3900
```

   Parse and retain the returned `lease_id`. If the command returns
   `LEASE_BUSY`, output `NO_REPLY` and stop without touching Git or delivery
   state. Renewal of the same live lease is fenced and must include:

```text
uv run python scripts/manage_automation_state.py acquire \
  --owner <same-owner> \
  --lease-id <same-lease-id> \
  --ttl-seconds 3900
```

3. Inspect the only safe next action:

```text
uv run python scripts/run_automation_tick.py \
  --owner <unique-owner> \
  --lease-id <lease-id>
```

   The inspector obtains one locked delivery/context snapshot, validates all
   context and capability controls, checks the lease fence, and proves the
   relevant Git branch, ancestry, commit parent, changed paths, and clean/dirty
   worktree shape. Retain its `delivery_generation` digest.
4. Execute at most one returned action, then rerun the inspector. Never select
   or start a second work item in the same tick.
5. Always release the exact lease in `finally`:

```text
uv run python scripts/manage_automation_state.py release \
  --owner <unique-owner> \
  --lease-id <lease-id>
```

The scheduler timeout must be at most 3300 seconds, leaving a 600-second margin
inside the 3900-second lease. Recheck the lease immediately before a Git,
delivery, or child mutation; renew with the same fenced ID before the remaining
TTL drops below 900 seconds.

## Exhaustive inspector action map

- `READY_NEW`, `READY_RETRY`: execute only `begin-attempt`.
- `CREATE_ATTEMPT_BRANCH`: create/switch the exact returned branch at the exact
  base.
- `START_DELIVERY_ITEM`: execute recorder `--start` with
  `--expected-generation <decision.delivery_generation>`.
- `RECONCILE_CHILD`: look up the deterministic run ID. Reattach an existing
  child even when `spawn_allowed=false`; spawn only when lookup proves absence
  **and** `spawn_allowed=true`.
- `WAIT_FOR_CHILD`: poll only the persisted child session; never spawn.
- `RESUME_VERIFICATION`: continue the declared checks.
- `PRESERVE_RETRY_WORK`, `PRESERVE_FAILED_WORK`: review and checkpoint only
  bounded task/claim files on the current feature branch using a local commit;
  reject secrets, PII, unrelated paths, or generated junk. Do not merge it.
- `SWITCH_TO_BASE_FOR_BLOCK`: switch to clean `main` only after the feature
  branch is clean and `main == base_commit`.
- `RECORD_DELIVERY_BLOCK`, `RECORD_UNSTARTED_DELIVERY_BLOCK`: execute recorder
  `--block` with the snapshot generation.
- `RESUME_MERGE`: switch to clean `main`; do not change history.
- `RESUME_FAST_FORWARD`: fast-forward only to the persisted task commit.
- `RECORD_MERGED`: execute state `record-merged`.
- `RESUME_DELIVERY_COMPLETION`: execute recorder `--complete` with the snapshot
  generation.
- `COMMIT_DELIVERY_STATE`, `COMMIT_BLOCK_STATE`: use
  `scripts/commit_delivery_control.py`; never call raw `git commit`.
- `RECORD_DELIVERY_COMMIT`, `RECORD_BLOCK_COMMIT`: persist the exact proven
  control commit in automation state.
- `FINALIZE_SUCCESSFUL_ATTEMPT`, `FINALIZE_BLOCKED_ATTEMPT`: record the matching
  terminal result.
- `LEGACY_RECOVERY_REQUIRES_MANUAL_RECONCILIATION`, `BLOCKED`: make no mutation;
  report the sanitized invariant that failed.
- `NO_ACTION`: output `NO_REPLY`.

Any unknown action is a hard stop.

## Attempt creation and child linkage

For `READY_NEW` or `READY_RETRY`, persist the attempt **before** creating a
branch, claiming delivery work, or spawning a child:

```text
uv run python scripts/manage_automation_state.py begin-attempt \
  --owner <unique-owner> \
  --lease-id <lease-id> \
  --work-item <ID> \
  --attempt-id <decision.attempt_id> \
  --branch <decision.branch> \
  --base-commit <decision.base_commit> \
  --child-run-id <decision.child_run_id>
```

Then follow these inspector actions:

- `CREATE_ATTEMPT_BRANCH`: create/switch only the exact returned branch at the
  exact base commit. Abort if an unexpected existing branch or dirty file is
  found.
- `START_DELIVERY_ITEM`:

```text
uv run python scripts/record_delivery_evidence.py \
  --work-item <ID> \
  --start \
  --expected-generation <decision.delivery_generation>
```

- `RECONCILE_CHILD`: look up the deterministic `child_run_id` in native
  Codex/TaskFlow state. Reattach an existing child. Spawn only when the runtime
  can prove no matching child exists. If lookup is unavailable or ambiguous,
  record recovery-required; never guess or spawn a duplicate.
- Before spawning, render the exact Markdown child brief:

```text
uv run python scripts/run_delivery_loop.py --format markdown
```

  Use that rendered brief, including its context packet, acceptance checks, and
  required evidence, as the child task.
- After deterministic child linkage:

```text
uv run python scripts/manage_automation_state.py attach-child \
  --owner <unique-owner> \
  --lease-id <lease-id> \
  --work-item <ID> \
  --attempt-id <attempt-id> \
  --child-session <native-child-session>
```

The Codex child may edit only the bounded task scope. It must never commit,
merge, push, deploy, contact external services, use production data, retrieve
secrets, weaken checks, or authorize a capability.

## Verification and successful completion

When the child is complete:

```text
uv run python scripts/manage_automation_state.py begin-verification \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id>
```

Review the diff, reject unrelated or unsafe changes, and run every acceptance
check declared by the queue item. Required integration checks may not skip.
Also run the cumulative repository gates:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages
uv run python scripts/apply_migrations.py
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
npm --prefix runtime/openclaw/public-cell/plugin test
```

Create the schema-valid evidence file before the task commit. The uncommitted
delivery claim is part of the task branch's bounded state and must not be left
dirty. With every gate passing:

1. Commit only the reviewed task code, tests, documentation, its evidence, and
   the exact delivery claim generation on the feature branch. The claim must
   still name this work item as `IN_PROGRESS` in both
   `delivery/WORK_QUEUE.yaml` and `delivery/LOOP_STATE.yaml`. Include no other
   delivery mutation. The resulting clean commit must descend from
   `base_commit`; the inspector proves that its full delta contains both claim
   files before allowing a fast-forward.
2. Record the exact commit:

```text
uv run python scripts/manage_automation_state.py record-task-commit \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --task-commit <task-commit>
```

3. Fast-forward local `main` only if it still equals `base_commit`; never merge
   with a merge commit and never reset.
4. Record the fast-forward:

```text
uv run python scripts/manage_automation_state.py record-merged \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id>
```

5. Complete delivery through the recorder, then commit only the three allowed
   delivery control files:

```text
uv run python scripts/record_delivery_evidence.py \
  --work-item <ID> \
  --complete \
  --evidence evidence/delivery-loop/<ID>.yaml \
  --expected-generation <decision.delivery_generation>
```

6. Rerun the inspector and use its fresh generation with the bounded commit
   wrapper:

```text
uv run python scripts/commit_delivery_control.py \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --kind complete \
  --expected-parent <task-commit> \
  --expected-generation <decision.delivery_generation>
```

   The wrapper holds the delivery mutex across compare-and-swap, staging, and
   Git commit. The delivery commit must have exactly one parent
   (`task_commit`), include `delivery/WORK_QUEUE.yaml` and
   `delivery/LOOP_STATE.yaml`, and change no path outside the three delivery
   control files.
7. Record and finalize:

```text
uv run python scripts/manage_automation_state.py record-delivery-commit \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --delivery-commit <delivery-commit>

uv run python scripts/manage_automation_state.py record-result \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --result PASSED \
  --summary "<sanitized concise summary>"
```

## Failure, retry, and block completion

For a genuine implementation failure on attempt one or two, keep the item
`IN_PROGRESS`. Review and checkpoint bounded partial work on the exact feature
branch so the worktree is clean and its HEAD still descends from `base_commit`;
then record one immutable retryable result:

```text
uv run python scripts/manage_automation_state.py record-result \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --result FAILED \
  --summary "<sanitized failure summary>"
```

The next attempt reuses that feature branch only while local `main` still
equals the original base.

For attempt three, an external/policy blocker, or deterministic recovery
failure:

1. Mark recovery required:

```text
uv run python scripts/manage_automation_state.py record-recovery-required \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id>
```

2. Preserve partial feature work locally without merging it. Switch to clean
   `main`, which must still equal `base_commit`.
3. Record the delivery blocker:

```text
uv run python scripts/record_delivery_evidence.py \
  --work-item <ID> \
  --block \
  --reason "<sanitized exact blocker>" \
  --expected-generation <decision.delivery_generation>
```

4. Rerun the inspector and create the block control commit only through:

```text
uv run python scripts/commit_delivery_control.py \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --kind block \
  --expected-parent <base-commit> \
  --expected-generation <decision.delivery_generation>
```

   The block commit must have exactly one parent (`base_commit`) and must not
   contain task code.
5. Record and finalize:

```text
uv run python scripts/manage_automation_state.py record-block-commit \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --delivery-commit <block-control-commit>

uv run python scripts/manage_automation_state.py record-result \
  --owner <unique-owner> --lease-id <lease-id> \
  --work-item <ID> --attempt-id <attempt-id> \
  --result BLOCKED \
  --summary "<sanitized concise blocker>"
```

If retry budget remains, a later unblock starts a new branch from the
then-current clean `main`; it never reuses the old blocked base. An item blocked
on attempt three cannot automatically unblock into attempt four: create a
separately reviewed corrective work item or explicitly revise the plan.
Legacy schema-v1 in-flight attempts without branch/base/child metadata require
manual reconciliation and may not spawn.

Block conversion is pre-merge only. `MERGED`, `DELIVERY_COMMITTED`, and
`BLOCK_COMMITTED` must recover forward or stop for manual reconciliation; they
must never regress to `RECOVERY_REQUIRED`.

## Recovery and integrity rules

- Resume any non-terminal attempt before selecting new work.
- `PREPARED` recovery handles crashes after attempt reservation, branch
  creation, and delivery claim as separate proven steps.
- A persisted `child_run_id` is the idempotency key. `CHILD_RUNNING` may poll
  only its persisted child session.
- Main divergence, unknown Git history, multiple parents, unexpected paths,
  dirty control state, malformed context, expired lease, or unsupported child
  lookup is a hard stop. Never repair these with reset, force checkout, stash,
  clean, force push, or deletion.
- Result summaries must not contain raw stderr/provider payloads, prompts,
  authorization headers, credentials, tokens, secrets, PII, or customer data.
- WAL recovery and abrupt-process tests cover process crashes. On Windows,
  durable directory metadata flush across sudden power loss cannot be proven
  with the current Python filesystem API; keep backups and rerun validation
  after an OS/power failure.

## Cron activation gate

Create the 15-minute cron job disabled. A forced disabled-job dry-run may only
be used after all controller tests and full repository gates pass. Enable it
only when an isolated run proves:

- schema-v2 migration and lease release work;
- native child lookup/reattachment by deterministic run ID works;
- no duplicate child is created across a simulated interruption;
- no push, deploy, public action, external message, or secret retrieval occurs.

Use a stable declaration key and a distinct delivery-read-only preflight
prompt. It may acquire/release the local execution lease, but it must fail
closed rather than recover a pending delivery journal:

```powershell
$preflight = @'
Preflight only in C:\Users\DELL\OneDrive\Desktop\nha-trang-laundry-ai.
Read AGENTS.md and context/AUTOMATION_PROTOCOL.md.
Acquire a unique 3900-second lease, run manage_automation_state.py status and
run_automation_tick.py --preflight once, then release the exact lease in
finally.
Do not begin an attempt, change Git, mutate delivery, spawn a child, push,
deploy, access secrets, or send an external message.
'@

openclaw cron add `
  --name "nha-trang-laundry-auto-dev" `
  --display-name "Nha Trang Laundry AI safe auto-dev tick" `
  --declaration-key "nha-trang-laundry-ai:auto-dev:v1" `
  --every 15m `
  --session isolated `
  --agent main `
  --message $preflight `
  --timeout-seconds 3300 `
  --thinking high `
  --tools "exec" `
  --no-deliver `
  --disabled `
  --json

openclaw cron run <job-id> --wait --wait-timeout 60m --poll-interval 2s
openclaw cron runs --id <job-id> --limit 5
```

After preflight, verify `.openclaw/state.json` has `lease: null` and inspect the
run history. Preflight success is **not** permission to enable: replace the
prompt/tool allowlist only after a separate interruption harness proves native
child lookup, polling, reattachment, and no duplicate spawn. Inspect the final
job with `openclaw cron show <job-id> --json`, force-run it disabled, inspect
history again, and only then use `openclaw cron enable <job-id>`.

If native child reconciliation cannot be proven, leave cron disabled and use
the same protocol on demand.

## Terminal condition

When all local work is complete/blocked and the remaining path needs
credentials, approvals, production infrastructure, public action, or
deployment authority, stop the recurring controller and report the exact
external action. Engineering evidence must never be relabeled as release
evidence.
