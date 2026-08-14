# Staff Operations Console — Engineering Specification V1

**Status:** implemented, this document describes what was built and why
**Scope:** `apps/web`, plus four changes to `apps/api` and one to `packages/db`
**Supersedes:** nothing. Extends `specs/IMPLEMENTATION_ROADMAP_V1.md` §M3 and §10.

---

## 1. What this console is for

Three jobs, in the order they matter:

1. **Supervise the agent.** Every outbound message needs a named human decision. The draft review
   queue, the unknown-send reconciliation queue and the manual-send attestation flow are the only
   places that decision can be recorded, and nothing else in the system can record it.
2. **Run the deterministic parts of a laundry day** that the API actually supports: price a request,
   open an order against an accepted quote, move its commercial state, open an incident.
3. **Tell the truth about the rest.** Nine of the fourteen M3 screens have no backing API. They are
   not omitted; each has a screen naming what is missing and what blocks it.

The third job is not a consolation prize. `IMPLEMENTATION_ROADMAP_V1.md:394` makes "no hidden
unsupported default" an exit criterion for this milestone, and a console that silently lacks payment
is a console whose operator writes payments on paper that nobody reconciles.

## 2. The central constraint: the API is write-complete and read-incomplete

This is the finding that shaped every screen, and it is not visible from a route list.

The command surface is genuinely good. Twenty-seven routes, strict request models, idempotency on
every mutation, optimistic concurrency where it matters, atomic mutation + event + audit + outbox.

The read surface cannot draw the screens the specification asks for:

| Screen | What the read model returns | Consequence |
|---|---|---|
| Order board | `order_id, store_id, commercial, intake, production, balance, row_version` | No customer, no timestamp, no total, no fulfilment mode. Seven fields, four of them enums. |
| Order detail | *no per-order route exists* | Detail is assembled by filtering the board list. |
| Quote builder | no line items in any response; no revision history; no `GET` for a single revision | The builder cannot re-render what it just built after a reload. |
| Approval queue | `envelope_hash` but not `resource_version`, `snapshot_hash` or `rendered_hash` | A valid decision request cannot be constructed. Deciding is therefore unavailable. |
| SLA risk | read model exists in `packages/db`, unrouted, and needs a per-order `ProductionSlaPolicy` | Blocked on a business decision, not on engineering. |

Building a beautiful order board was never the constraint. Having something to put on it was.

## 3. Stack: zero-build ES modules

`apps/web` is plain ES modules, no npm, no bundler, no TypeScript. `index.html` loads `app.js` as a
module; the browser resolves the graph.

**This deviates from ADR-0001**, which reserves TypeScript for "the future React/Vite staff PWA".
The deviation is recorded here, not assumed, and the reasoning is repository-specific rather than a
preference about frameworks. The four supply-chain call sites, the CSP and dev-server conflict, and
the source-text privacy gates are set out in `apps/web/README.md` §"No build step, and why", along
with the concrete prerequisites for adopting Vite later.

The strongest argument against is real and worth restating: `mypy --strict` covers every line of
Python in this repository and `apps/web` has no type checking at all. That gap is closed here by a
contract test that reads `app.routes` and asserts every path the console calls exists — which
catches the defect class TypeScript would catch, against the running application rather than against
a hand-written declaration that can itself drift.

## 4. Screen inventory, against M3

`IMPLEMENTATION_ROADMAP_V1.md:373-387` lists fourteen deliverables.

| # | M3 screen | Built | Route | Notes |
|---|---|---|---|---|
| 1 | login | partial | shell | Not an OIDC client — `connect-src 'self'` forbids reaching a provider. Signed-out screen explains the flow and links to a configured same-origin entry point. |
| 2 | new inquiry / customer | no | `#/gaps` | No `parties`, `contact_points` or `addresses` aggregate. |
| 3 | quote builder | **yes** | `#/quotes` | Multi-line, revision mode, reason codes, 6 kg disclosure. |
| 4 | approval queue | partial | `#/approvals` | Read + TTL countdown. Deciding structurally unavailable, §2. |
| 5 | order board | **yes** | `#/orders` | Limited by the read model, §2. |
| 6 | order timeline | **yes** | `#/orders/:id` | Wires the previously unused audit endpoint. |
| 7 | intake / measurement | no | `#/gaps` | Intake transitions have no route; only the commercial dimension is exposed. |
| 8 | production / ready | no | `#/gaps` | Same. |
| 9 | delivery legs | no | `#/gaps` | No aggregate. Blocked on `DEC-003`. |
| 10 | payment | no | `#/gaps` | No aggregate. No order can reach `COMPLETED`. |
| 11 | incident | **yes** | `#/incidents` | Intake only; fault and remedy are never decided here. |
| 12 | machine / batch / cycle, staff-minutes | no | `#/gaps` | Blocked on `SHOP-INSTRUMENT-001`. |
| 13 | delivery-cost capture | no | `#/gaps` | Same. |
| 14 | daily operations dashboard + export | no | `#/gaps` | No versioned read models; `FR-RPT-005` requires a denominator nothing supplies. |

Beyond M3, and delivered because the agent path needs them: draft review (`#/shadow`), unknown-send
reconciliation and manual send (`#/exceptions`), queue health and session (`#/system`), staff
administration (`#/staff`), and the gap catalogue (`#/gaps`).

**Counts honestly: 6 of 14 built, 2 partial, 6 impossible today.** The eight not built are blocked on
absent aggregates and open decisions, not on frontend work.

## 5. Authorization

`src/core/rbac.js` encodes what the **server enforces**, measured route by route. It is not the
matrix at `SECURITY_RELIABILITY_SPEC_V1.md:295`, which describes the intended system. Where they
differ, the console follows the server, because a control that predicts "allowed" and then fails is
worse than one that says "the server will refuse this, and here is who can".

Differences worth knowing:

- `AUDITOR` reads the order board and every Shadow surface, and is refused the quote list, the
  incident list, the approval queue and queue recovery.
- `OPERATOR` passes the *route* gate on the two Shadow write routes and is refused by the
  *repository*, which admits only `OWNER_ADMIN` and `OPS_APPROVER`. The console gates those controls
  at the repository's rule.
- Most operational routes also require `mfa_verified`. Four roles always have it by the session
  layer; `OPERATOR` and `DRIVER` do not, so an unverified operator authenticates and is then refused
  everywhere. The console says so rather than showing an empty screen.

**UI visibility is not authorization** (`SECURITY_RELIABILITY_SPEC_V1.md:313`). Nothing in `rbac.js`
grants anything; the server re-checks every call, and a screen that slips past the guard still
renders whatever refusal comes back. Controls are **disabled with a reason, never hidden**.

## 6. Error taxonomy

The specification's error envelope (`DOMAIN_DATA_API_SPEC_V1.md:1336`, with `ok`, `trace_id` and a
nested `error`) **does not exist**. The server sends `{"detail": …}` in three shapes: a string on
most routes, an object on `create_quote`'s 422, an array on FastAPI validation. `src/core/errors.js`
is written against the observed shape and says so in its header.

Seventeen kinds, the load-bearing distinctions being:

- `REQUIRE_HUMAN` — the engine declined to guess. **A result, not an error.** Rendered with its
  reason codes verbatim, and no row was written, so the same idempotency key may be reused.
- `STALE` — `STALE_VERSION:` prefix. Offers reload, never retry: retrying a stale version cannot
  succeed.
- `PRICEBOOK_UNAVAILABLE` — the 503 that means no approved price list, which is a refusal, not an
  outage.
- `FAULT` — 5xx. **Explicitly not retryable**, and the copy tells the operator to check the board
  rather than repeat the command, because a 500 can mean the command committed and the response did
  not.

Every request carries `X-Correlation-ID`; every failure surfaces it. It is the one string that
appears both on screen and in the server log.

## 7. Write discipline

- **Idempotency.** Every mutation carries `Idempotency-Key`. The key is minted per submission,
  cleared on any form edit, and cleared after a successful commit. The server hashes the payload
  alongside the key, so a stable key with edited content is a 409 rather than a replay — resetting
  on edit is what makes the retry semantics correct. `replayed: true` is surfaced as
  "kết quả được phát lại", never as a fresh success.
- **Optimistic concurrency.** `If-Match: "<row_version>"` where the route takes it — strong-quoted
  integer, the only form `_parse_if_match` accepts. The API emits no `ETag`; the version is read
  from a prior response body.
- **No automatic retry of a write, ever.** Not on timeout, not on network failure, not on 5xx.
- **No offline write queue.** No IndexedDB, no background sync, no retry buffer. Offline is
  read-only and says so.
- **One confirmation per commit**, in the result line under the form.

## 8. Display contract

- **Money is formatted, never computed.** No addition, no rounding, no `toFixed`, no `parseFloat`
  into a stored value. Integer VND in, grouping separators out. A non-integer amount is a contract
  violation and is rendered raw with a marker rather than coerced.
- **`null` is never rendered as `0`** (`IMPLEMENTATION_ROADMAP_V1.md:906`). This is the common case,
  not an edge: every quote the API can currently produce has an unresolved delivery fee, so
  `display_total_min_vnd` is genuinely null.
- **`net_service_subtotal_vnd` always carries a price-state badge.** The API gives it a scalar name;
  the service fills it from the domain's range *maximum*. Today range-priced services are refused so
  the two coincide. The day they do not, a bare number would show the top of a range as a price.
- **Mandated labels appear verbatim, with a Vietnamese gloss.** `ƯỚC TÍNH`, `KHOẢNG GIÁ`, `ĐÃ DUYỆT`
  and the seven critical warnings are rendered as `TOKEN · gloss`. The spec mandates the tokens and
  also mandates Vietnamese labels; dropping either is a defect. Server enums are shown the same way
  and never translated away.
- **The 6 kg disclosure.** Two choices had to be made and neither is in any spec, so both are stated
  in the code: the band is 5.0–7.0 kg inclusive plus any unparseable kilogram quantity (erring
  toward disclosure), and the notice describes the *shape* of the cliff — that a lighter load can
  cost more — without stating per-kilogram amounts, which belong to the published pricebook the
  server can republish.

## 9. Offline, accessibility, and the shell

Offline gates every mutating control and queues nothing. `navigator.onLine` plus a standing banner.

Accessibility is treated as an operating requirement rather than a checklist: 44 px minimum targets,
visible focus never removed, labels always present (a placeholder is a hint), `aria-live` on async
regions, `role="alert"` on refusals, focus moved to the screen container on every route change, and
state conveyed by a word as well as a colour.

Routing is hash-based. The static mount has no SPA fallback, so history routing would have required
a catch-all route in the API — a change to request handling on an authenticated surface made as a
side effect of a frontend delivery. `navigate()` is the only writer of `location.hash`, so switching
later is a two-function change.

## 10. Changes made outside `apps/web`

Four, all additive, each with a regression test.

**Three defect fixes** (`apps/api/tests/test_store_scope_refusals.py`, proven to fail before the fix):

1. `create_order`, `list_quotes`, `open_incident`, `list_incidents` caught only `ValueError`. The
   refusals they actually receive — `OrderAuthorizationError`, `StoreAccessError` — descend from
   `PermissionError` → `OSError`, so they fell through to a **500**. `_raise_operations_error` was
   already written to map both to 403; the except clauses never handed them over. This is the most
   common condition in the system: having no `staff_store_assignments` row is the default state of
   every staff user the API can create, because assigning a store has no route. A client treating
   500 as "transient, retry" would retry-storm on it.
2. `ShadowConsoleRepository.list_unknown_sends` had no `limit` bound, unlike every sibling. A
   negative limit reached `LIMIT %s` and PostgreSQL raised → 500; a huge one returned the table.

**One addition:**

3. `GET /internal/v1/stores` over the existing `member_store_ids`. Membership — not role — is what
   every store-scoped route enforces, and there was no way to ask what one's own membership is; the
   runbook told staff to paste a UUID by hand and recorded it as a real gap. An empty list is a
   legitimate answer and the console renders it as "you have no store yet", never as an error.

## 11. Server issues found and deliberately not fixed

These are authorization-model changes, not client wiring. Patching them inside a frontend delivery
would make a security decision without an owner. Reported, not touched:

| Id | Issue | Where |
|---|---|---|
| D2 | `POST /orders/{id}/transition` performs **no store-membership check**. Any operations-staff member of any store can drive any order's commercial state by id. | `packages/db/.../orders.py:218-367` |
| D6 | `GET /stores/{id}/shadow/audit/{aggregate_id}` requires membership of the **path** store but filters only on `aggregate_id`. A member of store A can read store B's audit trail by supplying their own store id. | `shadow_console.py:553-561` |
| D14 | `GET /shadow/unknown-sends` returns `channel_send_receipts` across **every store** with no membership filter; the only gate is `roles & SHADOW_READ_ROLES`, which admits `AUDITOR` and `OPERATOR`. Compare `approvals.list_pending`, which deliberately joins `staff_store_assignments`. | `shadow_console.py:382-401` |
| D7 | Route gate and repository gate disagree on the two Shadow write routes; `OPERATOR` passes one and is refused by the other. Worked around in the client. | `shadow_console.py:38` |
| D8 | No route creates a `staff_store_assignments` row. A newly provisioned staff member can do nothing until someone writes to the database directly. | `shadow_console.py:110-159`, unrouted |
| D9 | `AUDITOR` coverage is inconsistent across read routes. | §5 |
| D12 | `ManualSendResponse.row_version` is hardcoded `1` then `2` rather than read from the row. | `operations.py:546,589` |
| D13 | `queue_recovery` has no exception handling; `OperationsUnavailable` surfaces as 500. | `main.py` |
| — | `replay_available` is never set by the response builder, so it is always `false`. | `main.py` |

D2 and D8 are the two worth acting on first, and they pull in opposite directions: D8 means store
assignment is unmanaged, D2 means one store-scoped route does not check it at all.

## 12. Verification

| Check | Where |
|---|---|
| Every API path the console calls is a route the app serves | `test_staff_console_contract.py`, read from `app.routes` |
| No raw-HTML sink anywhere in the client | same |
| No arithmetic on any `*_vnd` field | same |
| No `indexedDB`, `sessionStorage`, background sync; `localStorage` only in `session.js` | same |
| Declared capabilities exist; store-scoped screens declare `needsStore`; paths unique | same |
| Every module parses (`node --check` against a `.mjs` copy) | same, skipped without node |
| Precache is exactly the assets on disk, names no API path, never writes a response | `test_staff_console_privacy.py` |
| 403-not-500 on store-scope refusal | `test_store_scope_refusals.py` |

### 12.1 Proven against a running stack

Verified on 2026-08-14 against PostgreSQL 16.10 with all 23 migrations applied, the seeded store and
its published pricebook, the real RS256 demo issuer, and the API under uvicorn — not stubs.

- **`812 passed, 1 skipped`** — the whole suite with `--require-postgres-integration`. The single
  skip is a BSD-file-flag test that does not apply to this platform.
- **34 of 34** request-level checks driving the console's exact header and body shapes: sign-in,
  every list, create-quote, idempotent replay (`replayed: true`), revision with `If-Match`, stale
  version refused, over-cap limit, CSRF mismatch refused, `REQUIRE_HUMAN` on an unknown service.
- **The 6 kg cliff is real and unsmoothed**, priced by the server through the console's own request
  shape: 5.9 kg → 147,500; 6.0 kg → 120,000; 6.1 kg → 122,000.
- **The defect fix, end to end:** all four previously-500 routes return `403 operation denied`
  against real PostgreSQL for a store the caller is not a member of.
- **Measured RBAC confirmed:** `AUDITOR` reads the order board and Shadow drafts (200) and is
  refused the quote list and approval queue (403) — exactly what `rbac.js` predicts, and exactly
  where the published spec matrix would have been wrong.

### 12.2 Proven in a real browser

Chrome via Playwright, run transiently — no dependency was added to the repository — driving the
genuine OIDC sign-in through a single origin, at phone (390×844) and desktop (1440×900) widths, in
light and dark:

- **Zero CSP violations** against the real header
  (`script-src 'self'; style-src 'self'; connect-src 'self'`, no `unsafe-inline`, no nonce).
- **Zero uncaught page errors and zero failed requests** across all eleven screens.
- Two console entries, both on the phone context and neither from a console screen: the expected
  `401` from the signed-out session probe, which is the correct behaviour that renders the
  signed-out screen; and one `404` that persisted after `icon.svg` was added, so it is **not** the
  console's favicon. It is most likely the demo issuer's own sign-in page, which has no icon and
  was navigated in the same context — but the failing URL was not captured and the stack has been
  torn down, so that attribution is **unverified** and is recorded here as unverified.
- The whole module graph loads and **all eleven screens render** under a DOM stub in Node.
- The mandated labels verified in a genuinely priced quote: `ƯỚC TÍNH`, `TAX UNVERIFIED`, and the
  reason codes shown verbatim.
- The 6 kg disclosure appears when a 5.9 kg quantity is typed, before anything is submitted.

Two layout defects were found this way and fixed: the banner slot had no grid area, so the app bar
did not span the sidebar; and the sidebar and app bar both claimed `position: sticky; top: 0` in one
stacking layer, so the sidebar painted over the header. Neither is visible to any source-text test,
which is the argument for running the thing.

**Not adopted: Playwright as a repository dependency.** `IMPLEMENTATION_ROADMAP_V1.md:171` mandates
it for critical operator journeys. It exists in no language here, and adding it means a dependency
group plus a browser download in CI — its own decision with its own gate cost. It was used
transiently for the verification above, which is the cheap way to get the evidence without paying
the standing cost. **Ten scripted journeys remain owed.**

## 12.3 Two corrections, from later work

**The approval recommendation in §13.3 below is wrong.** Returning the three binding fields and a
bound preview does *not* close the supervision loop: `rendered_hash` has no producer anywhere in the
system, `resource_version` has no defined meaning per `resource_type`, and transactional consent is
unmodelled so the spec-mandated recheck would refuse to render content every time. See
[`STAFF_CONSOLE_COMPLETION_PROGRAM_V1.md`](STAFF_CONSOLE_COMPLETION_PROGRAM_V1.md) §2, which
supersedes it. The projection widening remains worth doing as that document's item P7.

**The browser verification in §12.2 overstated what it proved.** It drove the quote builder with
Playwright's `page.fill()`, which sets a field's value in one shot. A human types, and typing
rebuilt the form on every keystroke: `STANDARD_WASH_DRY` entered character by character produced
`S`. The screen was unusable by an operator and the check could not see it. Fixed, and now covered
by a browser check that types with `keyboard.type()` and asserts focus survives — the lesson being
that a UI check which never types has not tested the UI.

## 13. Open questions for the owner

1. **Store assignment (D8).** Nothing works for a new staff member until this is provisioned. Route
   it, or document the SQL procedure as an operational runbook?
2. **Transition scoping (D2).** A real cross-store authorization hole. Fix is small; the decision is
   whether it lands as a security item with its own evidence.
3. **Approval decisions.** Unblocking needs the queue to return `resource_version`, `snapshot_hash`
   and `rendered_hash`, *and* a bound preview of the resource. Approving from a hash alone is blind
   approval regardless of what the UI does.
4. **The 6 kg disclosure band.** 5.0–7.0 kg was chosen here. No spec defines "near".
5. **SLA risk.** The read model exists and needs a per-order `ProductionSlaPolicy` — a business
   decision with no configuration source. One decision unblocks a finished screen.
6. **Vite.** Not now. The prerequisites are enumerated; the choice of whether to pay them is an
   architecture decision, not a frontend one.
