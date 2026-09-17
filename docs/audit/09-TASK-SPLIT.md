# 09 — Task split

## Claude executes autonomously (after approval of `08-REFACTOR-PLAN.md`)

- [ ] R-01 mechanics — project `resource_version`, `snapshot_hash`, `rendered_hash` onto
      `ApprovalResponse`; regenerate the API contract; remove the stale disclosure entry
- [ ] R-03 — `Tạo đơn từ báo giá này` on the accepted-quote card
- [ ] R-04 — `copyable()` on the contact id
- [ ] R-05 — platform guard fix; clean-clone green on Linux ×3
- [ ] R-06 — CSS wrapping; zero horizontal overflow at 390px on 12/12 screens
- [ ] R-07 — bind `ABSENT_ROUTE` / `ABSENT_WRITER`; empty parameter set becomes a failure
- [ ] R-08 — `matchesFilter()` with NFD + `đ/Đ` folding; four call sites collapsed
- [ ] R-09 — disable inputs on denied forms
- [ ] R-10 mechanics — `422` mapping (wording to be signed off, see below)
- [ ] R-11, R-12, R-13
- [ ] A regression test per P0 that fails before and passes after
- [ ] Playwright journey suite for all five personas, retained as the evidence for §13.5

## Human-required — I will not do these

- [ ] **R-02 — what an approver sees before approving.** F-01b. Returning the three hashes makes the
      button pressable; it does not make the decision informed. Deciding what a `MESSAGE_DRAFT`
      exposes, and to whom, is a policy and liability call.
- [ ] **D-04 — incident intake.** `contact_scope_hash` / `evidence_summary_hash` semantics are an
      open owner decision (`docs/DECISION_REQUEST_INCIDENT_INTAKE_2026-09.md`). Guessing would
      violate `CLAUDE.md`'s "unknown means stop".
- [ ] **F-02 / S-05 — renaming "Trợ lý AI" and restructuring `GIÁM SÁT AI`.** Product naming.
- [ ] **Final Vietnamese wording sign-off with a native CSKH operator**, especially R-10's error
      sentences. My register assessment is a reading, not a speaker's judgment.
- [ ] **Setting AI autonomy levels A0–A2 per surface.** Risk and liability, not engineering.
- [ ] Any irreversible deletion or data migration.
- [ ] Production access, secrets, credential rotation.
- [ ] Live Zalo OA / payment / shipping integration testing — all `NOT_AUTHORIZED`.
- [ ] Resolving spec contradictions (none blocking found; see `00-SPEC-LEDGER.md` §3).
- [ ] Accepting the final "shippable" judgment.

## Deliberately not handed to you

Everything I could verify myself, I verified myself rather than asking: the tenancy probe, the
idempotency and stale-write probes, the IME composition test, the 390px overflow measurement, the
clean-environment test run, and the end-to-end order creation that proved D-01 is a form defect and
not a backend one.
