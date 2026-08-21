# Decision request — ai chốt giá một bản báo giá, và bằng cách nào

**Opened:** 2026-08-21
**Owner:** BUSINESS_OWNER
**Registry entry:** `DEC-021`
**Status:** OPEN

---

## 1. What happens today

**No order can be created by this system.** Not "no order has been created yet" — no order *can*
be, by any route, any screen, any operator action.

The chain, each link opened and quoted:

**Order creation demands a quote in a state nothing produces.**
`OrderRepository.create` refuses unless the bound quote revision is `APPROVED_EXACT` **and**
`ACCEPTED_FINAL` **and** carries a non-null `approval_id`:

```
packages/db/src/nha_trang_laundry_db/orders.py:126-137
    or str(quote[1]) != "APPROVED_EXACT"
    or str(quote[2]) != "ACCEPTED_FINAL"
    ...
    or quote[4] is None                      # approval_id
        raise OrderStateError("accepted exact quote is missing, stale, or expired")
```

**The only producer of quote revisions cannot emit that state.**
`compose_quote_revision` hardcodes the opposite, on every path:

```
packages/domain/src/nha_trang_laundry_domain/quote_composition.py:232-233, 254-255
    finality=QuoteFinality.ESTIMATE,
    status=QuoteRevisionStatus.REVIEW_REQUIRED,
    required_approvals=BASE_REQUIRED_APPROVALS,     # ("TAX_TREATMENT_UNVERIFIED",)  :85
    approval_id=None,
```

**There is exactly one writer, and it is an INSERT.**
`packages/db/src/nha_trang_laundry_db/quotes.py:133` is the sole `INSERT INTO quote_revisions` in
the repository. There is no `UPDATE` anywhere, and there cannot be one — a trigger forbids it:

```
packages/db/migrations/0005_quote_snapshots.sql:80-82
CREATE TRIGGER quote_revisions_immutable
    BEFORE UPDATE OR DELETE ON quote_revisions
    FOR EACH ROW EXECUTE FUNCTION reject_quote_revision_mutation();
```

So a revision cannot be *promoted* into an orderable state. It must be *born* in one.

**Approving an envelope does not do it either.** The approval flow writes only the four `approval_*`
tables; it never touches a quote row, so an approved envelope's id is never fed back into a
revision.

**Measured against the running stack, 2026-08-21.** One sign-in as `demo-owner`, seeded store,
four live quote revisions — every one `finality=ESTIMATE, status=REVIEW_REQUIRED`:

```
POST /internal/v1/stores/{id}/orders
  → 409 {"detail":"accepted exact quote is missing, stale, or expired"}
GET  /internal/v1/stores/{id}/orders
  → 200 []          (before and after; zero orders exist and none can)
```

Three independent attempts were made to refute this — hunting for a writer, attacking the guard
predicate character by character, and trying to create an order live. All three failed to refute it.

### The one exception, and it is not a good one

There *is* code in this repository that lands the forbidden row. It is an eval fixture generator:

```
packages/evals/src/nha_trang_laundry_evals/synthetic_incidents.py:160-176
    final = build_quote_snapshot(replace(estimate.data,
        finality=QuoteFinality.APPROVED_EXACT,
        status=QuoteRevisionStatus.ACCEPTED_FINAL,
        required_approvals=(),
        approval_id=uuid4()))
```

It does not live under `tests/` — it is shipped source in a workspace package, reachable from a
command line (`runner.py:167`, mode `synthetic-incidents`) that connects to whatever `DATABASE_URL`
names. It fabricates `approval_id=uuid4()` with no envelope behind it, which the schema permits
because **`approval_id` has no foreign key**:

```
packages/db/migrations/0005_quote_snapshots.sql:48   approval_id UUID NULL,
packages/db/migrations/0005_quote_snapshots.sql:68   CHECK (finality <> 'APPROVED_EXACT' OR approval_id IS NOT NULL)
```

The column must be filled. Nothing checks that what fills it is real.

So the honest statement is not "nothing can create an orderable quote". It is: **no command path
can. One fixture generator can, by asserting the state rather than earning it, and it can be pointed
at any database.** That is a finding in its own right and it appears in the options below.

---

## 2. What specifically stops

**Every order in the business.** Báo giá works and prices correctly; the motion after it does not
exist. An operator prices a customer's laundry, and there is nowhere for that to become an order.

**And the only gate in play cannot be satisfied.** `SHADOW-001` is the G1 evidence carrier — the
fourteen-day internal pilot on real orders. Its evidence table requires:

| Requirement | Minimum |
|---|---:|
| **Real orders** | **30** |
| Batch/cycle logs | 10 |
| Delivery logs | 20 |
| Clean days | 14 |

Its task packet lists five *"required conditions before the first order"* — a human approving every
message, a measured baseline, retention in force, runbooks and SLOs, a signer registry
(`context/tasks/TASK-shadow-001.md`). **"An order can be created" is not among them,** and none of
`SHADOW-001`'s nine dependencies would produce it.

Across all 82 work items, **no item owns this.** The only one that even mentions a quote revision is
`QUOTE-COMMAND-001`, which is COMPLETE and built the *create* path. This is a hole in the
dependency graph, not a feature waiting its turn.

Two planning documents currently describe an operator who "takes an order" today
(`STAFF_CONSOLE_COMPLETION_PROGRAM_V1.md` §10) and list the order board as built
(`STAFF_CONSOLE_ENGINEERING_SPEC_V1.md` §4). Both are wrong in the same place, for this reason.

---

## 3. What the owner is being asked

Four questions. None needs code to answer.

**1. Khi nào một bản báo giá được coi là "đã chốt"?**
Hôm nay máy đòi ba điều kiện cùng lúc: giá chính xác đã duyệt, khách đã chốt, và có một phong bì
duyệt đứng sau. Trong thực tế ở tiệm, "chốt giá" là lúc nào — khi nhân viên cân xong và đọc giá cho
khách, hay khi khách nói đồng ý, hay khi có người thứ hai duyệt lại?

**2. Ai được chốt?**
Nhân viên đứng quầy tự chốt được không, hay phải là chủ tiệm / người duyệt? Nếu phải hai người, thì
lúc 7 giờ sáng chỉ có một nhân viên, tiệm xử lý thế nào?

**3. Phí giao hàng chưa quyết (`DEC-003`) có chặn việc chốt giá không?**
Hiện mọi báo giá đều thiếu phí giao nên không có tổng tiền. Với khách tự mang đến và tự lấy về —
không có giao hàng — thì có được chốt giá mà không cần con số phí giao không?

**4. Thuế (`TAX_TREATMENT_UNVERIFIED`) có chặn không?**
Mọi báo giá hiện mang cờ này. Nó có phải là điều cần chốt trước khi nhận đơn, hay là việc kế toán
xử lý sau?

---

## 4. Options

| # | Option | What it means at the counter | Cost | Risk |
|---|---|---|---|---|
| **A** | **Staff attestation** — a named staff member records "khách đã chốt giá này", which writes revision N+1 as `APPROVED_EXACT`/`ACCEPTED_FINAL` with an approval envelope behind it. **Recommended.** | Nhân viên bấm một nút "Khách đã chốt" sau khi đọc giá cho khách. | One route, one service method, one console control, one envelope producer. Follows the precedent `SETTLEMENT-001` already set: a fact a staff member witnesses at the counter, recorded as an attributed append-only attestation. | Low. The authority is *recorded* rather than borrowed. |
| **B** | **Two-person approval** — the quote enters the existing approval queue and a second role signs it. | Nhân viên báo giá, chủ hoặc người duyệt gật đầu, rồi mới nhận đơn. | Larger. The approval queue currently cannot surface non-order-backed items (`approvals.py:491` joins through `orders`), so it needs the P7 widening first, and manual review of every quote is a real staffing cost. | Medium. Stronger control, but a shop with one person on shift stalls. |
| **C** | **Auto-approve when unambiguous** — a quote with no unresolved fee, no range price and no open flag is born `APPROVED_EXACT`. | Máy tự chốt khi giá đã rõ ràng. | Smallest code change. | **High, and invisible if wrong.** The machine decides a price is final. That is the exact class of decision this repository refuses to let software make. |
| **D** | **Do nothing for now** — accept that no order can be created, and re-sequence `SHADOW-001`. | Đơn hàng vẫn ghi tay. | Zero. | The G1 pilot cannot start. Nothing else changes; the gate simply does not move. |

**Recommendation: A**, with one condition — whatever is chosen, `approval_id` should gain a foreign
key to `approval_requests(id)`. Today the column must be filled but nothing checks that what fills
it is real, which is how the fixture generator manufactures an orderable quote. That is a schema
correction, not a policy question, and it is worth doing under whichever option is signed.

**Not recommended: C.** It is cheapest and it is the one that decides a business question in code.

---

## 5. Until it is signed

The fail-closed behaviour holding today, stated precisely enough to verify:

- `POST /internal/v1/stores/{store_id}/orders` returns **409** `accepted exact quote is missing,
  stale, or expired` for every quote this system can produce. Verified live on 2026-08-21.
- Báo giá continues to work and price correctly. Quotes are created, stored immutably, and readable.
- The console **says so**. `CONSOLE-ORDER-GAP-001` landed a `#/gaps` entry naming this, bound to a
  test that fails if a producer for `APPROVED_EXACT` ever appears without the disclosure changing.
- Orders are recorded on paper, as they are today.
- `SHADOW-001` cannot start and should not be scheduled as though it could.

**No code will be written to route around this.** A workaround that produced an orderable quote
without an answer to question 1 would be this system deciding, on the owner's behalf, when a price
becomes final.

---

## 6. What is not claimed here

- **CẦN XÁC MINH:** whether an operator with database access has ever created a quote revision
  out of band (psql, an admin console, a data-fix script). Nothing in `scripts/` or `deploy/`
  writes quote data, but source cannot establish what someone did at a terminal. The owner or
  whoever holds database credentials could confirm.
- This packet does not say which of `DEC-003` (delivery fee) or the tax flag *must* be resolved
  first. Questions 3 and 4 ask that rather than assuming it.
- The `RANGE` finality path appears equally unproduced — `compose_quote_revision` refuses range
  prices outright — but that was confirmed only for the composer, not by a separate repo-wide
  search. It is not load-bearing for this decision.
