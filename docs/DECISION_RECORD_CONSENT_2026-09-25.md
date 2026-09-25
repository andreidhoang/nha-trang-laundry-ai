# Decision record — a STOP stops service messages too (`DEC-033`), 2026-09-25

**Authority.** Delegated, not an owner signature. The business owner wrote on 2026-09-25: *"continue
with the remaining follow-up items and make decisions as real founder of the business and lead
engineer"*, which extends the delegation recorded under `DEC-028`. The lead took rulings 1, 2 and 4
under it and they are registered as **delegated** so the register stays true about who decided what.

**The boundary kept.** `DEC-028` says a delegated decision asserts no legal basis. Ruling 3's content
— which grounds count for a service message, and for how many hours — *is* the shop's legal basis
for sending service messages under Vietnamese personal-data rules, so it is **not** decided here.
It is shipped as a recommendation the owner confirms by publishing
(`docs/POLICY_TRANSACTIONAL_MESSAGING_V1.md`). Until the owner publishes it, every service send is
refused with `MESSAGING_POLICY_UNPUBLISHED`. Nothing here sends a message; the only sender that
exists is a human's manual send.

**The gap closed.** A manual send is hardcoded `TRANSACTIONAL` and ran no consent or suppression
check at all: `check_egress_allowed` modelled only `MARKETING`, `suppression_entries.purpose` admitted
only `MARKETING`, and the ingress STOP wrote a marketing row only. A customer who wrote STOP could
still be sent a service message by staff (`SECURITY_RELIABILITY_SPEC_V1.md` §7.1, §8.4, §14;
`STAFF_CONSOLE_COMPLETION_PROGRAM_V1.md` §2 point 3).

Each ruling is reversible, and says how.

---

## 1. A STOP stops everything the shop initiates on that channel

**Ruling.** An exact registered withdrawal writes suppression for both `MARKETING` and
`TRANSACTIONAL` on that contact and channel (`SUPPRESSED`); an ambiguous opt-out sets both to
`PENDING_REVIEW_BLOCKED`. One consent event per purpose, both citing the same inbound message, in
the same transaction and under the same advisory lock as before. Other channels are untouched.

Existing rows: migration `0053` (forward-only, additive) widens the purpose checks and gives every
existing `SUPPRESSED` / `PENDING_REVIEW_BLOCKED` marketing row a `TRANSACTIONAL` twin citing the same
source consent event — the conservative reading of a withdrawal recorded before the purposes were
separated. The `0008` projection guard still refuses to undo a withdrawal; its one new exception is
the release below.

**Why.** A customer who writes "STOP" is not distinguishing marketing from service. Reading it
narrowly is the reading that can hurt them.

**Reverse:** stop writing the `TRANSACTIONAL` row in `inbox._insert_suppression` (one tuple). Rows
already written stay, and are lifted one by one with the release below; `0053` is not reverted.

## 2. Release is a human act with server-verified evidence

**Ruling.** A `TRANSACTIONAL` block (`SUPPRESSED` or `PENDING_REVIEW_BLOCKED`) is lifted only by an
`OWNER_ADMIN` or `OPS_APPROVER` with MFA, a member of the store, citing an inbound message the server
verifies is from this contact binding, on this channel, a customer message that was not itself an
opt-out, received after the consent event the block rests on. The console offers the message from
the server's own list; it is never typed. The release appends a `RELEASE` consent event (with who
released it and for which store) and moves the entry to `CLEAR`, with its domain event, audit row and
outbox row in one transaction, under the same advisory lock. `MARKETING` is never released this way:
a marketing grant needs a real consent request (§8.4).

**Why.** The customer writing to the shop again is the only evidence the server can prove. A note,
a phone call or a staff member's memory is not.

**Reverse:** withdraw the route (`POST …/service-messaging/release`); every block then stays until an
engineer changes the rule. Narrowing it to `OWNER_ADMIN` is one line (`RELEASE_ROLES`).

## 3. A service message needs a basis the server can prove — **owner's to confirm**

**Rule as built.** `check_egress_allowed(purpose="TRANSACTIONAL")`, inside the caller's transaction
after the advisory lock: (a) no `TRANSACTIONAL` suppression — `SUPPRESSED` refuses as `SUPPRESSED`,
`PENDING_REVIEW_BLOCKED` / `UNKNOWN_BLOCKED` as `REQUIRE_HUMAN`; no row or `CLEAR` passes this half;
and (b) at least one basis the **published** policy names: `CUSTOMER_INITIATED` (an inbound customer
message from this contact on this channel within `service_window_hours`) or `OPEN_ORDER` (an order
bound to this contact that is not closed, or completed within `closed_order_grace_hours`). No
published policy refuses with `MESSAGING_POLICY_UNPUBLISHED`. Window edges are inclusive to the
microsecond; a cancelled order records no close time and earns no grace.

**Recommended, not decided:** 48 hours, 72 hours, both bases —
`templates/transactional-messaging-policy-dec-033.json`. Publishing it
(`scripts/publish_messaging_policy.py --actor-id <owner>`, refused for anyone but an active owner) is
the owner's confirmation that these are the shop's grounds.

**Reverse:** publish a changed document (fewer bases, other hours); it becomes the next version with
no deploy. There is no unpublish: the stricter reversal is a document with the narrower basis.

## 4. Where it is enforced

**Ruling.** (i) `ManualSendRepository.prepare`, before the envelope that makes the content copyable
exists — refused means nothing is written; (ii) `ManualSendRepository.attest`, which re-checks in its
own transaction, so a STOP that arrived after the envelope was locked refuses the attestation;
(iii) raising a `SEND_MESSAGE` approval over a `MESSAGE_DRAFT` — advisory-early, it refuses only on
a `TRANSACTIONAL` suppression (a basis or a policy may yet appear before the send); (iv) the worker /
outbox path — **no automated TRANSACTIONAL sender exists**: the outbox worker claims only internal
event types, `ChannelSendReceiptRepository` records attempts nobody makes, and
`ApprovalRepository.claim_execution` is exercised only by evals. Whoever builds a sender
(`CHANNEL-ZALO-001`) must call the guard in its send transaction. The guard's answer — decision,
suppression state, basis, policy version and digest, the instant judged — is recorded on the
`MANUAL_SEND_APPROVED` and `MANUAL_SEND_RECORDED` event payloads.

**Reverse:** removing any one enforcement point reopens the gap it closes; none should be removed
without replacing it.
