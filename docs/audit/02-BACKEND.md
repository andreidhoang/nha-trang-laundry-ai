# 02 — Backend & core API audit

Headline: **the backend is the strong half of this system.** Auth, tenancy, idempotency and
optimistic locking are correct under adversarial test. Every P0 in this audit is a *reachability*
defect in the console or in a read model's field list — not a correctness defect in the domain.

## 1. Auth & tenancy — VERIFIED PASS

Store B (`2222…6666`) was created with no members. The owner principal (member of store A only)
then forged requests against it from inside an authenticated browser session.

| Request | Result |
|---|---|
| `GET /stores/<A>/orders` (baseline) | `200` + rows |
| `GET /stores/<B>/orders` | `403 {"detail":"operation denied"}` |
| `GET /stores/<B>/quotes` | `403 operation denied` |
| `GET /stores/<B>/incidents` | `403 operation denied` |
| `GET /stores/<B>/order-requests` | `403 operation denied` |
| `GET /stores/<B>/settlements/today` | `403 operation denied` |
| `GET /stores/<B>/shadow/drafts` | `403 shadow access denied` |
| `GET /stores/<B>/shadow/reviews` | `403 shadow access denied` |
| `GET /stores/<B>/assistant/turns` | `403 operation denied` |
| `POST /stores/<B>/counter-tickets` | `403 operation denied` |
| `POST /stores/<B>/order-requests` | `403 operation denied` |
| `GET /stores/<0000…0000>/orders` (nonexistent store) | `403 operation denied` |

Two properties worth stating explicitly, because both are easy to get wrong and this code gets
both right:

1. **No enumeration oracle.** A store that does not exist and a store you are not a member of
   return the byte-identical refusal. An attacker cannot map the tenant space.
2. **Authorization is at the data layer, not the UI.** The auditor's disabled buttons are
   cosmetic; the server refuses independently (`core/rbac.js:20` claims this, and the forged
   requests above confirm it).

**B-04 (P3):** `POST /stores/<B>/incidents` answered `422` (body validation) rather than `403`,
because Pydantic validates before the handler's membership check runs. No data leaks — the request
schema is already public in `specs/contracts/internal-api-v1.openapi.yaml` — but the refusal *kind*
now varies with payload shape on one route, which is an inconsistency in an otherwise uniform
boundary. Status: VERIFIED.

## 2. Idempotency & concurrency — VERIFIED PASS

| Probe | Result | Verdict |
|---|---|---|
| Double-submit, same `Idempotency-Key`, same payload | both `201`, identical `ticket_id 755c1aad…`, identical `ticket_number 11` | replayed, not duplicated — correct |
| Mutation with no `Idempotency-Key` | `422 {"loc":["header","Idempotency-Key"],"msg":"Field required"}` | correct |
| Mutation with no `X-CSRF-Token` | `403 {"detail":"CSRF validation failed"}` | correct |
| Two operators, same order, both holding `row_version 1` | first `200` (v1→v2), second `409 {"detail":"STALE_VERSION: order is missing or stale"}` | **no last-write-wins data loss** |
| Mutation with no `If-Match` | `428 {"detail":"If-Match is required"}` | correct — precondition required, not silently accepted |

The console matches this discipline: `core/api.js` never auto-retries a write, never queues one
offline, and refuses to issue a mutation without an idempotency key (`core/api.js:110`). That is
the right rule and it is enforced in the one place that can enforce it.

## 3. Error taxonomy — mostly good, one structural gap

Errors are typed (`core/errors.js`) and every failure surfaces a correlation id that appears in the
server log. Spot-checked Vietnamese microcopy is genuinely good: the stale-write path says *"Đơn
này vừa được người khác đổi trong lúc bạn đang xem… Tải lại bảng đơn rồi làm lại theo số mới"*
(`orders.js:705-709`) and offers a "Tải lại bảng" button, which is exactly the only action that
helps. The timeout path on order creation says *"Đừng bấm lại — hãy tải lại bảng đơn và tìm mã
khách này trước"* (`orders.js:449-452`) rather than inviting a duplicate.

**B-05 (P2):** raw `422` bodies from Pydantic reach the client as a JSON array of `{type, loc, msg}`
objects in English (`"Input should be 'AWAITING_HANDOFF', …"`). `errorNotice()` renders the detail
verbatim. A staffer who trips a validation error the client did not pre-check sees an English
enum list. Observed at runtime during the concurrency probe. Status: VERIFIED.

## 4. Observability — PASS with one forward-looking gap

An assistant turn is fully reconstructible from one row:
`assistant_turns(turn_id, store_id, staff_user_id, question, intent, answer, links, reason_codes,
correlation_id, created_at)`. Structured logs carry `correlation_id` + `trace_id` per request.
Approvals carry the complete signed envelope plus `requested_by`, `requested_at`, `expires_at`.

**B-06 (P2, forward-looking):** `assistant_turns` has **no model identifier and no model version
column**. Today that is correct — no model is called. The moment a provider is wired, §8 of the
audit directive ("actor = model + version") cannot be satisfied without a migration. Flagging now
because adding the column before the first provider call is free and adding it after is not.
Status: INFERRED (schema read; no model exists to test).

## 5. Cost & rate limits

Per-tenant LLM spend tracking: **N/A** — no provider is called. Authentication abuse is bounded
(`AuthSettings.auth_attempt_limit = 30 / 300s`), and the reasoning for the number, including why a
finer bucket key would be unsafe behind a shared proxy, is recorded at `auth.py:57-78`. That is the
right analysis, written down where the next person will find it.

## 6. Test suite & typecheck — one real defect

Run from a clean environment against an isolated database:
`DATABASE_URL=… uv run pytest -q --require-postgres-integration` →
**1188 passed, 1 failed, 3 skipped in 224.93s.**

**B-01 (P1) — the suite cannot go green on Linux.**
`packages/evals/tests/test_workspace_env.py:86` is guarded
`@pytest.mark.skipif(not hasattr(stat, "UF_HIDDEN"), reason="UF_HIDDEN is a BSD file flag")`.
`stat.UF_HIDDEN` is defined on **every** platform in CPython's `stat` module; `os.chflags` is the
BSD-only symbol. So on Linux the guard passes and line 93 raises
`AttributeError: module 'os' has no attribute 'chflags'`. The companion test at line 105 guards on
the same wrong predicate in the opposite direction and skips when it should run. Fix: guard both on
`hasattr(os, "chflags")`. Status: VERIFIED (full traceback captured).

**B-02 (P2) — `uv run mypy apps packages`, the command in `CLAUDE.md`, fails on Linux** with 3
errors, all the same root cause: `scripts/workspace_env.py:96,117` read `st_flags` and
`packages/evals/tests/test_workspace_env.py:93` calls `os.chflags`, neither of which exists in the
Linux stubs. A documented command that cannot pass on a contributor's machine trains people to
ignore it. Status: VERIFIED.

**B-03 (P1) — two contract tests in the honesty suite assert nothing.**
`apps/api/tests/test_console_disclosure_contract.py:456` and `:497` both report
`SKIPPED … got empty parameter set for (entry)`. They parametrise over disclosure categories
`ABSENT_ROUTE` and `ABSENT_WRITER`, and `specs/contracts/console-disclosures-v1.yaml` registers
**zero** entries in either. Worse, of 191 registered disclosures **166 (87%) are `DESCRIPTIVE`** —
free prose with no machine binding — against 25 that a test can falsify:

```
DESCRIPTIVE 166 · SERVER_GATE 11 · ABSENT_TABLE 5 · REPOSITORY_ROLES 3 · MODEL_SEAM 2
RESPONSE_SHAPE 1 · READ_ONLY_MODULE 1 · ALL_AUTHENTICATED 1 · POLICY_BOUND 1
```

The disclosure-contract framework is one of the best things in this repository — it asserts that
the console's honesty claims stay true as the code moves. But a green run currently proves that
claim for 13% of the claims, and reports the two empty categories as SKIP rather than as a gap.
Per `CLAUDE.md` — *"a green test run is not completion evidence"* — this is the repository's own
rule applied to its own suite. Status: VERIFIED.
