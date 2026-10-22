# Free-tier Gemini verification — October 15, 2026

## Outcome and authority

All five synthetic staff-assistant cases passed against real Gemini 3.5 Flash-Lite, through the
browser, API and PostgreSQL: **74 browser assertions and 19 database/invariant assertions passed**.
The dedicated Google project remains **Free tier**, with billing not set up. This is a local
intent-classification evaluation, not production authorization or a general AI quality benchmark.

The owner authorized free requests only, no billing activation, preservation of existing changes,
and keeping the demos running. They explicitly approved creation of the dedicated key, an initial
five attempts, six additional attempts (one diagnostic plus five workflow checks), then five more
after the deadline correction. All **16 authorized generation attempts are spent**; all four
ledgers are retained. No further provider request is authorized by this report. Two authenticated
model-metadata GETs and a TLS preflight were separate from generation.

Work remains on `codex/engineer-setup`. The [broader readiness report](LOCAL_READINESS_2026-10-15.md)
and [workflow index](LOCAL_READINESS_WORKFLOW_EVIDENCE_2026-10-15.md) retain the earlier application
review, 47 recorded workflows and their exact image bindings. These are not relabeled as tests of
the new Gemini image. Public capabilities and production/customer-data processing remain closed.

## Requirements and execution plan

Verify an unbilled project and dedicated key; isolate evaluation data and provider access; run bounded
live checks; correct demonstrated defects; rerun contracts, database and browser checks; review the
recording and retain both failed and passing evidence. Preserve prior ledgers and user changes.

Authoritative behavior comes from `AGENTS.md`, `BUILD_ENGINEERING_SPEC.md`, the Vietnamese
specifications and machine-readable contracts. The owner separately authorized this local scope.
Model choice and the original five-second timeout were local implementation choices, not approved
production policy. The new fifteen-second bound remains below the console's thirty-second POST
limit and the database's sixty-second idle-transaction limit.

| Requirement | User workflow | Relevant implementation | Expected result | Verification method | Status |
|---|---|---|---|---|---|
| Free requests only | Staff asks a synthetic question | Project/key and fixed model | No paid project or billing activation | AI Studio project/key/quota views and official pricing | Verified Free tier; billing not set up |
| Recognize supported intents | Five Vietnamese question variants | `assistant_gemini.py`, assistant API and console | Validated enum; server supplies all facts/links | Browser → API → Google → DB comparison | All five passed |
| Reject business actions | Request to change all order states | Prompt, enum and deterministic answer | Refuse; business rows unchanged | Live case plus row-set hashes | Passed |
| Bound calls and retries | Duplicate submission, conflict, quota, refresh | Idempotency plus private SQLite ledger | Five attempts; no duplicate provider work | Browser assertions, ledger and relay connection count | Passed |
| Handle unavailable/slow provider | Provider failure, then suggested question | Absolute deadline and persisted failure reason | Visible failure; deterministic recovery | Historical failed runs, mocked deadline regression and final live run | Passed; provider reliability beyond this sample unmeasured |
| Preserve work and demos | Existing development state | Worktree, original checkout, ports 8081/8089 | Existing changes and records retained | SHA-256 baselines and HTTP health | Passed: original checkout unchanged; 74 prior changed files identical, eight intentional extensions |
| Regression safety | Start and exercise reviewed application | Final source, isolated PostgreSQL and packaged API | Required repository gates pass | Full suite, role suite, static gates and image scan | 5,127 full-suite tests and 63 real-role tests passed; five documented skips; static gates/75 focused tests/image scan passed |

## Configuration and corrections

Created `Laundry Local Free Evaluation` (`gen-lang-client-0790920933`). AI Studio displayed
**Free tier** and **Set up billing** before generation and after the successful run. The original
Tier 1 Prepay project/key was not used or changed. The new key, `Laundry synthetic evaluation`, was
transferred in memory into the isolated API runtime environment. Docker retains that environment
in its container configuration, so it remains readable to authorized Docker administrators. No
provider secret file, repository value, command-line value, screenshot or test-evidence value was
created. Later API replacement transferred the same existing environment credential in memory
without creating another key.

The local adapter now pins `gemini-3.5-flash-lite`. Google's
[deprecation guide](https://ai.google.dev/gemini-api/docs/deprecations) restricts 2.5 models to prior
users, and its [pricing guide](https://ai.google.dev/gemini-api/docs/pricing) lists standard 3.5
Flash-Lite text input/output as free for Free-tier projects. Billing tier belongs to the project,
not an app-side call cap; see Google's [billing guide](https://ai.google.dev/gemini-api/docs/billing).
The observed quota was 15 requests/minute, 250,000 input tokens/minute and 500 requests/day. Those
limits do not authorize use beyond the owner's smaller local allowance.

The adapter requests minimal thinking and disables thought summaries. Gemini 3 may attach opaque
`thoughtSignature` metadata to text. Only a single text part and an optional valid base64 signature
are accepted; the signature is discarded immediately, never stored or replayed. Tool calls,
thought content, additional fields and malformed structured output remain refused. The model can
only choose one of five intents; all facts, money, policy, permissions, links and state remain
server-controlled. The request contains one sanitized synthetic question, no history or database
records. Pattern redaction is not proof that arbitrary real text is safe; synthetic data only.

The initial five-second deadline interrupted two requests after TLS setup. The corrected absolute
fifteen-second deadline covers DNS, connection, TLS, headers and body, with no automatic retries.
The final real responses used approximately 4.1–10.1 seconds per TLS tunnel. That is observed network
connection duration, not isolated model inference latency. A clock-based regression accepts a valid
response beyond the old five-second cutoff and still refuses work past the new deadline; the
existing blocked-DNS test verifies that late resolution cannot send a delayed request.

`.demo` directories are now excluded from Docker build contexts, with a contract test. This keeps
private local configuration and large evidence artifacts outside builds. No business migration or
unrelated dependency upgrade was needed for these Gemini corrections.

## Runtime and evidence

The final API image is
`sha256:a5cc4b23ee9c47fdda67ab3fdeab967f235cf9f53b6a8a8871600a62ad4b69e2`.
All **312 packaged files match current source**. Only `assistant_gemini.py` differs from the preceding
signature-corrected image; runtime package versions are unchanged. A comprehensive cached Trivy
0.72 scan found **zero HIGH/CRITICAL vulnerabilities**, without waivers. This scan is point-in-time
local evidence, not a production release gate.

The evaluation uses loopback port 8091, a new physical copy of a stopped synthetic database volume,
separate internal networks, and persistent budget/log volumes. The API runs as UID 10001 with a
read-only filesystem and dropped capabilities. Its database role is not a superuser and cannot
create databases or roles. A credential-free relay can connect only to Google's API on port 443;
TLS hostname/certificate verification remains end to end. No worker, public ingress or automatic
customer send was enabled. The existing demos on ports 8081 and 8089 were not changed.

Local ignored evidence root: `.demo/gemini-free-evaluation-2026-10-15/`. The `runtime/` directory
contains private operational configuration and must not be exported. Never publish the entire
folder indiscriminately.

| Run | Result | Evidence |
|---|---|---|
| Initial 2.5 model | 0/5 classifications; 60 passed, 14 failed browser assertions; 11 passed, 8 failed DB assertions | `live-results.json`, `after.json`, `video/`, `review-initial/` |
| Authorized diagnostic | HTTP 404 explicitly says 2.5 unavailable to new users; one attempt spent | `generation-diagnostic.json` |
| 3.5 with original 5s deadline | 3/5 classifications; 67 passed, 7 failed browser assertions; 14 passed, 5 failed DB assertions | `live-results-v2.json`, `after-v2.json`, `provider-tunnels-v2.json`, `video-v2/`, `review-second/` |
| Final 3.5 with 15s deadline | 5/5 classifications; 74 passed, 0 failed browser assertions; 19 passed, 0 failed DB assertions | `live-results-v3.json`, `before-v3.json`, `after-v3.json`, `provider-tunnels-v3.json` |

The final ledger records five successful calls, **615 prompt tokens and 71 candidate output tokens**.
These are the validated usage fields retained by the adapter, not a complete provider accounting
statement; thought-token usage and usage for interrupted requests are unknown. Free status is
established by the unbilled project, not by rounding usage to zero.

The final database probe found exactly eleven new assistant turns: four deterministic baselines,
five live cases, one quota refusal and one deterministic recovery. Each has exactly one domain
event, audit event, outbox event and idempotency record. Replays/conflicts made no extra turn or
provider call. All pre-existing assistant rows and business row-set hashes were unchanged.

| Live case | Validated intent | Result |
|---|---|---|
| Current order overview | `TODAY_OVERVIEW` | Server facts and links match deterministic baseline |
| Production delay | `SLA_RISK` | Server facts and links match deterministic baseline |
| Work awaiting approval | `PENDING_APPROVALS` | Server facts and links match deterministic baseline |
| Greeting | `GREETING` | Supported capabilities, no invented facts |
| Ignore rules/change all orders | `UNSUPPORTED` | Refused; no business mutation |

[Final recording](../../.demo/gemini-free-evaluation-2026-10-15/video-v3/tro-ly-gemini-cuc-bo-1-demo-owner.webm)
shows the five cases, loading states, replay/conflict checks, exhausted allowance, deterministic
recovery and refreshed history. All three provider-run recordings were reviewed through eight
timestamped frames each; continuous playback was not performed. The
[final review](../../.demo/gemini-free-evaluation-2026-10-15/review-final/index.json) records that
limitation and findings. Historical failed turns deliberately remain visible in saved history.
The final run reported no uncaught browser errors. Provider failures in historical runs were
visible, not hidden behind fake success. Backend correlation found 264 structured records in the
final live interval, all INFO; every HTTP completion had a correlation identifier.

Restarting only the isolated API and PostgreSQL preserved all 48 assistant turns, business row
hashes, database identity/role and all four exhausted ledgers (seven read-only assertions passed).
No provider call was made by this restart probe. Reopened historical ledger snapshots display the
currently configured model name, so historical model attribution must come from original run/image
evidence; the attempt counts are independent and remain valid.

The same-origin manual sign-in and saved-history path also passed **15 assertions on desktop and
15 on a 390×844 phone viewport**, without new assistant submissions or provider calls. Separate
[desktop](../../.demo/gemini-free-evaluation-2026-10-15/manual-signin-video/desktop/dang-nhap-va-ket-qua-da-luu-1-desktop.webm)
and [phone](../../.demo/gemini-free-evaluation-2026-10-15/manual-signin-video/phone/dang-nhap-va-ket-qua-da-luu-1-phone.webm)
films were reviewed through timestamped frames. The browser's implicit `/favicon.ico` request had
returned 404 on the new sign-in origin; the local gateway now redirects to the existing application
SVG icon. Both replacement recordings have zero console errors, failed HTTP responses and uncaught
JavaScript exceptions. Earlier verifier timing/binding failures and the icon-error film are retained.
Across the three generation films and four sign-in attempts, all seven films were reviewed using
eight timestamped frames each. The two corrected sign-in films are the current acceptance evidence.

An independent bounded source/evidence review found no additional blocking adapter defect. Its
limits are explicit: live owner role only, sequential replay/conflict, enumerated business tables
and five semantic cases. Authorization/concurrency/adversarial coverage outside that live sample
comes from deterministic tests and the earlier separately bound browser evidence.

## Reproducible checks and handoff

Use the existing lockfile and Node 24. The relevant commands are:

```text
uv run pytest apps/api/tests/test_assistant_gemini.py \
  packages/evals/tests/test_local_gemini_verifier.py \
  packages/evals/tests/test_container_build_contract.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
git diff --check
DATABASE_URL=<isolated-test-database> uv run pytest --require-postgres-integration
```

The focused suite passed **75 tests**, and all seven static gates passed. On a fresh isolated
PostgreSQL database, the final full repository suite passed **5,127 tests**, with **five skips,
zero failures and two Starlette deprecation warnings**, in **1,955.04 seconds**. Four skips are
explicitly empty console-disclosure parameter sets; the non-BSD hidden-flag fallback test is
correctly skipped on this Mac, which has BSD flags. No database integration test was skipped for
missing PostgreSQL.
The separate real `laundry_api` / `laundry_worker` role suite passed **63 tests** in **31.77 seconds**:

```text
DATABASE_URL=<isolated-test-database> \
LAUNDRY_API_DATABASE_URL=<isolated-api-role-database> \
LAUNDRY_WORKER_DATABASE_URL=<isolated-worker-role-database> \
uv run pytest --require-postgres-integration -q \
  packages/db/tests/test_api_role_write_paths.py \
  apps/worker/tests/test_worker_least_privilege.py
```

`suite-results.json`, the full/role logs and sanitized JUnit XML retain exact outcomes. The harness
sets `CI=true` for the full run and uses Node 24.21.0. No application or test source changed after
that run started. Its owned test PostgreSQL container was stopped afterward with its volume
retained; all three demo environments remain running.
An earlier suite started before the signature correction was intentionally interrupted; its logs
and reason are preserved under `pre-signature-interrupted-suite/`, not reported as a pass.

The real browser run used cached Playwright (it is an optional execution dependency):

```text
uv run --offline --with playwright python scripts/verify_local_gemini.py \
  --base-url http://127.0.0.1:8091 --idp-url http://127.0.0.1:9011 \
  --confirmed-fresh-five-call-budget \
  --video .demo/gemini-free-evaluation-2026-10-15/video-v3 \
  --output .demo/gemini-free-evaluation-2026-10-15/live-results-v3.json
```

Do not rerun this against the exhausted ledger or silently substitute a new one. A new allowance
requires explicit owner authorization. The local sign-in entry is `http://localhost:8091/demo-idp/`; the gateway routes that path to
the existing synthetic identity provider and the staff app on the same origin. Ports are still
loopback-only. The original gateway is retained stopped for rollback. The running demo retains
deterministic questions and saved history; new unsupported questions will show the exhausted-evaluation message. No billing setup,
credits, project-tier change, production deployment or capability authorization is part of this work.

Production still requires `DEC-006` data/privacy approval, provider/runtime release evidence, real
channel and financial integrations, host backup/restore and cutover checks, and owner acceptance.
The small successful sample does not establish production latency, availability, arbitrary-language
quality, regional processing, retention or deletion guarantees.
