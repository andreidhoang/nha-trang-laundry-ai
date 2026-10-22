# Local application readiness verification

This review prepares `codex/engineer-setup` for a reproducible local demonstration and assesses
remaining release blockers. A passing local run does not authorize a production deployment.

## Authority and assumptions

- `AGENTS.md`, `BUILD_ENGINEERING_SPEC.md`, the Vietnamese specifications and machine-readable
  contracts govern application behavior. Later owner decisions supersede older prose. In particular,
  `DEC-034`–`DEC-052` and the round 7–9 specifications extend the older core-workflow description.
- The October 15 user instruction authorizes local corrections and Gemini implementation and
  verification with synthetic data. It does not approve real customer data transfer, public
  automation, production deployment, new commercial terms, or an unbounded provider budget.
- Public capabilities and the existing release registry remain closed. `DEC-006` remains unresolved
  for production data handling. The key supplied in conversation has not been validated or adopted
  as a production credential; rotate it before a real deployment. Secret values must never enter
  this file or repository.
- The useful initial Gemini scope is classification of a redacted staff question into a closed set
  of supported intents. Existing server code supplies facts, calculations, refusals and links.
  The model receives no database tool, business record, conversation history or write authority.
- Preserve the original checkout's nine uncommitted files and the setup branch's ten existing
  modified files. Keep the user's five-container synthetic demo on port 8081 running.
- Test accounts, published policy fixtures and database clock manipulations belong only to an
  isolated synthetic stack. They are not owner-approved production configuration.
- `docs/CORE_BUSINESS_WORKFLOWS_V1.md` explicitly describes observed implementation and contains
  older statements superseded by later decisions. Use its workflow map as evidence of behavior,
  not as authority to override the Vietnamese specifications or newer owner decisions. The
  original checkout's proposed sales-agent documents are preserved and are not silently adopted.

## Execution plan

1. Inventory first-party modules, functions, screens, endpoints, migrations, AI seams and operational
   configuration. Distinguish structural inventory, manual inspection and runtime verification.
2. Reproduce defects and fix their root causes. First repair assistant idempotency before connecting
   a bounded provider adapter; retain deterministic policy and authorization.
3. Run targeted regression tests, then the repository gates and PostgreSQL integration suites on
   isolated databases. Exercise both native desktop and mobile browser behavior.
4. Record each core workflow separately on the actual synthetic application. Review each recording
   or timestamped frames, correlate requests/logs/persistence, and replace any failing recording.
5. Reconcile evidence, limitations and setup instructions; verify preservation and demo health.

## Requirements to verification matrix

| Requirement | User workflow | Relevant implementation | Expected result | Verification method | Status |
|---|---|---|---|---|---|
| Identity and authorization | Sign in, role boundaries, session revocation | API auth, identity repositories, console RBAC | Store and role access enforced server-side | API negative tests and browser journeys | Recorded synthetic roles/session workflows passed; full suite passed |
| Intake and pricing | Walk-in or returning customer to quote and order | Counter, customers, quote composition, order repositories | Exact published prices and immutable acceptance | Domain/DB tests, recorded workflow | Recorded pricing, intake and two-counter workflows passed; full suite passed |
| Production and custody | Receive, wash, QC, collect or deliver | Order steps, promises, delivery legs | Valid transitions and accurate custody | Race/refusal tests, refresh and database proof | Desktop/mobile lifecycles and restart persistence passed; full suite passed |
| Money and accounts | Deposits, balance, refunds, account payment | Payments, settlement, accounts, invoices | Correct ledger and one logical effect on retry | DB integration and recorded workflows | Recorded money, deposit, account and export workflows passed; full suite passed |
| Exceptions | Cancel, rewash, compensation, storage and disposal | Incidents, remedies, unclaimed goods | Authority, fee and custody rules preserved | Boundary/negative tests, recorded recovery | Recorded cancellation, remedy, rewash and unclaimed-goods workflows passed; full suite passed |
| Messaging | Review, approve, attest manual send | Consent, shadow review, approvals, outbox | Suppression and exact-content approval enforced | API and browser negative paths | Synthetic consent/manual-send boundaries passed; actual channel delivery unverified |
| Reporting | Summary, expenses, cash count, export | Governed SQL projections and templates | Figures reconcile with persistent transactions | DB assertions and browser inspection | Recorded reporting/export/cash-count workflows passed; full suite passed |
| Gemini assistant | Vietnamese operational question | Assistant service and provider classifier | Bounded classification; deterministic factual answer | Mock contracts, live synthetic evaluation | Five live cases passed on a dedicated unbilled project; see the free-tier supplement |
| AI failure handling | Retry, timeout, malformed response, injection | Idempotency, strict schema, provider limits | No duplicate provider invocation on replay; safe fallback | Concurrent and adversarial regression tests | Contract fixtures passed; live retry/conflict/quota/recovery and five semantic cases passed; broader provider quality unverified |
| Operations | Start, migrate, monitor, restore | Compose, SQL migrations, backup and monitoring scripts | Reproducible startup and recovery | Fresh synthetic stack, compatibility tests and full suite | 70 migrations, packaged startup, local archive compatibility and restart passed; full suite passed; host restore unverified |
| Interface quality | Desktop/mobile core workflows | Staff console, browser verification scripts | Controls, feedback and persistence work | Separate recordings and visual review | 47 workflows passed; 63 role/tab films reviewed through timestamped frames; broad browser gate passed 874 checks and all 212 controls |

## Baseline and evidence locations

Branch starts this review at `e97e8bc26646013ae101aa5bde20d991a08043be` with ten preserved local
modifications from the previous Mac repair. Baseline snapshots and subsequent private evidence:
`/var/folders/jk/23y_gcc56qg_43vr0br4l82h0000gn/T/laundry-readiness-u4yfwgto`.

The immediately preceding run's `.demo/mac-fixes-verification-report.json` records 4,979 passed,
five expected skips, 63 additional least-privilege tests, 942 stubbed browser assertions, 81 live
daily-operation assertions and 871 conformance assertions covering 212 controls. These are
**baseline results**, not proof of changes in this review. It contains no workflow videos.

The durable, ignored artifact bundle is `.demo/readiness-2026-10-15/evidence`. The
[workflow evidence index](LOCAL_READINESS_WORKFLOW_EVIDENCE_2026-10-15.md) links each of the 47
selected workflows, its result, recording and timestamped visual review. The selected runs contain
1,430 passing assertions and no failures; repeated navigation assertions are not unique test cases.
Two separate missing-key AI recordings verify truthful fallback against the actual API/database.

The updated synthetic demo remains available at `http://localhost:8089/demo-idp/`; the original
demo remains on port 8081. Restarting the updated API and PostgreSQL preserved all 25 orders,
13 settlements and one saved assistant turn, with matching data hashes and a successful fresh
authenticated browser read. Neither restart used a reset or a new volume.
The final demo configuration lives in the ignored private directory
`.demo/readiness-2026-10-15/runtime`; the exact resume command is in section 9 of the
[cross-machine handoff](../CODEX_CROSS_MACHINE_HANDOFF.md). Relocation checked each bind path,
recreated the gateway when Compose retained its old path, and rechecked the same data hashes.
The initial failed path check is retained separately from the successful relocation proof.
After the final browser checks the updated demo contains 26 orders, 13 settlements and two saved
assistant turns: exactly one new synthetic order and one new assistant turn were added, and all
pre-existing rows remained unchanged. A fresh authenticated browser read passed. The temporary
database relay was stopped; the five demo services remain running.

## Corrected behavior to preserve

- Assistant role and current store membership are checked before provider selection. Governed reads
  and the brain run inside the winning idempotency callback; completed replays and conflicting keys
  cannot spend another provider call. A provider effect cannot be rolled back with the business
  transaction, so a separate persistent attempt ledger bounds failures and uncertain outcomes.
- The default-off local Gemini adapter accepts only a strict intent enum. Existing server templates
  supply all facts and links. Demo identity configuration, subject, MFA, store and role checks limit
  selection. The provider receives one sanitized synthetic question and no business records, history
  or tools. The console preserves classification/fallback provenance after refresh.
- Final boundary review reproduced exponential backtracking in the phone-number redactor with a
  long digit sequence followed by a letter. This occurred before the provider's transport deadline.
  A single-pass phone scanner and a raw-input limit remove that path; both raw and sanitized
  provider questions are limited to 600 characters. The regression uses a separately bounded
  process so a reintroduced hang fails the test rather than hanging the suite. Existing recognized
  deterministic questions still bypass the provider.
- Browser request deadlines cover response-body consumption as well as headers. A stalled or
  truncated response now produces a usable timeout/network error, without automatic write retries.
  Signing out still prevents late responses from reaching the old session.
- A shared-device account switch after session expiry now clears the previous person's open
  dialogs, receipt offers, incident handoffs and pending responses before exposing the new
  identity. The remembered identity survives an ended or temporarily unreachable session;
  signing back in as the same person preserves unfinished work. Generation checks also prevent
  stale session or store-membership replies from overwriting a newer login. Nine regression
  cases and actual offline browser reproductions cover these boundaries. The final-image
  real-API check passed 19 assertions across separate late-header and late-body recordings,
  preserved existing business rows and matched the new user's visible home total to the server.
- Malformed URL escapes resolve to the unknown-route screen. Browser verification now fails on
  uncaught JavaScript errors in every role context and records secondary contexts separately.
- The assistant disclosure describes optional intent classification and saved-answer streaming.
  Its contract exercises the actual injected API factory with the feature both enabled and
  disabled, rejects provider-authored facts, preserves monetary refusals and reconstructs the
  saved SSE answer while regeneration is forbidden. Signed-out screens explain how staff can
  recover without exposing token, cookie or HTML configuration instructions.
- A later controlled browser run passed 973 checks and failed one obsolete wording assertion:
  it required the phrase “cần máy chủ” after that technical copy had been simplified. The actual
  expiry reason, sign-in route and successful session recovery were present. The corrected
  verifier preserves its route/expiry checks and additionally requires visible sign-in and
  enabled recheck controls, then performs the recovery. This follows the UX/refusal requirements;
  the before/after DOM proof and failed run are retained. Seven dependent tests and the corrected 974-check full controlled browser run pass. This
  verifier-only change does not alter the packaged application or its recorded behavior.
- Browser verification waits for completed report and vehicle-rule rendering instead of assuming
  fixed delays. The busy-payment scenario explicitly owns its database row lock until refusal and
  no-write assertions finish. A regression holds the lock beyond the old twelve-second timer and
  verifies release after success and an interrupted browser action. Product assertions remain
  unchanged; slow loading and lock-expiry failures are retained as earlier evidence.
- The long integration run exposed two approval tests whose fifteen-minute fixture lifetime began
  during module collection. They now timestamp their direct rows inside the test. An aged-module
  reproduction failed before the correction and passed afterward; all nineteen decision-time
  tests pass independently. Runtime expiration and unknown-resource refusal checks are unchanged.
- Visual review found a verifier navigating to nonexistent `#/today` and a list-bound check that
  could accept a server error. The verifier now uses the registered home route and requires HTTP
  200 at valid bounds. Related refusal checks require their specific status and reason rather
  than any error; quote acceptance requires actual success rather than a missing default value.
- The live-Gemini verifier guards its storage initializer by the intended origin. Browser
  `about:blank` contexts cannot access local storage; a regression preserves that boundary before
  any navigation or provider request.
- Non-ASCII CSRF tokens and public locators are refused before constant-time string comparison.
  Tool Facade malformed JSON, invalid UTF-8, excessive integer/depth values and non-ASCII version headers produce controlled
  validation/authorization responses instead of server errors, after the existing authentication gate.
  Both JSON decoding and recursive contract validation are guarded before bearer admission; the
  same bearer remains usable for a subsequent valid request after invalid input is refused.
- Both Tool Facade database factories use the shared connection, statement, lock and idle timeout
  defaults. Explicit injected factories remain available for tests.
- Redaction and durable evidence guards recognize Google credential forms. Credential-shaped JSON
  field names are hashed even when their text also contains a sensitive-field marker; their raw
  contents must never survive merely because the value was redacted.
- Tracing no longer lets the OpenTelemetry SDK automatically serialize exception messages and
  stack traces outside the application redactor. A real in-memory recording SDK regression proves
  that nested spans retain a fixed error status and trace correlation, while the original
  exception still reaches its caller. The current demo has no exporter; this closes a latent
  disclosure path before an exporter is enabled.
- A correctly signed identity token with an out-of-range negative issue timestamp now receives
  HTTP 401 before database access or session-cookie creation. Previously, platform timestamp
  conversion raised an uncaught overflow. Real RS256/HTTP regressions cover integer, float and
  calendar-range cases; no unauthorized session was demonstrated.
- Internal workers recheck authority after a claim and before invoking a handler. An exhausted
  retry now reports `DEAD` and increments the dead-letter metric, matching the repository result.
  The injectable agent pipeline carries the supervisor's authority check through the runner,
  tool bridge and ledger/draft/failure writes; revoked terminal writes roll back. The 206-test
  worker suite passed on PostgreSQL. These are currently unwired, disabled execution paths, and
  the checks cannot forcibly terminate an arbitrary callback already running outside them.
- The API image now retains the SQLite extension and a patched runtime library required by the
  local attempt ledger. A build-time application import and SQLite query catch the actual missing
  binary dependency that host Python tests did not detect. Worker and Tool Facade images continue
  to omit SQLite.
- PyJWT is constrained to `>=2.15.0,<3` in the API, worker and Tool Facade and locked at 2.15.0.
  The image scan found published critical/high advisories in 2.13.0; the later 2.15 fix also covers
  a deeply nested token payload failing before verification. All other 67 locked package versions
  are unchanged. Strict JWS parsing now rejects noncanonical signature aliases before a session
  can be created. The single-sign-in contract still uses a stable signing-input digest; its test
  no longer requires an invalid token encoding to be accepted.
- Infrastructure now pins PostgreSQL 16.15 and Caddy 2.11.7. The unchanged `gosu` release and
  Caddy entrypoint are rebuilt with patched Go dependencies; the backup target uses patched age
  and rclone binaries. Runtime privilege separation, the PostgreSQL 16 data layout and backup
  encryption format are retained. CI builds the same patched database target, and the release
  scan covers both database variants and the gateway as well as the three application images.
  Comprehensive detection includes bundled dependencies omitted by the scanner's default mode.

The API image adds `sqlite-libs>=3.53.4-r0`, and the focused Python lockfile change pins PyJWT
2.15.0. No database migration is required. Earlier Mac restore,
monitoring, demo-role and browser modifier-key fixes remain part of the preserved local baseline.

## Review coverage and workflow traces

The refreshed structural inventory contains 237 Python implementation modules, 3,046 named functions,
130 endpoint declarations, 70 migrations, 77 SQL function declarations and 133 trigger
declarations. The frontend inventory contains 66 first-party modules, 1,939 functions including
callbacks, 31 registered routes and 212 declared interactive controls; its generated service worker
is excluded from the first-party function count. Current-source deduplicated Python manual review
records 75 modules or selected ranges: 65 complete modules, 10 partial modules, 661 fully read
functions and 17 partially read functions. The remaining 2,368 functions have structural inventory
only. Historical snapshots and overlapping review notes are retained without adding their counts
or transferring approval to changed source.

The trace records cover authentication, pricing and quote acceptance, payment and settlement,
order state and approval, consent and the internal queue, the fixed Tool Facade, worker lease
recovery and the staff assistant. Browser scenarios follow the visible controls through actual
API requests, database assertions and refreshed screens. Runtime outcomes are recorded separately
from source inspection. All 70 migration files, totaling 7,181 lines, also received a complete
semantic read, including 77 function definition versions, 133 trigger declarations and six
anonymous migration blocks. Those counts include historical replacements and two temporary
backfill functions later dropped; they are not counts of live schema objects. The SQL review made
no database changes and does not independently prove concurrency or authorization properties.
Most Python/JavaScript functions still have structural rather than complete manual review;
dynamic calls and per-function runtime coverage are not exhaustively measured.

The synthetic demo's worker cycles remain disabled by configuration. PostgreSQL tests exercise
worker authorization, leases and repository behavior; queue recordings show pending work and
closed execution boundaries. They do not establish live customer-message delivery or enabled
provider/background execution.

## Earlier broad verification results

These results precede the final Gemini model/deadline correction. The
[current Free-tier supplement](GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md) records the final
5,127-test regression pass, 63 real-role tests and live Gemini/browser evidence. The table below
retains the earlier runs with their original source and image bindings.

| Check | Result | Scope and limit |
|---|---|---|
| Static and repository gates | 8 passed after final source freeze | Lint, formatting, types, contracts, context, delivery report, console manifest and diff checks |
| Full Python/PostgreSQL suite | 5,116 passed, 0 failed, 5 expected skips | Fresh PostgreSQL 16.15 database; no skipped database integrations; 1,892.13 seconds |
| Separate API/worker database-role suite | 63 passed, 0 failed, 0 skipped | Actual separately provisioned API/worker roles; sequential isolated-database run, 33.10 seconds |
| Browser fixture/transport checks | 974 passed, 0 failed | Controlled browser fixtures, including real HTTP response-body deadline tests and expanded account-switch assertions |
| Broad browser control gate | 874 passed, 0 failed; 212/212 controls | Actual synthetic API/database and two successful rendered-QR decodes; exact earlier image binding retained |
| Selected workflow recordings | 47 workflows, 1,430 passing checks, 63 films | 45 desktop and 2 mobile workflows; every film reviewed through timestamped samples |
| Final-image account-switch recordings | 19 passed, 0 failed; 2 additional films | Same-person draft recovery, distinct-person cleanup, actual delayed 200 responses rejected, correct home total and unchanged business rows |
| Final-image navigation and session recordings | 54 passed, 0 failed; 4 additional films | Offline recovery, navigation, device revocation and role boundaries; one new synthetic order, existing rows unchanged |
| Final-image default assistant | 22 passed, 0 failed | Actual browser/API and persistence evidence, no extra film or provider call; one new saved turn, earlier rows unchanged |
| Final-image missing-key AI workflow | 24 passed, 0 failed; 2 additional films | Actual API/database/browser on accepted image `25e8858…`; all 312 source files match, zero provider attempts, unchanged business state and auditor refused |
| Final packaged runtime checks | Passed with network disabled | All three images suppress raw exception tracing; API rejects malformed authentic timestamps, validates injected Gemini output and permits exactly 5 of 20 concurrent budget reservations; restart cannot raise the cap |
| Vulnerability scans | 6 exact arm64 images passed; 0 HIGH/CRITICAL findings | Comprehensive detection against the recorded database; not an exhaustive security assurance |
| API/database restart and durable relocation | Passed | 25 orders, 13 settlements and one assistant turn preserved; same named volumes and durable private configuration on port 8089 |
| Live Gemini | Subsequently passed on an isolated stack | Five cases, 74 browser checks and 19 DB/invariant checks; see [current supplement](GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md). Earlier blocked/failed evidence remains historical |

The workflow index links the logs, reports, source/image bindings and sampled-frame review notes.
The final image checks are recorded in `final-image-verification.json`, the application source
comparison in `scan-observability-final/source-runtime-delta.json`, and the six packaged contract
checks in `scan-observability-final/packaged-contract-results.json` inside the evidence bundle.
All 312 packaged first-party API files match the frozen host source. Seven files differ from the
earlier broad browser-gate image: the Gemini adapter, authentication, API wiring comments, browser
application/session files, generated service worker and tracing. The later focused checks verify
the affected paths; they do not relabel the earlier 874-check run as a final-image run.
The ignored artifact directory also contains a standalone `README.md`, a portable workflow
manifest and a path map. Original absolute paths remain historical provenance; portable links
resolve the included copies and explicitly label private or excluded references as unavailable.
Pytest XML includes credential-shaped parameter names from redaction fixtures. Such reports
are exported only as clearly marked sanitized copies with unchanged test/failure/skip counts
and the original file hashes; the originals remain private and unchanged.
The final-image navigation/session/default-assistant checks total 95 passing assertions and six
additional films. A separate final-image missing-key AI run adds 24 passing assertions and two
role films, with eight timestamped frames independently reviewed by the lead agent. Its three
new assistant turns correlate one-to-one with domain, audit, outbox and idempotency records;
business rows remain unchanged and no provider attempt occurred. Runtime log
review found no unclassified errors or tracebacks. An earlier account-switch attempt used an
operations-only total oracle against the auditor and was retained as a failed verifier attempt;
the auditor correctly received a permission notice and HTTP 403. The successful replacement uses
a distinct operations user for the total assertion without weakening the privacy checks.

## Verification environment and reproducibility

Use Node 24, Python 3.12 and the repository's locked workspace. This Mac used uv 0.8.17 from
`/Users/danghuyhoang/.local/bin/uv`; an older Conda uv precedes it on some shell paths. Sync with
`uv sync --frozen --all-packages --all-groups` to preserve the lockfile. Run:

```text
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy apps packages
uv run --no-sync python scripts/verify_contracts.py
uv run --no-sync python scripts/check_context_drift.py
uv run --no-sync python scripts/report_delivery_status.py
uv run --no-sync python scripts/generate_staff_console_manifest.py --check
git diff --check
CI=true uv run --no-sync pytest --require-postgres-integration -q -ra
uv run --no-sync pytest --require-postgres-integration -q packages/db/tests/test_api_role_write_paths.py apps/worker/tests/test_worker_least_privilege.py
```

The final run invoked `.venv/bin/python -m pytest --require-postgres-integration -q -ra
--junitxml=<private-evidence>/integration-tests.xml` with `CI=true` and Node 24 first on `PATH`.
The role run invoked `.venv/bin/python -m pytest --require-postgres-integration -q
packages/db/tests/test_api_role_write_paths.py apps/worker/tests/test_worker_least_privilege.py
--junitxml=<private-evidence>/role-tests.xml`. Both exited 0. The five expected full-suite skips
were four empty disclosure parameter sets and one platform-specific hidden-file-flag test;
no PostgreSQL integration was skipped. The two existing Starlette warnings remain nonblocking.
Only documentation changed after this run started; `final-suite-end-binding.json` records that
comparison. Application, test, contract, build and lockfile hashes stayed unchanged.

For the last two commands, load `DATABASE_URL` from private local configuration for a disposable
PostgreSQL 16 test database. Apply all migrations first. The final command additionally requires
the separately provisioned `LAUNDRY_API_DATABASE_URL` and `LAUNDRY_WORKER_DATABASE_URL`. Run these
suites sequentially against a shared test database; the tests deliberately manipulate synthetic
state. Never point them at the owner's demo or a real shop database.

Browser verification uses `scripts/verify_console_interaction.py` for controlled fixtures,
`scripts/verify_daily_operations.py` and `scripts/verify_workflow_conformance.py` for the real
synthetic API/database, and `scripts/verify_consent_walk.py` for consent/manual-send boundaries.
Use their `--base-url`, `--idp-url`, store and database options for isolated stacks; pass `--video`
and `--slow-mo` to capture evidence. The full conformance run must omit `--only` to enforce all
212 controls. Separate `--only` runs create focused workflow recordings. Set
`CONSOLE_VIEWPORT=phone` for the 390-by-844 mobile checks. Signing material and `NTL_HASH_KEY_FILE`
must match the target synthetic stack; do not put their contents into commands or evidence.

For pixel-level QR decoding, include `--with pillow==12.2.0 --with zxing-cpp==3.1.1` alongside
`--with playwright` in the isolated `uv run` verifier environment. The repository lockfile is
unchanged by those tools. The final full browser gate requires successful imports and a real
decode; older recordings whose log explicitly says decoding was skipped prove display and
payment behavior only. A Pillow 12.3.0 import stalled during macOS binary validation; the verified
12.2.0 environment required no system-security workaround.

A pre-final browser attempt had direct-request refusals because a private driver's port
substitution accidentally changed the store UUID. The authoritative synthetic seed is
`11111111-2222-4333-8444-555555555555`. Browser store selection masked the typo for UI requests,
while direct verification requests received 403. Correcting the private driver and adding
SQL/store/hash/authentication preflight resolved the setup mismatch; no product assertion was
weakened. Failed-attempt logs and volumes remain distinct from final evidence.

## Evidence classification

The backend and frontend inventories cover first-party modules and named functions structurally,
including inputs, returns, calls, side-effect signals and test references. Static call resolution is
incomplete for dynamic dispatch. Targeted manual review is recorded separately by file and line range;
the inventory is not a claim that every function received an independent line-by-line audit.

Provider-contract tests use controlled transports. Browser status fixtures test presentation only.
Neither proves that Google accepted a request or classified a question correctly. The live harness
requires a fresh five-attempt server ledger and a verified free-tier project under the user’s
zero-paid-usage authorization, and marks
provider success only when the actual persisted result contains `ASSISTANT_GEMINI_CLASSIFIED`.

Early full-suite attempts were intentionally interrupted to include subsequently reproduced
boundary fixes. Their logs are retained as interrupted baseline evidence and must not be reported
as final passing runs. Earlier videos are likewise retained separately from final-image recordings.

One completed pre-final run reported 5,066 passed, three failed and five expected skips. Two failures
were the collection-aged approval fixtures described above; the third used a backup-test assertion
imported before the patched image implementation was finalized. The current assertion and the
nineteen decision-time tests pass independently. That failed run is retained under
`pre-fixture-clock-full-suite`; a new full run uses a fresh database on PostgreSQL 16.15. Two existing
Starlette warnings deprecate the symbolic name for HTTP 422; they do not change its numeric result.

The following run was deliberately interrupted after 413 passes and four expected skips to include
the newly reproduced redactor CPU fix. It is retained under `pre-redaction-interrupted-suite` and
is not a completed gate. The subsequent redactor-only API image was separately rebuilt and scanned;
its 312 packaged first-party files differed from the preceding browser-gate image only in
`assistant_gemini.py`. That intermediate image has its own packaged redaction/budget and missing-key
checks. A later run was interrupted after 1,904 passes and four expected skips to include the
tracing and worker corrections; it is retained under `pre-tracing-interrupted-suite`. The final
image also includes subsequent authentication and shared-device session corrections and requires
separate affected-workflow evidence. The broad gate and original recordings retain their actual
image bindings.

The three application-image scans are separate from infrastructure-image scans. The API, worker
and Tool Facade passed the high/critical filter after the PyJWT update. A broader scan then found
older packages in the Caddy and PostgreSQL images that the existing three-image release job did
not cover. Raw scanner severity is retained separately from vendor severity and runtime
reachability: for example, an OpenSSL issue affecting 32-bit applications does not establish an
arm64 exploit, and Go packages in `gosu` belong to a startup privilege helper rather than the
PostgreSQL network server. The patched gateway, PostgreSQL runtime and backup image pass
comprehensive HIGH/CRITICAL scans with zero findings. Actual TLS hostname validation, startup
transactions, cross-version age encryption/decryption and local archive/WAL upload behavior were
verified. No finding is silently waived; arm64 archive checks do not establish amd64 runtime
behavior, future vulnerability status or a production restore drill.

Video review also caught a stale assistant disclosure claiming no model was ever called. The
previous disclosure test checked only the default brain and missed the injected factory. The
corrected copy and contract distinguish optional intent classification from deterministic facts,
persisted answer replay and forbidden business actions. The replacement AI recording uses that
correction. Other workflow recordings retain their exact source and
infrastructure provenance; a later infrastructure-only check must not be misrepresented as the
environment used to film them.

The subsequent completed full run reported 5,114 passed, two failed and five expected skips.
Both failures correctly detected that the local synthetic agent evidence still pinned the old
worker runner. The repository’s `EVIDENCE-REPIN-001` procedure was followed: retain v5 byte-for-byte,
advance capture defaults and supersession guards, execute the actual PostgreSQL-backed evaluator,
and write v6 as the current bundle. All 32 results remain `SKIP` on `DETERMINISTIC_DEGRADED`,
with unchanged blockers and no release effect. All eight guarded evidence tests pass. The first
capture attempt refused a missing deployment hash key; a fresh ephemeral synthetic key supplied
only through the process environment allowed the successful rerun. The failed suite remains in
`pre-evidence-repin-full-suite`; the replacement suite passed on a fresh database. This evidence/tooling
correction changes no packaged application source or browser behavior.

## Release boundaries still requiring external evidence

Real customer model processing requires provider data-use/retention/region/deletion approval and
release evidence. Real channels, bank reconciliation and invoice-provider execution require their
own integrations and credentials. Shop cutover requires the host's trusted TLS, external backups,
a timed host restore drill and owner acceptance of a real counter transaction. None is replaced by
a synthetic browser run.

`scripts/verify_contracts.py` still reports these eight blockers from the separate public runtime
registry: `IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED`, `AGENT_RUNTIME_IMAGE_NOT_VERIFIED`,
`EFFECTIVE_PROVIDER_REQUEST_NOT_VERIFIED`, `SECURITY_PROVIDER_DATA_APPROVAL_MISSING`,
`PRIVACY_PROVIDER_DATA_APPROVAL_MISSING`, `DEDICATED_PROVIDER_CREDENTIAL_NOT_VERIFIED`,
`REAL_CUSTOMER_DATA_DISABLED` and `CANDIDATE_IS_EVAL_ONLY`. The six locally scanned service images
do not satisfy that public-agent release registry. All thirteen capability authorizations remain
`NOT_AUTHORIZED`; no delivery evidence or owner approval was invented to clear them.

Historical pre-setup blocker (subsequently resolved for a new unbilled project; see the
[free-tier supplement](GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md)): in the signed-in browser,
AI Studio’s API Keys and Billing views reached `/520` with a subscription-check/unknown error;
Google Cloud’s credentials page for the observed project reported an invalid project, and its All
projects selector failed to load resources. The initial AI Studio project row showed Tier 1 Prepay.
This establishes neither a Free-tier key nor a definitive mapping of the supplied key to that paid
project. At that stage, readable project/key metadata and verified Free tier were required before
running the prepared five-case evaluation. No model request, billing change, project creation,
key creation, or paid usage had yet been performed. The later authorized Free-tier setup and live
results are documented separately in the linked supplement. `gemini-free-tier-verification.json` records
the exact inspection scope without account identifiers or secret values.
