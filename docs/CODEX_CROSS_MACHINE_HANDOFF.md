# Codex and engineer cross-machine handoff

**Reconciled:** 2026-09-13 (Asia/Ho_Chi_Minh)

**Repository:** `https://github.com/andreidhoang/nha-trang-laundry-ai.git`

**Supported branch:** `main`

**Machine-readable authority:** `delivery/WORK_QUEUE.yaml`, `delivery/LOOP_STATE.yaml`,
`delivery/CAPABILITY_STATUS.yaml`

This is the entry point for an engineer or Codex agent opening the project on another machine. It
separates what GitHub can reproduce everywhere from the one shop installation that may hold real
orders and money.

## 1. Current engineering truth

- R1 is a deterministic, staff-only laundry operations console. It supports the reviewed workflows
  in `docs/CORE_BUSINESS_WORKFLOWS_V1.md`.
- The last complete guarded verification passed **1,189 tests with 3 intentional skips**. Live
  synthetic verification passed 24/24 security checks, 70/70 daily-operation browser checks,
  106/106 workflow-conformance checks, and 59/59 detailed console-interaction checks.
- Pricing, permission, SLA, order state, settlement and delivery decisions remain deterministic
  Python/PostgreSQL behavior. The browser and any model are not business authorities.
- All thirteen AI/public capabilities remain `NOT_AUTHORIZED`. A fresh clone must report that state;
  it is the correct day-one result, not a missing setup step.
- The isolated, EVAL_ONLY OpenClaw dependency tree builds and passes its 9 tests, but the registry
  audit currently reports **4 high and 4 moderate advisories**. Three high findings have no available
  registry fix. This is the recorded `RUNTIME-SECURITY-001` blocker; do not run `npm audit fix`,
  repin evidence or treat that runtime as deployable. It is not part of the deterministic R1 shop
  console.
- The current shop Mac checkout is `/Users/danghuyhoang/laundry`. Its real-shop containers are
  stopped pending an external archive disk and macOS trust of `.shop/ca/ca.crt`. Do not claim that
  shop cutover is complete until the preflight, restore drill and one owner-approved counter
  transaction pass.

Re-measure instead of trusting these numbers:

```bash
uv run python scripts/check_context_drift.py
uv run python scripts/run_delivery_loop.py --format controller-json
uv run python scripts/report_delivery_status.py
git status --short
git rev-parse HEAD
```

## 2. Two modes that must not be confused

| Mode | May run on any engineer machine? | Data | Entry point |
|---|---:|---|---|
| Local engineering/demo | Yes | Synthetic only | `docs/runbooks/demo-stack.md` |
| Real shop till | No; one explicitly chosen shop Mac | Real orders and settlement records | `docs/runbooks/shop-till-mac.md` |

Git intentionally excludes `.shop/`, `.demo/`, `.env`, private keys, certificates and database
volumes. A GitHub clone is therefore safe to use for engineering and synthetic UI verification, but
it is **not** a copy of the live shop. Never solve that difference by committing, messaging or
copying live credentials through an AI chat.

Running multiple independent real-shop databases would create competing sources of truth. Other
machines should use the synthetic demo, or become an authorized replacement host through the
backup/restore runbook. They must not start a second production ledger from newly generated secrets.

## 3. Fresh machine bootstrap

Prerequisites: Git, Docker Desktop (or compatible Docker Compose), `uv`, and Node.js 24 for the
isolated OpenClaw comparison-plugin checks. Clone outside Desktop, Documents, Downloads, OneDrive,
Dropbox or iCloud-synced directories.

```bash
git clone https://github.com/andreidhoang/nha-trang-laundry-ai.git ~/laundry
cd ~/laundry
uv sync --all-packages --all-groups
uv run python scripts/workspace_env.py --check
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
```

If an existing checkout was moved rather than freshly cloned, recreate or reinstall its disposable
`.venv`; executable launchers can retain the old absolute interpreter path even when imports work.
`uv sync --all-packages --all-groups --reinstall` repairs that case. Do not move `.shop/` separately
from an established shop checkout.

Then read, in order:

1. `AGENTS.md`
2. `BUILD_ENGINEERING_SPEC.md`
3. `context/PROJECT_CONTINUATION.md`
4. `docs/CORE_BUSINESS_WORKFLOWS_V1.md`
5. `context/tasks/TASK-cross-machine-bootstrap-001.md`

On Windows, choose a local non-synced path and translate only shell syntax; do not translate the
contracts, environment names or Compose topology. The production shop-till runbook is macOS-specific.

## 4. Reproduce the application with synthetic data

One command, from a fresh clone, on any machine (`CLONE-AND-RUN-001`):

```bash
uv run python scripts/start_demo.py
```

It runs the sequence below in order, stops at the first failure and names it, and prints the console
URL once the stack verifies healthy. `--preflight-only` checks the prerequisites and changes
nothing. `packages/evals/tests/test_start_demo.py` keeps the script and `docs/runbooks/demo-stack.md`
from drifting apart.

The individual steps, for when one of them has failed and you want to run it alone:

```bash
docker network create --internal nha-trang-laundry-staging-database-private
uv run python scripts/generate_demo_material.py
docker compose -f compose.yaml -f compose.production.yaml -f compose.demo.yaml up -d --wait --build
uv run python scripts/verify_demo_stack.py
```

If the network already exists, inspect and reuse it; do not delete a shared Docker network merely
to make the command quiet. Open:

```text
http://localhost:8081/demo-idp/
```

Use one of the four synthetic identities. The owner account can exercise all staff workflows; the
auditor account must remain read-only.

Browser verification:

```bash
uv run --with playwright python scripts/verify_console_interaction.py
uv run --with playwright python scripts/verify_daily_operations.py \
  --base-url http://localhost:8081 --idp-url http://localhost:8081/demo-idp
uv run --with playwright python scripts/verify_workflow_conformance.py \
  --base-url http://localhost:8081 --idp-url http://localhost:8081/demo-idp \
  --store 11111111-2222-4333-8444-555555555555 \
  --psql-container nha-trang-laundry-private-staging-postgres-1 \
  --database nha_trang_laundry
```

The last two scripts create and settle synthetic orders. Never point them at a real shop unless the
owner explicitly authorizes those records.

## 5. Full engineering gate

Create a separate synthetic test database. This is the repository's local-development database, not
the private-staging database and never the shop ledger:

```bash
cp .env.example .env
docker compose up -d postgres
uv run python scripts/apply_migrations.py
```

Then run the CI-equivalent gates:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
uv run python scripts/generate_staff_console_manifest.py --check
npm --prefix runtime/openclaw/public-cell/plugin ci
npm --prefix runtime/openclaw/public-cell/plugin run build
npm --prefix runtime/openclaw/public-cell/plugin test
git diff --check
```

Do not report success if PostgreSQL-backed tests skipped. Use a fresh disposable PostgreSQL database
for an isolated full-suite run; do not aim tests at the shop database. `docker compose down` stops
this development database while preserving its named volume.

The release supply-chain check
`npm --prefix runtime/openclaw/public-cell/plugin audit --audit-level=high` is expected to fail on the
recorded OpenClaw advisories above. Report its current result, but do not waive it or call the public
runtime release-ready. The ordinary plugin build/test passing does not supersede that blocker.

## 6. How a Codex agent resumes safely

Use this prompt after opening the cloned folder in Codex:

```text
Read AGENTS.md and BUILD_ENGINEERING_SPEC.md completely. Treat specs/contracts and specs/evals as
normative. Read delivery/LOOP_STATE.yaml, run check_context_drift.py and run_delivery_loop.py, then
assemble the selected task's declared context packet. Do not enable public channels, model calls or
automated sends. Use only synthetic data. Verify every change with targeted tests and the task's full
acceptance gates. Report the requirement touched, tests run, rollback impact and unresolved blockers.
Do not mark any capability or launch gate passed without recorded evidence.
```

For new-machine validation specifically, give the agent
`context/tasks/TASK-cross-machine-bootstrap-001.md`. That packet is intentionally outside the product
delivery queue: validating a clone must not rewrite delivery history.

## 7. Real-shop continuation on the chosen Mac only

The current Mac has already completed checkout relocation and loopback hostname configuration. Its
remaining blockers are host-local and therefore absent from GitHub:

1. attach a different physical disk and set
   `R1_LOCAL_ARCHIVE_PATH=/Volumes/<drive>/laundry-archive`;
2. trust `/Users/danghuyhoang/laundry/.shop/ca/ca.crt` in the macOS System keychain;
3. pass `uv run python scripts/preflight_shop_till.py` with zero blockers;
4. recreate the shop containers from the relocated checkout—do not `docker start` containers whose
   bind mounts name the old path;
5. verify database roles/grants, migrations, closed capability flags and TLS;
6. execute and time the backup/restore drill;
7. complete one owner-approved transaction from ticket through `COMPLETED`.

No other machine may infer or reconstruct `.shop/` secrets. A replacement machine receives data only
through the documented encrypted restore process and owner-controlled backup identity.

## 8. Handoff report required from every machine

Report all of the following:

- commit SHA and whether the worktree was clean;
- operating system, architecture, Docker version and Python version;
- commands run with pass/fail counts;
- whether the run used synthetic or real data;
- UI pages and roles exercised;
- container health and unexpected-error log scan;
- requirement/contract touched;
- rollback impact;
- unresolved assumptions or external blockers;
- confirmation that all public/AI capabilities remained `NOT_AUTHORIZED`.

Do not include passwords, tokens, DSNs, certificate private keys, customer information or raw order
exports in the report.
