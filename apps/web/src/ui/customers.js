/**
 * The customer search field and the "Thêm khách mới" sheet (`CUSTOMER-001`, `DEC-034`), shared by
 * ＋ Nhận đồ step 1 and `#/customers` so the two can never search or record differently.
 *
 *   - **One field.** "SĐT hoặc tên khách": the server decides whether what was typed is a full
 *     phone, the last four digits or a name (`GET …/customers?q=`), and says which (`mode`). The
 *     field never guesses, and a search that searched nothing says why in one line.
 *   - **The owner's notice first.** The sheet reads `GET …/customer-privacy-notice` and, until the
 *     owner has published one, says so in tier 1 and keeps "Lưu" off -- the server would refuse
 *     `PRIVACY_NOTICE_UNPUBLISHED` anyway. Once published, the sentence the staff member reads
 *     aloud and both ticks are the notice's own words, so the customer agrees to the version the
 *     record will cite.
 *   - **Two consents, never merged.** The service tick is required; the promotions tick is separate
 *     and starts off.
 *   - **No number is kept on the device.** The typed value lives in the input and the request; the
 *     console stores nothing but the store id (`test_the_client_persists_nothing_but_a_store_...`).
 *
 * @module ui/customers
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import {
  CUSTOMER_SEARCH_LIMIT,
  customerRefusalText,
  customerTitle,
  existingCustomerId,
  phoneText,
  searchModeHint,
} from "../core/customers.js";
import { errorNotice } from "./components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  list,
  listRow,
  searchField,
  sheet,
  show,
  skeletonRows,
} from "./kit.js";

/** Typing pauses this long before a search is sent (a read; nothing is written). */
const SEARCH_DEBOUNCE_MS = 250;

/**
 * The published notice, or `{published: false}`. One read per screen that needs it.
 *
 * @param {string} store
 * @returns {Promise<any>}
 */
export function readNotice(store) {
  return request(`/internal/v1/stores/${encodeURIComponent(store)}/customer-privacy-notice`);
}

/**
 * The notice's text as paragraphs; a leading `**Heading.**` becomes a bold run. Text nodes only.
 *
 * @param {string} text
 * @returns {HTMLElement[]}
 */
export function noticeParagraphs(text) {
  return String(text || "")
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.replace(/\s*\n\s*/g, " ").trim())
    .filter(Boolean)
    .map((paragraph) => {
      const match = /^\*\*(.+?)\*\*\s*(.*)$/.exec(paragraph);
      return match
        ? h("p", { class: "hint" }, h("strong", null, match[1]), ` ${match[2].replaceAll("**", "")}`)
        : h("p", { class: "hint" }, paragraph.replaceAll("**", ""));
    });
}

/**
 * The search field with its results.
 *
 * @param {object} spec
 * @param {string} spec.id
 * @param {string} spec.store
 * @param {(customer: any, row: HTMLElement) => void} [spec.onPick] a tap on a result (a write
 *   follows, so the row is marked as needing the network); without it each row links to the page
 * @param {(query: string) => void} [spec.onAdd] "Thêm khách mới", with what was typed
 * @param {unknown} [spec.addControl] how the add action is presented (gated by the caller)
 * @param {boolean} [spec.listWhenEmpty] with nothing typed, list the newest customers
 * @returns {{node: HTMLElement, input: HTMLInputElement, search: (query: string) => void, query: () => string}}
 */
export function customerSearch(spec) {
  const results = h("div", { class: "customer-search__results", id: `${spec.id}-results` });
  const status = h("div", { class: "customer-search__status" });
  let sequence = 0;
  let timer = 0;
  let current = "";

  const field = searchField({
    id: spec.id,
    label: "SĐT hoặc tên khách",
    placeholder: "SĐT, 4 số cuối hoặc tên khách",
    onInput: (value) => {
      current = value;
      clearTimeout(timer);
      timer = window.setTimeout(() => void run(value), SEARCH_DEBOUNCE_MS);
    },
    onSubmit: (value) => {
      current = value;
      clearTimeout(timer);
      void run(value);
    },
  });

  /** @param {any} customer */
  function row(customer) {
    const open = Number.isInteger(customer.open_order_count) ? customer.open_order_count : 0;
    const title = customerTitle(customer);
    const meta = phoneText(customer);
    if (spec.onPick) {
      const node = listRow({
        onClick: () => spec.onPick?.(customer, node),
        leading: "user",
        title,
        meta,
        trailing: open > 0 ? `${open} đơn mở` : undefined,
        data: { customer: String(customer.customer_id || ""), requiresNetwork: "true" },
      });
      return node;
    }
    return listRow({
      href: `#/customers/${encodeURIComponent(String(customer.customer_id || ""))}`,
      leading: "user",
      title,
      meta,
      trailing: open > 0 ? `${open} đơn mở` : undefined,
      data: { customer: String(customer.customer_id || "") },
    });
  }

  /** @param {string} value */
  async function run(value) {
    const query = value.trim();
    const mine = ++sequence;
    if (!query && !spec.listWhenEmpty) {
      render(results);
      render(status);
      return;
    }
    if (!results.childElementCount) render(results, skeletonRows(2));
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(spec.store)}/customers?q=${encodeURIComponent(query)}&limit=${CUSTOMER_SEARCH_LIMIT}`,
      );
      // A slower answer to an older query must not overwrite a newer one.
      if (mine !== sequence) return;
      const customers = Array.isArray(body?.customers) ? body.customers : [];
      const hint = searchModeHint(String(body?.mode || ""));
      render(
        results,
        customers.length
          ? list(customers.map(row), { label: "Khách tìm thấy", id: `${spec.id}-list` })
          : !query
            ? emptyState({
                icon: "user",
                title: "Chưa có khách nào trong danh sách",
                body: "Khách được lưu khi đồng ý, lúc nhận đồ hoặc bằng “Thêm khách”.",
              })
            : null,
      );
      render(
        status,
        hint
          ? h("p", { class: "hint", role: "status" }, hint)
          : !customers.length && query
            ? h("p", { class: "hint", role: "status" }, "Không có khách nào khớp.")
            : null,
        body?.truncated
          ? h(
              "p",
              { class: "hint" },
              `Chỉ hiện ${CUSTOMER_SEARCH_LIMIT} khách gần nhất. Gõ thêm để thu hẹp.`,
            )
          : null,
      );
    } catch (error) {
      if (mine !== sequence) return;
      render(results);
      show(status, errorNotice(error, { onRetry: () => void run(value) }));
    }
  }

  const add = spec.addControl
    ? h("div", { class: "customer-search__add" }, spec.addControl)
    : null;
  const node = h(
    "div",
    { class: "stack stack--tight customer-search" },
    field.node,
    status,
    results,
    add,
  );
  if (spec.listWhenEmpty) void run("");
  return {
    node,
    input: field.input,
    search: (value) => {
      field.input.value = value;
      current = value;
      void run(value);
    },
    query: () => current,
  };
}

/**
 * Whether a typed query looks like a phone the sheet should start with (digits, +, spaces, dots).
 *
 * @param {string} query
 * @returns {boolean}
 */
function looksLikePhone(query) {
  return /^[+\d][\d\s.-]{3,}$/.test(query.trim()) && /\d{4}/.test(query.replace(/\D/g, ""));
}

/**
 * The "Thêm khách mới" sheet.
 *
 * @param {object} spec
 * @param {string} spec.store
 * @param {string} spec.saveLabel "Lưu và tiếp tục" at the counter, "Lưu khách" on #/customers
 * @param {(customer: any) => Promise<void>|void} spec.onSaved with the created record
 * @param {(customerId: string) => void} [spec.onExisting] a duplicate number: go on with that record
 * @param {string} [spec.existingLabel] what that button says ("Chọn khách này", "Mở khách này")
 * @returns {{node: HTMLDialogElement, open: (query?: string) => void, close: () => void}}
 */
export function newCustomerSheet(spec) {
  const create = new Submission("customer-create");
  /** @type {any|null} */
  let notice = null;
  let busy = false;
  const draft = { phone: "", name: "", note: "", consent: false, marketing: false };

  const alertHost = h("div");
  const noticeHost = h("div", { class: "stack stack--tight" }, skeletonRows(1));
  const infoHost = h("span");

  function edited() {
    create.reset();
    drawSave();
  }

  const phone = /** @type {HTMLInputElement} */ (
    h("input", {
      type: "text",
      id: "customer-new-phone",
      inputmode: "tel",
      autocomplete: "off",
      placeholder: "0905 123 456",
      onInput: (event) => {
        draft.phone = event.target.value;
        edited();
      },
    })
  );
  const name = /** @type {HTMLInputElement} */ (
    h("input", {
      type: "text",
      id: "customer-new-name",
      autocomplete: "off",
      maxlength: "80",
      placeholder: "chị Lan",
      onInput: (event) => {
        draft.name = event.target.value;
        edited();
      },
    })
  );
  const note = /** @type {HTMLTextAreaElement} */ (
    h("textarea", {
      id: "customer-new-note",
      maxlength: "200",
      rows: "2",
      placeholder: "Giặt riêng đồ trắng",
      onInput: (event) => {
        draft.note = event.target.value;
        edited();
      },
    })
  );
  const consent = /** @type {HTMLInputElement} */ (
    h("input", {
      type: "checkbox",
      id: "customer-new-consent",
      onChange: (event) => {
        draft.consent = event.target.checked;
        edited();
      },
    })
  );
  const marketing = /** @type {HTMLInputElement} */ (
    h("input", {
      type: "checkbox",
      id: "customer-new-marketing",
      onChange: (event) => {
        draft.marketing = event.target.checked;
        edited();
      },
    })
  );
  const consentText = h("span");
  const marketingText = h("span");
  const saveReason = h("p", { class: "hint", id: "customer-new-save-reason" });
  const save = button({
    label: spec.saveLabel,
    variant: "primary",
    network: true,
    id: "customer-new-save",
    onClick: () => void submit(),
  });
  save.setAttribute("aria-describedby", "customer-new-save-reason");

  function drawSave() {
    const reason = !notice
      ? "Đang đọc thông báo bảo mật…"
      : !notice.published
        ? "Chưa lưu được."
        : !draft.phone.trim()
          ? "Nhập số điện thoại của khách."
          : !draft.consent
            ? "Đọc câu trên cho khách và đánh dấu khách đồng ý."
            : "";
    save.disabled = Boolean(reason) || busy;
    saveReason.textContent = reason;
  }

  function drawNotice() {
    if (!notice) return;
    // Until the owner publishes, the form is not offered at all: fields to fill for a record that
    // cannot be saved would be work thrown away at the counter.
    fields.hidden = !notice.published;
    if (!notice.published) {
      render(
        noticeHost,
        inlineAlert({
          state: "warn",
          title: "Chủ tiệm cần công bố thông báo bảo mật trước khi lưu khách.",
          body: "Hôm nay: phát phiếu vãng lai như bình thường.",
        }),
      );
      render(infoHost);
    } else {
      render(
        noticeHost,
        h("p", { class: "customer-consent__lead" }, "Đọc cho khách:"),
        h("blockquote", { class: "customer-consent__sentence" }, String(notice.consent_sentence || "")),
      );
      render(
        infoHost,
        infoButton(
          "Khách được báo những gì?",
          h("p", { class: "hint" }, h("strong", null, String(notice.title || ""))),
          ...noticeParagraphs(String(notice.text || "")),
          h(
            "p",
            { class: "hint" },
            `Bản thông báo số ${notice.version} do chủ tiệm công bố. Hồ sơ khách ghi lại bản này, ` +
              "ai đánh dấu và lúc nào.",
          ),
        ),
      );
      consentText.textContent = String(notice.service_consent_label || "");
      marketingText.textContent = String(notice.marketing_consent_label || "");
    }
    drawSave();
  }

  async function loadNotice() {
    try {
      notice = await readNotice(spec.store);
      drawNotice();
    } catch (error) {
      show(noticeHost, errorNotice(error, { onRetry: () => void loadNotice() }));
    }
  }

  async function submit() {
    if (busy || !notice?.published) return;
    busy = true;
    drawSave();
    save.setAttribute("aria-busy", "true");
    render(alertHost);
    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(spec.store)}/customers`, {
        method: "POST",
        body: {
          phone: draft.phone.trim(),
          display_name: draft.name.trim() || null,
          note: draft.note.trim() || null,
          service_consent: draft.consent,
          marketing_consent: draft.marketing,
        },
        idempotencyKey: create.key(),
      });
      create.reset();
      busy = false;
      save.removeAttribute("aria-busy");
      await spec.onSaved(created.customer);
      drawSave();
    } catch (error) {
      busy = false;
      save.removeAttribute("aria-busy");
      drawSave();
      const existing = existingCustomerId(error);
      show(
        alertHost,
        existing && spec.onExisting
          ? inlineAlert({
              state: "warn",
              title: customerRefusalText(error),
              actions: button({
                label: spec.existingLabel || "Mở khách này",
                variant: "secondary",
                id: "customer-new-existing",
                onClick: () => {
                  made.close();
                  spec.onExisting?.(existing);
                },
              }),
            })
          : errorNotice(error, { title: customerRefusalText(error) || undefined }),
      );
    }
  }

  const fields = h(
    "div",
    { class: "stack customer-new__fields" },
    h(
      "div",
      { class: "stack stack--tight" },
      h("label", { for: "customer-new-phone" }, "Số điện thoại"),
      phone,
    ),
    h(
      "div",
      { class: "stack stack--tight" },
      h("label", { for: "customer-new-name" }, "Tên gọi (không bắt buộc)"),
      name,
    ),
    h(
      "div",
      { class: "stack stack--tight" },
      h("label", { for: "customer-new-note" }, "Ghi chú cách giặt (không bắt buộc)"),
      note,
      h("p", { class: "hint" }, "Chỉ ghi cách giặt. Không ghi sức khoẻ, tôn giáo hay chuyện riêng."),
    ),
    h(
      "div",
      { class: "check-row" },
      h("label", { class: "check-row__label", for: "customer-new-consent" }, consent, consentText),
      h(
        "label",
        { class: "check-row__label customer-new__optional", for: "customer-new-marketing" },
        marketing,
        marketingText,
      ),
    ),
  );
  const body = h(
    "div",
    { class: "stack customer-new" },
    noticeHost,
    fields,
    h("div", { class: "fact-line" }, saveReason, infoHost),
    alertHost,
  );
  const made = sheet({
    id: "customer-new-sheet",
    title: "Thêm khách mới",
    body,
    actions: save,
  });

  return {
    node: made.node,
    close: () => made.close(),
    open(query = "") {
      const typed = String(query || "").trim();
      Object.assign(draft, {
        phone: looksLikePhone(typed) ? typed : "",
        name: typed && !looksLikePhone(typed) ? typed : "",
        note: "",
        consent: false,
        marketing: false,
      });
      phone.value = draft.phone;
      name.value = draft.name;
      note.value = "";
      consent.checked = false;
      marketing.checked = false;
      create.reset();
      render(alertHost);
      // An unpublished notice is read again on every open: the owner may have published since.
      if (!notice?.published) void loadNotice();
      else drawNotice();
      drawSave();
      made.open();
      (draft.phone ? name : phone).focus();
    },
  };
}
