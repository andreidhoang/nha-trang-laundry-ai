/**
 * Đồ chờ lấy: laundry finished and not yet collected (`UNCLAIMED-001`, `DEC-036`) — the pieces the
 * waiting list (`#/pickup`) and the order page share.
 *
 *   - **Ghi lần liên hệ** — how the shop tried to reach the customer (gọi, Zalo, SMS, tới nhà) and
 *     what came of it, with an optional few words. Works before the owner publishes the storage
 *     policy: calling customers is how the shop gets its shelf back.
 *   - **Lưu kho** on the order page — days waiting, the storage fee the server computed, the
 *     contact attempts, and the two decisions `DEC-036` names: *Miễn phí lưu kho* (an approver or
 *     the owner, with a reason) and, when the server says it is legal, *Thanh lý* (the owner), whose
 *     sheet states the rule verbatim as the server built it from the owner's published figures.
 *
 * What this module never does: count days, compute a fee, decide whether disposal is legal, or
 * keep anything on the device. Every figure and every verdict is the server's (`storage_fee`,
 * `disposal_verdict`, `days_waiting`); this only puts words beside them. The customer's number is
 * never printed here: the list's "Gọi" carries it inside a `tel:` link, and a note may not hold one.
 *
 * @module ui/unclaimed
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { dateOnly, dateTime, money } from "../core/format.js";
import {
  CONTACT_CHANNEL_VI,
  CONTACT_OUTCOME_VI,
  REASON_NOTE,
  REMINDER_STEP_VI,
} from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal } from "../core/session.js";
import { errorNotice, gated } from "./components.js";
import {
  button,
  choiceChips,
  confirmButton,
  infoButton,
  inlineAlert,
  keyValues,
  list,
  listRow,
  section,
  segmented,
  sheet,
  show,
  toast,
} from "./kit.js";

/** How long a note or a reason may be; the server refuses more (`NOTE_TOO_LONG`). */
export const NOTE_LIMIT = 120;

/** The attempts the order page lists before "và N lần trước đó". */
const ATTEMPTS_SHOWN = 5;

/**
 * The storage fee on an order read: the `STORAGE_FEE` entry of the server's own `charges`, or null.
 * A lookup, never a sum.
 *
 * @param {any} order an `OrderViewResponse`
 * @returns {number|null}
 */
export function storageCharge(order) {
  const found = (Array.isArray(order?.charges) ? order.charges : []).find(
    (charge) => charge?.kind === "STORAGE_FEE",
  );
  return found && Number.isInteger(found.amount_vnd) ? found.amount_vnd : null;
}

/**
 * "Gồm phí lưu kho 25.000 ₫" beside what the counter collects, or null when there is none.
 *
 * @param {any} order
 * @returns {string|null}
 */
export function storageChargeLine(order) {
  const amount = storageCharge(order);
  return amount ? `Gồm phí lưu kho ${money(amount)}` : null;
}

/**
 * How long the laundry has waited, in the counter's words. `days` is the server's count of shop
 * days since the laundry was ready.
 *
 * @param {number|null|undefined} days
 * @returns {string}
 */
export function waitingText(days) {
  if (!Number.isInteger(days)) return "Chưa rõ ngày xong";
  if (days === 0) return "Xong hôm nay";
  return `Chờ ${days} ngày`;
}

/**
 * The fee in words, from the server's `storage_fee` (status and amount) and the published figures.
 *
 * A fixed fee is "đã thu" once the order is paid, and "đã ghi công nợ" while it sits on the
 * customer's account (`PAYMENT-002`: the account charge fixed it, no money was taken yet).
 *
 * @param {any} fee a `StorageFeeResponse`
 * @param {any|null} policy a `StoragePolicyResponse`, or null before publication
 * @param {string} [balance] the order's balance, when the caller has the order
 * @returns {string}
 */
export function feeText(fee, policy, balance) {
  const status = String(fee?.status || "");
  if (status === "ACCRUING") {
    if (fee.capped) return `${money(fee.amount_vnd)} · mức tối đa`;
    return Number.isInteger(fee.chargeable_days) && Number.isInteger(fee.fee_per_started_day_vnd)
      ? `${money(fee.amount_vnd)} · ${fee.chargeable_days} ngày × ${money(fee.fee_per_started_day_vnd)}`
      : money(fee.amount_vnd);
  }
  if (status === "FREE_PERIOD") {
    return policy ? `Miễn phí tới hết ngày thứ ${policy.free_days}` : "Chưa tính phí";
  }
  if (status === "WAIVED") return "Đã miễn";
  if (status === "FIXED") {
    if (!fee.amount_vnd) return "Không có";
    return balance === "ON_ACCOUNT"
      ? `${money(fee.amount_vnd)} · đã ghi công nợ`
      : `${money(fee.amount_vnd)} · đã thu`;
  }
  if (status === "POLICY_UNPUBLISHED") return "Chưa tính (chủ tiệm chưa công bố)";
  return "Không có";
}

/**
 * One contact attempt as a line: "Gọi điện · Không nghe máy", and the reminder it answered when it
 * answered one ("Zalo · Đã gửi tin · Nhắc lần 2 (ngày 3)", PICKUP-REMIND-001).
 *
 * @param {{channel?: string, outcome?: string, reminder_step?: string|null}} attempt
 * @returns {string}
 */
export function attemptTitle(attempt) {
  const channel = CONTACT_CHANNEL_VI[String(attempt?.channel)] || String(attempt?.channel || "");
  const outcome = CONTACT_OUTCOME_VI[String(attempt?.outcome)] || String(attempt?.outcome || "");
  const step = attempt?.reminder_step ? REMINDER_STEP_VI[String(attempt.reminder_step)] : "";
  return step ? `${channel} · ${outcome} · ${step}` : `${channel} · ${outcome}`;
}

/**
 * The refusal for a sheet: the server's reason, never a toast.
 *
 * @param {unknown} error
 * @param {() => void} [reload]
 * @returns {HTMLElement}
 */
function refusal(error, reload) {
  const kind = /** @type {any} */ (error)?.kind;
  if ((kind === "STALE" || kind === "PRECONDITION_REQUIRED") && reload) {
    return errorNotice(/** @type {any} */ (error), {
      title:
        "Đơn vừa được đổi trong lúc bạn đang xem, nên chưa có gì được ghi. Tải lại rồi làm lại.",
      actions: [button({ label: "Tải lại", icon: "refresh", onClick: reload })],
    });
  }
  return errorNotice(/** @type {any} */ (error));
}

/** COUNTER-UI-RACE-009 (C4): what a storage sheet says after its in-place "Tải lại". */
const RELOADED = "Đã tải lại đơn mới nhất — kiểm tra lại rồi bấm.";

/**
 * "Tải lại" inside the waiver or disposal sheet after a stale refusal (COUNTER-UI-RACE-009, C4).
 * With `spec.reread` (the order page's re-read) the sheet stays open: the fresh order and storage
 * reads go to `apply`, which redraws the sheet's figures and makes the next press carry the new
 * version -- the reason typed stays -- or answers false when the decision is no longer offered,
 * and the sheet closes. Without it, the sheet closes and the page re-reads, as before.
 *
 * @param {{order: any, onChanged: () => void, reread?: () => Promise<any|null>}} spec
 * @param {{node: HTMLElement, close: () => void}} made
 * @param {HTMLElement} alertHost
 * @param {(order: any, storage: any) => boolean} apply
 * @returns {() => Promise<void>}
 */
function reloadInPlace(spec, made, alertHost, apply) {
  return async () => {
    if (!spec.reread) {
      made.close();
      spec.onChanged();
      return;
    }
    const order = await spec.reread();
    if (!order || !made.node.isConnected) return;
    const storage = await readStorage(String(order.order_id));
    if (!made.node.isConnected) return;
    if (!storage || !apply(order, storage)) {
      made.close();
      return;
    }
    show(alertHost, inlineAlert({ state: "info", title: RELOADED }));
  };
}

/**
 * Press once, wait once: the control is off while the request is in flight.
 *
 * @param {HTMLButtonElement} control
 * @param {() => Promise<void>} work
 */
async function pressing(control, work) {
  control.disabled = true;
  control.setAttribute("aria-busy", "true");
  try {
    await work();
  } finally {
    if (control.isConnected) {
      control.disabled = false;
      control.removeAttribute("aria-busy");
    }
  }
}

/**
 * A one-line text field with a counter, for a note or a reason. Tells the person, beside the
 * field, not to type a phone number: the server refuses one (`NOTE_LOOKS_LIKE_PHONE`).
 *
 * @param {{id: string, label: string, placeholder: string, onInput: (text: string) => void}} spec
 * @returns {HTMLElement}
 */
function noteField(spec) {
  return h(
    "div",
    { class: "stack stack--tight" },
    h("label", { class: "field-label", for: spec.id }, spec.label),
    h("input", {
      id: spec.id,
      type: "text",
      class: "input",
      autocomplete: "off",
      maxlength: String(NOTE_LIMIT),
      placeholder: spec.placeholder,
      onInput: (event) => spec.onInput(/** @type {HTMLInputElement} */ (event.target).value),
    }),
    h("p", { class: "hint" }, "Tối đa 120 ký tự. Không ghi số điện thoại khách."),
  );
}

/**
 * Ghi lần liên hệ: one attempt on `POST /orders/{id}/contact-attempts`. No `If-Match` — an attempt
 * changes nothing on the order — and one `Idempotency-Key` per intent, so a resend after a timeout
 * replays and a changed answer is a new attempt.
 *
 * @param {object} spec
 * @param {string} spec.orderId
 * @param {string} spec.title what the sheet calls the order ("Phiếu 17 · chị Lan")
 * @param {string} [spec.channel] the channel to start on (CALL after "Gọi")
 * @param {(recorded: any) => void} spec.onRecorded
 * @returns {{node: HTMLDialogElement, open: () => void, close: () => void}}
 */
export function contactSheet(spec) {
  const id = encodeURIComponent(spec.orderId);
  let channel = spec.channel || "CALL";
  let outcome = "";
  let note = "";
  const submission = new Submission("contact-attempt");
  let intent = "";
  const alertHost = h("div");
  const submit = button({
    label: "Lưu lần liên hệ",
    variant: "primary",
    block: true,
    network: true,
    id: "contact-submit",
    onClick: () => void send(),
  });

  async function send() {
    if (!outcome) {
      show(alertHost, inlineAlert({ state: "danger", title: "Chọn kết quả của lần liên hệ." }));
      return;
    }
    const body = { channel, outcome, note: note.trim() ? note.trim() : null };
    const next = JSON.stringify(body);
    if (next !== intent) {
      submission.reset();
      intent = next;
    }
    render(alertHost);
    await pressing(submit, async () => {
      try {
        const recorded = await request(`/internal/v1/orders/${id}/contact-attempts`, {
          method: "POST",
          body,
          idempotencyKey: submission.key(),
        });
        submission.reset();
        intent = "";
        made.close();
        toast(`Đã ghi lần liên hệ · ${spec.title}`);
        spec.onRecorded(recorded);
      } catch (error) {
        show(alertHost, refusal(error));
      }
    });
  }

  const made = sheet({
    id: "contact-attempt",
    title: "Ghi lần liên hệ",
    body: h(
      "div",
      { class: "stack" },
      h("p", { class: "hint" }, spec.title),
      h("p", { class: "field-label" }, "Liên hệ bằng"),
      segmented({
        label: "Liên hệ bằng",
        id: "contact-channel",
        wrap: true,
        options: [
          { value: "CALL", label: "Gọi" },
          { value: "ZALO", label: CONTACT_CHANNEL_VI.ZALO },
          { value: "SMS", label: "SMS" },
          { value: "VISIT", label: CONTACT_CHANNEL_VI.VISIT },
        ],
        value: channel,
        onChange: (value) => {
          channel = value;
        },
      }),
      h("p", { class: "field-label" }, "Kết quả"),
      choiceChips({
        label: "Kết quả",
        name: "contact-outcome",
        // MESSAGE_SENT is a reminder's (PICKUP-REMIND-001): recorded from Nhắc khách lấy đồ with
        // its step, never from this sheet, which names none.
        options: Object.entries(CONTACT_OUTCOME_VI)
          .filter(([value]) => value !== "MESSAGE_SENT")
          .map(([value, label]) => ({ value, label })),
        onChange: (value) => {
          outcome = value;
        },
      }),
      noteField({
        id: "contact-note",
        label: "Ghi chú (không bắt buộc)",
        placeholder: "Ví dụ: hẹn chiều mai qua",
        onInput: (text) => {
          note = text;
        },
      }),
      alertHost,
    ),
    actions: submit,
    onClose: () => made.node.remove(),
  });
  return made;
}

/**
 * Lưu kho on the order page: days waiting, the fee, the attempts, and the two owner decisions.
 * Returns null when the order has nothing to say about storage (it is not waiting, and no fee,
 * waiver or disposal is on record).
 *
 * @param {object} spec
 * @param {any} spec.order the `OrderViewResponse` as last read
 * @param {any} spec.storage the `OrderStorageResponse`
 * @param {HTMLElement} spec.sheetsHost where the sheets are mounted
 * @param {() => void} spec.onChanged re-read the order after a write
 * @param {() => Promise<any|null>} [spec.reread] re-read the order and answer it: a sheet's
 *   "Tải lại" after a stale refusal then keeps the sheet open on the fresh figures (C4)
 * @param {string} spec.title the order's short name, for the sheets and the toasts
 * @returns {HTMLElement|null}
 */
export function storageSection(spec) {
  const { order, storage } = spec;
  const fee = storage?.storage_fee || {};
  const policy = storage?.policy || null;
  const disposal = storage?.disposal || null;
  const relevant =
    storage?.awaiting_pickup ||
    disposal ||
    storage?.waiver ||
    (fee.status === "FIXED" && fee.amount_vnd > 0);
  if (!relevant) return null;
  const who = principal();
  const alertHost = h("div");

  if (disposal) {
    return section({
      title: "Lưu kho",
      id: "order-storage",
      children: h(
        "div",
        { class: "stack stack--tight", dataDisposed: "true" },
        inlineAlert({
          state: "info",
          title: `Đã thanh lý ngày ${dateOnly(disposal.disposed_at)}.`,
          body: `Đồ chờ ${disposal.days_waiting} ngày; tiệm đã liên hệ ${disposal.attempts_counted} lần trong ${disposal.attempt_days} ngày.`,
        }),
        keyValues([
          ["Tổng khách nợ lúc thanh lý", money(disposal.owed_vnd)],
          ["Đã trả, tiệm giữ", money(disposal.kept_vnd)],
          ["Xoá nợ", money(disposal.written_off_vnd)],
          disposal.disposed_by_name ? ["Chủ tiệm duyệt", disposal.disposed_by_name] : null,
        ]),
      ),
    });
  }

  const attempts = Array.isArray(storage.attempts) ? storage.attempts : [];
  const shown = attempts.slice(-ATTEMPTS_SHOWN);
  const verdict = storage.disposal_verdict || {};
  const facts = keyValues([
    storage.awaiting_pickup
      ? [
          "Chờ lấy",
          `${waitingText(storage.days_waiting)}${storage.ready_at ? ` (xong ${dateOnly(storage.ready_at)})` : ""}`,
        ]
      : null,
    ["Phí lưu kho", h("span", { dataStorageFee: String(fee.status || "") }, feeText(fee, policy, order?.balance))],
    storage.waiver
      ? ["Lý do miễn", `${storage.waiver.reason} · ${storage.waiver.waived_by_name || "—"}`]
      : null,
    [
      "Liên hệ khách",
      storage.attempts_total
        ? `${storage.attempts_total} lần · ${verdict.attempt_days ?? 0} ngày khác nhau`
        : "Chưa lần nào",
    ],
  ]);
  const attemptRows = shown.length
    ? list(
        shown.map((attempt) =>
          listRow({
            leading: attempt.channel === "VISIT" ? "store" : attempt.channel === "CALL" ? "phone" : "message",
            title: attemptTitle(attempt),
            meta: [dateTime(attempt.attempted_at), attempt.attempted_by_name, attempt.note]
              .filter(Boolean)
              .join(" · "),
            data: { attempt: String(attempt.attempt_id) },
          }),
        ),
        { label: "Các lần liên hệ", id: "order-attempts" },
      )
    : null;
  const earlier =
    storage.attempts_total > shown.length
      ? h("p", { class: "hint" }, `Và ${storage.attempts_total - shown.length} lần trước đó.`)
      : null;

  const controls = [];
  if (storage.awaiting_pickup) {
    controls.push(
      gated(
        button({
          label: "Ghi lần liên hệ",
          icon: "phone",
          id: "order-contact",
          network: true,
          onClick: () => {
            const made = contactSheet({
              orderId: String(order.order_id),
              title: spec.title,
              onRecorded: () => spec.onChanged(),
            });
            render(spec.sheetsHost, made.node);
            made.open();
          },
        }),
        can(who, "PICKUP_CONTACT"),
      ),
    );
  }
  if (fee.status === "ACCRUING") {
    controls.push(
      gated(
        button({
          label: "Miễn phí lưu kho",
          id: "order-storage-waive",
          network: true,
          onClick: () => openWaiver(spec, fee),
        }),
        can(who, "STORAGE_FEE_WAIVE"),
      ),
    );
  }
  if (verdict.allowed === true) {
    controls.push(
      gated(
        button({
          label: "Thanh lý",
          variant: "danger",
          id: "order-dispose",
          network: true,
          onClick: () => openDisposal(spec),
        }),
        can(who, "UNCLAIMED_DISPOSE"),
      ),
    );
  }

  return section({
    title: "Lưu kho",
    id: "order-storage",
    info: infoButton(
      "Phí lưu kho tính thế nào?",
      policy
        ? h("p", null, policy.receipt_line_vi)
        : h(
            "p",
            null,
            "Chủ tiệm chưa công bố phí lưu kho (DEC-036), nên chưa tính phí và chưa thanh lý được. " +
              "Danh sách đồ chờ lấy và việc ghi liên hệ vẫn dùng bình thường.",
          ),
      h(
        "p",
        { class: "hint" },
        "Máy chủ đếm ngày theo lịch của tiệm, từ ngày đồ xong. Phí cộng vào số còn lại của đơn và " +
          "được chốt khi khách trả đủ; khách đã trả gì thì đúng số lúc đó.",
      ),
      policy ? h("p", { class: "hint" }, policy.disposal_rule_vi) : null,
    ),
    children: h(
      "div",
      { class: "stack stack--tight" },
      facts,
      attemptRows,
      earlier,
      controls.length ? h("div", { class: "btn-row" }, controls) : null,
      alertHost,
    ),
  });
}

/**
 * Miễn phí lưu kho: `POST /orders/{id}/storage-fee-waiver`, with the order's version (`If-Match`),
 * because what the order owes changes.
 *
 * @param {Parameters<typeof storageSection>[0]} spec
 * @param {any} fee
 */
function openWaiver(spec, fee) {
  const id = encodeURIComponent(String(spec.order.order_id));
  // The order read the press is against, and the fee shown; a reload in the sheet replaces both.
  let shown = spec.order;
  const feeHost = h("div");
  const drawFee = (/** @type {any} */ now) =>
    render(feeHost, keyValues([["Đơn", spec.title], ["Phí hiện tại", money(now.amount_vnd)]]));
  drawFee(fee);
  const submission = new Submission("storage-waiver");
  let reason = "";
  let intent = "";
  const alertHost = h("div");
  const submit = button({
    label: "Miễn phí lưu kho",
    variant: "primary",
    block: true,
    network: true,
    id: "waiver-submit",
    onClick: () => void send(),
  });

  async function send() {
    if (!reason.trim()) {
      show(alertHost, inlineAlert({ state: "danger", title: REASON_NOTE.NOTE_REQUIRED }));
      return;
    }
    const next = `${shown.row_version}|${reason.trim()}`;
    if (next !== intent) {
      submission.reset();
      intent = next;
    }
    render(alertHost);
    await pressing(submit, async () => {
      try {
        await request(`/internal/v1/orders/${id}/storage-fee-waiver`, {
          method: "POST",
          body: { reason: reason.trim() },
          ifMatch: shown.row_version,
          idempotencyKey: submission.key(),
        });
        made.close();
        toast(`Đã miễn phí lưu kho · ${spec.title}`);
        spec.onChanged();
      } catch (error) {
        show(
          alertHost,
          refusal(
            error,
            reloadInPlace(spec, made, alertHost, (order, storage) => {
              const now = storage.storage_fee || {};
              if (now.status !== "ACCRUING") return false;
              shown = order;
              drawFee(now);
              return true;
            }),
          ),
        );
      }
    });
  }

  const made = sheet({
    id: "storage-waiver",
    title: "Miễn phí lưu kho",
    body: h(
      "div",
      { class: "stack" },
      feeHost,
      h("p", { class: "hint" }, "Miễn rồi thì đơn này không tính phí lưu kho nữa. Lý do được ghi lại."),
      noteField({
        id: "waiver-reason",
        label: "Lý do miễn",
        placeholder: "Ví dụ: khách quen",
        onInput: (text) => {
          reason = text;
        },
      }),
      alertHost,
    ),
    actions: submit,
    onClose: () => made.node.remove(),
  });
  render(spec.sheetsHost, made.node);
  made.open();
}

/**
 * Thanh lý: the owner's decision, on `POST /orders/{id}/disposal` with the order's version. The
 * sheet states the rule exactly as the server built it from the owner's published figures, and what
 * happens to the money in the server's own figures (`paid_vnd` kept, `remaining_vnd` written off).
 * Two presses: the first arms it.
 *
 * @param {Parameters<typeof storageSection>[0]} spec
 */
function openDisposal(spec) {
  // The order and storage reads the sheet shows; a reload in the sheet replaces both (C4).
  let order = spec.order;
  let storage = spec.storage;
  const id = encodeURIComponent(String(order.order_id));
  const submission = new Submission("unclaimed-disposal");
  const alertHost = h("div");
  const factsHost = h("div");
  function drawFacts() {
    const verdict = storage.disposal_verdict || {};
    render(
      factsHost,
      keyValues([
        ["Đơn", spec.title],
        ["Chờ lấy", waitingText(storage.days_waiting)],
        ["Đã liên hệ", `${verdict.attempts_counted ?? 0} lần · ${verdict.attempt_days ?? 0} ngày`],
        ["Khách đã trả (giữ nguyên)", money(order.paid_vnd)],
        ["Còn nợ (xoá)", money(order.remaining_vnd)],
      ]),
    );
  }
  drawFacts();
  const confirm = confirmButton({
    label: "Thanh lý",
    confirmLabel: "Bấm lần nữa để thanh lý",
    block: true,
    onConfirm: () => void send(),
  });
  confirm.id = "disposal-confirm";

  async function send() {
    render(alertHost);
    await pressing(confirm, async () => {
      try {
        await request(`/internal/v1/orders/${id}/disposal`, {
          method: "POST",
          ifMatch: order.row_version,
          idempotencyKey: submission.key(),
        });
        made.close();
        toast(`Đã thanh lý · ${spec.title}`);
        spec.onChanged();
      } catch (error) {
        show(
          alertHost,
          refusal(
            error,
            reloadInPlace(spec, made, alertHost, (fresh, read) => {
              if (read.disposal_verdict?.allowed !== true) return false;
              order = fresh;
              storage = read;
              drawFacts();
              return true;
            }),
          ),
        );
      }
    });
  }

  const made = sheet({
    id: "unclaimed-disposal",
    title: "Thanh lý đồ không ai lấy",
    body: h(
      "div",
      { class: "stack" },
      h(
        "blockquote",
        { class: "rule-quote", dataField: "disposal-rule" },
        storage.policy ? storage.policy.disposal_rule_vi : "",
      ),
      factsHost,
      alertHost,
    ),
    actions: gated(confirm, can(principal(), "UNCLAIMED_DISPOSE")),
    onClose: () => made.node.remove(),
  });
  render(spec.sheetsHost, made.node);
  made.open();
}

/**
 * Read one order's storage facts, or null when the read is refused or fails -- a side read the
 * order page stands without, as it does without its capture.
 *
 * @param {string} orderId
 * @returns {Promise<any|null>}
 */
export async function readStorage(orderId) {
  try {
    return await request(`/internal/v1/orders/${encodeURIComponent(orderId)}/storage`);
  } catch {
    return null;
  }
}

/**
 * The storage rule's one receipt line once the owner published it, else null (`DEC-036`: "a fee
 * the customer was never told about is a fee the shop should not charge").
 *
 * @param {any|null} storage an `OrderStorageResponse`
 * @returns {string|null}
 */
export function receiptStorageLine(storage) {
  return storage?.policy?.receipt_line_vi ? String(storage.policy.receipt_line_vi) : null;
}
