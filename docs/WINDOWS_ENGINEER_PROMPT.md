# Copy-paste prompt for the Windows engineer

You are the Windows deployment engineer for Nha Trang Laundry AI. Clone the engineering handoff branch, read the October 26 evidence, rerun clean verification on the actual Windows target, then implement and verify its remaining deployment requirements. Continue through routine, reversible fixes without stopping at an audit or plan.

Repository: https://github.com/andreidhoang/nha-trang-laundry-ai

Branch: `codex/engineer-setup`

Historical October 22 handoff commit: `a9653231c9b9009e5defa1cfdb1080f4aca0f10b`.
Read the latest branch changes and record the actual starting SHA; the October 26 review is a
separate source/image-bound refresh, not evidence that this historical commit contains later fixes.

Start here: https://github.com/andreidhoang/nha-trang-laundry-ai/blob/codex/engineer-setup/docs/WINDOWS_ENGINEER_HANDOFF_2026-10-22.md

## 1. Establish the Windows environment and checkout

Inspect Windows edition/build, architecture, virtualization, memory, disk and Docker/Compose. Keep application services in Linux containers. Follow the handoff's supported-environment decision; Windows deployment has not been verified. Docker Desktop is not supported on Windows Server.

Run the following in the selected Linux environment, using its Linux filesystem outside OneDrive and `/mnt/c`:

```bash
git clone --branch codex/engineer-setup --single-branch \
  https://github.com/andreidhoang/nha-trang-laundry-ai.git ~/laundry
cd ~/laundry
git rev-parse HEAD
git switch -c codex/windows-deployment
uv sync --frozen --all-packages --all-groups
```

Preserve any existing checkout and uncommitted changes. Never reset or force-push them. If the handoff branch has advanced, inspect the intervening commits and record the actual starting SHA. Never work on `main`.

## 2. Read the instructions and resume the actual work item

Read `AGENTS.md`, `CLAUDE.md`, `BUILD_ENGINEERING_SPEC.md`, `context/INVARIANTS.md`, the Windows handoff, and `context/PROJECT_CONTINUATION.md`.

Inspect `delivery/WORK_QUEUE.yaml`, `delivery/LOOP_STATE.yaml`, the capability registry and both task packets named in the handoff. Read `docs/audit/DAILY_WORKFLOW_REVIEW_2026-10-26.md` with `evidence/handoff/2026-10-26/summary.json` and `evidence/handoff/2026-10-26/workflow-recordings.json`; retain `evidence/handoff/2026-10-22/` as dated history.

Run the context validator and delivery controller. `PILOT-RELEASE-PREFLIGHT-001` is COMPLETE,
with canonical evidence in `evidence/delivery-loop/PILOT-RELEASE-PREFLIGHT-001.yaml`.
`WINDOWS-DEPLOYMENT-001` is BLOCKED on the unavailable/unverified actual target; no independent
item is selected. Obtain target access, rerun clean checks there and collect its prerequisite
evidence. Unblock the Windows item only when that evidence exists, using the existing controller
and a fresh generation/CAS. The local preparation completion does not accept the Windows host.

## 3. Rerun clean verification on the Windows target

The October 22 full run had 5,178 passes, one stale queue-count assertion failure and five skips;
its guard was corrected and six context rechecks passed. On October 26 a clean full snapshot passed
**5,197 tests with five skips**. Later affected rechecks passed **12**, then **127 with four skips**;
the unique test-ID union is **5,201 passed/five skipped**, not a new monolithic final-tree full run.
Separate real-role checks passed **63**. Final daily browser walks passed **82 each** on desktop and
phone; core conformance **872/871** belongs to the earlier frontend image, with **104** later
affected recovery checks. Separate real-API QR screenshot pixels passed **28** offline decode
checks, without real bank-transfer acceptance. Follow the exact source/image bindings in the
October 26 portable evidence. None of these local Mac/Linux-container runs verifies Windows.

Install Node 24 and the Linux recovery-test tools `age`, `age-keygen`, `gzip` and `tar`. Use the frozen lockfile and a fresh isolated PostgreSQL test database, with separately provisioned API/worker roles. Keep credentials out of commands, logs and committed files.

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
CI=true uv run pytest --require-postgres-integration
```

Then run the separate API/worker role tests specified in the handoff with their database URLs securely configured. `CI=true` makes missing recovery tools fail rather than silently skip. Investigate every failure and explain every skip. Do not weaken assertions or describe targeted rechecks or a union of test IDs as a single full-suite pass. Reproduce the daily browser journeys and persisted money/state invariants on the actual Windows target before accepting it.

## 4. Work through the Windows blockers

Follow the handoff's acceptance matrix. Implement and verify:

- Startup, operating checks and backup scheduling, including missed runs, overlap, reboot, wake and Docker/WSL interruption.
- Windows-specific preflight, browser certificate trust, hostname resolution, permissions, firewall and host/guest network boundaries.
- Exact target-architecture images, including Keycloak, with fresh security scans. Its earlier build succeeded, but no completed vulnerability scan was retained.
- Real Keycloak MFA, realm-policy read-back, session revocation, database grants and store membership.
- A separate encrypted archive, recovery-key custody away from the production host, and an actual timed restore meeting RPO ≤15 minutes and RTO ≤4 hours.
- Forced alert delivery observed by the operator.
- Owner-approved counter acceptance and paper fallback before real shop use.

Do not copy Mac credentials, `.venv`, `.shop`, `.demo` or synthetic volumes into production. Mac-only scripts are reference material, not Windows acceptance evidence.

Keep AI, public channels and automatic sends disabled. All thirteen capabilities remain NOT_AUTHORIZED. The Gemini allowance is exhausted; make no new model calls or billing changes.

Complete safe preparation independently. Obtain explicit authorization before paid resources, destructive operations or production activation.

Keep durable notes, reproducible commands, exact test results, source/image bindings and remaining blockers current. Preserve failed evidence and distinguish mocks, local integration tests and actual Windows observations. Commit changes on your engineering branch and finish with a clear handoff showing what passed, what remains blocked and the next concrete action.
