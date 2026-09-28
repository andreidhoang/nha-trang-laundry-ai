# Decision record — the five remaining gaps (2026-09-28)

**Authority.** The business owner wrote on 2026-09-28: "ultrathink on what you can solve right now
and best decision for these and spec engineering and execute implementation", naming e-invoices,
automatic bank-transfer confirmation, the 10% late-delivery credit, automatic pickup reminders and
an AI-written summary. This extends the delegation recorded under `DEC-028` and `DEC-034`–`DEC-039`.
It is **delegated, not signed**, and is recorded that way so the registry stays true.

**Boundaries every ruling keeps:**

- The software asserts no legal or tax basis. Anything that rests on one waits for the owner to
  publish it, and a feature that needs one refuses by name until then.
- No money moves by a rule the owner has not ratified. The only money figures used here are the
  ratified ones (`DEC-004`: late by more than 2 hours by the shop's fault earns 10% credit on the
  next bill; staff approve up to 100.000 ₫) and the owner's own published storage figures.
- Nothing here decides `DEC-001`–`DEC-006`. No provider is called, no credential is created, no
  public ingress is opened, and no capability's authorisation changes.

The question for each gap was the same: **what part is blocked by something only the owner can
give, and what part is engineering that the shop needs anyway?** The engineering part is built
now. The blocked part is reduced to one switch the owner turns.

---

## DEC-040 — E-invoices: capture every invoice request now; issuing stays with the owner's provider

**Grounding.** Hotels and homestays on an account (`DEC-035`) need a *hóa đơn* for their own
books, and walk-in companies ask for one too. Today the request is said out loud and lost. A
Vietnamese e-invoice can only be issued through a licensed e-invoice provider under the shop's own
tax code and signature. Which invoice type the shop issues, at what rate and on which date are the
owner's and the accountant's to settle, and they depend on the shop's own tax registration.

**Decision.**

- **Record the request, never issue the invoice.** *Khách cần hóa đơn* can be added to an order,
  or to an account customer's month. It stores the buyer's details at the moment of the request:
  - unit name (required);
  - tax code (optional; 10 digits, 10 digits + `-` + 3 digits, or 12 digits);
  - address (required when a tax code is given);
  - invoice email (optional);
  - buyer name (optional).
  An account customer's details are saved on their record and pre-filled next time.
- **Every request has a visible state.** *Hóa đơn cần xuất* lists the open requests, oldest first,
  with the amount the shop charged. The bookkeeper issues the invoices in the provider's own portal,
  from a download of that list. Staff then record the invoice's symbol (*ký hiệu*), number and date,
  which closes the request as issued. Each request is one of: REQUESTED, ISSUED, or CANCELLED with a
  reason.
- **No tax arithmetic, no tax words.** The list and the download carry the amounts as charged, and
  they are labelled as such. The software never splits a tax, never names a rate and never calls
  anything it prints a *hóa đơn*. The customer receipt stays *Phiếu cho khách*.
- **Personal data.** A buyer name or email can identify a person, so capture refuses with
  `PRIVACY_NOTICE_UNPUBLISHED` until the owner publishes the privacy notice (`DEC-034`), as customer
  records do.
- **Not built:** automatic issuing through a provider's API. That needs the owner to choose a
  provider, confirm the invoice type and rate with the accountant, and supply the account and its
  credential. None of those is engineering.

**Reversal.** Hide the screen; requests stay on record.

## DEC-041 — Bank transfers: an exact QR on every bill now; automatic confirmation waits for a feed

**Grounding.** Customers in Vietnam pay by scanning a QR code. When they type the amount and the
memo themselves, the memo is empty or wrong, the amount is off, and the counter cannot tell which
order a transfer was for. Automatic confirmation needs a feed of the shop's incoming transactions: a
bank's API or a notification service, with an account, a credential, and either a public webhook or
an outbound connection. Every one of those is the owner's to choose (`AGENTS.md`: credentials,
provider calls, public ingress).

**Decision.**

- **A VietQR code for the exact amount due.** The QR follows the NAPAS VietQR / EMVCo standard,
  built and checksummed on the server, with no provider call. It carries:
  - the shop's account;
  - the amount still owed, read from the payment ledger at that moment;
  - a transfer code that names the order: `NTL` + ticket day (`ddmm`) + ticket number (3 digits),
    for example `NTL2809012`, or `NTL` + 8 characters of the order's id when it has no ticket.
    Account months use `NTLCN` + 6 characters of the account id + `mmyy`.
  The QR is shown on the order page at *Thu tiền*, on the printed *Phiếu cho khách* while money is
  owed, and on the account statement.
- **The owner publishes the account once.** `scripts/publish_bank_account.py` takes the bank's
  NAPAS BIN, the account number, the account holder's name and the bank's display name. Before
  publishing, `--preview` writes a test QR worth 1.000 ₫. The owner scans it and pays it from their
  own phone, then publishes with `--test-transfer-confirmed`. A wrong BIN is caught by that test,
  not by a customer at the counter. Until the account is published every QR refuses with
  `BANK_ACCOUNT_UNPUBLISHED` and the counter works as it does today.
- **Confirmation stays a person's glance, made exact.** When staff record a transfer, the sheet
  shows the transfer code and the amount to look for in the bank app. The order search also finds an
  order by its transfer code, so a transfer that arrives later leads straight to its order.
- **Not built:** automatic confirmation. Its contract is written in the build spec. Incoming
  transactions are recorded in a normalised form. A match needs an exact transfer code and an amount
  no larger than what is owed. A match is proposed and confirmed with one tap, never posted
  silently. This waits for the owner to choose a feed service or bank API and to decide on its
  credential and its network path.

**Reversal.** Withdraw the published account; no QR is shown.

## DEC-042 — The 10% late-delivery credit: measured by the server, decided with one tap

**Grounding.** The ratified rule (`DEC-004`): a delivery more than 2 hours late through the shop's
fault earns 10% credit on the next bill. The remedy already exists, but a person has to notice the
lateness, open an incident and type the minutes. Delivery orders now carry a promised time
(`DEC-037`) and a recorded arrival (the successful RETURN leg), so the lateness is a fact the server
can measure.

**Decision.**

- **The clock.** The deadline is the first promise. Only a *hẹn lại* made because the customer asked
  (`CUSTOMER_REQUEST`) moves it, to that new time. Every other reason (machine, workload, drying,
  extra treatment, other) is on the shop's side and does not move it. Otherwise re-promising would
  quietly cancel the customer's credit.
- **Measured, not typed.** The minutes late are the time between the deadline and the successful
  RETURN leg. An order late by more than the published threshold (120 minutes) appears on *Giao trễ
  cần xử lý*. Any failed delivery attempt made before the deadline is shown beside it ("Giao 14:05
  không gặp khách"), because that points to the customer's side.
- **One tap each way.**
  - *Lỗi của tiệm — giảm 10%* opens the late-delivery incident and the `LATE_DELIVERY_CREDIT`
    proposal, with the measured minutes and the shop's fault confirmed by the person pressing. The
    existing remedy rules then apply unchanged: the 10% is computed by the domain on the settled
    total, staff approve up to 100.000 ₫ and the owner above, the credit goes on the next bill, and
    there is none on a refunded bill.
  - *Không phải lỗi tiệm* records a reason: customer not at home, wrong address or phone from the
    customer, customer asked for a later time, or other with a short note. The order leaves the list
    and the decision is kept.
- **Reported.** Late deliveries, credited, not the shop's fault, and still undecided.
- **Not built:** a credit for a self-collect order that was not ready on time. The ratified rule
  covers deliveries only, and the on-time figure already reports the rest.

**Reversal.** Hide the list; the manual remedy path is unchanged.

## DEC-043 — Pickup reminders: the server decides who, when and what; staff send in two taps

**Grounding.** Sending automatically on Zalo needs an Official Account, templates approved by Zalo,
a token and a per-message cost: all the owner's. What a Vietnamese shop actually loses today is
simpler. Nobody remembers to message the customer on day 3, day 7 and day 14. Then the laundry
reaches day 21 with no attempts recorded, and neither the storage fee nor a donation can be defended.

**Decision.**

- **A fixed schedule, counted in shop days from *đồ đã xong*.** Day 0 "đồ đã xong", then day 3,
  day 7 and day 14. The fifth reminder falls on the last shop day before the storage fee starts and
  exists only when the storage policy is published; its text quotes the owner's published figures
  exactly. From day 21 the existing *Đồ chờ lấy* flow takes over. A reminder is due until a contact
  attempt is recorded for that step.
- ***Nhắc khách lấy đồ*** on *Hôm nay* lists the reminders due, each with three actions:
  - *Mở Zalo* (a `zalo.me` link to the customer's number);
  - *Chép tin nhắn*, which copies a fixed Vietnamese text built on the server with the ticket, the
    ready day, the amount still owed, the opening hours and, for the last step, the fee;
  - *Đã nhắc*, which records a contact attempt (channel and outcome) against that step, in the same
    append-only log the disposal rule already counts.
- **The existing egress guard runs first.** *Chép tin nhắn* refuses with
  `MESSAGING_POLICY_UNPUBLISHED` until the owner publishes the messaging policy (`DEC-033`), and
  with `SUPPRESSED` for a customer who wrote STOP. A call (*Gọi*) stays available as it is today.
  Orders with no phone and no chat contact are counted as unreachable, not hidden.
- **No model writes the text and nothing is sent by the software.** The template is versioned
  (`pickup-reminder-v1`).
- **Not built:** sending automatically through a Zalo Official Account or ZNS. That waits for the
  owner's account, template approval and credential. The schedule and the text above are what that
  channel will send.

**Reversal.** Hide the list; contact attempts stay on record.

## DEC-044 — The evening summary gets "what needs attention", computed, not written by a model

**Grounding.** What the owner actually wants from an "AI summary" is to be told what is unusual and
what needs doing. A language model adds a provider, a data-sharing decision (`DEC-006`, which is
the security and privacy owner's and stays open) and a chance to misstate a number, and it adds no
fact the database does not already hold. The ruthless call for a two-person shop is that
deterministic rules deliver the value now, at zero cost and zero risk.

**Decision.**

- A ***Cần chú ý*** block heads the evening summary. It holds at most five lines, in this order,
  and prints a line only when its condition is met:
  1. deliveries late and not yet decided (`DEC-042`);
  2. orders past their promised-ready time and not ready;
  3. pickup reminders due and not done, and laundry reaching the storage fee within 3 days
     (`DEC-043`, `DEC-036`);
  4. invoice requests older than 3 days (`DEC-040`);
  5. today's collected money or orders received outside 70–130% of the average for the same weekday
     over the previous four weeks, stated with both figures and only when at least 3 of those weeks
     have data, and on the day itself only from closing time (20:00). After the 10th of a month, it
     also names the margin cost categories *last* month still has no line for, when the shop traded
     last month. Bills for a month arrive early in the next, so a nudge about the current month
     on the 11th would be noise.
- Every figure is computed by a versioned read model and only printed by the template. A source that
  cannot answer is left out with its reason, never shown as zero. The text still carries no
  personal data.
- **Not built:** prose written by a model. If the security and privacy owner opens `DEC-006`, the
  contract in the build spec applies: the model receives these computed facts only and returns
  wording only, and a deterministic check refuses any number in its text that the facts do not
  contain, falling back to the template.

**Reversal.** The block is omitted; the summary is as before.
