# OpenClaw orchestration state

The repository delivery state remains authoritative:

- `delivery/WORK_QUEUE.yaml`
- `delivery/LOOP_STATE.yaml`
- `delivery/PROGRAM_PLAN.yaml`
- `delivery/CAPABILITY_STATUS.yaml`

OpenClaw keeps only local scheduler mechanics in this directory:

- `state.json` — machine-readable lease, retry counter, and last result; this is the local scheduler
  source
- `state.md` — human-readable best-effort projection of the JSON state
- `.state.mutex` — short inter-process lock for an individual state mutation

All runtime files are intentionally gitignored. They survive ordinary Gateway/repository restarts but
must never override the repository state machine, authorize a release, or contain secrets/PII. If a
crash occurs between the two projection writes, JSON wins and the next state mutation rewrites Markdown.

The controller protocol is `context/AUTOMATION_PROTOCOL.md`. Atomic Codex prompts are referenced from
each work item through its `task_packet` field.
