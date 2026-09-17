# TASK-field-debrief-001 — the conversation that happened gets written down

**Goal:** a sales conversation the owner had at a customer's door becomes a structured row in
`templates/interactions.csv` from a sixty-second Vietnamese voice note, instead of becoming nothing.

**Domains:** `business_truth`, `privacy_consent`

**Stable work item:** `FIELD-DEBRIEF-001`

**Stage:** assist plane (`specs/CUSTOMER_SUPPORT_AND_ACQUISITION_SPEC_V1.md` §8) — no release gate.
**Risk:** LOW as code. **MEDIUM as privacy**, and the whole packet turns on that distinction.

**Status of this packet:** drafted, **not enqueued**. See §6.

---

## 1. Why this exists

`docs/CLIENT_ACQUISITION_EXECUTION_2026-08.md` §5.4 identifies it in one line:

> *"Ghi lại sau khi gặp … **Đây là chỗ tự động hoá có giá trị nhất mà không ai nghĩ tới** — vì đây
> chính là bước thật sự hỏng trong bán hàng nhỏ lẻ: không ai ngồi ghi CRM sau khi đi về."*

The plan for the next weeks is thirty conversations at thirty doors. The step that fails is not the
conversation and not the follow-up message. It is the ten minutes afterwards that nobody spends.

`templates/interactions.csv` already exists with the right columns —
`interaction_id, account_id, contact_id, direction, channel, occurred_at, summary, permission_basis,
human_approver, next_step, next_step_at, outcome` — and has **one row: the header**. That is the
measurement.

## 2. What this is not

- **Not a CRM.** It writes a spreadsheet. `DEC-015` resolved that no customer-record layer exists,
  and this packet does not reopen it.
- **Not outbound.** It has no recipient, no channel credential, no send client, and no reachable
  messaging endpoint. It may draft a follow-up; a human sends it.
- **Not in the record plane.** It never writes to PostgreSQL. Not to `orders`, not to
  `contact_channel_bindings`, not to `consent_events`.
- **Not a judgement.** It structures what the note says. It does not infer permission, does not
  score the lead, and does not decide the next step — it transcribes the one the owner stated.

## 3. Scope

### 3.1 Input

One audio file plus the `account_id` the owner names. Nothing else. No contact list, no database
handle, no web access during the run.

### 3.2 Output

Exactly one appended row in `templates/interactions.csv`, plus a plain-text transcript retained under
the note's retention class. Every field is either present in the note or empty. **There is no
inference step**, and `permission_basis` in particular is copied from what the owner said or left
empty — never guessed from tone, and never filled because a row looks incomplete.

If the note names a person who has not agreed to anything, that name goes in the **transcript**,
which is the owner's own record, and **not** into `templates/contacts-consent.csv`. A row crosses
into that file only when the owner states that permission was given, and states the wording.

### 3.3 The one thing this may write that looks like a promise

Nothing. A `next_step` is what the owner said they would do. If the note contains a commitment the
owner made to the prospect — a price, a slot, a volume — it is recorded verbatim in `summary` and
flagged, because `SALES_AND_NURTURE_PLAYBOOK.md` forbids promising a slot and AI-confirmable capacity
is still **0 kg/day**. Flagging an already-made human promise is a record, not a policy decision.

## 4. Constraints

- **`DEC-006` applies and is not waived by calling this tier "internal".** A note about a sales visit
  names a third party and their role; that is personal data, and sending it to a model provider is
  exactly what `DEC-006` governs. Two admissible paths: a local model on the shop's Mac, or a
  narrowly scoped `DEC-006` answer covering this use and no other. **Pick one explicitly and record
  which.** Neither is chosen here.
- **Assist-plane isolation** (`specs/CUSTOMER_SUPPORT_AND_ACQUISITION_SPEC_V1.md` §S11). No channel
  credential. No provider messaging endpoint reachable. A test asserts the second.
- **No chain-of-thought is stored.** `AGENTS.md` forbids it, and a debrief is exactly the shape of
  output where it leaks in as "reasoning about the prospect".
- **Append-only.** A correction is a new row, not an edit, so a re-run cannot silently rewrite what
  the owner said last week.
- **Deterministic identity.** `interaction_id` is derived from the note's content hash and the
  timestamp, so the same note processed twice produces one row, not two.

## 5. Acceptance checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy apps packages
uv run pytest
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus, specific to this item and not satisfiable by a green suite:

- the same note processed twice appends one row;
- a note that states no permission produces an **empty** `permission_basis`, and a test asserts the
  field is not filled from surrounding context;
- a note naming a person produces **no** row in `templates/contacts-consent.csv`;
- the process holds no database credential and cannot resolve a provider messaging endpoint;
- no transcript, summary or intermediate text is written outside the note's declared retention class;
- the chosen `DEC-006` path (local model or scoped answer) is recorded in the item's evidence, with
  the effective provider request captured if a provider was used.

## 6. Why this is not enqueued

Two reasons, and only the first is about scheduling.

1. **`DEC-006` is open.** Enqueueing an item whose first line of work is a model call, when no model
   call in this project has ever been authorized, would either sit `BLOCKED` as noise or imply the
   decision is expected to go a particular way. Adding a row to `delivery/WORK_QUEUE.yaml` is a
   scheduling act and belongs to whoever schedules.
2. **The work it serves has not started.** This automates the write-up of door conversations. There
   have been none. Building the recorder before the first visit optimises a step nobody has taken
   yet, which is the failure mode `docs/PRODUCTION_READINESS_ASSESSMENT.md` keeps finding in this
   repository under a different name: building ahead of the evidence that would shape the build.

**So the honest trigger is: enqueue this after the first ten door conversations have happened and
been written up by hand.** Ten hand-written rows are the specification for the automatic ones, and
they cost nothing to produce.
