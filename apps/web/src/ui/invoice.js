/**
 * Hóa đơn: a customer's invoice request, captured at the counter (`EINVOICE-REQUEST-001`,
 * `DEC-040`) — the pieces the order page, the account card, the statement and *Hóa đơn cần xuất*
 * share.
 *
 *   - **The row** (`invoiceSection`): what the order or the account month has — nothing yet, a
 *     request waiting for the bookkeeper, or the invoice's symbol, number and date — and the one
 *     thing to do about it. The server says whether a new request may be made (`refusal`).
 *   - **Khách cần hóa đơn** (`requestSheet`): the buyer's five fields, pre-filled for an account
 *     customer, with "Lưu cho lần sau" when the server offers it.
 *   - **Ghi số hóa đơn** (`issuedSheet`) and **Huỷ yêu cầu** (`cancelSheet`), each under
 *     `If-Match`.
 *
 * What this module never does: issue anything, name a tax, compute an amount (every figure is the
 * server's, through `money()`), or keep anything on the device. The bookkeeper issues the invoice in
 * the provider's own portal; this records that the customer asked, and what was issued.
 *
 * @module ui/invoice
 */

import { Submission, request } from "../core/api.js";
import { monthName, statementPath } from "../core/accounts.js";
import { h, render } from "../core/dom.js";
import { businessDate, calendarDay, dateOnly, money } from "../core/format.js";
import { can } from "../core/rbac.js";
import { principal } from "../core/session.js";
import { errorNotice, gated } from "./components.js";
import {
  button,
  choiceChips,
  confirmButton,
  infoButton,
  inlineAlert,
  section,
  sheet,
  show,
  skeletonRows,
  statusPill,
  toast,
} from "./kit.js";

/** A request's state as the counter says it. Scoped: the tokens mean other things elsewhere. */
export const INVOICE_STATUS_VI = {
  REQUESTED: "Chờ kế toán xuất",
  ISSUED: "Đã xuất",
  CANCELLED: "Đã huỷ",
};

/** @type {Record<string, "warn"|"ok"|"neutral">} */
const STATUS_STATE = { REQUESTED: "warn", ISSUED: "ok", CANCELLED: "neutral" };

/** Why a request was cancelled, as the sheet's chips and the list say it. */
export const INVOICE_CANCEL_REASON_VI = {
  CUSTOMER_WITHDREW: "Khách không cần nữa",
  DUPLICATE: "Trùng yêu cầu khác",
  WRONG_DETAILS: "Ghi sai thông tin",
  OTHER: "Lý do khác",
};

/** Who records an issued invoice, said short enough to sit under a button in a row. */
export const CLOSE_SHORT = "Chủ tiệm hoặc người duyệt ghi số.";

/**
 * A role refusal said short, for a control repeated in rows: the full sentence (`can()`'s, with the
 * roles) is in the navigation and on the screen's one primary action. A missing second factor keeps
 * its own sentence, because the fix is different.
 *
 * @param {{allowed: boolean, reason: string}} verdict
 * @param {string} short
 * @returns {{allowed: boolean, reason: string}}
 */
export function roleVerdict(verdict, short) {
  if (verdict.allowed || verdict.reason.startsWith("Phiên này chưa xác thực")) return verdict;
  return { allowed: false, reason: short };
}

/** The title of a refusal about one field: the fix is to correct it, never to work around it. */
const FIELD_REFUSED = "Chưa lưu. Sửa ô được đánh dấu đỏ rồi bấm lưu lại.";

/** The tier-2 explanation behind every ⓘ about invoices: who issues, and what the amount is. */
export const INVOICE_HOW = [
  "Tiệm chỉ ghi lại yêu cầu của khách. Kế toán xuất hóa đơn điện tử trên cổng của nhà cung cấp " +
    "hóa đơn, từ danh sách tải ở màn Hóa đơn cần xuất, rồi ghi ký hiệu, số và ngày hóa đơn vào " +
    "đây (DEC-040).",
  "Số tiền là số tiệm đã tính cho khách (gồm phí lưu kho nếu có), chưa tách thuế. Loại hóa đơn và " +
    "thuế suất do chủ tiệm và kế toán quyết.",
];

/**
 * A request as one short line: its code and the buyer's unit name.
 *
 * @param {any} item an `InvoiceRequestResponse`
 * @returns {string}
 */
export function requestTitle(item) {
  const unit = item?.buyer?.unit_name || (item?.buyer?.erased ? "Khách đã xoá thông tin" : "—");
  return `${item?.request_code || "—"} · ${unit}`;
}

/**
 * What a request is for, in the counter's words: "Phiếu 12 · 28/09" or "Công nợ tháng 9/2026".
 *
 * @param {any} item an `InvoiceRequestResponse` or `InvoiceSubjectResponse`
 * @returns {string}
 */
export function subjectText(item) {
  if (item?.subject_kind === "ACCOUNT_MONTH") return `Công nợ ${monthName(item.period_month).toLowerCase()}`;
  if (Number.isInteger(item?.ticket_number)) {
    return `Phiếu ${item.ticket_number}${item.ticket_issued_on ? ` · ${calendarDay(item.ticket_issued_on, { weekday: false })}` : ""}`;
  }
  return item?.order_id ? `Đơn ${String(item.order_id).slice(0, 8).toUpperCase()}` : "—";
}

/**
 * Where the subject lives on screen.
 *
 * @param {any} item
 * @returns {string|null}
 */
export function subjectHref(item) {
  if (item?.subject_kind === "ACCOUNT_MONTH" && item.customer_id && item.period_month) {
    return statementPath(String(item.customer_id), String(item.period_month));
  }
  return item?.order_id ? `#/orders/${encodeURIComponent(String(item.order_id))}` : null;
}

/**
 * "Ký hiệu 1C26TYY · Số 0000123 · 27/09/2026".
 *
 * @param {any} item
 * @returns {string}
 */
export function issuedText(item) {
  return `Ký hiệu ${item.invoice_symbol} · Số ${item.invoice_number} · ${dayText(item.invoice_date)}`;
}

/**
 * The amount a request is for, as the server read it now.
 *
 * @param {any} amount an `InvoiceAmountResponse`
 * @returns {string}
 */
export function amountText(amount) {
  return money(amount?.total_vnd, "chưa có tổng");
}

/**
 * @param {string|null|undefined} day `YYYY-MM-DD`
 * @returns {string}
 */
function dayText(day) {
  return calendarDay(day, { weekday: false, year: true });
}

/**
 * @param {any} item
 * @returns {HTMLElement}
 */
export function statusOf(item) {
  const token = String(item?.status || "");
  return statusPill({
    state: STATUS_STATE[token] || "neutral",
    text: INVOICE_STATUS_VI[token] || token,
    token,
  });
}

/**
 * The field a refusal is about, when the server named one (`detail.field`).
 *
 * @param {unknown} error
 * @returns {string|null}
 */
function refusedField(error) {
  const detail = /** @type {any} */ (error)?.detail;
  if (typeof detail !== "string" || !detail.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(detail);
    const inner = parsed && typeof parsed === "object" && "detail" in parsed ? parsed.detail : parsed;
    return typeof inner?.field === "string" ? inner.field : null;
  } catch {
    return null;
  }
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
 * A labelled text field.
 *
 * @param {object} spec
 * @param {string} spec.id
 * @param {string} spec.label
 * @param {string} [spec.value]
 * @param {string} [spec.placeholder]
 * @param {string} [spec.inputmode]
 * @param {string} [spec.type]
 * @param {number} [spec.maxlength]
 * @param {string} [spec.autocomplete]
 * @returns {{node: HTMLElement, input: HTMLInputElement}}
 */
function field(spec) {
  const input = /** @type {HTMLInputElement} */ (
    h("input", {
      id: spec.id,
      type: spec.type || "text",
      class: "input",
      autocomplete: spec.autocomplete || "off",
      inputmode: spec.inputmode || null,
      maxlength: spec.maxlength ? String(spec.maxlength) : null,
      placeholder: spec.placeholder || null,
      value: spec.value ?? "",
    })
  );
  return {
    input,
    node: h(
      "div",
      { class: "invoice-field" },
      h("label", { class: "field-label", for: spec.id }, spec.label),
      input,
    ),
  };
}

/**
 * Khách cần hóa đơn: the buyer's details for one order or one account month.
 *
 * One `Idempotency-Key` per intent: a resend of the same details after a timeout replays, and any
 * change to a field is a new intent. The refusal stays in the sheet, next to the field it is about.
 *
 * @param {object} spec
 * @param {string} spec.path the create route, written out in full by the caller
 * @param {any} spec.subject the `InvoiceSubjectResponse` the row read
 * @param {string} spec.title what the sheet calls the subject ("Phiếu 12", "Công nợ tháng 9/2026")
 * @param {(created: any) => void} spec.onCreated
 * @returns {{node: HTMLDialogElement, open: () => void, close: () => void}}
 */
export function requestSheet(spec) {
  const prefill = spec.subject?.prefill || {};
  const submission = new Submission("invoice-request");
  let intent = "";
  const unit = field({
    id: "invoice-unit",
    label: "Tên đơn vị (bắt buộc)",
    value: prefill.unit_name || "",
    maxlength: 200,
    autocomplete: "organization",
  });
  const tax = field({
    id: "invoice-tax",
    label: "Mã số thuế",
    value: prefill.tax_code || "",
    inputmode: "numeric",
    maxlength: 14,
    placeholder: "10 hoặc 12 số",
  });
  const address = field({
    id: "invoice-address",
    label: "Địa chỉ (bắt buộc khi có mã số thuế)",
    value: prefill.address || "",
    maxlength: 300,
  });
  const email = field({
    id: "invoice-email",
    label: "Email nhận hóa đơn",
    value: prefill.email || "",
    type: "email",
    maxlength: 254,
    autocomplete: "email",
  });
  const buyer = field({
    id: "invoice-buyer",
    label: "Người mua hàng",
    value: prefill.name || "",
    maxlength: 120,
  });
  const fields = { buyer_unit_name: unit, buyer_tax_code: tax, buyer_address: address, buyer_email: email, buyer_name: buyer };
  const save = /** @type {HTMLInputElement} */ (
    h("input", { type: "checkbox", id: "invoice-save-profile", checked: Boolean(spec.subject?.prefill_from_profile) || null })
  );
  const alertHost = h("div");
  const submit = button({
    label: "Lưu yêu cầu",
    variant: "primary",
    block: true,
    network: true,
    id: "invoice-request-save",
    onClick: () => void send(),
  });

  function clearMarks() {
    for (const entry of Object.values(fields)) entry.input.removeAttribute("aria-invalid");
  }

  async function send() {
    const body = {
      buyer_unit_name: unit.input.value.trim(),
      buyer_tax_code: tax.input.value.trim() || null,
      buyer_address: address.input.value.trim() || null,
      buyer_email: email.input.value.trim() || null,
      buyer_name: buyer.input.value.trim() || null,
      save_profile: Boolean(spec.subject?.profile_savable && save.checked),
    };
    const next = JSON.stringify(body);
    if (next !== intent) {
      submission.reset();
      intent = next;
    }
    clearMarks();
    render(alertHost);
    await pressing(submit, async () => {
      try {
        const created = await request(spec.path, {
          method: "POST",
          body,
          idempotencyKey: submission.key(),
        });
        submission.reset();
        intent = "";
        made.close();
        toast(`Đã ghi yêu cầu ${created.request_code} · ${spec.title}`);
        spec.onCreated(created);
      } catch (error) {
        const named = refusedField(error);
        const marked = named ? fields[/** @type {keyof typeof fields} */ (named)] : null;
        if (marked) {
          marked.input.setAttribute("aria-invalid", "true");
          marked.input.focus();
        }
        show(
          alertHost,
          errorNotice(/** @type {any} */ (error), marked ? { title: FIELD_REFUSED } : {}),
        );
      }
    });
  }

  const made = sheet({
    id: "invoice-request-sheet",
    title: "Khách cần hóa đơn",
    body: h(
      "div",
      { class: "stack" },
      h(
        "p",
        { class: "invoice-sheet__subject", dataField: "invoice-subject" },
        spec.title,
        spec.subject?.amount ? ` · ${amountText(spec.subject.amount)}` : "",
      ),
      h("div", { class: "invoice-form" }, unit.node, tax.node, address.node, email.node, buyer.node),
      spec.subject?.profile_savable
        ? h(
            "label",
            { class: "invoice-check", for: "invoice-save-profile" },
            save,
            h("span", null, "Lưu cho lần sau (điền sẵn cho khách công nợ này)"),
          )
        : null,
      h("p", { class: "hint" }, "Ghi đúng như trên giấy phép kinh doanh. Không ghi số điện thoại khách."),
      alertHost,
    ),
    actions: submit,
    onClose: () => made.node.remove(),
  });
  return made;
}

/**
 * Ghi số hóa đơn: the symbol, number and date the bookkeeper read back from the provider's portal.
 * `If-Match` is the request's row version; a request somebody else closed meanwhile is refused.
 *
 * @param {object} spec
 * @param {string} spec.store
 * @param {any} spec.item the `InvoiceRequestResponse`
 * @param {(updated: any) => void} spec.onDone
 * @returns {{node: HTMLDialogElement, open: () => void, close: () => void}}
 */
export function issuedSheet(spec) {
  const { item } = spec;
  const submission = new Submission("invoice-issued");
  let intent = "";
  const symbol = field({ id: "invoice-symbol", label: "Ký hiệu", maxlength: 12, placeholder: "Ví dụ 1C26TYY" });
  symbol.input.style.textTransform = "uppercase";
  const number = field({ id: "invoice-number", label: "Số hóa đơn", inputmode: "numeric", maxlength: 8 });
  const day = field({ id: "invoice-date", label: "Ngày hóa đơn", type: "date", value: businessDate() });
  day.input.max = businessDate();
  const fields = { invoice_symbol: symbol, invoice_number: number, invoice_date: day };
  const alertHost = h("div");
  const submit = button({
    label: "Lưu số hóa đơn",
    variant: "primary",
    block: true,
    network: true,
    id: "invoice-issued-save",
    onClick: () => void send(),
  });

  async function send() {
    const body = {
      invoice_symbol: symbol.input.value.trim().toUpperCase(),
      invoice_number: number.input.value.trim(),
      invoice_date: day.input.value,
    };
    const next = JSON.stringify(body);
    if (next !== intent) {
      submission.reset();
      intent = next;
    }
    for (const entry of Object.values(fields)) entry.input.removeAttribute("aria-invalid");
    render(alertHost);
    await pressing(submit, async () => {
      try {
        const updated = await request(
          `/internal/v1/stores/${encodeURIComponent(spec.store)}/invoice-requests/${encodeURIComponent(String(item.invoice_request_id))}/issued`,
          { method: "POST", body, idempotencyKey: submission.key(), ifMatch: item.row_version },
        );
        submission.reset();
        intent = "";
        made.close();
        toast(`Đã ghi số hóa đơn · ${item.request_code}`);
        spec.onDone(updated);
      } catch (error) {
        const named = refusedField(error);
        const marked = named ? fields[/** @type {keyof typeof fields} */ (named)] : null;
        if (marked) {
          marked.input.setAttribute("aria-invalid", "true");
          marked.input.focus();
        }
        show(
          alertHost,
          errorNotice(/** @type {any} */ (error), marked ? { title: FIELD_REFUSED } : {}),
        );
      }
    });
  }

  const made = sheet({
    id: "invoice-issued-sheet",
    title: "Ghi số hóa đơn",
    body: h(
      "div",
      { class: "stack" },
      h("p", { class: "invoice-sheet__subject" }, `${requestTitle(item)} · ${amountText(item.amount)}`),
      h("div", { class: "invoice-form invoice-form--three" }, symbol.node, number.node, day.node),
      h("p", { class: "hint" }, "Chép đúng ký hiệu, số và ngày trên hóa đơn kế toán đã xuất. Ghi rồi không sửa được."),
      alertHost,
    ),
    actions: submit,
    onClose: () => made.node.remove(),
  });
  return made;
}

/**
 * Huỷ yêu cầu: a reason (and a few words for "Lý do khác"). `If-Match` the request's version.
 *
 * @param {object} spec
 * @param {string} spec.store
 * @param {any} spec.item
 * @param {(updated: any) => void} spec.onDone
 * @returns {{node: HTMLDialogElement, open: () => void, close: () => void}}
 */
export function cancelSheet(spec) {
  const { item } = spec;
  const submission = new Submission("invoice-cancel");
  let intent = "";
  let reason = "";
  const note = field({ id: "invoice-cancel-note", label: "Ghi chú (bắt buộc với lý do khác)", maxlength: 200 });
  const alertHost = h("div");
  const submit = confirmButton({
    label: "Huỷ yêu cầu",
    confirmLabel: "Bấm lần nữa để huỷ yêu cầu",
    block: true,
    onConfirm: () => void send(),
  });
  submit.id = "invoice-cancel-confirm";

  async function send() {
    if (!reason) {
      show(alertHost, inlineAlert({ state: "danger", title: "Chọn lý do huỷ." }));
      return;
    }
    const body = { reason, note: note.input.value.trim() || null };
    const next = JSON.stringify(body);
    if (next !== intent) {
      submission.reset();
      intent = next;
    }
    render(alertHost);
    await pressing(submit, async () => {
      try {
        const updated = await request(
          `/internal/v1/stores/${encodeURIComponent(spec.store)}/invoice-requests/${encodeURIComponent(String(item.invoice_request_id))}/cancellation`,
          { method: "POST", body, idempotencyKey: submission.key(), ifMatch: item.row_version },
        );
        submission.reset();
        intent = "";
        made.close();
        toast(`Đã huỷ yêu cầu ${item.request_code}`);
        spec.onDone(updated);
      } catch (error) {
        show(alertHost, errorNotice(/** @type {any} */ (error)));
      }
    });
  }

  const made = sheet({
    id: "invoice-cancel-sheet",
    title: "Huỷ yêu cầu hóa đơn",
    body: h(
      "div",
      { class: "stack" },
      h("p", { class: "invoice-sheet__subject" }, requestTitle(item)),
      choiceChips({
        label: "Lý do huỷ",
        name: "invoice-cancel-reason",
        options: Object.entries(INVOICE_CANCEL_REASON_VI).map(([value, label]) => ({
          value,
          label,
          title: value,
        })),
        onChange: (value) => {
          reason = value;
        },
      }),
      note.node,
      h("p", { class: "hint" }, "Không ghi số điện thoại khách."),
      alertHost,
    ),
    actions: submit,
    onClose: () => made.node.remove(),
  });
  return made;
}

/**
 * The tier-1 line under a subject whose new request the server would refuse, by its reason.
 *
 * @param {string|null|undefined} refusal
 * @param {any} subject
 * @returns {string|null}
 */
function refusalLine(refusal, subject) {
  if (refusal === "PRIVACY_NOTICE_UNPUBLISHED") {
    return "Chủ tiệm cần công bố thông báo bảo mật trước khi ghi yêu cầu hóa đơn.";
  }
  if (refusal === "INVOICE_SUBJECT_UNAVAILABLE") {
    return subject?.subject_kind === "ACCOUNT_MONTH"
      ? "Tháng này chưa có đơn nào ghi công nợ."
      : "Đơn đã huỷ: không ghi yêu cầu hóa đơn.";
  }
  if (refusal === "INVOICE_REQUEST_EXISTS" && !subject?.live) {
    return subject?.subject_kind === "ACCOUNT_MONTH"
      ? "Một đơn trong tháng đã có yêu cầu hóa đơn riêng."
      : "Đơn này nằm trong yêu cầu hóa đơn tháng của khách.";
  }
  return null;
}

/**
 * The *Hóa đơn* row: on the order page (an order), on the account card and the statement (an
 * account month). Reads the subject, then shows what it has and the one thing to do.
 *
 * @param {object} spec
 * @param {string} spec.readPath the subject read, written out in full by the caller
 * @param {string} spec.createPath the create route, written out in full by the caller
 * @param {string} spec.store
 * @param {string} spec.title what the sheets call the subject
 * @param {string} [spec.heading] the section's title ("Hóa đơn" / "Hóa đơn tháng")
 * @param {HTMLElement} spec.sheetsHost where the sheets are mounted
 * @param {boolean} [spec.bare] no section frame (the account card draws its own)
 * @returns {{node: HTMLElement, load: () => Promise<void>}}
 */
export function invoiceSection(spec) {
  const who = principal();
  const writeVerdict = can(who, "INVOICES_WRITE");
  const closeVerdict = can(who, "INVOICES_CLOSE");
  const body = h("div", { class: "invoice-row", id: "invoice-row" }, skeletonRows(1));
  const alertHost = h("div");

  function mount(made) {
    render(spec.sheetsHost, made.node);
    made.open();
  }

  async function load() {
    try {
      const subject = await request(spec.readPath);
      draw(subject);
    } catch (error) {
      render(body, errorNotice(/** @type {any} */ (error), { onRetry: () => void load() }));
    }
  }

  /** @param {any} subject */
  function draw(subject) {
    const live = subject.live;
    const cancelled = (subject.history || []).filter((item) => item.status === "CANCELLED").length;
    if (live) {
      const open = live.status === "REQUESTED";
      render(
        body,
        h(
          "div",
          { class: "invoice-row__main" },
          h(
            "div",
            { class: "invoice-row__facts" },
            h("div", { class: "invoice-row__line" }, statusOf(live), h("span", { dataField: "invoice-code" }, requestTitle(live))),
            h(
              "p",
              { class: "invoice-row__meta" },
              live.status === "ISSUED"
                ? issuedText(live)
                : [
                    live.buyer?.tax_code ? `MST ${live.buyer.tax_code}` : "Không có mã số thuế",
                    `Ghi ${dateOnly(live.requested_at)}`,
                  ].join(" · "),
            ),
          ),
          open
            ? h(
                "div",
                { class: "invoice-row__actions" },
                gated(
                  button({
                    label: "Ghi số hóa đơn",
                    variant: "secondary",
                    network: true,
                    id: "invoice-record-issued",
                    onClick: () =>
                      mount(issuedSheet({ store: spec.store, item: live, onDone: () => void load() })),
                  }),
                  roleVerdict(closeVerdict, CLOSE_SHORT),
                ),
                gated(
                  button({
                    label: "Huỷ yêu cầu",
                    variant: "quiet",
                    network: true,
                    id: "invoice-cancel",
                    onClick: () =>
                      mount(cancelSheet({ store: spec.store, item: live, onDone: () => void load() })),
                  }),
                  writeVerdict,
                ),
              )
            : null,
        ),
      );
      return;
    }
    const reason = refusalLine(subject.refusal, subject);
    // Off with its reason beside it (`gated` marks it `data-denied`, so coming back online does not
    // switch it on again): the role first, then what the server says about this subject.
    const verdict = !writeVerdict.allowed
      ? writeVerdict
      : subject.refusal
        ? { allowed: false, reason: reason || "Chưa ghi được yêu cầu cho mục này." }
        : writeVerdict;
    const press = button({
      label: "Khách cần hóa đơn",
      variant: "secondary",
      icon: "plus",
      network: true,
      id: "invoice-request-open",
      onClick: () =>
        mount(
          requestSheet({
            path: spec.createPath,
            subject,
            title: spec.title,
            onCreated: () => void load(),
          }),
        ),
    });
    render(
      body,
      h(
        "div",
        { class: "invoice-row__main" },
        h(
          "div",
          { class: "invoice-row__facts" },
          h("p", { class: "invoice-row__line" }, "Chưa có yêu cầu hóa đơn"),
          cancelled ? h("p", { class: "invoice-row__meta" }, `${cancelled} yêu cầu đã huỷ`) : null,
        ),
        h("div", { class: "invoice-row__actions", dataField: "invoice-refusal" }, gated(press, verdict)),
      ),
    );
  }

  const info = infoButton("Hóa đơn làm thế nào?", ...INVOICE_HOW.map((text) => h("p", null, text)));
  const node = spec.bare
    ? h("div", { class: "invoice-block", id: "invoice-section" }, h("p", { class: "field-label" }, spec.heading || "Hóa đơn", info), body, alertHost)
    : h(
        "div",
        { id: "invoice-section" },
        section({ title: spec.heading || "Hóa đơn", info, children: h("div", null, body, alertHost) }),
      );
  return { node, load };
}
