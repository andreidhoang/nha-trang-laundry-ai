# Decision request — how a member of staff opens an incident

**Date:** 2026-09-10
**Status:** OPEN. Raised by `WORKFLOW-CONFORMANCE-001`; needs a `DEC-` number when the owner takes it up.
**Trigger:** the R1 shop release. A customer complaining at the counter is a week-one event, and today
the console cannot record one.
**Measured:** 2026-09-10 by driving `#/incidents` in a browser as `OWNER_ADMIN` against a real API and
database, then searching the whole repository for a producer of the two values the form demands.

---

## 0. What this request does not do

It does not choose. Both questions below decide what a stored hash *means*, and the answer binds the
agent pipeline as much as the counter. An engineer settling it while fixing a form would make this
system's only such convention a side effect of a screen change.

## 1. The finding

`POST /internal/v1/stores/{store_id}/incidents` requires two fields
(`apps/api/src/nha_trang_laundry_api/main.py:305-309`), both `^sha256:[0-9a-f]{64}$`:

- `contact_scope_hash`
- `evidence_summary_hash`

**Nothing in this repository produces either value.** No route returns one, no screen renders one, no
script computes one; a search for a digest call naming either field finds nothing outside request
models and INSERT statements. `apps/api/tests/test_staff_console_behaviour.py` now asserts that, so
the day a producer exists this document and the console copy have to be revisited.

Both are agent-pipeline concepts. `specs/contracts/agent-tools-v1.openapi.yaml:932` binds a fact
reference to the contact scope it was resolved for, so a fact resolved for one customer cannot be
used in a message to another; `packages/db/src/nha_trang_laundry_db/incidents.py:30` defaults
`actor_type` to `AGENT_RUNNER`, which is the caller the command was shaped for. The staff console
calls the same command, and the counter has no equivalent of either value.

**Consequence at the counter:** a customer says their shirt came back stained. The staff member opens
`#/incidents`, fills in the order id, and cannot complete the form. Until today the field hints told
them to "chép nguyên văn" — copy verbatim — from a screen that does not exist. The incident *list*
reads normally, so the shop can see incidents that the agent path would have opened, of which there
are none, because that path is `NOT_AUTHORIZED`.

Related, and part of the same question: `DEC-024`'s `SHOP_FAULT_NO_CHARGE` custody resolution is
described as the case where the shop is at fault, and nothing opens an incident when it is used.
`orders.incident_open` exists in the schema and no code sets it.

## 2. Question one — what is a staff-opened incident's contact scope?

| Answer | Consequence |
|---|---|
| **A. The order's bound contact.** The server derives the hash from `orders.bound_contact_id`, which for a walk-in is the counter ticket (`DEC-013`). | Works for every incident the console can open, because the route already requires an order. Sets the convention for the agent path by precedent. Two incidents about the same walk-in on two different tickets hash differently — correct if a ticket is the customer, wrong if the person is. |
| **B. Drop the field for the staff path.** A staff-opened incident records no contact scope; the column becomes nullable, or a separate staff command is added. | Smallest change, no invented convention. Costs the ability to group incidents by customer later, and makes the column mean different things depending on who wrote the row. |
| **C. Wait for the customer-record layer.** Keep the form disabled and honest until `DEC-015`'s successor gives customers an identity. | Nothing is decided prematurely. The shop records complaints on paper for the whole of R1. |

**Engineering note, not a recommendation:** A and B are both about a day of work. C is free and is what
the console does today.

## 3. Question two — where does the evidence summary live?

`evidence_summary_hash` commits to a summary of the evidence. Nothing stores the summary itself, and
`docs/RETENTION_STORE_001_DESIGN_REVIEW_2026-08.md:460` records that `evidence_assets` — the spec's
home for it (`DOMAIN_DATA_API_SPEC_V1.md:614`) — is not built, while `DEC-009` settled that
customer-supplied media is never fetched.

| Answer | Consequence |
|---|---|
| **A. Hash only.** Staff type a summary, the console hashes it, the text is discarded. | No new personal data is stored. The hash proves the summary has not changed and proves nothing about what it said — nobody can read it back, including the owner deciding fault. |
| **B. Store the summary.** A text column on `customer_incidents`, with a retention class. | The incident becomes usable evidence for the remedy decision `DEC-004` already governs. Brings a new personal-data class into scope, so it needs a retention answer alongside `DEC-008`. |
| **C. Neither yet.** No summary, no hash; the incident records only the order and the time. | Honest and small. A complaint recorded with no description is close to a tally mark. |

## 4. What the shop does until this is answered

Written on `#/incidents` and in the gap register: record the complaint on paper against the ticket
number and tell the owner the same day. The incident list stays readable, and no invented hash is
written into a table whose rows are append-only.

## 5. What was changed today, and what was not

**Changed** — the console stopped implying a source exists. `#/incidents` states that the form cannot
be completed and why, `#/gaps` carries an entry naming this document as the blocker, and a test
asserts the "no producer" claim so it cannot go stale quietly.

**Not changed** — the route, the request model, the table, and the agent contract. None of them is
wrong; they are waiting on the two answers above.
