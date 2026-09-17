# 07 — Subtraction pass

Test applied to every screen, panel, column, toggle and badge: *which persona, in which daily task,
would be unable to do their job without this?*

## 1. The honest answer: there is very little to delete

This codebase has already been through a subtraction pass
(`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md`, landed 2026-08-17, whose §3.2 is itself a noise
audit). Dead CSS, dead aliases and duplicate notices were removed then. I looked for the usual
targets and did not find them:

| Usual target | Found here? |
|---|---|
| Decorative metrics nobody acts on | No — the one money tile is explicitly labelled *"không phải chỉ số hiệu suất"* |
| Settings with one sensible value | No settings screen at all |
| Duplicate navigation paths | No |
| Columns nobody reads | No |
| Empty states that explain nothing | No — every empty state names what would fill it and why it is empty |
| Tooltips compensating for a bad label | No `title`-only affordances carrying meaning |
| Modals that could be inline | No modals at all |

## 2. Delete

| # | Item | Why | Blast radius |
|---|---|---|---|
| S-01 | The disclosure **"Tại sao nút Duyệt đang tắt?"** on `/approvals` | Deleted *by fixing F-01*, not on its own. Once the three fields are projected the button works and the disclosure becomes false. Deleting the explanation is the goal; the disclosure must go in the same slice as the fix, or it becomes a lie. | `approvals.js` + the disclosure registry entry |
| S-02 | 166 `DESCRIPTIVE` disclosure entries that no test can falsify | Not deleted — **promoted**. See `08-REFACTOR-PLAN.md` R-07. A claim that cannot be checked is not a disclosure, it is a comment. | `specs/contracts/console-disclosures-v1.yaml` |

## 3. Merge

| # | Item | Into | Why |
|---|---|---|---|
| S-03 | Four copies of the list-filter predicate (`orders.js:347`, `incidents.js:346`, `quotes.js:781`, `orderRequests.js:196`) | one `matchesFilter()` in `core/format.js` | Identical code, identical defect (G-02). One fix instead of four. |
| S-04 | `Tạo đơn` form on `/orders` | the accepted-quote card on `/quotes` | The form's four inputs exist only because the data has to cross a screen boundary by clipboard. Put the action where the data already is and three of the four inputs stop existing. This is the subtraction that matters: **deleting a confusing form beats explaining it**, and the explanation currently on the screen is false anyway (U-09). |

## 4. Keep — explicitly

These look like candidates for deletion and must not be deleted:

- **`/gaps` (Chưa hỗ trợ)** — 21 entries, no fetch, pure prose. It is the compliance surface that
  makes every other screen's silence legible, and `IMPLEMENTATION_ROADMAP_V1.md:394` makes "no
  hidden unsupported default" an exit criterion. Deleting it would convert honest absence into
  silent absence.
- **The 6kg cliff panel** on `/quotes` — it is long, it appears mid-form, and it is the single most
  important thing on the screen. `CLAUDE.md` names it as a confirmed rule, not a bug to smooth.
- **`ƯỚC TÍNH · chưa phải giá cuối` badges** — repeated on every money figure. The repetition is
  the point.
- **`MANUAL_SEND_RECORDED không có nghĩa là khách đã nhận`** — the longest notice in the app and
  the one that prevents the worst operational mistake.
- **The disabled-with-reason pattern for the auditor.** Hiding those controls would teach staff the
  capability does not exist; disabling them teaches who to ask.

## 5. The one structural subtraction worth proposing

**S-05 — collapse `GIÁM SÁT AI` from four nav entries to two.**

Today: `Duyệt`, `Trợ lý AI`, `Bản nháp AI`, `Ngoại lệ`. Of these, `Duyệt` cannot decide (F-01),
`Bản nháp AI` has no drafts because no agent runs, and `Trợ lý AI` is not AI (F-02). A CSKH agent
reads four AI supervision entries and can act on none of them.

Proposal, **for owner decision, not unilateral**: rename the section `GIÁM SÁT`, move `Trợ lý AI`
out of it (renamed `Hỏi nhanh`, it belongs under `VẬN HÀNH`), and keep `Duyệt` + `Ngoại lệ` +
`Bản nháp AI` as the three genuine supervision surfaces — all three of which become real once F-01
lands. This reduces the section to what it claims to be rather than removing capability.
