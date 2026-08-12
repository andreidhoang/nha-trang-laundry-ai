# CLAUDE.md — Nha Trang Laundry AI

Lean operating card, loaded every turn. Conventions and pointers only — **never status**.
Live status lives in `delivery/` and `context/PROJECT_CONTINUATION.md`; every token here is paid on
every turn of every session.

## Read order

1. `AGENTS.md` — the durable contract, including the continue-execution protocol
2. `context/PROJECT_CONTINUATION.md` — where we are and what is next
3. `delivery/WORK_QUEUE.yaml` + `LOOP_STATE.yaml` — machine truth, outranks all prose
4. `docs/PRODUCTION_READINESS_ASSESSMENT.md` — measured distance to production
5. The selected item's `task_packet` before touching its code

## Non-negotiables

Full list in `AGENTS.md`. The four that get violated under time pressure:

- **The model never computes money, decides policy/SLA/state/permission, selects a customer, or
  sends.** Deterministic domain code does. The 6 kg pricing cliff is a real confirmed rule, not a bug
  to smooth.
- **Unknown means stop.** Missing config, stale flag, ambiguous policy → `REQUIRE_HUMAN` /
  `NOT_SUPPORTED`. Never guess to make a flow complete.
- **A green test run is not completion evidence.** Never relabel a synthetic `SKIP` as provider-backed,
  never weaken a check to pass a gate, never mark a capability authorized without a signed manifest.
- **Never rewrite immutable blocked history.** Frozen items (`AGENT-001`, `OPENCLAW-REPACK-001`,
  `RUNTIME-SECURITY-001`) stay frozen — ADR-0004.

## Commands

```bash
uv sync --all-packages --all-groups
uv run ruff check . && uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/run_delivery_loop.py        # selects the next legal item
uv run python scripts/report_delivery_status.py
```

**Known environment defect:** a freshly synced `.venv` survives exactly one full pytest run. After
that the editable `.pth` files stay on disk but stop being applied and every workspace package
vanishes from `sys.path`, producing phantom failures (observed: 15 and 28 "failures" where the true
count was 0). Run static gates *before* pytest, and `rm -rf .venv && uv sync --all-packages
--all-groups` between suite runs. Not yet root-caused.

## Delivery protocol

The repo has one authoritative queue and its own controller with lease + generation compare-and-swap.
**Do not build a second orchestration layer around it.** Claude Code drives the existing controller:
`run_delivery_loop.py` selects, you implement, `record_delivery_evidence.py --expected-generation`
records. `context/AUTOMATION_PROTOCOL.md` takes precedence for any scheduled run.

Autonomous recurring execution is **not authorized** until an isolated runtime test proves
deterministic child lookup, reattachment, and no duplicate child across interruption.

## What an agent may not decide here

Push, PR, deployment, secrets, credentials, provider calls, public ingress, capability
authorization, and any of `DEC-001`–`DEC-006`. When blocked on one of these, record the blocker and
move to an independent ready item; if none exists, stop and ask.

## Structure

```
apps/api            FastAPI internal API + staff console routes
apps/worker         supervisor, durable agent worker, bounded Responses runtime
apps/public-agent-tools  the 10-operation Tool Facade
apps/web            staff PWA (no Shadow surface yet — SHADOW-CONSOLE-001)
packages/domain     deterministic money/pricing/promotion/delivery/SLA — the authority
packages/db         migrations + repositories, atomic mutation+event+audit+outbox
packages/policy     typed fail-closed decision point
packages/evals      eval manifest, graders, synthetic suites
specs/contracts     machine-readable contracts; these win over prose
```
