---
name: delivery-item
description: Select, scope and execute one item from the delivery queue under the continue-execution protocol. Use when the instruction is "continue", "continue execute", "work the queue", "start the next item", or names a specific work-item ID.
---

# Work one delivery item

This wraps the existing controller. It does not replace it and it is not a second orchestration
layer — `scripts/run_delivery_loop.py` selects, you implement, `scripts/record_delivery_evidence.py`
records. If a scheduled run or any process holding the `.openclaw/state.json` automation lease is
active, **`context/AUTOMATION_PROTOCOL.md` takes precedence over everything here.**

Autonomous recurring execution is **not authorized** until an isolated runtime test proves
deterministic child lookup, reattachment, and no duplicate child across interruption.

This skill restates the continue-execution protocol so it can be loaded on demand. That means it can
drift. **Where this skill and `AGENTS.md` disagree, `AGENTS.md` wins and this skill is the bug** —
say so rather than following the version in front of you.

## 1. Reconcile with machine truth — before concluding anything

```bash
uv run python scripts/workspace_env.py --check
uv run python scripts/check_context_drift.py
uv run python scripts/run_delivery_loop.py --format controller-json
```

Take the **generation digest** from the controller JSON now. You will need it to record, and a stale
one is how two sessions overwrite each other.

Resume the one `IN_PROGRESS` item if there is one. Do not restart it from a milestone description or
a previous chat summary — inspect its code, tests, contracts and existing evidence first. If none is
active, start only the item the controller selected.

**Verify blockers against the registry, not the queue.** `blocked_by_decisions` in
`delivery/WORK_QUEUE.yaml` has gone stale before — `MULTIMODAL-PERCEPTION-001` listed three decisions
of which two were already `RESOLVED`. Read `context/DECISION_REGISTRY.yaml` directly. Report the
discrepancy; do not silently reconcile it.

`PROJECT_CONTINUATION.md`, `STATUS.md` and `DELIVERY_BOARD.md` are projections, not authority. Where
a projection disagrees with the queue, the queue wins and the projection is a bug to report.

## 2. Scope before writing

Read the item's `task_packet` in full. **An item with no packet is not ready to be worked** — say so
and stop, because inventing the scope is how a wrong feature gets built correctly.

```bash
uv run python scripts/assemble_context.py --task-id <ITEM-ID> --domain <each context_domain>
```

Read the **contracts** first. Read a specification only where a contract is ambiguous, and name the
ambiguity that sent you there.

State five things before touching code: the requirement, the seam, the invariant from
`context/INVARIANTS.md` most at risk, **what you will not touch**, and the rollback.

## 3. Implement one reviewable slice

These override anything the packet implies:

- Deterministic code decides money, policy, SLA, state, permission and capacity. Not a model, not a
  default, not a fallback.
- An unknown business fact is `REQUIRE_HUMAN` or `NOT_SUPPORTED`. Never a plausible value. If you are
  choosing a number the business has not confirmed, stop and use the `decision-request` skill.
- Every material mutation commits atomically with its domain event, audit row and required outbox row
  through `commit_material_change`, or it does not commit.
- Capability flags stay `false`. Wiring a path is not enabling it. A provider credential in the
  environment authorizes nothing — `docs/runbooks/provider-credentials.md`.
- Frozen items stay frozen: `AGENT-001`, `OPENCLAW-REPACK-001`, `RUNTIME-SECURITY-001` (ADR-0004).
- Touching `apps/web` means regenerating `sw.js`; delegate to the `console-engineer` subagent.

## 4. Verify with the checks the item declared

Run **every** `acceptance_check` on the item, unmodified, and paste the real output.

If one fails: show the failure. Do not adjust the check. A weakened check is worse than a failing
one, because a failing check is information and a weakened one is a lie with a green tick.

Static gates before pytest — `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy apps packages`. If you see import errors or a surprising failure count, that is the
`.pth` defect in `CLAUDE.md`, not your change; re-run before reporting anything.

## 5. Record, or block

To record: use the `record-evidence` skill. It requires an adversarial audit first, and a **fresh**
generation digest.

To block: record the precise external or policy blocker with
`scripts/record_delivery_evidence.py` and a fresh digest, then continue an independent
dependency-complete item if one exists. A blocked item does not stop unrelated work.

Stop and ask only when no safe independent progress remains and what is needed is a business-policy
decision, a credential or external system, a destructive or public action, or an authorization
outside this repository.
