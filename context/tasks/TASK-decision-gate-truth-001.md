# TASK-decision-gate-truth-001 — six items are gated on decisions that were already made

**Goal:** make `blocked_by_decisions` say what actually gates each item, and add the guard that
stops it drifting again.

**Domains:** `platform`, `business_truth`

**Stable work item:** `DECISION-GATE-TRUTH-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Nothing is currently mis-selected, because the one item at risk is held out by a
hand-set `BLOCKED` status. The risk is that the field meant to enforce a policy gate does not, and
nobody would find out until it mattered.

## Why this exists

Opened 2026-08-24 while triaging which open decisions actually cost anything. The triage read
`blocked_by_decisions` and got a wrong answer, which is how the defect surfaced.

`blocked_by_decisions` is machine truth, not documentation. `run_delivery_loop.py:136` refuses to
select an item while any decision it names is unresolved, and `record_delivery_evidence.py:233`
reads the same field. Six non-complete items name decisions that have since resolved:

| Item | Named | Already resolved |
|---|---|---|
| `AUTONOMY-001` | DEC-001, 002, 003, 004 | all four |
| `CHANNEL-001` | DEC-005, DEC-006 | DEC-005 |
| `CHANNEL-ZALO-001` | DEC-005 | yes |
| `EVAL-PUBLIC-CORPUS-001` | DEC-005 | yes |
| `MULTIMODAL-PERCEPTION-001` | DEC-009, 006, 008 | DEC-009, DEC-008 |
| `RETENTION-STORE-001` | DEC-008 | yes |

Five of those are merely misleading: they overstate how decision-blocked the project is, which
distorts the "what should I decide first" question this triage was trying to answer.

**The sixth is a live fail-closed hole.** `RETENTION-STORE-001` has **no unmet dependencies**, and
the only decision it names has resolved — so its decision gate is open. Its own `blocking_condition`
prose names `DEC-018` and `DEC-020`, both open, and neither appears in the machine field. The single
thing standing between the controller and work that two open decisions forbid is a hand-set
`BLOCKED` status, not the gate meant to enforce it. The prose is right; the field the machine reads
is wrong.

This is the pattern `PRODUCTION_READINESS_ASSESSMENT.md` §3 G5 named for
`MULTIMODAL-PERCEPTION-001`, and undercounted: it found one instance and there are six.

## What must be true when this is done

1. No item that is not `COMPLETE` names a resolved decision.
2. `RETENTION-STORE-001` names `DEC-018` and `DEC-020` — transcribed from its own
   `blocking_condition`, not inferred.
3. A guard fails the drift check when a resolved decision is left in a live gate, and it is proven
   to fail by reintroducing the original defect rather than asserted to work.
4. The guard applies to every status. The first shape exempted `COMPLETE` items on the grounds that
   their gates are historical record; `test_delivery_state.py` refuted it, because its fixtures demote
   completed hardening items to `PENDING` to reconstruct historical scenarios, so the exemption
   evaporates exactly when it is needed. `RETENTION-001`'s stale `DEC-008` is therefore pruned too —
   its history is preserved in `evidence/delivery-loop/RETENTION-001.yaml`, which names `DEC-008` nine
   times, and its status, evidence and dependencies are untouched.

## Boundary

**No decision is resolved, opened, or re-statused, and no item's `status` changes.** This edits which
decisions each item cites, nothing else. No item becomes selectable: all six still have unmet
dependencies or open decisions, and the controller returns nothing before and after. The only thing
that changes is what the register says is true.

**Nothing is inferred.** The five removals are mechanical — the register says resolved. The one
addition is transcribed verbatim from the item's own prose. Where prose and field disagreed, the
prose was treated as the finding and the field as the defect, never the reverse.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
