# Daily staff workflow review — October 26, 2026

## Scope and authority

Review baseline: `codex/engineer-setup`, commit
`713db633436029b7b4a04de58414d2a0af3253e2`. The engineering checkout was clean and
matched `origin/codex/engineer-setup` at the start. Existing demos and their data
are preserved. This is local synthetic verification on macOS, not Windows or
production acceptance.

Authority follows `BUILD_ENGINEERING_SPEC.md` §2: approved published policy and
immutable snapshots, machine-readable contracts, approved specifications, then
execution guidance. Read current decision records and completed work items when
older workflow prose describes features as missing. In particular,
`CORE_BUSINESS_WORKFLOWS_V1.md` contains historical gap/control counts that later
work superseded. Code and passing tests alone do not authorize a policy change.

The initial real product is the private staff console described in
`WINDOWS_ENGINEER_HANDOFF_2026-10-22.md`: quote, accept, intake, production,
payment, collection, receipt and cash reconciliation. Optional workflows are
tested where implemented. All 13 public/AI capabilities remain
`NOT_AUTHORIZED`. The Gemini request allowance is exhausted; no new provider
requests, billing, real customer data, public ingress or production activation
are authorized by this review.

## Execution plan

1. Preserve the checkout, inspect instructions/specifications, assemble the
   repository context packet and inventory first-party surfaces.
2. Run a fresh isolated PostgreSQL baseline and a separate synthetic application
   instance built from the pushed source. Keep existing demos running.
3. Trace daily workflows through browser, API, deterministic domain, persistence,
   event/audit and authorization. Independently review frontend, business/data,
   and AI/worker boundaries; reproduce findings before correcting them.
4. Fix demonstrated defects with meaningful regressions. Recheck affected paths,
   run the full database suite and rerun the affected checks after any later fixes.
5. Record desktop/phone workflows, inspect visual evidence and runtime diagnostics,
   and leave exact evidence and remaining deployment blockers here.

## Requirements-to-verification matrix

| Requirement | User workflow | Relevant implementation | Expected result | Verification method | Status |
|---|---|---|---|---|---|
| Named identity, MFA and store scope | Sign in, switch store, revoke session | API auth/session, staff repositories, console shell | Server enforces role/store/MFA; expiry and revocation are visible | Real API browser conformance and negative DB/API tests; production Keycloak separate | Local synthetic passed; real realm/device MFA unverified |
| Deterministic quote and acceptance | Walk-in ticket → service/weight → quote → agreement | Domain pricing/quote composition; quote/order-request repositories; new/quotes screens | Exact integer VND, 6 kg tier, published config, immutable revision and expiry | Daily browser walk, conformance, boundary/property/integration tests | Passed locally; worklist association corrected |
| Intake and production state | Accept bag, wash, ready, hold/resume | Order commands/state machine, intake/production repositories and screens | Valid transitions only; stale versions refused; clocks and custody preserved | Real browser journeys and state/persistence/concurrency tests | Passed locally |
| Payment, collection and cancellation | Deposit/part/full pay → handover; refund/cancel | Domain settlement/money, order payments, custody, remedies | No duplicate/overpayment; goods released only when paid; correct cash/refund ledger | Browser conformance and transaction/role tests | Passed locally; no real bank transaction |
| Delivery outcomes and trip costs | Pickup/return leg, failed attempt and recovery | Delivery-leg repository, deterministic fee policy, delivery sheets | Outcome truthful, retry safe, costs attributed once | Daily/conformance browser walks and targeted DB regressions | Passed after terminal-success correction |
| Cash, invoices, credit and reports | Close day, count drawer, issue invoice, inspect reports | Cash counts, invoices/accounts/report repositories and screens | Server figures match ledger; issued amounts stable; tab/window labels match rows | Browser conformance, delayed-response regression, persistence tests | Passed locally after invoice race correction |
| Customers and privacy | Find/create customer, consent, erase contact | Customer/retention repositories and customer screen | Store-scoped access, encrypted contact, consent and retention enforced | Browser conformance, API/DB negative tests | Passed locally with synthetic contacts; production privacy approval separate |
| Incidents, remedies and approvals | File complaint, request remedy, distinct approver decides | Incident/remedy/approval repositories and screens | Snapshot-bound approval; edit invalidates; authorized human actions only | Browser conformance plus approval/race regressions | Passed locally |
| Staff, settings, expenses and exports | Hire/assign/disable; publish settings; export approved window | Staff/config/expense/export repositories and screens | Owner boundaries, no orphan owner; sanitized one-use export; policy publication explicit | Browser conformance, contract and role tests | Passed locally; real owner acceptance separate |
| Offline, failures and mobile interaction | Type, navigate, refresh, lose network/session, retry | Console core, forms, service worker, CSS | No silent write retry, stale repaint or false success; actionable recovery | Stub browser failures plus real desktop/phone conformance | Passed sampled desktop/phone workflows after list recovery corrections |
| Bounded assistant/AI and workers | Governed assistant questions; disabled customer automation | Assistant/Gemini classifier, agent runner, Tool Facade, inbox/outbox | Deterministic facts/actions, validated enum/schema, bounded deadlines, fail closed | Deterministic mocked contract tests and runtime refusal checks; no new live model requests | Mock/disabled-path checks passed; no new live AI evidence |
| Operability and durable records | Readiness, audit, restart and recovery | API readiness, worker supervisor, migration/grants, recovery scripts | Consistent persisted state; failures observable; no demo disruption | Fresh migrations, real-role tests, browser refresh/restart and local monitoring | Local restart and TLS passed; Windows/off-host acceptance blocked |

## Evidence and limitations

Local logs, private runtime material and recordings live under the ignored
`.demo/daily-review-2026-10-26/`. Secrets and large recordings must not enter Git.
Inspection, mocked tests, real local integration and browser observations are
reported separately below. A finite review cannot establish that every feature is
free of bugs or that a Windows production deployment is ready.

## Corrected behavior

1. **A completed delivery leg cannot acquire another outcome or trip cost.** The
   repository checks for a previous success while holding the order lock, before
   creating another attempt. Same-key replay still returns the original result.
   Regression tests cover both leg kinds, failure/success attempts, cost/event/
   audit/outbox preservation and a concurrent lock waiter. Two new cases failed
   before the correction; all 13 delivery-leg cases pass afterward.
2. **Quote rows retain the correct ticket, customer and order link.** Previously
   the UI joined quotes against a separate, capped list of 100 recent intakes.
   Older quotes could lose their real association after newer unquoted intakes
   arrived. The server now projects same-store associations in its quote summary;
   the UI consumes one response. Tests cover more than 101 newer intakes,
   converted/unconverted quotes, store isolation and contact erasure. Orphan rows
   use an honest generic label rather than an invented ticket.
3. **Late responses cannot repaint the wrong invoice tab or list.** Each read has
   a generation guard. Superseded successes and failures cannot alter rows,
   metadata or hooks. A further regression exposed old rows reappearing when a
   user typed or changed scope after a failed/held reload. A reload now invalidates
   loaded data until the latest request succeeds; filters survive recovery while
   stale rows and counts remain unavailable. The final focused browser test moved
   from 66 passing / 8 failing to 74 passing / 0 failing.
4. **Agent tool authority closes on every terminal return.** The coordinator closes
   its bridge in `finally`, including adapter failures and output validation
   refusal. Three new regressions failed before and pass after. The bounded
   Responses adapter already closed its own bridge; this correction protects the
   shared coordinator contract. It does not activate a customer agent.
5. **Streamed oversized requests receive 413.** The size exception now carries the
   framework HTTP contract instead of being swallowed into a generic 400. Tests
   cover absent/understated lengths, the exact limit and multi-message streams.
6. **Verification waits for the operation it actually submitted.** Daily checks
   previously used fixed delays and could mistake an older POST for a later
   transition. They now match the exact order, endpoint and submitted intent, then
   wait for the corresponding DOM/version result. Database observation honors an
   explicitly supplied Docker client, fails with a sanitized error on query
   failure, and uses `ON_ERROR_STOP`. A scenario crash now increments the recorded
   failure count. An intentional verifier-only failure produced exit 1 and a
   visibly failed final banner; it is excluded from application acceptance.

The obsolete quote-intake-read disclosure was removed from the generated registry:
604 current entries, previously 605. Its exact-count guard was updated for that
specific deletion. No assertion was relaxed to hide an application failure. The
service-worker source digest was regenerated. No dependency upgrade or business
migration was added.

## Review coverage

The first-party review covered the staff console, API/auth/validation, deterministic
domain, PostgreSQL repositories/migrations/grants, worker/agent boundaries,
policy/contracts/evaluations, scripts, and deployment/identity/recovery
configuration. The inventory records 175 runtime Python modules, 2,221 Python
functions, 77 web files and 31 reachable frontend routes. The API source has 126
route decorators; the generated internal contract contains 127 method/path
operations. These counts describe inventory, not a claim that every function was
individually executed or inspected line by line. Generated files and third-party
code were excluded from manual implementation review unless relevant to a defect.

The daily traces followed browser control → request/session/store validation →
domain decision → transaction/event/audit/outbox → response → visible state →
refresh/database read. Broader conformance also covers approvals, customer
consent/erasure, accounts, invoices, hiring, exports, settings, expenses,
unclaimed goods, reminders, assistant refusal and session revocation. Disabled and
unfinished integrations remain explicitly disclosed, rather than returning fake
success. This is risk-based coverage of implemented workflows, not exhaustive
correctness or a penetration test.

## Reproducible verification

Environment: macOS, Python 3.12.11, Node 24.21.0, PostgreSQL 16.15 in isolated
Linux containers, `CI=true` for the full suite. Existing 8081/8089/8091 demos were
preserved. Candidate demos use 8093/8094 with separate synthetic databases and
provider/customer-automation flags disabled. Test-role credentials and runtime
secrets are in ignored local files only.

| Command / check | Result | Evidence / qualification |
|---|---|---|
| `uv run ruff check .` | Passed | Includes final verifier changes |
| `uv run ruff format --check .` | Passed | 820 Python files at execution |
| `uv run mypy apps packages` | Passed | 463 source files; new verifier helper also checked separately |
| `uv run python scripts/verify_contracts.py` | Passed | 21 JSON / 3 YAML contracts, 669 frozen combinations, 127 internal operations, 604 disclosures; eight release blockers remain |
| `uv run python scripts/check_context_drift.py` | Passed | Current references/decisions/capabilities checked |
| `CI=true uv run pytest --require-postgres-integration -q -ra` with isolated `DATABASE_URL` | 5,197 passed / 5 skipped / 0 failed | 4,211.53s pytest time; fresh migrations and grants; full-run source snapshot retained |
| Real-role suite: `uv run pytest --require-postgres-integration -q packages/db/tests/test_api_role_write_paths.py apps/worker/tests/test_worker_least_privilege.py` with API/worker DSNs | 63 passed / 0 skipped | Real distinct database roles, not owner-role substitution |
| Final verifier suites: `test_workflow_database.py`, `test_console_recording.py`, `test_conformance_coverage.py` with `--require-postgres-integration` | 12 passed | Includes four new database-observer regressions and real lock helpers |
| Final frontend suites: `test_staff_console_contract.py`, `test_staff_console_shell.py`, `test_staff_console_privacy.py`, `test_console_disclosure_contract.py` | 127 passed / 4 skipped | Current UI and disclosure source, Node 24 |
| `scripts/verify_console_interaction.py` through console contract tests | Broad run 1,032 / 0; final focused delta 74 / 0 | Broad run preceded the last list-recovery change; final targeted run covers that change; stub APIs, not real backend evidence |
| `scripts/verify_workflow_conformance.py --record` with isolated DB observation | Desktop 872 / 0; phone 871 / 0 | 43 scenarios each; 58 named WebMs each including secondary identity/device tabs |
| `scripts/verify_daily_operations.py --record` | Desktop 82 / 0; phone 82 / 0 | Final image, complete walk-in and pickup/return shop days |
| Real-API list recovery driver | 104 / 0 across four cases | Desktop/phone orders/quotes; controlled held GET and injected 503, real authenticated success/retry reads, zero business writes |
| Independent decoded QR checks | 28 / 0 across desktop and phone | Clean UI pixels match format, checksum, synthetic recipient, memo, amount and API payload; no bank transaction |
| PostgreSQL and candidate API restart | 82 / 82 business-table snapshots unchanged | 41 tables in each of two synthetic DBs, before/after both restarts; not off-host restore evidence |
| `scripts/staging_smoke.py` against `https://localhost:8453` with generated CA | Passed | Certificate verified; no insecure bypass; synthetic deployment only |
| Gitleaks 8.28.0 candidate source scan | Zero findings | Final staged source is scanned again before handoff; private runtime/large artifacts excluded |

The five full-suite skips are four empty disclosure parametrizations (no-route,
response-shape, read-only, no-writer) and the Linux-only hidden-file-inert variant
on a Mac with a BSD hidden-file flag. No required database, Node or age tests were
skipped. Two existing Starlette deprecation warnings remain.

Bounded monitoring retained 185 samples per original demo: 8081, 8089 and 8091
were ready in every sample. Candidate 8093 includes pre-start and planned restart
refusals. Candidate 8094 was ready in 129 of 130 samples; one four-second readiness
timeout during concurrent verification has no established root cause. Later
samples and final readiness were healthy. Final candidate API/worker container
logs have no traceback, ASGI exception or HTTP 500 lines in their current
incarnations. See `evidence/handoff/2026-10-26/monitoring.json`; this does not
establish a production availability or latency SLO. Observation processes stop at
handoff while all demos remain running.

**Source timing matters:** the full run began before later verifier-only edits and
the last shared-list recovery correction. The affected suites above were rerun
on current source. Comparing the final collection with the full and affected
JUnit files covers **5,206 / 5,206 current test IDs: 5,201 unique passed, five
skipped, zero failed or missing**. This is a union of runs, not one immutable
full-suite run on the last tree. The exact source snapshots and JUnit hashes are
in the portable summary. A clean Windows run is still required.

## Browser and recording evidence

Portable results and per-workflow recording index:

- `evidence/handoff/2026-10-26/summary.json`
- `evidence/handoff/2026-10-26/workflow-recordings.json`
- `evidence/handoff/2026-10-26/application-source-binding.json`
- `evidence/handoff/2026-10-26/monitoring.json`

The index records local paths, SHA-256, duration, workflow, result, source variant
and availability. **Large videos, raw logs and private runtime files are not in
GitHub.** They remain on the engineering Mac under the ignored directory; another
engineer must rerun capture or arrange an authorized artifact transfer. The source
push alone does not make a local recording accessible on Windows.

| Workflow evidence | Local artifact directory | Result / source |
|---|---|---|
| All 43 implemented conformance scenarios, separate recordings | `final/conformance-desktop/`, `final/conformance-phone/` | 872 / 0 and 871 / 0, API image A below |
| Walk-in → quote → intake → wash → ready → cash → collection → refresh | `final/daily-desktop/`, `final/daily-phone/` (`walk-in.webm` and originals) | Final image B, 128,000 VND paid = settled, completed/released/paid |
| Pickup → processing → payment → failed return → successful return | Same directories (`delivery.webm` and originals) | Final image B, 170,000 VND paid = settled; one successful pickup, one failed return, one successful return |
| Delayed/failed reads, filter/scope, retry and refresh | `real-api-list-recovery-verified/` | Four current-image recordings, 104 passing checks; injected read failure identified explicitly |
| QR and cash-count refresh | `clean-qr-cash-centered-20261026/clean-videos/` | Three current-image read-only recordings; original capture limits preserved |
| Verifier failure reporting | `final/expected-verifier-failure/` | Deliberate exception, exit 1, visible 8 passed / 1 failed; excluded from acceptance |

Paths in this table are relative to `.demo/daily-review-2026-10-26/`.
Daily focused clips are stream-copy excerpts, not additional executions; keyframe
padding retains adjacent preconditions/negative checks. Original recordings and
timing metadata remain intact.

The earlier core run used API **A**
`sha256:4d1e87f65072c7fe92f72df33065e5d993ead7330b1163af41009dbb0ad9b3a7`.
The final daily, recovery and clean QR captures used API **B**
`sha256:9cea2955cfbde327e4fb7cb07b061d3f87903237b27ce15f2d12c3cf48931f49`.
The intervening runtime change is the final shared-list recovery patch and its
generated service-worker digest. Worker image
`sha256:e22cdf9187804a2c69c617d4ca749072ca4027f07cb71b4af0978557aa7021ff`
is unchanged. Final source comparison checks 305 API and 148 worker files with
zero packaged-source mismatches. Earlier recordings are not relabeled as image B.

Every retained conformance video was sampled at timestamped frames (126 desktop
and 116 phone frames), with all 43 final screenshots per viewport inspected.
Independent reviewers also sampled all final daily originals/excerpts and the
four latest recovery clips. The root engineer inspected representative frames,
including unobscured completed payments and the deliberately failed verifier
banner. No new material visible defect was observed in those samples. This is
timestamped-frame review, **not continuous playback of every recording**.

The initial QR verifier skipped pixel decode because its decoder was unavailable;
that historical skip remains a skip. A later artifact-only, offline decoder
checked clean, centered UI screenshots and passed 28 checks. The cash count
retains a historical 10,000 VND shortage against its original 3,486,000 basis while
showing the later current expectation of 3,476,000. Refresh did not silently
rewrite the historical basis. No payment or new business write was performed for
this read-only follow-up.

## AI, remaining limits and deployment handoff

The staff assistant's deterministic facts, schemas, enum classifier boundary,
redaction, refusal, timeout and tool lifecycle are covered by tests. New local
agent evidence is `evidence/agent-shadow/local-synthetic-suite-v7.json`:
32 SKIP / `DETERMINISTIC_DEGRADED`, provider false, release effect NONE. Versions
1–6 are preserved byte-for-byte. This evidence is not a successful live model run.
All 16 previously authorized Gemini attempts remain exhausted; **no new live
model/provider requests were made**, and production privacy/provider evidence
remains outstanding. All 13 capabilities stay `NOT_AUTHORIZED`.

Known limits and next actions:

- **Windows host acceptance:** inventory OS/architecture and choose the supported
  Linux-container path (WSL2/Docker Desktop or a Linux VM). Verify startup/login/
  shutdown, operating schedules, browser trust/private exposure and real realm
  MFA/roles on that machine. This Mac provides none of that target evidence.
- **Recovery and operations:** attach the separate encrypted archive, keep its
  recovery key off the host, perform a timed target restore, verify RPO/RTO and
  delivered alerts. Same-host restart snapshots do not replace these checks.
- **Supply chain:** run the release gate on the exact target app, database, proxy
  and Keycloak images and review fresh blocking vulnerability evidence. This run
  provides contracts and a secret scan, not a complete current image security
  assessment.
- **Real integrations and owner acceptance:** verify the actual printer, physical
  phone/browser, approved bank recipient/reconciliation, owner policies and a
  supervised counter transaction. Synthetic QR correctness does not prove bank
  settlement. No real send/channel/provider was activated.
- **Generic HTTP streaming edge:** a contrived ASGI handler that starts its
  response and only then consumes an oversized request cannot change already-sent
  headers to 413. The shipped daily routes have no reproduced instance. If a
  streaming-body endpoint is added, test consumption before response start; do
  not treat the current request-size tests as proof for every possible handler.
- **Coverage:** native date controls follow the test browser locale; check the
  Windows Vietnamese locale and physical printing. Phone-sized Chromium is not
  an actual mobile device. Performance/SLO/load testing, every-frame visual
  review, third-party outages and exhaustive security are not established here.
  Reproduce and investigate the isolated four-second readiness timeout under a
  measured target workload before setting availability/latency acceptance.

No schema migration is required for these corrections. Rollback must preserve
data/archives and must not restore known terminal-delivery, stale-read or recovery
false-success defects. The current Windows handoff and prompt remain the entry
points for target work. Safe local fixes/tests are complete; production activation
and missing external acceptance remain blocked.
