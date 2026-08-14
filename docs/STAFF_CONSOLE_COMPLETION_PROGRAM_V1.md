# Staff Console Completion Program V1

**What this is:** the specification for finishing the staff operations console — every screen wired,
seamless, safe, and correct — and an honest account of what "finishing" can mean given what the
system underneath it can currently do.

**Baseline, stamped deliberately.** Measured at HEAD `18c6c00`, with `STORE-ASSIGNMENT-001`
`IN_PROGRESS` and holding an uncommitted working tree. Queue census at that point: **76 items — 38
COMPLETE, 22 PENDING, 15 BLOCKED, 1 IN_PROGRESS**. Route census, enumerated from the built `app`
object rather than by grep: **29 routes**, two of which are uncommitted and belong to the in-flight
item. Three of the six surveys behind this document were falsified by commits that landed while they
were being written; `docs/DELIVERY_BOARD.md:13` is stale by six items. **Any plan against this
codebase must stamp its baseline or repeat that.**

---

## 1. "Done" is not fourteen screens

`IMPLEMENTATION_ROADMAP_V1.md:373-387` lists fourteen M3 screens, and six of them cannot be built
because the aggregates they read do not exist. Chasing fourteen would mean building most of the
domain, which is not a frontend programme and is not what would make the console useful sooner.

The console is done when **the two daily loops the system is actually driving at both close**, with
no hand-copied identifiers and no step performed outside the console. Both loops are required for
`G1_INTERNAL_SHADOW_READY`, which is the only capability gate currently in play — every entry in
`delivery/CAPABILITY_STATUS.yaml` reads `NOT_AUTHORIZED`, and `INTERNAL_SHADOW` is the one marked
`IN_PROGRESS`.

**Loop A — agent supervision.** `agent drafts → human reviews → human approves → human sends by hand
→ human attests → unknown outcomes reconciled`. This is the AI product's daily operation.

**Loop B — order fulfilment.** `quote → order → intake → production → ready → settle → complete`.
`SHADOW-001` is an internal pilot **on real orders**, so Loop B has to work before Loop A has
anything true to talk about.

Everything outside those two loops — CRM, delivery legs, remedies, cost analytics, the reporting
dashboard, the unified inbox — serves G2 and later. Deferring it is correct sequencing, not neglect,
and §8 says so per item.

---

## 2. A correction to the previous recommendation

The `STAFF_CONSOLE_ENGINEERING_SPEC_V1.md` conclusion — that returning `resource_version`,
`snapshot_hash` and `rendered_hash` on the approval queue, plus a bound preview, "closes the
supervision loop" — **is wrong, and this document supersedes it.** It is the cheap and safe half of
a change whose expensive half is blocked on three unresolved things:

1. **`rendered_hash` has no producer anywhere in the system.** Every occurrence in `apps/` and
   `packages/` is a function parameter, a dict key or a pass-through; nothing computes one outside
   test fixtures. An approval envelope for `SEND_MESSAGE` therefore requires a value that no code
   path can legitimately generate. **A rendered-message artefact with a canonical hash has to exist
   before the loop can close** — that is a producer, not a projection.
2. **`resource_version` has no defined meaning per `resource_type`.** It is caller-supplied and
   checked only `>= 1` (`packages/domain/.../approvals.py:135`). For `QUOTE_REVISION` it could be
   `quote_revisions.revision` or `quotes.row_version`. Any preview claiming to "recheck the current
   revision" (`SECURITY_RELIABILITY_SPEC_V1.md:387`) is a silent no-op until this is pinned per type.
3. **Transactional consent and suppression are unmodelled.** `check_egress_allowed`
   (`packages/db/.../consent_egress.py:84-92`) returns `REQUIRE_HUMAN` for every purpose except
   `MARKETING`, and manual send is hardcoded `TRANSACTIONAL`. A spec-faithful preview would refuse
   to render content **100% of the time**.

The projection widening is still worth doing and is item **P7** below. It must not be allowed to
absorb the producer problem, because an item that ships "the approval queue now returns the binding
fields" and calls the loop closed would be the most consequential false-completion available here.

**Loop A cannot close in this programme.** Loop B can.

---

## 3. The tiers, and the one that matters

| Tier | Meaning | Count |
|---|---|---|
| **Shipped** | screen exists, backend exists, wired and tested | 11 screens |
| **Thin wiring** | domain and repository complete and tested, no route, no new table, no decision | 5 items |
| **New aggregate, no decision** | needs a table but no owner call | 1 item (`SETTLEMENT-001`, queued) |
| **Decision-gated** | blocked on an owner decision, not on engineering | 4 screens |
| **Calendar-bound** | blocked on weeks of external process | 4 screens |

The thin-wiring tier is the whole opportunity: **domain code that is written, tested, and has never
executed in production because nothing calls it.**

> **The trap this programme exists to avoid.** `OrderTransitionCommand` already carries
> `intake_target`, `production_target`, `intake_readiness` and `production_accepted_at`
> (`packages/db/.../orders.py:61-65`); `OrderRepository.transition` already dispatches all three
> dimensions (`:281-297`); the single `UPDATE orders` already writes all four columns (`:311-317`).
> **Zero callers exist — including zero tests.** Those branches have never run.
>
> This makes "intake and production are one route away" irresistible and *operationally false*.
> `transition_production` refuses unless intake is `ACCEPTED` (`domain/orders.py:175-176`, DB CHECK
> `0007:243`), and `CONFIRMED → ACTIVE` refuses unless intake is `ACCEPTED` (`domain/orders.py:119-120`,
> DB CHECK `0007:244`). Ship the route while deferring `ACCEPTED` as "the part that needs a decision"
> and you get a real route, passing tests, a closed work item — **and an order board that still
> stalls at `CONFIRMED`.** The failure is silent and looks like success.

---

## 4. Where each screen stands

| # | M3 screen | State | Tier | What it waits on |
|---|---|---|---|---|
| 1 | login | partial | — | Not an OIDC client; `connect-src 'self'` forbids it. Deployment supplies the entry point. |
| 2 | new inquiry / customer | absent | decision | `DEC-011` **is not registered** — see §8 |
| 3 | quote builder | shipped | — | usability gated on **P3** (catalog read) |
| 4 | approval queue | read-only | see §2 | **P7** widens it; deciding needs a producer |
| 5 | order board | shipped | — | enriched by **P4**, paginated by **P5** |
| 6 | order timeline | shipped | — | — |
| 7 | intake / measurement | absent | thin wiring | **P1** |
| 8 | production / ready | absent | thin wiring | **P1** (cannot fire without intake `ACCEPTED`) |
| 9 | delivery legs | absent | decision | `DEC-003`, sequenced behind 20 delivery logs |
| 10 | payment | absent | new aggregate | **P2** = `SETTLEMENT-001`; only unusual shapes need `DEC-010` |
| 11 | incident | shipped | — | remedy needs `DEC-004` |
| 12 | machine / batch / staff-minutes | absent | calendar | `SHOP-INSTRUMENT-001`, 4–6 weeks |
| 13 | delivery-cost capture | absent | calendar | `SHOP-INSTRUMENT-001` |
| 14 | daily dashboard + export | absent | downstream | needs every numerator above first |

Console-specific and not in M3: draft review, unknown-send reconciliation, manual send, queue health,
staff administration, the gap catalogue — all shipped. SLA risk is **P6**.

---

## 5. The programme, in forced order

Every item sequences after `STORE-ASSIGNMENT-001` records evidence, because it currently holds
`main.py`, `operations.py`, `approvals.py`, `shadow_console.py` and `store_access.py`. **New
migrations start at 0025** — an untracked `0024` is inside that lease.

### P1 — Order lifecycle transition route, including intake `ACCEPTED`
**Forced first: it is the linchpin.** `ACTIVE`, all of production, `RELEASED` and therefore
`COMPLETED` every one gate on intake `ACCEPTED`. Nothing downstream moves without it.

Ships as: a widened request model mirroring the repository's exactly-one-dimension rule
(`orders.py:220-229`); one service method; one route; timezone-aware enforcement on
`production_accepted_at` (a naive value raises `CanonicalizationError`, which the transition route's
`except` tuple does not catch, so it escapes as a **500** — `main.py:783`); an **append-only staff
attestation** recording the six `IntakeReadiness` booleans; and the first tests three repository
branches have ever had. Inherits store scoping, RBAC, `If-Match` CAS and atomic commit for free.

**The readiness-authority question is blocking, not deferrable** — it gates 100% of this item's
value. Recommended resolution, on precedent the repo already set: `TASK-settlement-001.md:37-44`
accepts, as in-scope and decision-free, "a fact a staff member witnesses at the counter… the same
shape as `manual_send_attestations`". Six booleans recorded as an audited, attributed, append-only
attestation is that identical trust shape — the authority is *recorded*, not borrowed from a request
body that vanishes. **This needs the owner's yes.** If refused, P1 collapses to the `REJECTED` path
alone and the whole order re-sequences.

### P2 — `SETTLEMENT-001` *(queue-owned — reference, never respecify)*
Forced second: selectable today, but its own Done-When — "takes an order from creation to
`COMPLETED`" (`TASK-settlement-001.md:89`) — is **unreachable until P1 exists**. Its `depends_on`
lists only `QUOTE-COMMAND-001` and is therefore incomplete. P1 + P2 together produce **the first
legitimately transitioned `COMPLETED` order in the system's history**; today the only one that ever
existed was `INSERT`ed by an eval fixture. Exact payment in full at handover needs no decision —
`DEC-010` covers only the shapes beyond it.

### P3 — Catalog read route
Highest capability-per-hour in the programme. Pure read, no decision, no new table: the payload
already sits in one `configuration_versions` row and `published_price_rules` reconstructs it
losslessly (`pricebook_import.py:115-169`).

This is a **correctness** defect, not ergonomics. `service_code` is free text validated only by
`^[A-Z][A-Z0-9_]{1,62}$`. A well-formed but wrong code passes both the client and server regexes and
surfaces as `PRICE_RULE_UNRESOLVED` — a reason code that accuses the pricebook rather than the
typist. Nobody can use the quote builder without having memorised 43 service codes.

### P4 — The read-back bundle
`GET /orders/{id}`; `GET` quote revision, store-scoped (`QuoteRepository.get_revision` takes a bare
cursor and performs **no membership check**, unlike its sibling `list_for_store` — fix on the way
past); `session_id` on `SessionResponse` so sessions can be listed and revoked; order-board field
enrichment. One quote-revision endpoint serves both the builder's read-back and P7's preview — do not
build two.

### P5 — Keyset pagination and a matching index
Must land **before `SLO-VERIFY-001`**, or that item certifies a truncated board. `NFR-PERF-004`
requires 10,000 orders at p95 < 2s "với pagination/index"; **both halves are absent.** Every list is
`LIMIT`-only, capped at 200, with no cursor and no offset. The only index is
`(store_id, commercial_status, created_at)` while the query orders by `created_at DESC, id`, so
`commercial_status` sits between the two columns the query needs and Postgres must sort. At 10,000
orders the board is not slow, **it is truncated** — order 201 is unreachable and there is no
`GET /orders/{id}` to fall back to. `ORDER BY created_at DESC, id` is already a unique, stable keyset.

### P6 — SLA risk board
The read model is finished and unrouted (`shadow_console.py:497-549`) and the three policies are
owner-confirmed. Gated on one mapping decision (§8) and on that file being released from its lease.

### P7 — Approval queue projection widening + `GET /approvals/{id}`
The **safe slice only.** All three binding fields are already `NOT NULL` columns (`0007:124-126`)
and `_lock_approval` already selects them (`approvals.py:507-509`); only `list_pending` drops them.
Also widen the queue's `JOIN orders o ON o.id = r.resource_id` (`approvals.py:489`), which silently
hides **10 of the 12 `ApprovalAction` resource types including `SEND_MESSAGE`**, and relax
`WHERE s.status = 'REQUESTED'` (`:492`), which makes an approved envelope vanish the instant it is
approved — leaving manual send no surface to start from.

**The bound preview and manual-send completability are a separate, later item** blocked by §2.

### P8 — Console seamlessness fixes — **DONE**
All four were fixed while this document was being written, because three of them made screens that
are live today unusable rather than merely awkward. Covered by a browser check that **types**
(`scripts`-external, `keyboard.type()` with a per-character delay) and asserts focus and field
contents survive; verified to fail when reverted.

- **Quote lines rebuilt on every keystroke.** `onInput → onChange() → redrawBuilder →
  render(builderBody, …)` called `replaceChildren`, destroying focus after the first character.
  Typing `STANDARD_WASH_DRY` produced **`S`**. Structural changes (adding or removing a line) now
  rebuild; a typed character updates state in place and refreshes only the validity flag and that
  line's 6 kg notice. The caret is preserved when uppercasing.
- **A 401 never reached the session.** Nothing called `session.end()` when a request came back 401 —
  only the boot path did. A session that idled out left the app bar listing the operator's roles and
  the navigation looking live while every submit failed. `core/api` now notifies one observer from
  the single place all responses pass through, and `core/session` registers at module load.
- **Typed input lost when the session ended.** `session.js` promised "a half-typed incident is still
  on screen"; `contentKey()` included `status`, so `end()` re-rendered and discarded the form. The
  screen is no longer rebuilt on an ending session, and a banner says the input was kept.
- **Connectivity blips re-rendered the screen.** `online`/`offline` notified the session, which
  rebuilt the current screen and discarded the form — several times a shift on shop wifi. Excluded
  from the content key; the eleven network-dependent controls update live instead.

---

## 6. Work no screen owns

| | Item | Status |
|---|---|---|
| T1 | Pagination and cursors | **P5** |
| T2 | Typed input across a 401 | **P8** |
| T3 | Data freshness — no screen records its fetch time; seven of eleven have no reload control | **unowned** |
| T4 | Search and filter — none anywhere; with a 200 cap and no `GET` by id, a named order cannot be found | **unowned** |
| T5 | Time zones — `Asia/Ho_Chi_Minh` pinned and applied correctly | **non-defect** |
| T6 | Concurrent editors — `If-Match` CAS is right, but the board never refreshes, so the first sign of a colleague's edit is a 409 | folds into T3 |
| T7 | Printing and receipts — none. A counter taking exact payment at handover has no artefact for the customer | **unowned** |
| T8 | Bulk operations — none, and correctly so given per-row CAS | **deliberate non-goal** |

T3, T4 and T7 should be scoped into P4/P5 rather than left to be rediscovered.

---

## 7. Invariants that must survive the growth

Every one of these is enforced by a test today and must still be as the surface triples: no
arithmetic on any `*_vnd`; `null` never rendered as `0`; no raw-HTML sink; nothing persisted on the
device but a store UUID; no automatic retry of a write; no offline write queue; every mutation
carries an idempotency key; controls disabled with a reason rather than hidden; every path the client
calls exists on the built `app`; the service worker precaches the shell and nothing else.

Two to add as the programme lands: **every new list route ships with its cursor** (P5 is much harder
retrofitted), and **every new screen declares its data's fetch time** (T3).

---

## 8. Decisions, ranked by what they unblock

| Decision | Status | Unblocks | Cost to decide |
|---|---|---|---|
| **Intake readiness authority** | unregistered | **P1, and therefore P2 and every downstream screen** | one owner conversation; recommendation in §5 |
| `DEC-010` settlement shapes | **OPEN, registered** | partial/over/on-account payment only — *not* the ordinary case | afternoon |
| SLA scope mapping | unregistered | P6 | needs care: pricebook categories and SLA scopes do not align, and the catch-all `SLA_OTHER_SPECIAL` is `PENDING`, not `OWNER_CONFIRMED` |
| `DEC-011` CRM | **does not exist** — registry stops at `DEC-010` | screen 2 | opening it is an owner act |
| `DEC-003` delivery | OPEN | screen 9 | sequenced behind 20 delivery logs — confirm whether that ordering binds |
| `DEC-004` remedy | OPEN | incident remedy | afternoon |

The first row is the whole programme's critical path and is not currently registered anywhere.

---

## 9. Out of scope, with reasons

CRM (`DEC-011` unopened) · delivery legs (`DEC-003`, measurement-first) · custody, machine/batch and
delivery-cost capture (all behind `SHOP-INSTRUMENT-001`, 4–6 weeks of real measurement) · reporting
dashboard (downstream of every numerator above) · unified inbox (`webhook_events.encrypted_payload`
has neither an encryptor nor a decryptor in source) · bulk operations (deliberate) · **store
assignment — already owned by `STORE-ASSIGNMENT-001`, including deletion of its `#/gaps` entry.**

---

## 10. What this adds up to

P1 through P4 close Loop B and make the console genuinely operable for a real-order pilot: an
operator prices from a picker instead of memory, takes an order, records intake, moves production,
settles, and the order reaches `COMPLETED` — for the first time in this system's existence.

P5 through P7 make it hold up: paginated, current, comprehensible under concurrency. P8 is done.

**Loop A does not close here,** and no amount of frontend work closes it. It needs a rendered-message
producer, a per-type meaning for `resource_version`, and a transactional consent model — three
backend and policy questions that §2 sets out and that this programme deliberately does not pretend
to answer.
