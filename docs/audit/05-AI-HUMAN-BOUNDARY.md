# 05 — AI / human boundary

## 1. The headline finding

**F-01 (P0) — the A2 gate has no working human side. The approval queue cannot approve.**

Status: **VERIFIED at runtime, then confirmed against the stored row.**

An approval request was created through `POST /internal/v1/approvals`
(`SEND_MESSAGE` / `MESSAGE_DRAFT`, `201`, id `28c6629f-60bb-417d-80d1-d2885d5dd449`, required role
`OPS_APPROVER`, expiring in 30 minutes). The owner then opened `#/approvals`. The console rendered:

```
28c6629f…d449
thời gian còn lại trước khi hết hạn   còn 29 phút
Trạng thái        Đã yêu cầu
Ai được quyết     Người duyệt vận hành
Hết hạn lúc       14:52 17/09/2026
Mã niêm phong     JCS-SHA256-V1:455a0542…e809
[ Duyệt ]  [ Từ chối ]        ← both disabled=true
Không bấm được: quyết định cần ba giá trị mà danh sách này không trả về.
```

Screenshot: `shots/approvals-with-item.png`.

The cause is three missing fields on one read model:

- `POST /internal/v1/approvals/{id}/decisions` requires `resource_version`, `snapshot_hash`,
  `rendered_hash` (`main.py:283-289`, `ApprovalDecisionRequest`).
- `GET /internal/v1/approvals` returns `approval_request_id, status, envelope_hash, required_role,
  expires_at, replayed` (`main.py:336-342`, `ApprovalResponse`) — **none of the three**.

And the three values are *already stored*. `\d approval_requests` shows `resource_version bigint
NOT NULL`, `snapshot_hash text NOT NULL`, `rendered_hash text NOT NULL`, and the seeded row carries
all three plus the full `envelope` JSONB. Nothing needs to be computed, migrated or decided. Three
fields need to be projected onto a response model.

**Why this is the most severe finding in the audit.** The directive's §8 sets A2 — *AI proposes,
human must approve before any external effect* — as the default for anything reaching a customer.
This product's A2 surface exists, is correctly gated, correctly role-scoped, correctly
time-bounded, cryptographically sealed… and **no human in the product can press the button**. An
approval created at 14:22 expires unactioned at 14:52. Every customer-facing action the system is
designed to gate is therefore permanently blocked, and the safety property "a human approved this"
is unachievable rather than merely unexercised.

The console is honest about it — the screen badges itself **"Chỉ đọc"** and says *"Đây là màn hình
chỉ đọc. Không có thao tác nào ở đây ghi vào máy chủ, kể cả khi một việc sắp hết hạn."* Honesty is
not a substitute for the capability. `gaps.js:298-309` describes the condition accurately,
including the correct reason that approving on a hash alone would be *"duyệt mù"* (blind approval).

**F-01b (P1)** — the second half of `gaps.js:298`'s remedy is the real design question and needs an
owner decision, not an engineering guess: returning the three hashes makes the button *pressable*,
but approving a `MESSAGE_DRAFT` you cannot read is still blind approval. The decision route also
needs a way to render the draft body. Flagged to §14 as `NEEDS_HUMAN_DECISION`.

## 2. Level taxonomy per surface

`HITL` source of truth: `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` + `delivery/CAPABILITY_STATUS.yaml`.

| Surface | Declared level | What the UI actually does | Verdict |
|---|---|---|---|
| `/approvals` Duyệt | **A2** | queue visible, decision impossible | **VIOLATION — F-01** |
| `/shadow` Bản nháp AI | A2 | draft decision route wired (`shadow.js:437`); queue empty in this environment | BLOCKED — no drafts exist to exercise; route reachable by code |
| `/exceptions` Ngoại lệ | A2 | manual-send prepare → attest, both reachable, both write | OK |
| `/assistant` Trợ lý AI | **H0** | read-only classifier over own rows; no model, no send | OK, but **mislabelled — see §3** |
| `/orders`, `/quotes`, `/order-requests`, `/incidents`, `/staff` | H0 | human-only; no AI path reaches them | OK |
| `/` Hôm nay | H0 | read-only aggregate | OK |
| Outbound customer message | A2 | **no channel adapter exists**; `MANUAL_SEND_RECORDED` is explicitly disclosed as *not* "delivered" | OK — correctly fenced |

**No surface silently changes level between states**, and no A0 or A1 surface exists at all. Given
every capability is `NOT_AUTHORIZED`, that is the correct state: there is nothing the machine does
autonomously today.

## 3. F-02 (P1) — "Trợ lý AI" is not AI

The screen is titled **Trợ lý AI** under a nav section titled **GIÁM SÁT AI**, and its own lede
says:

> *Trợ lý này không gọi mô hình ngôn ngữ và không tính tiền. Mỗi câu trả lời được máy chủ quyết
> định và ghi lại trước, rồi hiện dần trên màn hình — chữ chạy là nhịp hiển thị của câu trả lời đã
> lưu, không phải mô hình đang sinh từ.*

Verified: asking *"Hôm nay thế nào?"* produced a deterministic answer with intent
`TODAY_OVERVIEW`, counted from the store's own rows, carrying the disclaimer *"không phải chỉ số
hiệu suất"*, recorded in `assistant_turns` with `staff_user_id`. Network trace shows exactly three
calls, none to any provider.

The disclosure is excellent and the implementation is honest. **The name is not.** A CSKH staffer
under time pressure reads the tab, not the paragraph. Calling a deterministic lookup "AI" in a
product whose whole safety story is "you always know whether the machine or a person is
responsible" spends the one piece of trust this product cannot afford to spend. Proposed:
**"Hỏi nhanh"** (or "Tra cứu nhanh"), with the section renamed from "GIÁM SÁT AI" to
"GIÁM SÁT" for the three surfaces that genuinely supervise machine output.

This is a naming decision with product consequences, so it goes to §14 as a recommendation, not a
unilateral rename.

## 4. Attribution

| Requirement | State |
|---|---|
| Human actor id on every write | PASS — `staff_user_id` on every command, audit event, and assistant turn |
| Model + version on every AI action | **N/A today; no column exists** — see B-06 |
| Approver identity recorded | PASS in schema (`approval_decisions`), **unreachable in practice** — F-01 |
| Timestamp, input, output | PASS |
| Escalation to a human in one click from anywhere | **PARTIAL** — `#/incidents` is one nav click from every screen, but there is no "escalate this" affordance bound to the thing you are looking at; the incident form demands an order UUID and two sha256 hashes typed by hand (see `03-TRACE.md` D-04) |

## 5. Confidence scores

None are displayed anywhere. Correct — the directive forbids bare confidence numbers and this
product shows none.
