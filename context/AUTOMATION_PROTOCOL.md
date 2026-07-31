# OpenClaw-Codex automation protocol

This protocol controls one local engineering iteration for `nha-trang-laundry-ai`. Timing is provided
by OpenClaw cron. The authoritative task/dependency/evidence state remains in `delivery/`; local lease
and retry mechanics live in the gitignored `.openclaw/` directory.

It does not authorize provider credentials, real customer data, public ingress, outbound messages,
production deployment, destructive migrations, release signatures, or capability enablement.

## Fixed repository

```text
C:\Users\DELL\OneDrive\Desktop\nha-trang-laundry-ai
```

Never search for or substitute another repository. Never operate on the personal OpenClaw workspace as
if it were this project.

## One cron iteration

1. Change to the fixed repository and read `AGENTS.md`, `delivery/LOOP_STATE.yaml`, and this protocol.
2. Generate a unique owner for this cron run (`auto-dev:<random-UUID>`) and acquire the local lease:

```text
uv run python scripts/manage_automation_state.py acquire \
  --owner <unique-owner> --ttl-seconds 3900
```

   If another unexpired owner holds it, return `NO_REPLY` without modifying Git or delivery state.
   Never reuse a constant owner across separate cron runs. Renew with the same owner before a long
   verification phase if the remaining lease is insufficient.
3. Inspect `git status --short --branch`.
   - Continue only on `main` or `feature/auto-dev-<current-work-item-lowercase>`.
   - On `main`, unrelated tracked/untracked user changes are a blocker. Do not stash, reset, clean,
     delete, overwrite, or include them in a commit.
   - On the exact active feature branch, preserve and inspect prior partial work before continuing.
4. Run `uv run python scripts/check_context_drift.py`. A failure is a controller blocker, not permission
   to weaken validation.
5. Select work:
   - If `LOOP_STATE.current_work_item` is set, resume exactly that item.
   - Otherwise run `uv run python scripts/run_delivery_loop.py --format json`.
   - If no safe item exists, record the stopped/blocked result, release the lease, and announce the
     exact external condition once.
6. For a new item, create `feature/auto-dev-<task-id-lowercase>` from clean local `main`, then use
   `scripts/record_delivery_evidence.py --work-item <ID> --start`. Never start a second item.
7. Render the full prompt with `uv run python scripts/run_delivery_loop.py`. It contains the assembled
   normative context, atomic `task_packet`, declared checks, and required evidence.
8. Read `.openclaw/state.json`. Reuse the selected item's current `attempt_id` only when it has no
   recorded result (crash/timeout resume); otherwise construct the next stable ID from task ID, base
   commit, and attempt ordinal. Record it idempotently:

```text
uv run python scripts/manage_automation_state.py begin-attempt \
  --owner <unique-owner> --work-item <ID> --attempt-id <stable-attempt-id>
```

   Then launch exactly one Codex native subagent for the selected item. Give it the rendered prompt and
   require it to:
   - inspect existing code/tests/contracts before editing;
   - edit only the bounded task scope;
   - use tests first or with implementation;
   - preserve fail-closed/public-disabled behavior;
   - report files changed, tests, rollback impact, and unresolved assumptions;
   - never commit, merge, push, deploy, use production data, or contact an external service.
9. Review the returned diff. Reject unrelated edits, invented evidence, weakened/skipped checks,
   credentials/PII, direct-send/generic tools, authorization changes, or dependency drift.
10. Run targeted checks, then every acceptance command declared on the work item. Also run the full
    repository gates before completion:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages
uv run pytest
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
npm --prefix runtime/openclaw/public-cell/plugin test
```

11. A required integration test that skips because infrastructure is absent is not `PASSED`.
12. If checks fail, give Codex the exact sanitized failure and diff for a bounded repair. Before
    releasing the lease, record the immutable attempt result with the same `attempt_id`. Summaries must
    contain no raw stderr/provider payload, authorization header, credential, token, or secret. Allow
    no more than three recorded attempts for the item across cron runs.
13. After three failed attempts or a genuine external/policy blocker:
    - record the exact blocker with `record_delivery_evidence.py`;
    - do not merge partial implementation;
    - preserve the feature branch for inspection;
    - release the lease and announce the blocker once.
14. On success:
    - create a schema-consistent evidence file under `evidence/delivery-loop/`;
    - list every declared command with its actual `PASSED` result;
    - include requirement/contract, required evidence, rollback impact, and assumptions;
    - complete the item only through `scripts/record_delivery_evidence.py`;
    - review `git diff --check` and the staged diff;
    - commit atomically on the task branch as `feat(<task-id-lowercase>): <bounded outcome>`;
    - fast-forward local `main` only when it is still the exact clean parent;
    - never push. A remote PR/push requires a separate explicit authorization and configured identity.
15. Record the result through `manage_automation_state.py record-result`, then release the lease with
    the unique owner in a `finally`-style cleanup. Return:
    - `NO_REPLY` for lease-busy/no-change iterations;
    - one concise update when an item completes, blocks, or controller integrity fails.

## Recovery rules

- Gateway restart: cron and its persistent custom session survive in OpenClaw SQLite.
- Agent timeout: the local lease expires; the next iteration resumes the queue/branch rather than
  starting a new task.
- Detached Codex child: OpenClaw creates a mirrored TaskFlow record automatically; inspect it with
  `openclaw tasks flow`.
- Missing managed TaskFlow API: do not fabricate a flow ID. Repository state plus the local lease are
  the recovery authority for this controller.
- Merge conflict or unexpected Git history: stop. Never resolve by reset, force checkout, force push,
  or deleting user changes.

## Terminal condition

When all locally executable work is complete or blocked and the remaining path needs credentials,
approvals, production infrastructure, public action, or deployment authority, stop the recurring
controller and report the exact next external action. Engineering evidence must never be relabeled as
release evidence.
