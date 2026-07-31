# OpenClaw orchestration state

Repository delivery state remains authoritative:

- `delivery/WORK_QUEUE.yaml`
- `delivery/LOOP_STATE.yaml`
- `delivery/PROGRAM_PLAN.yaml`
- `delivery/CAPABILITY_STATUS.yaml`

The first three delivery files are one logical generation. Their shared mutex
and write-ahead journal are implemented by `scripts/delivery_state.py`.

OpenClaw keeps only local scheduler execution metadata here:

- `state.json` — schema-v2 lease and per-work-item recovery state
- `state.md` — human-readable best-effort projection of `state.json`
- `.state.mutex` — bounded inter-process mutation lock

Schema v2 persists the lease fence (`owner` plus `lease_id`) and, per active
attempt:

```text
phase
branch
base_commit
child_run_id
child_session
task_commit
delivery_commit
legacy_migrated
```

Mutations validate the complete state before publishing it. The machine JSON
is authoritative if a crash occurs between JSON and Markdown projection
writes. Runtime files are gitignored and must never contain secrets, PII,
prompt contents, raw provider payloads, or customer data.

Schema-v1 completed attempts migrate to explicitly marked legacy `TERMINAL`
records. Schema-v1 in-flight attempts migrate to marked
`RECOVERY_REQUIRED` records without invented recovery metadata and require
manual reconciliation. Schema-v2 records cannot claim the legacy exception
without that migration marker.

The executable controller guard is `scripts/run_automation_tick.py`; the full
operating contract is `context/AUTOMATION_PROTOCOL.md`. Atomic Codex prompts
remain referenced by stable queue IDs through each work item's `task_packet`.

This state cannot authorize a release, deployment, public capability, external
message, push, or production action.
