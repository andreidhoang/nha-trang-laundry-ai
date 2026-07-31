# Codex repository guidance

Read `BUILD_ENGINEERING_SPEC.md` before implementation. The Vietnamese specifications and
machine-readable files under `specs/contracts/` and `specs/evals/` are normative.

## Commands

After installing uv/Python, use:

```text
uv sync --all-packages --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
docker compose up -d postgres
uv run python scripts/apply_migrations.py
```

## Non-negotiables

- Do not let an LLM calculate money, policy, SLA, permission, or order state.
- Public/customer-facing automation remains disabled unless a signed release gate authorizes it.
- Preserve atomic mutation + domain-event + audit + outbox semantics.
- Never add generic agent tools, direct-send capability, secrets, raw PII fixtures, or chain-of-thought
  storage.
- Use typed schemas at every boundary and add negative/authorization tests for sensitive changes.
- Treat unknown business policy as fail-closed (`REQUIRE_HUMAN` or `NOT_SUPPORTED`).
- Assemble a context packet from `context/CONTEXT_MAP.yaml` for any multi-file or sensitive task.

## Continue-execution protocol

When the user's instruction is `continue`, `continue execute`, or an equivalent request to resume:

**Scheduled-controller precedence:** an OpenClaw cron run or any process holding
the `.openclaw/state.json` automation lease must follow
`context/AUTOMATION_PROTOCOL.md` instead of the generic manual sequence below.
It must persist `begin-attempt` before branch/queue/child mutations, obey
`scripts/run_automation_tick.py`, execute at most one returned action per tick,
and use the snapshot generation CAS for delivery mutations. These rules take
precedence over any “continue to the next item” wording in this section.

1. Read `delivery/LOOP_STATE.yaml`, then run `uv run python scripts/check_context_drift.py` and
   `uv run python scripts/run_delivery_loop.py`. The machine-readable queue and state override prose
   status pages.
2. Resume the one `IN_PROGRESS` item; do not restart it from a milestone description or the previous
   chat summary. Inspect its code, tests, declared contracts, and existing evidence first.
3. If no item is active, start only the item selected by `scripts/run_delivery_loop.py`. Obtain the
   current generation from `run_delivery_loop.py --format controller-json` and pass it to
   `record_delivery_evidence.py --expected-generation <SHA-256>`.
4. Work through safe local implementation and verification without asking for another prompt. After
   a task's declared checks genuinely pass, create its evidence record, mark it complete with
   `scripts/record_delivery_evidence.py`, and continue to the next dependency-complete item when the
   remaining work is still local, reversible, and in scope.
5. A description of prior work, existing code, or a green generic test run is not completion evidence.
   Do not silently accept skipped required integration tests, fabricate results, weaken a check, or
   mark a release/capability gate passed.
6. If blocked by an unknown policy, missing external credential/service, destructive action, public
   deployment, or authority outside this repository, fail closed. Record the blocker with
   `scripts/record_delivery_evidence.py` using a fresh expected-generation digest, then continue an
   independent ready item if one exists. Unblock also requires a fresh digest; it returns the item to
   `PENDING` and never bypasses unresolved decision blockers. Ask the user only when no safe
   independent progress remains.
7. Keep `delivery/WORK_QUEUE.yaml`, `delivery/LOOP_STATE.yaml`, evidence, and human status projections
   synchronized. Run the full repository gates before handoff.

The detailed state machine and evidence rules are in `context/CONTINUATION_PROTOCOL.md`.

## Change handoff

State the requirement/contract touched, tests run, rollback impact, and unresolved assumptions. Do not
claim a launch gate is passed without recorded evidence.
