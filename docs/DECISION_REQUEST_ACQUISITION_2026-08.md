# Decision request — how a client becomes a customer: the record, the channel, and the name on the door

**Opened:** 2026-08-18 · **Owner:** `BUSINESS_OWNER` · **Decisions:** `DEC-015`, `DEC-016`, `DEC-017`
**Status:** unsigned. All three fail closed today. None blocks the other two.

These came out of the client-acquisition work, not from a code review. Each is a question that
engineering cannot answer without choosing a business policy on the owner's behalf, and each one
currently stops a specific acquisition motion dead.

Read `docs/CLIENT_ACQUISITION_EXECUTION_2026-08.md` first for why these three and not others.

---

## DEC-015 — What is a customer record, and when does a person become one?

### Why this is being opened now, under this number

Two documents already recommend opening exactly this decision, and both name it `DEC-011`:

- `docs/PRODUCTION_READINESS_ASSESSMENT.md` §5.2 — "`PARTY-001` — decide whether a CRM exists at
  all, then `parties` / `contact_points` / `addresses`. Not currently a registered decision.
  **Recommend opening `DEC-011`.**"
- `docs/STAFF_CONSOLE_COMPLETION_PROGRAM_V1.md` §8 — "`DEC-011` CRM — **does not exist** — registry
  stops at `DEC-010`."

`DEC-011` was subsequently used for the staff identity provider (Keycloak). The CRM recommendation
was left pointing at a number that now means something else, including in the live staff console:
`apps/web/src/screens/gaps.js` shows operators "Chưa có quyết định về CRM (đề xuất mở DEC-011)".
**This request takes the number `DEC-015` and the console pointer is corrected to match.** No
existing decision changes.

### What happens today

**The system can record zero customers, from any source.** Measured 2026-08-18:

1. `CreateOrderCommand.bound_contact_id` is required — `packages/db/.../orders.py:43`.
2. The only writer of `contact_channel_bindings` is `ChannelBindingRepository.resolve_or_create`
   — `packages/db/.../channel.py:112` — keyed on `(provider, provider_user_ref)`.
3. That provider column is `CHECK`ed to `ZALO_OA`, `TELEGRAM_SANDBOX`, `FACEBOOK_MESSENGER` —
   `packages/db/migrations/0020_channel_envelope.sql`. There is no counter value.
4. No provider is connected (`FEATURE_PUBLIC_CHANNELS_ENABLED = "false"`, asserted by two contract
   tests; `DEC-005` records that neither the Telegram token nor the Zalo OA application exists).

So a customer who messages has nothing to message; a customer who walks in has no identity that can
be created. `DEC-013` covers the counter case narrowly. **`DEC-015` is the wider question it sits
inside**, and it is the question every acquisition motion runs into the moment it succeeds.

### What the owner is being asked

1. **Does a customer record exist as a thing at all** — a `parties` aggregate with contact points and
   addresses — or does the shop deliberately keep only orders, with the customer identified by
   whatever the channel supplied?
2. **What may be stored about an organisation** the shop has researched but never spoken to? The
   recommendation on record is: published business name, published business address, published
   business phone, public listing URL, published room count — and **no individual's name, personal
   mobile, or personal email** until that person has agreed. Confirm or change this.
3. **What consent wording do customers actually agree to**, and where is it kept? Vietnam's
   `Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15`, in force 2026-01-01, requires that consent be
   voluntary, informed, **expressed per purpose**, and recorded in a form that can be reproduced as
   evidence (Điều 9); Điều 28 governs personal data in advertising specifically. Its implementing
   decree is **`Nghị định 356/2025/NĐ-CP`** (issued 2025-12-31, effective 2026-01-01) — note that
   **`Nghị định 13/2023/NĐ-CP` ceased to be effective on 2026-01-01** and is no longer the operative
   instrument, which `RESEARCH_BRIEF.md` §11 does not yet reflect.

   `suppression_entries` already models an affirmative `CLEAR` state that must be written by a
   deliberate audited act. What is missing is **the sentence the customer says yes to, and its
   version**.

   Two points of relief worth knowing before this is priced: Điều 39 lets processing that already
   had valid consent under `NĐ 13/2023` continue without re-obtaining it, and Điều 38 lets small
   enterprises defer the impact-assessment dossier and the designated data-protection officer for
   five years (micro-enterprises and household businesses are exempt). **Both defer paperwork, not
   the duty to obtain consent.**

### The boundary this decision is really drawing

> **The sheet holds prospects. The database holds customers. A row crosses when a person agrees.**

`templates/accounts.csv`, `contacts-consent.csv`, `interactions.csv` and `pilots-orders.csv` already
exist, empty, shaped exactly as `RESEARCH_BRIEF.md` §10 specifies, and are read by no code. That is
the deliberate low-tech side of the boundary and `RESEARCH_BRIEF.md` §12 says to keep it that way for
now. This decision says where the line falls and what may cross it.

### Options

| Option | What it means |
|---|---|
| **Sheet for prospects, database only after consent** (recommended) | Costs nothing to start. A researched hotel lives in a spreadsheet; a person becomes a database row only when they have agreed and that agreement is recorded. Keeps the personal-data surface as small as the law prefers. |
| Build `PARTY-001` now | A real CRM in PostgreSQL. Contradicts `RESEARCH_BRIEF.md` §12 at this stage, and creates a processing record for parties who have not been asked. |
| No customer record ever | Orders keyed only to a channel identity. Simplest and most private; makes repeat-customer recognition and B2B accounts impossible. |

### Until it is signed

`ACQUISITION-001` (`context/tasks/TASK-acquisition-001.md`) is written but **not enqueued**. The
prospect list ships as a spreadsheet, organisations only. No person-level row is created anywhere.

---

## DEC-016 — Who staffs the inbound channel, and whose account is it?

### What happens today

`BUSINESS_TRUTH_INTAKE.md` §1 contains this line, and it is blank:

```
- Người trực inbound:
```

Nobody is named. Meanwhile §4 commits the business to a **5–10 minute response** between 08:00 and
20:00, and §5 records that the Zalo presence is "tài khoản theo số hotline 0382 318 492 — **chưa xác
nhận là Zalo OA**" — that is, quite possibly a personal Zalo account on the owner's own phone number.

Two staff work 08:00–20:00 and also do the washing, the folding and the deliveries.

### Why this blocks the acquisition work specifically

Every acquisition motion produces inbound. A leaflet, a hotel front-desk QR, a Google listing, a
listicle entry — all of them end with a stranger sending a message. If no one is named as the person
who answers it, then:

- the 5–10 minute promise has no owner and will be broken in public, in reviews;
- an automated assistant answering out of hours would be making promises no human is behind, which
  the playbook explicitly forbids ("Không được hứa slot");
- the business's customer relationships live in a personal account that leaves with the person.

`RESEARCH_BRIEF.md` §8 is blunt about the last point: an agent is a 24/7 receptionist at best; it
does not make pickup, washing or delivery 24/7.

### What the owner is being asked

1. **Who answers inbound**, by name, in which hours, and who covers the other hours.
2. **Whose account the channel is** — a business Zalo OA owned by `CÔNG TY TNHH A & T CARE`, or a
   personal account. This matters when a staff member leaves.
3. **What the shop says outside 08:00–20:00.** Recommended: acknowledge, capture, promise nothing,
   name the hour a human will reply. Silence and a fake promise are both worse.

### Until it is signed

No channel is connected, so nothing is broken yet. But `CHANNEL-TELEGRAM-001` and
`CHANNEL-ZALO-APPLY-001` should not be started before this is answered, because they deliver
strangers to a phone with no named owner.

---

## DEC-017 — Which name does the shop go to market under?

### What was found, and what was not

Verified on 2026-08-18 from public sources:

- **"Giặt Là Sạch Cộng" is a national franchise chain**, headquartered in Hanoi at
  `giatlasachcong.com`, claiming **500+ stores across 48 provinces**, actively selling franchises
  (`Nhượng Quyền`) with an investment enquiry form ranging from 200 million to over 800 million VND.
- Its **public store directory (`/danh-sach-tiem/`) lists no store in Khánh Hòa at all**, and none at
  3A Lê Đại Hành.
- `CÔNG TY TNHH A & T CARE`, tax code `4202059758`, at Số 3A Lê Đại Hành, Nha Trang, **is confirmed**
  in public Vietnamese tax/business registry listings.
- `BUSINESS_TRUTH_INTAKE.md` records the brand name as `ĐÃ XÁC NHẬN` and sets an identity rule —
  "dùng 'Giặt Là Sạch Cộng' khi giao tiếp thương hiệu" — but **records no franchise relationship
  anywhere**, and there is no franchise agreement, licence, or territory grant in this repository.

**Not verified, and not assumed:** whether this shop holds a franchise agreement with that chain.
That is a fact only the owner has. This request does not assert either answer.

### Why this is a decision and not a footnote

It determines every acquisition asset the shop is about to produce.

**The brand name does not find this shop.** A search for "Giặt Là Sạch Cộng" returns the Hanoi
franchisor, its franchise-sales funnel, and hundreds of other stores. A hotel manager who googles the
name to check who they are about to hand 40 bedsheets to lands on a page selling franchises. Brand
search sends this shop's traffic to 500 competitors-in-name and to a company trying to recruit
investors.

Meanwhile the shop appears in **none** of the "top 10 / top 20 tiệm giặt ủi Nha Trang" listicles that
dominate local search, while at least seven competitors do — including **Smile Laundry at 85/7 Lê
Đại Hành, on the same street, at 30,000đ/kg, advertising pickup and delivery**, and a laundry
operated by a joint-stock company at 11A Lê Đại Hành.

So the shop is invisible under its own brand and absent from the local layer where its neighbours are
visible.

### What the owner is being asked

1. **Is there a franchise agreement?** If yes: what does it require or forbid regarding the shop's
   own marketing, its own Zalo OA, its pricing, and its territory — and why is the store not in the
   franchisor's directory? If no: using the name carries trademark exposure that should be assessed
   before it is printed on leaflets, QR codes and a Google listing.
2. **Which identity goes on customer-facing assets** — the chain brand, a local name, or the legal
   entity? The recommendation on record: market locally on **place and proof** (address, street,
   photographs, the tax code for B2B credibility) rather than on a brand name that resolves
   elsewhere, whatever the franchise answer turns out to be.
3. **Which name goes on a B2B contract?** This one is already settled and should stay settled:
   `CÔNG TY TNHH A & T CARE`, MST `4202059758`, per the playbook's one-page offer template.

### Until it is signed

No customer-facing asset is produced under either name. The prospect list, the route and the offer
draft are all internal. `SALES_AND_NURTURE_PLAYBOOK.md` already forbids issuing the one-page offer
while any field is a guess; the name is now one of those fields.

`BUSINESS_TRUTH_INTAKE.md` §1 has been given a `CẦN XÁC MINH` line recording that the franchise
relationship is unrecorded, so that the identity rule immediately above it is no longer read as
settled business truth. **Nothing about the brand name itself was changed** — the owner confirmed it
and it stays confirmed; what is now marked is the relationship behind it.

One practical note for whichever way this resolves: Google's Business Profile naming rule requires
the profile name to be "your business's real-world name, as used consistently on your storefront",
and keyword-stuffed or mismatched names are the most common cause of profile suspension. So the name
on the signboard and the name on Google have to agree — which means this decision should be answered
**before** the profile is claimed and verified, not after. Re-verification is the cost of getting the
order wrong.
