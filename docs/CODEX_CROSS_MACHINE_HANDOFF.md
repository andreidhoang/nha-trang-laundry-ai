# Codex and engineer cross-machine handoff

**Reconciled:** 2026-10-22 (Asia/Ho_Chi_Minh). Start with
[the Windows deployment handoff](WINDOWS_ENGINEER_HANDOFF_2026-10-22.md). Windows supersedes the
Mac as intended shop host; target deployment is blocked. Sections below retain historical local
setup/evidence and must not be mistaken for a tested Windows production procedure.

**Repository:** `https://github.com/andreidhoang/nha-trang-laundry-ai.git`

**Application/handoff branch:** `codex/engineer-setup`; `main` does not yet contain this handoff.

**Machine-readable authority:** `delivery/WORK_QUEUE.yaml`, `delivery/LOOP_STATE.yaml`,
`delivery/CAPABILITY_STATUS.yaml`

This is the entry point for an engineer or Codex agent opening the project on another machine. It
separates what GitHub can reproduce everywhere from the one shop installation that may hold real
orders and money.

## 1. Current engineering truth

- R1 is a deterministic, staff-only laundry operations console. It supports the reviewed workflows
  in `docs/CORE_BUSINESS_WORKFLOWS_V1.md`.
- Historical guarded verification (round 9, `82a66a9`) passed **4,962 tests with 5 skips**.
  Live synthetic verification passed the stubbed console suite 942/0, the daily-operations walk
  81/0 at desk and phone size, workflow conformance 870/0 (desk) and 869/0 (phone) with 212/212
  controls, and a 43-chapter, 1,220-check film. Re-measure; do not trust these numbers.
- Pricing, permission, SLA, order state, settlement and delivery decisions remain deterministic
  Python/PostgreSQL behavior. The browser and any model are not business authorities.
- All thirteen AI/public capabilities remain `NOT_AUTHORIZED`. A fresh clone must report that state;
  it is the correct day-one result, not a missing setup step.
- OpenClaw was retired (ADR-0009): there is one dependency tree, `uv.lock`, and no `npm` project.
  Do not look for an OpenClaw runtime or run `npm audit`.
- The shop Mac checkout is `/Users/danghuyhoang/laundry`. The earlier handoff recorded stopped
  real-shop containers and outstanding archive-disk/TLS-trust steps; this synthetic review did
  not revalidate those host prerequisites. Do not claim shop cutover is complete until the
  preflight, restore drill and one owner-approved counter transaction pass.

Re-measure instead of trusting these numbers:

```bash
uv run python scripts/check_context_drift.py
uv run python scripts/run_delivery_loop.py --format controller-json
uv run python scripts/report_delivery_status.py
git status --short
git rev-parse HEAD
```

**For the business owner (Vietnamese, non-technical):** `docs/GIOI_THIEU_CHO_CHU_TIEM_VI.html` - open it in a browser. It explains what the app solves, the default deterministic workflows and the daily routine. The optional local Gemini evaluation below is separate from the shop's default behavior. Keep the briefing in step with approved product workflows.

**AI models and providers:** read `docs/AI_MODEL_AND_PROVIDER_SETUP.md` before touching anything
model-related. The default application remains provider-free. The October 15 setup branch adds an
explicitly enabled, synthetic-only Gemini intent classifier for local evaluation; see
`docs/runbooks/local-gemini-evaluation.md`. Production model processing remains blocked by `DEC-006`
and the existing release gates. Live evaluation also requires an explicitly authorized budget.
For which model operates in which workflow, and what is built vs not wired, read `docs/AI_ROLE_MAP.md`.

## 2. Two modes that must not be confused

| Mode | May run on any engineer machine? | Data | Entry point |
|---|---:|---|---|
| Local engineering/demo | Yes | Synthetic only | `docs/runbooks/demo-stack.md` |
| Real shop till | Only after the selected Windows target passes its deployment checks | Real orders and settlement records | `docs/WINDOWS_ENGINEER_HANDOFF_2026-10-22.md`; Mac runbook retained as reference |

Git intentionally excludes `.shop/`, `.demo/`, `.env`, private keys, certificates and database
volumes. A GitHub clone is therefore safe to use for engineering and synthetic UI verification, but
it is **not** a copy of the live shop. Never solve that difference by committing, messaging or
copying live credentials through an AI chat.

Running multiple independent real-shop databases would create competing sources of truth. Other
machines should use the synthetic demo, or become an authorized replacement host through the
backup/restore runbook. They must not start a second production ledger from newly generated secrets.

## 3. Fresh machine bootstrap

Prerequisites: Git, Docker Desktop (or compatible Docker Compose), `uv`, and Node.js 24 for the
staff-console behaviour and syntax tests (pytest skips them without Node; CI uses 24). Clone outside Desktop, Documents, Downloads, OneDrive,
Dropbox or iCloud-synced directories.

```bash
git clone https://github.com/andreidhoang/nha-trang-laundry-ai.git ~/laundry
cd ~/laundry
git switch codex/engineer-setup
git switch -c codex/engineer-followup
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

On Windows, use the reviewed Linux environment described in the Windows handoff. Host startup,
scheduling, trust and archive mounts require actual adaptation and testing; changing shell syntax
alone is insufficient. Contracts, environment names and R1 trust boundaries remain unchanged.

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

Browser verification needs Google Chrome installed (the scripts default to the `chrome` channel), or
`uv run --with playwright playwright install chromium` plus `CONSOLE_BROWSER_PATH=<chromium>`. The
stack must be the demo stack above; the scripts default to `127.0.0.1:8100`, so pass the flags shown.
The demo console uses loopback port 8081; its database is not published to the host.

For the complete workflow check, arrange a temporary loopback connection to the **synthetic demo**
database and set `DEMO_DATABASE_URL` to its migration-role DSN, using the generated
`.demo/secrets/migration_database_url` credentials and the connection's host/port. The check runs
host-side policy and STOP-recording commands that need a direct database connection;
`--psql-container` alone does not cover them. Use the same demo's hash key for those commands as
the API uses. Do not print or commit the DSN or key. If material is outside `.demo`, set
`DEMO_MATERIAL_DIR` to that directory and replace the hash-key path in the command below.

```bash
uv run --with playwright python scripts/verify_console_interaction.py
uv run --with playwright python scripts/verify_daily_operations.py \
  --base-url http://localhost:8081 --idp-url http://localhost:8081/demo-idp
env -u NTL_HASH_KEY NTL_HASH_KEY_FILE="$PWD/.demo/secrets/hash_key" \
  uv run --with playwright python scripts/verify_workflow_conformance.py \
  --base-url http://localhost:8081 --idp-url http://localhost:8081/demo-idp \
  --store 11111111-2222-4333-8444-555555555555 \
  --database-url "${DEMO_DATABASE_URL:?set the synthetic demo migration DSN first}"
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
git diff --check
```

Do not report success if PostgreSQL-backed tests skipped. Use a fresh disposable PostgreSQL database
for an isolated full-suite run; do not aim tests at the shop database. `docker compose down` stops
this development database while preserving its named volume.

The public OpenClaw runtime and its npm plugin were retired by ADR-0009 (`OPENCLAW-RETIRE-001`); the
repository has one dependency tree, `uv.lock`. Its history is in `evidence/openclaw-retirement/`.

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

The earlier shop handoff records checkout relocation and loopback hostname configuration. Recheck
the following host-local prerequisites on the chosen Mac; the October 15 synthetic verification
does not establish their current completion:

1. verify a separate physical archive disk is attached and set
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

## 9. October 15 local review and optional Gemini evaluation

The `codex/engineer-setup` worktree preserves the earlier Mac fixes and adds bounded-input,
authentication dependency, browser recovery and assistant corrections. Read
[the dated verification report](audit/LOCAL_READINESS_2026-10-15.md) for requirements, exact
verification scope, evidence and remaining release blockers. Do not substitute the earlier green
baseline for the final results in that report.

The optional local staff classifier is documented in
[the Gemini evaluation runbook](runbooks/local-gemini-evaluation.md). It is off by default, accepts
synthetic questions only and cannot perform business actions. A provider key alone does not
authorize requests or establish a billing limit. This narrowly scoped local evaluation does not
change the public capability registry or approve production data processing. The user authorized
free-tier requests only, with no paid usage. A later authorized setup created the separate unbilled
`Laundry Local Free Evaluation` project and a dedicated key. Five live Gemini 3.5 Flash-Lite
workflows now pass end to end (74 browser checks and 19 persistence/invariant checks). The original
paid project/key was not used. See the [free-tier verification supplement](audit/GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md)
for the exact image, preserved failed runs, recording and remaining limits. All authorized
provider attempts are spent; restarting must not reset the ledger.

The separate Gemini evaluation is reachable at `http://localhost:8091/demo-idp/` on this Mac.
Sign in as the synthetic owner to inspect saved Gemini results or use deterministic questions.
New unsupported questions show the exhausted local-evaluation allowance. The provider-free demos
on ports 8081 and 8089 remain running. To resume the existing Gemini containers after Docker stops,
without rebuilding, deleting volumes, copying the key or resetting an allowance:

```bash
docker start laundry-gemini-free-20261015-postgres laundry-gemini-free-20261015-idp \
  laundry-gemini-free-20261015-google-relay laundry-gemini-free-20261015-api \
  laundry-gemini-free-20261015-gateway
```

Wait for `/readyz` on port 8091 to return 200. Private local configuration and persistent volumes
must remain in place; the current API image and credential are already held by these containers.
The credential-free image archive and exact image binding are in the ignored evidence supplement.


Use the frozen lockfile: PyJWT 2.15.0 is required for the API, worker and Tool Facade. The API image
also retains the patched SQLite library used by the persistent evaluation budget; rebuild it
rather than copying host Python results onto an older image. Preserve the budget ledger on
restart. No business database migration was added by this review.

The reviewed infrastructure uses PostgreSQL 16.15 and Caddy 2.11.7, with patched Go dependencies
in their helper binaries and the backup tools. Build the repository's Dockerfiles, including the
separate `postgres-runtime` and `postgres-backup` targets; an upstream tag alone is not the same
verified artifact. The release workflow now scans all six application/infrastructure images in
comprehensive mode for those dated results; the later workflow also includes Keycloak. A successful
Keycloak vulnerability scan was not retained, and exact target-architecture verification is a
deployment blocker. The dated report records exact image IDs, compatibility checks and limits.
Adopting these images on a real shop host still requires its backup, restore and cutover checks.

The original checkout and its unrelated edits remain separate. The existing synthetic demo on
port 8081 must remain available; verification stacks and their databases use separate project
names and volumes. Never run the destructive-state integration suite against the demo database.

On the reviewed Mac, the updated synthetic demo is also available at
`http://localhost:8089/demo-idp/`. Its private configuration is now under
`/Users/danghuyhoang/laundry-codex-setup/.demo/readiness-2026-10-15/runtime`, with the same existing
named volumes. Relocation preserved the 25 existing orders, 13 settlements and one saved assistant
turn; subsequent verification records are identified separately in the dated report. To resume
these already-built, already-initialized services on this Mac:

```bash
docker compose --project-name nha-trang-laundry-codex-verified-films \
  --file /Users/danghuyhoang/laundry-codex-setup/.demo/readiness-2026-10-15/runtime/compose.runtime.json \
  up -d --no-build --pull never --no-deps --wait --wait-timeout 120 \
  postgres demo-idp api worker tls
```

This machine-local command uses the recorded image IDs and existing secrets; it is not a fresh
machine bootstrap or a production deployment command. Keep the ignored runtime directory private.
The shareable evidence index is
`.demo/readiness-2026-10-15/evidence/README.md`; it excludes runtime configuration, credentials,
database connection strings and image archives. The dated report distinguishes recordings from
earlier images from the final account-switch and packaged-runtime checks.
