# 10 — Retracted candidate findings

§1.1 of the directive: *a finding you cannot evidence is deleted, not softened.* Four candidates
were investigated and dropped. They are recorded here because a reader who runs the same probes
will hit the same traps, and because an audit that reports only confirmations is not measuring
itself.

## R-01 — "The quote form submits nothing" — RETRACTED

First run showed `Tính giá` producing no network request. Cause: my selector set
`main select` index 0 (the fulfilment mode) which re-rendered the line rows and cleared the service
selection. With `#quote-line-0-code` addressed directly, the form returns
`201 POST /internal/v1/stores/…/quotes`. **Harness defect, not an application defect.**

## R-02 — "The accepted-quote card disappears after acceptance" — RETRACTED

My button filter excluded any label matching `Sao chép`, which removed the entire result card from
the output. The card is present after acceptance, showing `Bản sửa đổi 2 / ĐÃ DUYỆT`, the quote id
with a copy button, `Phiên bản dòng v2`, and the snapshot hash with a copy button. **Observation
error.**

The surviving finding from that area is narrower and stands: there is no `Tạo đơn` action on the
quotes screen (`03-TRACE.md` D-01).

## R-03 — "The approvals screen renders no decision buttons" — RETRACTED, then re-established stronger

With an empty queue the only button is `Tải lại`, which looked like decision controls were absent
entirely and made `gaps.js:298`'s claim that they *"hiện ra nhưng bị vô hiệu hoá"* look false. After
seeding a real approval, the buttons do render, disabled, with a reason. `gaps.js` is accurate.

The finding was re-established on stronger evidence and is now F-01 — which is more serious than
the version I retracted, not less.

## R-04 — "The assistant shows a contradictory empty state next to a live answer" — RETRACTED

`Chưa có câu hỏi / Viết câu hỏi rồi bấm Gửi` appeared above a successful answer. Cause: the
suggestion chips call `void ask()` directly (`assistant.js:683-688`), so my subsequent click on
`Gửi` submitted an already-cleared textarea and legitimately produced the empty-question warning.
**Harness defect.**

## Environment contamination, disclosed

Running `uv run pytest` with `DATABASE_URL` pointed at the demo database truncated the seeded
identity rows, and sign-in began returning `401 identity exchange rejected`. This was my error, not
the application's. The database was re-seeded and all subsequent test runs used an isolated
`ntl_test` database. No finding in this audit derives from the contaminated window; the
`1188 passed / 1 failed` figure in `02-BACKEND.md` is from the clean isolated run.
