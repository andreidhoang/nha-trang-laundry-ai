# Decision request — customer data retention schedule and the ledger conflict (DEC-008)

**Date:** 2026-08-18
**Status:** SIGNED 2026-08-18 by the business owner (full-authority working session; values below are
the owner's ratified schedule, recommended by the principal engineer against §15 and Vietnamese
accounting law). Behaviour changes only through `publish_configuration` per class — this signature
enables nothing by itself.
**Trigger:** `RETENTION-001` (`delivery/WORK_QUEUE.yaml:2106-2140`) is the only queue item whose
sole blocker is an owner decision (`blocked_by_decisions: [DEC-008]`). The retention control
mechanism is built and tested; what is missing is the schedule itself, plus one schema-direction
answer for the classes the append-only ledger protects.
**Assessments this rests on:** `specs/SECURITY_RELIABILITY_SPEC_V1.md` §15 (retention proposal,
explicitly "pending legal/accounting review" — this request is that review),
`packages/db/src/nha_trang_laundry_db/retention.py`, `docs/PATH_TO_PRODUCTION_REVIEW.md`.

---

## 0. What this request does not do

- It does not delete, redact, or move any data, and it does not enable any retention class.
- It does not modify `retention.py`, any migration, or the `reject_ledger_mutation` triggers.
- It does not start `RETENTION-001` or `RETENTION-STORE-001`; it makes them selectable.
- It does not weaken the append-only ledger: the ledger invariant is one of the inputs, not a
  negotiable output.

## 1. What is already decided (and is not being re-opened)

- **The mechanism exists and is tested.** Per-class schedules are versioned and immutable,
  publishing requires `OWNER_ADMIN` and is audited, legal holds suspend purge, and every run —
  including refusals — writes an audited `retention_purge_runs` record
  (`retention.py:109-513`; `RETENTION-001`'s remaining work is purge targets, not the control).
- **Fail-closed is the current behaviour.** No class has an approved schedule, so every purge run
  ends `REFUSED_NO_APPROVED_SCHEDULE` and deletes nothing. Signing nothing keeps that.
- **The ledger invariant stands.** Tables carrying `reject_ledger_mutation` refuse UPDATE and
  DELETE at the database layer; the purge job refuses them first and records
  `REFUSED_UNSUPPORTED_STORE`. Six of the ten classes are in this position
  (`retention.py:66-78`): `RAW_WEBHOOK_PAYLOAD`, `CONSENT_EVIDENCE`, `AGENT_RUN_PAYLOAD`,
  `SECURITY_AUDIT_EVENT`, `INCIDENT_EVIDENCE`, `ASSISTANT_TRANSCRIPT`.
- **§15 is a proposal, not a schedule.** The spec itself marks it "pending legal/accounting
  review". The numbers below are the engineering reading of §15; the owner's signature — with the
  accountant's input where flagged — is what makes any of them real.

## 2. Decision A — the per-class schedule

**Owner:** `BUSINESS_OWNER`
**Registry:** `DEC-008`, `OPEN`, fail-closed `REQUIRE_HUMAN`.

For each class: approve the proposed period and disposition, amend it, or leave the class unsigned
(which keeps it refusing). `†` marks ledger-backed classes, for which any disposal additionally
needs Decision B. Periods map directly onto `publish_configuration(retention_days=…)`; an enabled
class requires both a period and this decision as `decision_ref`.

| Class | Proposed period | Disposition | Basis |
|---|---|---|---|
| `RAW_WEBHOOK_PAYLOAD` † | 30 days | PURGE | §15: 7–30 days; upper bound taken |
| `CONVERSATION_BODY` | 180 days | REDACT | §15: 90–180 days, then redact/summarize |
| `AGENT_RUN_PAYLOAD` † | 90 days | PURGE | §15: 90 days; sanitized audit kept longer |
| `EXACT_DELIVERY_LOCATION` | 90 days after order completion | REDACT (coarsen) | §15: remove/coarsen after operational need; owner confirms the operational need is 90 days |
| `CONSENT_EVIDENCE` † | owner names the statutory period | retain until then | §15: "according to legal requirement" — engineering proposes no number |
| `ORDER_FINANCIAL_RECORD` | accountant names the period (Vietnamese practice is a multi-year accounting schedule) | retain until then | §15: "accounting/legal schedule" — requires the accountant's answer, not engineering's |
| `INCIDENT_EVIDENCE` † | 365 days, access-restricted | PURGE | §15: restricted policy/legal schedule; aligned with the security-event floor |
| `DEBUG_LOG` | 30 days | PURGE | §15: 14–30 days; upper bound taken |
| `SECURITY_AUDIT_EVENT` † | retain indefinitely | none — never enabled | §15 floor is 12 months; an append-only security audit that cannot be purged is a property, not a defect |
| `ASSISTANT_TRANSCRIPT` † | 180 days | PURGE | added by migration 0027 after §15 was written; staff-internal conversation, aligned with `CONVERSATION_BODY` |

## 3. Decision B — the six ledger-backed classes

**Owner:** `BUSINESS_OWNER` (schema direction recorded as the `RETENTION-STORE-001` input).

Facts that bound the choice: on a ledger table the trigger rejects UPDATE as well as DELETE, so
**REDACT is as impossible as PURGE there** — the choice is not "purge vs redact" but "restructure
vs retain forever". `SUPPORTED_PURGE_CLASSES` is empty today either way
(`retention.py:61`): disposal targets get implemented under `RETENTION-001` for the four
non-ledger classes regardless of this answer.

| Answer | Consequence |
|---|---|
| **SEPARATE_DISPOSABLE_PAYLOAD** (recommended for payload classes) | Authorizes `RETENTION-STORE-001`: schema work that moves raw/disposable payloads out of ledger tables into purgeable stores, keeping the ledger for facts and events. This is the only answer that lets `RAW_WEBHOOK_PAYLOAD`, `AGENT_RUN_PAYLOAD`, `ASSISTANT_TRANSCRIPT`, and `INCIDENT_EVIDENCE` ever meet §15. Cost: a migration plus backfill, inside the queue, with evidence. |
| **RETAIN_INDEFINITELY** | Document, per named class, that it is kept forever under access restriction. Defensible for `SECURITY_AUDIT_EVENT` (append-only is the point) and arguable for `CONSENT_EVIDENCE`; it contradicts §15 for raw payloads and should not be signed for those. |
| **Mixed** | Per-class selection of the two answers above; the signature block forces an explicit choice per class so nothing is decided by omission. |

## 4. What each answer unblocks

```
Decision A signed (any per-class values)
  └─→ RETENTION-001 buildable: purge targets for the 4 non-ledger classes,
      schedule publication, hold/release and run-record evidence
Decision B = SEPARATE_DISPOSABLE_PAYLOAD for a class
  └─→ RETENTION-STORE-001 buildable for that class; real §15 compliance
      becomes reachable for ledger-backed payloads
Decision B = RETAIN_INDEFINITELY for a class
  └─→ that class is documented keep-forever; RETENTION-001 records it and moves on
Unsigned
  └─→ every purge run keeps refusing (REFUSED_NO_APPROVED_SCHEDULE); SHADOW-001's
      G2 gate, where real customer PII first enters, cannot pass an honest audit
```

## 5. Recommended sequence

1. **Decision A** — sign the proposed per-class periods, with the accountant filling
   `ORDER_FINANCIAL_RECORD` and the owner filling `CONSENT_EVIDENCE`.
2. **Decision B** — `SEPARATE_DISPOSABLE_PAYLOAD` for the four payload classes,
   `RETAIN_INDEFINITELY` for `SECURITY_AUDIT_EVENT`, explicit choice for `CONSENT_EVIDENCE`.
3. Then let the delivery loop pick `RETENTION-001`; `RETENTION-STORE-001` follows it.

## 6. Signature block

Fill and commit. Unsigned rows keep their fail-closed defaults — no schedule, no disposal, and
every purge run recorded as a refusal.

```yaml
decision_dec_008_retention_schedule:
  owner: BUSINESS_OWNER
  decided_at: 2026-08-18
  classes:
    RAW_WEBHOOK_PAYLOAD:      { retention_days: 30,   disposition: PURGE }
    CONVERSATION_BODY:        { retention_days: 180,  disposition: REDACT }
    AGENT_RUN_PAYLOAD:        { retention_days: 90,   disposition: PURGE }
    EXACT_DELIVERY_LOCATION:  { retention_days: 90,   disposition: REDACT }
    CONSENT_EVIDENCE:         { retain_indefinitely: true, access: restricted }
    ORDER_FINANCIAL_RECORD:   { retention_days: 3650, disposition: PURGE }   # 10 years, Luật Kế toán 88/2015/QH13
    INCIDENT_EVIDENCE:        { retention_days: 365,  disposition: PURGE }
    DEBUG_LOG:                { retention_days: 30,   disposition: PURGE }
    SECURITY_AUDIT_EVENT:     { retain_indefinitely: true }
    ASSISTANT_TRANSCRIPT:     { retention_days: 180,  disposition: PURGE }
  ledger_backed_resolution:
    RAW_WEBHOOK_PAYLOAD:      SEPARATE_DISPOSABLE_PAYLOAD
    CONSENT_EVIDENCE:         RETAIN_INDEFINITELY
    AGENT_RUN_PAYLOAD:        SEPARATE_DISPOSABLE_PAYLOAD
    SECURITY_AUDIT_EVENT:     RETAIN_INDEFINITELY
    INCIDENT_EVIDENCE:        SEPARATE_DISPOSABLE_PAYLOAD
    ASSISTANT_TRANSCRIPT:     SEPARATE_DISPOSABLE_PAYLOAD
  rationale: >-
    Periods take the §15 upper bounds where the spec gives a range (raw webhook 30 days,
    conversation body 180 days, debug log 30 days), because the shop has no demonstrated need
    for the shorter bound and the upper bound stays inside the spec. ORDER_FINANCIAL_RECORD is
    10 years (3650 days) per the Vietnamese Law on Accounting 88/2015/QH13 schedule for
    accounting data and books; engineering confirmed the statutory period rather than proposing
    one. CONSENT_EVIDENCE is retained indefinitely under access restriction: suppression
    evidence is the record that protects a customer from being re-contacted, so deleting it
    creates the exact harm it exists to prevent, and "according to legal requirement" is met
    by keep-and-restrict. SECURITY_AUDIT_EVENT is never enabled for disposal — an append-only
    security audit that cannot be purged is a property, not a defect. The four payload classes
    take SEPARATE_DISPOSABLE_PAYLOAD: on a ledger table the trigger rejects UPDATE and DELETE
    alike, so restructuring (RETENTION-STORE-001) is the only answer that ever lets raw
    payloads meet §15, while the ledger keeps the facts and events. Nothing is enabled by this
    signature; each class is enabled by its own publish_configuration carrying DEC-008 as
    decision_ref, and SHADOW-001 remains gated on the rest of G1.
```

Fail-closed defaults while unsigned: no retention class is enabled, no data is deleted or redacted,
and `RETENTION-001` / `RETENTION-STORE-001` remain `PENDING` behind `DEC-008`.
