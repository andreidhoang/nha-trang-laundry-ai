# Decision request — một đơn giao hàng kết thúc thế nào

**Opened:** 2026-08-26 · **Owner:** `BUSINESS_OWNER` · **Decision:** `DEC-023`
**Status:** unsigned. Delivery orders fail closed and the console will say so.

---

## 1. What was measured

On 2026-08-26 the full shop lifecycle was run against the live database for both fulfilment modes,
through the real service and repository path — ticket, quote, chốt, order, ten state transitions,
settlement, completion.

**Khách tự mang tự lấy — hoàn tất.**

```
*** HOAN TAT ***  COMPLETED  balance=PAID  legs_ok=False  self_collect=True
```

**Giao hàng 4km — dừng ở hai chỗ.**

```
tat toan -> settlement shape is not supported
hoan tat -> INVALID_STATE_TRANSITION: fulfillment is incomplete
DUNG O    ACTIVE  balance=UNPAID  legs_ok=False  self_collect=False
```

Everything up to that point worked: the delivery fee was priced (10,000đ for the 2–6km zone), the
total was presented (130,000đ), the customer's agreement was recorded, the order was taken and
washed. It is the last two steps — being paid, and being closed — that have no path.

## 2. Why this is one decision and not two

The two refusals have different causes and they are **coupled**, which is why they are asked
together rather than fixed one at a time.

**The first is a decision.** `evaluate_settlement` refuses any settlement where the goods did not go
back to the customer at the counter. That is `DEC-010`, resolved 2026-08-18 as *deliberately
deferred*: partial payment, deposits, instalments and credit stay `NOT_SUPPORTED` "because no B2B
credit relationship exists yet and the built exact-payment self-collection path already covers the
ordinary retail case." That was true when it was signed. Delivery was not in view.

**The second is unbuilt.** `orders.required_delivery_legs_succeeded` is read in three places, defaults
`FALSE`, and **nothing anywhere writes it** — `FULFILMENT-001` is the item that would, and `DEC-003`
already cleared its decision gate on 2026-08-18. It has never been enqueued.

**Building the second without deciding the first ships half a feature.** With delivery legs recorded
and no settlement shape, a delivery order reaches "delivered, unpaid, permanently ACTIVE" — worse
than today, because it looks finished and is not.

And the coupling runs the other way too: *how* a delivered order gets paid decides what a delivery
leg has to record. If the driver collects cash, the leg carries a money event and the person who
took it. If the customer paid at drop-off, the leg carries nothing about money and the settlement
happened days earlier. **The record cannot be designed before the answer.**

## 3. Câu hỏi cho chủ tiệm

**1. Khách giao hàng trả tiền lúc nào?**

| | Khi nào | Nghĩa là |
|---|---|---|
| **A** | **Trả trước, lúc gửi đồ tại quầy** | Giống hệt khách tự mang: cân, báo giá, chốt, trả tiền ngay. Shipper chỉ giao đồ, không cầm tiền. **Đơn giản nhất, và gần như không phải sửa gì về tiền.** |
| **B** | Trả cho shipper khi nhận đồ | Shipper cầm tiền của tiệm. Cần ghi ai cầm, bao nhiêu, đối soát cuối ngày. |
| **C** | Cả hai, khách chọn | Cần cả hai đường. |

**2. Ai xác nhận là đã giao xong?**
Shipper tự bấm trên điện thoại? Hay nhân viên ở tiệm ghi lại khi shipper về báo? Câu này quyết định
ai cần tài khoản và ai được ghi vào sổ.

**3. Giao không thành thì sao?**
Khách không có nhà, không nghe máy. Đồ quay về tiệm — đơn hàng lúc đó ở trạng thái nào, và khách có
phải trả phí giao lần hai không?

## 4. Options

| # | Option | Cost | Risk |
|---|---|---|---|
| **A** | **Trả trước tại quầy + ghi nhận giao hàng.** `FULFILMENT-001` records legs; settlement is unchanged because it happens at the counter before the goods leave. **Recommended.** | Smallest. One table, one write path, one console control. `DEC-010` is not reopened at all. | Low. The shop is never owed money by someone holding its laundry. |
| B | Shipper thu tiền (COD). Legs plus a money event, a driver identity, and end-of-day reconciliation. | Largest. Reopens `DEC-010`, needs a driver role, and creates a cash-handling process the shop does not have. | Medium-high. Cash in a bag on a motorbike, reconciled by hand. |
| C | Không giao hàng qua hệ thống. Delivery jobs stay on paper. | Zero. | The delivery half of the business stays invisible to the system, and `SHADOW-001`'s twenty delivery logs have to come from paper. |

**Recommendation: A.** It is the only option that makes delivery orders completable without
reopening a settlement decision, and it matches how a small shop actually protects itself — the
laundry does not leave until it is paid for.

**Not recommended: B**, unless the shop already does COD today and says so. It is a cash-control
system disguised as a feature.

## 5. Until it is signed

- Delivery orders can be taken, priced, agreed and washed. They cannot be settled or completed.
- The console will carry an entry saying exactly that, in the register operators already read.
- `FULFILMENT-001` stays unenqueued. Building it before question 1 is answered would mean guessing
  what a delivery leg must record.
- Nothing about the walk-in flow is affected. That path is complete and in use.

## 6. What is not claimed

- **CẦN XÁC MINH:** whether the shop currently takes payment before or after delivery. This packet
  assumes nothing and asks. The recommendation would change if the shop already runs COD.
- This does not say how many delivery legs an order needs. `DEC-003` ratified one negotiated fee for
  pickup + return as a pair; whether the system records that as one leg or two is an engineering
  question that follows the answer above, not a decision for the owner.
