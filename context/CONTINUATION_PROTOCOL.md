# Codex continuation protocol

This protocol makes a short user instruction such as `continue execute` sufficient to resume safe
repository work. It coordinates engineering delivery only. It never authorizes a launch, customer
data, a public channel, or an automated send.

## Source-of-truth order

```text
normative specs/contracts
          |
          v
delivery/WORK_QUEUE.yaml ---- dependencies / acceptance / required evidence
          |
          v
delivery/LOOP_STATE.yaml ----- exactly one active work item or none
          |
          v
context packet -------------- only the domains required by that work item
          |
          v
code + tests + evidence ------ observable implementation truth
          |
          v
delivery/CAPABILITY_STATUS.yaml
          |
          +-------------------- remains NOT_AUTHORIZED without signed release gates
```

Chat summaries and human status pages are navigation aids, not execution authority.

## State machine

```text
                    dependency and decision checks pass
 PENDING / READY ------------------------------------------+
        ^                                                   |
        | unblock with verified changed condition           v
     BLOCKED <---- record exact external/policy blocker -- IN_PROGRESS
                                                            |
                                                            | all declared checks pass
                                                            | + evidence recorded
                                                            v
                                                         COMPLETE
                                                            |
                                                            +--> select next safe item
```

Only one item may be `IN_PROGRESS`. A `COMPLETE` item is immutable planning history unless a new
corrective work item is created. A blocked item does not prevent an independent dependency-complete
item from proceeding.

## Resume algorithm

1. Validate context, queue, state, gate registry, and capability status.
2. Render the active/next work brief and assemble its declared context packet.
3. Reconcile code and existing evidence; never infer completion from prose.
4. Implement one reviewable slice. Preserve fail-closed and authorization boundaries.
5. Run targeted checks, then every acceptance command declared on the work item.
6. Record requirement/contract, checks, rollback impact, and unresolved assumptions.
7. Mark complete only after evidence validation; otherwise record a precise blocker.
8. Immediately select the next safe item while local, reversible work remains.

## Stop conditions

Stop and request user/external action only when no independent ready work remains and progress needs:

- a business-policy decision not present in an approved source;
- credentials, provider tenant, production data, or another unavailable external system;
- destructive migration/deletion, public deployment, public ingress, or outbound communication;
- a release/capability authorization or materially expanded scope.

Test failure, implementation difficulty, or stale prose is not by itself a reason to stop.

## Evidence boundary

Engineering evidence proves a local work item met its declared checks. Release evidence is separate
and must satisfy `delivery/GATE_REGISTRY.yaml` plus the release-gate JSON Schema. Neither evidence type
may be substituted for the other.
