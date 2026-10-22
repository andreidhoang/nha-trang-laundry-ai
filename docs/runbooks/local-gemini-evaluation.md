# Local Gemini assistant evaluation

This opt-in path evaluates Vietnamese intent recognition in the staff assistant with synthetic
questions. It does not enable the public Concierge or process real customer data. The October 15
local verification authorization is recorded in `docs/audit/LOCAL_READINESS_2026-10-15.md`.

## Behavior and limits

The existing deterministic assistant answers recognized questions first. Money refusals and explicit
order lookups retain precedence. For an otherwise unsupported question, Gemini may return only
`GREETING`, `TODAY_OVERVIEW`, `SLA_RISK`, `PENDING_APPROVALS` or `UNSUPPORTED`. The server validates
that enum and renders its existing answer from authorized reads. The provider cannot supply a
number, link, identifier, tool call, message, state transition or database mutation.

The request contains one sanitized question and a fixed classification instruction. It contains no
history or business records. Pattern redaction removes recognized secrets, email addresses, phone
numbers and UUIDs; it cannot prove arbitrary text contains no personal information. Use synthetic
questions only.

The adapter pins `gemini-3.5-flash-lite` at Google's `generateContent` endpoint, requests one JSON
candidate, requests minimal thinking with thought summaries disabled, caps output at 128 tokens, both raw and sanitized input at 600
characters and the response at 16 KiB. It makes no automatic retries and follows no redirects or environment proxies.
A fifteen-second deadline includes DNS, connection, TLS, headers and response-body reads, and stays
below the console's thirty-second write timeout. The earlier five-second cutoff interrupted two
real Free-tier requests; the revised bound accommodates observed latency without adding retries. Provider
error bodies and credentials never enter answers or evidence.

## Configuration

| Name | Meaning |
|---|---|
| `ASSISTANT_GEMINI_ENABLED` | Defaults to `false`; explicit synthetic evaluation opt-in |
| `AGENT_PROVIDER_API_KEY` | Secret process environment value; never a CLI argument or repository file |
| `ASSISTANT_GEMINI_BUDGET_PATH` | Absolute path to a persistent attempt ledger in a private directory owned by the API process |
| `ASSISTANT_GEMINI_MAX_CALLS` | Default 5; accepted range 1–20; an existing ledger cannot have its original cap raised |

The budget directory must have mode `0700`, and the ledger mode `0600`. In a container, create the
directory for UID 10001 and mount it writable independently of the read-only application. Preserve
the ledger on restart. Deleting it starts a new allowance and requires a new authorized evaluation.
The ledger contains only attempt outcomes and token totals, not prompts, responses or credentials.
The API image retains Python's SQLite extension and requires `sqlite-libs>=3.53.4-r0`, the
[Alpine 3.23 package](https://pkgs.alpinelinux.org/package/v3.23/main/aarch64/sqlite-libs) available
when verified on October 15. Its build runs an application-import and SQLite-query smoke check.
The retained final arm64 image passed the comprehensive HIGH/CRITICAL vulnerability scan recorded
in the dated readiness report. Any rebuilt release image requires its own scan and provenance.

Configuration alone is insufficient: the API also requires the demo issuer, audience and JWKS
configuration, the seeded synthetic store, a seeded demo staff subject and an authorized operations
role. Membership is checked before idempotency admission. The model is called only inside the
winning request's callback; completed replays and conflicting keys do not call it again. Attempts
are reserved outside the business transaction so failures, unknown outcomes and restarts cannot
refund the allowance. A database rollback can still require a new attempt on a later retry;
the durable cap bounds that uncertainty.

Enable this only on an isolated local stack with its own database, test accounts and provider
budget. The ordinary demo on port 8081 and production Compose configuration remain provider-free.
The local API needs an explicitly provided outbound route to Google; its default internal Docker
networks intentionally have no such route.

## Observable outcomes

Successful classification carries `ASSISTANT_GEMINI_CLASSIFIED`. The console says Gemini helped
interpret the question while the server supplied the answer. Provider failure, invalid output,
exhausted allowance, missing configuration and oversized input each have a separate reason code
and an actionable Vietnamese message. They retain the deterministic unsupported answer. No result
claims that a customer message was sent or a business action executed.

History and refresh read the persisted result. The displayed streaming effect replays that result;
it does not make another provider request.

## Verification and release boundary

The October 15 final missing-credential browser run passed 24 checks against the actual API
image `sha256:25e8858aef0ad506e84eb48d43321eef038cec6d0c067090b199a97e4f4f12a6` and
PostgreSQL database; all 312 packaged source files match the reviewed host source. It verified
persisted fallback/recovery, role refusal, no duplicate turn on replay and no provider attempt. The API had no provider credential and remained on internal
networks. Two role recordings were inspected through timestamped frames. This is failure-path
evidence, not a successful Google integration. The [workflow evidence index](../audit/LOCAL_READINESS_WORKFLOW_EVIDENCE_2026-10-15.md)
links the recordings and database/log proof. The user authorized free-tier requests only, with
no paid usage. The visible AI Studio project initially showed Tier 1 Prepay, while the key/billing
views and Google Cloud project discovery returned errors. The supplied key has not been mapped
to a verified Free-tier project, so live classification was initially blocked and that
credential was not used or stored. A later authorized setup created a dedicated unbilled
project and a new key; see the [free-tier verification supplement](../audit/GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md)
for the subsequent five successful live cases and current evidence. The initial live run exposed Google’s restriction of 2.5 models to prior
users. The local pin is now 3.5 Flash-Lite, with minimal thinking rather than a promise that thinking
is disabled. Do not enable billing, add credits, or silently use a paid project to finish this evaluation.

Run `uv run pytest apps/api/tests/test_assistant_gemini.py` for deterministic provider contracts and
failure cases, and the assistant integration tests with `DATABASE_URL` pointing only to an isolated
test database. Run the full `pytest --require-postgres-integration` suite before handoff. Keep live
provider evidence separate from fixtures and label the provider/model, request count, latency,
usage, response validation and browser persistence checks.

`uv run python scripts/verify_local_gemini.py --check-plan` validates the fixed five-case evaluation
without network access. Run the live harness with `uv run --with playwright python scripts/verify_local_gemini.py`; Playwright is an optional execution dependency. For an authorized live run, supply `--base-url` for the isolated API,
`--idp-url` for a loopback demo identity-provider origin serving `/token`,
`--confirmed-fresh-five-call-budget`, `--video` and `--output`. Confirm the server ledger has zero
attempts and a ceiling of five first. The harness checks five semantic classifications, same-key
replay and conflict, a sixth-question quota refusal, deterministic recovery and refreshed history.
It does not read the provider key. Do not reset the ledger to turn a failed run into a new allowance.

Google documents JSON-schema output support and the API's generation controls in its
[structured-output guide](https://ai.google.dev/gemini-api/docs/structured-output) and
[generateContent reference](https://ai.google.dev/api/generate-content). Its
[pricing page](https://ai.google.dev/gemini-api/docs/pricing) lists Flash-Lite standard text rates
of $0.30 per million input tokens and $2.50 per million output tokens, and standard Free-tier text input/output as free of charge, checked October 15, 2026.
A call-count ceiling is not an account billing limit. Google’s [billing guide](https://ai.google.dev/gemini-api/docs/billing/)
explains that free versus paid access depends on the project. For this evaluation, verify Free
tier and billing disabled before making a request; any paid use needs separate explicit
authorization. Retain unknown usage as unknown.

The [Gemini terms](https://ai.google.dev/gemini-api/terms) state that unpaid-service inputs and
outputs may be used for product improvement and reviewed by humans. This evaluation does not
establish production retention, regional processing or deletion guarantees. `DEC-006`, the public
runtime model pin and signed capability release gates remain separate prerequisites. Rotate any
credential previously pasted into a conversation before real deployment.
