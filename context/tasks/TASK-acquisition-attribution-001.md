# TASK-acquisition-attribution-001 — every order says where the customer came from, and nothing guesses it

**Goal:** an order carries one attested `acquisition_source`, written by the staff member who took
it, at the moment it is created, never afterwards and never by a model. The shop can then answer
"which channel produced a paying order" with a query instead of an opinion.

**Domains:** `business_truth`, `orders_audit`

**Stable work item:** `ACQUISITION-ATTRIBUTION-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW as code, LOW as privacy. This item stores a channel name on an order. It stores
nothing about a person, and it is the one slice of `ACQUISITION-001` for which that is true.

---

## 1. Why this exists now and not on 2026-08-18

`context/tasks/TASK-acquisition-001.md` §3.3 specified this field and then said, correctly at the
time, that it was "genuinely useless today: `orders` cannot receive rows at all until 3.1 or 3.2
lands, and attribution over an empty table measures nothing."

**3.1 landed eight days later and this was not revisited.** `DEC-013` and `DEC-015` were both
resolved 2026-08-26, `packages/db/migrations/0032_counter_ticket.sql` shipped the counter identity,
and `OrderRepository.create` now accepts a customer reference from either a counter ticket or a
channel binding (`packages/db/src/nha_trang_laundry_db/orders.py:192-209`). Orders can be created.
The precondition this slice was waiting on is met.

So the finding that opens `docs/CLIENT_ACQUISITION_EXECUTION_2026-08.md` §1.1 — "hệ thống hiện chưa
ghi nhận được một khách hàng nào" — **is no longer true**, and that document's §10 pointer to an
unenqueued packet is the only remaining trace of a blocker that has been gone since August.

## 2. Why it is worth doing before the shop opens rather than after

Attribution is one of the few facts in this system that **cannot be backfilled**. Order state can be
corrected, money can be reconciled, a mis-typed ticket can be traced. But nobody can be asked in
November how a customer found the shop in September, and no model may infer it — §4 forbids exactly
that. An order taken before this field exists is permanently unattributable.

That is not urgency: the shop has not cut over, so nothing is being lost yet. It is *ordering*.
Built before the first real order, the attribution record has no hole in it. Built after, it has a
hole exactly as wide as the pilot, which is the period whose numbers the owner most needs.

## 3. Scope

`ACQUISITION-001` §3.3 exactly, and nothing else from that packet.

A closed enum on `orders`, `NOT NULL`, written at creation:

`WALK_IN` · `GOOGLE_MAPS` · `ZALO` · `FACEBOOK` · `PARTNER_FRONT_DESK` · `REFERRAL_CUSTOMER` ·
`LEAFLET_QR` · `RETURNING` · `UNKNOWN`

Three properties of that list are load-bearing and are not stylistic:

**`UNKNOWN` is a first-class value and the console must not discourage it.** A staff member who did
not ask must be able to say so without a warning colour, a confirmation, or a hint that reads as
disapproval. A required field with no honest option is a field that gets filled with a lie, and a
channel report built on lies is worse than no report, because it will be believed.

**`RETURNING` is the customer's claim, not the system's knowledge.** `DEC-015` resolved that no
customer-record layer exists, so this system is structurally incapable of recognising a returning
customer — two orders from the same person share nothing. The console label says so. If it did not,
`RETURNING` would read as a fact the database checked.

**`PARTNER_FRONT_DESK` ships as a bare enum value with no partner reference.** `ACQUISITION-001`
§3.3 wants an organisation reference so a per-partner QR is measurable, and that reference is
deferred here: no QR can be printed until `DEC-017` answers which name goes on it, and a partner
table whose rows no asset points at would be a schema for a decision that has not been made.
Adding the reference later is additive; the enum value does not change.

## 4. Constraints

- **Attested, never inferred.** No model, no heuristic, no parse of a message body. The staff member
  who takes the order records where the customer said they came from.
- **Written once.** Enforced in the database by extending `enforce_order_projection_update()`
  (`packages/db/migrations/0008_operations_constraints.sql:76`), which already guards `store_id`,
  `bound_contact_id` and `created_at` the same way. Application-level enforcement alone would be a
  convention; the trigger is a constraint.
- **Out of the tool facade entirely.** No agent-reachable tool may read or write it. `apps/public-agent-tools`
  is hash-pinned and is not touched by this item; a contract test asserts the absence rather than
  trusting it.
- **No consent state moves.** `MarketingDeliveryRepository` continues to hold every marketing send
  with `MARKETING_AUTHORIZATION_UNAVAILABLE`, and `suppression_entries.CLEAR` stays unreachable.
  Knowing which channel produced a customer is not permission to message them.

## 5. What this still cannot measure, stated so the report is read correctly

- **Cost per acquisition.** `SHOP-INSTRUMENT-001` is `BLOCKED`; contribution per order and per kg
  are unmeasured. This item yields orders per channel, which is a count, not a return.
- **Repeat rate and lifetime value.** No customer record exists by decision (`DEC-015`), so the
  system cannot tell two orders from one person apart from two orders from two people.
- **Anything about people who did not order.** Prospects live in `templates/accounts.csv` and no
  code reads them, per `RESEARCH_BRIEF.md` §12. This measures the last step of the funnel only.

## 6. Acceptance checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus, specific to this item and not satisfiable by a green suite:

- an order carries exactly one acquisition source, written at creation, and an `UPDATE` that changes
  it is refused by the database, not by Python;
- `UNKNOWN` is reachable from the console in one interaction and carries no warning styling;
- no route, tool or schema under `apps/public-agent-tools` mentions the field;
- the channel report distinguishes "no orders from this channel" from "this channel was never
  offered", and states that it counts orders rather than profit.
