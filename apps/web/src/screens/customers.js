/**
 * Khách hàng: the shop's customer list and one customer's page (`CUSTOMER-001`, `DEC-034`).
 *
 *   - **`#/customers`** — the one search field ("SĐT hoặc tên khách"; the server decides whether it
 *     is a full phone, the last four digits or a name), the newest customers when nothing is typed,
 *     and "Thêm khách" with consent once the owner has published the privacy notice.
 *   - **`#/customers/:customerId`** — the name and number with **Gọi** (`tel:`) and **Zalo**
 *     (`https://zalo.me/<số>`), open orders first, then history and unused credits, then what is
 *     on record; **Sửa** under `If-Match`, and **Xoá thông tin (khách yêu cầu)** for the owner or
 *     an approver, which erases the personal fields and keeps every order.
 *
 * What this screen never does: compute money (every figure is a server integer through `money()`),
 * decide who may see the number (the server masks it for an auditor, and a masked number has no
 * link), or keep anything on the device -- the number lives in this page and nowhere else. There is
 * no export of the list: `DEC-034` allows one only through an owner-approved envelope, and this
 * console does not offer it.
 *
 * @module screens/customers
 */

import { Submission, request } from "../core/api.js";
import {
  customerRefusalText,
  customerTitle,
  formatPhone,
  maskedPhone,
  telHref,
  zaloHref,
} from "../core/customers.js";
import { h, render } from "../core/dom.js";
import { UUID, dateOnly, dateTime, money } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { orderStatus } from "../core/orderStatus.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated, icon } from "../ui/components.js";
import { customerSearch, newCustomerSheet, readNotice } from "../ui/customers.js";
import {
  button,
  confirmButton,
  emptyState,
  infoButton,
  inlineAlert,
  keyValues,
  list,
  listRow,
  page,
  section,
  segmented,
  sheet,
  show,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";
import { orderName } from "./orders.js";

/**
 * What a credit was issued for, in the words ＋ Nhận đồ and the order page use.
 *
 * @type {Record<string, string>}
 */
const CREDIT_KIND = {
  DAMAGE_COMPENSATION: "Bồi thường món bị hỏng",
  LATE_DELIVERY_CREDIT: "Giảm trừ do giao trễ",
  LOST_ITEM: "Mất đồ",
};

/** @param {string|null|undefined} day */
function ticketDay(day) {
  return day ? dateOnly(`${day}T12:00:00+07:00`) : "";
}

// =============================================================================================
// #/customers
// =============================================================================================

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const who = principal();
  const readVerdict = can(who, "CUSTOMERS_READ");
  const writeVerdict = can(who, "CUSTOMERS_WRITE");
  const noticeHost = h("div");

  const addSheet = newCustomerSheet({
    store,
    saveLabel: "Lưu khách",
    existingLabel: "Mở khách này",
    onSaved: (customer) => {
      addSheet.close();
      toast(`Đã lưu · ${customerTitle(customer)}`);
      location.hash = `#/customers/${encodeURIComponent(String(customer.customer_id))}`;
    },
    onExisting: (customerId) => {
      location.hash = `#/customers/${encodeURIComponent(customerId)}`;
    },
  });
  const add = gated(
    button({
      label: "Thêm khách",
      icon: "plus",
      variant: "primary",
      id: "customers-add",
      onClick: () => addSheet.open(lookup ? lookup.query() : ""),
    }),
    writeVerdict,
  );
  const lookup = readVerdict.allowed
    ? customerSearch({ id: "customers-search", store, listWhenEmpty: true })
    : null;

  async function loadNotice() {
    try {
      const notice = await readNotice(store);
      render(
        noticeHost,
        notice?.published
          ? null
          : inlineAlert({
              state: "warn",
              title: "Chủ tiệm cần công bố thông báo bảo mật trước khi lưu khách.",
              body: "Đến lúc đó, khách vẫn được phục vụ bằng số phiếu.",
            }),
      );
    } catch (error) {
      render(noticeHost, errorNotice(error, { onRetry: () => void loadNotice() }));
    }
  }
  if (readVerdict.allowed) void loadNotice();

  return h(
    "section",
    { class: "screen customers" },
    page({
      title: "Khách hàng",
      action: add,
      info: infoButton(
        "Danh sách này lưu gì?",
        h(
          "p",
          { class: "hint" },
          "Khách đồng ý thì tiệm lưu số điện thoại, tên gọi, địa chỉ giao đồ và một ghi chú cách " +
            "giặt, để lần sau nhận ra khách, báo khi đồ xong và giao đồ (DEC-034). Tin ưu đãi là " +
            "một đồng ý riêng, mặc định là không.",
        ),
        h(
          "p",
          { class: "hint" },
          "Tìm bằng số điện thoại đầy đủ, 4 số cuối, hoặc tên (không cần dấu). Khách yêu cầu xoá " +
            "thì chủ tiệm hoặc người duyệt xoá ở trang của khách; đơn hàng vẫn giữ. Không có đơn nào " +
            "trong 24 tháng thì tiệm xoá theo lịch.",
        ),
      ),
    }),
    noticeHost,
    lookup ? lookup.node : h("p", { class: "hint" }, readVerdict.reason),
    addSheet.node,
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/customers",
  title: "Khách hàng",
  capability: "CUSTOMERS_READ",
  needsStore: true,
  render: render_,
};

// =============================================================================================
// #/customers/:customerId
// =============================================================================================

/**
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
export function renderDetail(context) {
  const store = storeId();
  const who = principal();
  const writeVerdict = can(who, "CUSTOMERS_WRITE");
  const eraseVerdict = can(who, "CUSTOMERS_ERASE");
  const customerId = String(context?.params?.customerId || "").trim();
  const id = encodeURIComponent(customerId);
  const back = { href: "#/customers", label: "Khách hàng" };

  const headHost = h("div", null, page({ back, title: "Khách hàng" }));
  const contactHost = h("div");
  const bodyHost = h("div", { class: "stack" }, skeletonRows(4));
  const actionHost = h("div");
  const techHost = h("div");
  const alertHost = h("div");
  const sheetsHost = h("div");
  const edit = new Submission("customer-update");
  const erase = new Submission("customer-erase");
  /** @type {any|null} */
  let detail = null;

  async function load() {
    try {
      detail = await request(`/internal/v1/stores/${encodeURIComponent(store)}/customers/${id}`);
      draw();
    } catch (error) {
      render(contactHost);
      render(actionHost);
      render(
        bodyHost,
        /** @type {any} */ (error)?.status === 404
          ? emptyState({
              icon: "user",
              title: "Không tìm thấy khách này",
              body: "Mã sai, hoặc khách thuộc cửa hàng khác.",
            })
          : errorNotice(error, { onRetry: () => void load() }),
      );
    }
  }

  function draw() {
    const customer = detail.customer;
    const erased = Boolean(customer.erased_at);
    render(
      headHost,
      page({
        back,
        title: customerTitle(customer),
        subtitle: erased
          ? `Đã xoá ngày ${dateOnly(customer.erased_at)}`
          : h(
              "span",
              { class: "customer__subtitle" },
              h(
                "span",
                { class: "customer__phone", dataField: "phone" },
                customer.phone ? formatPhone(customer.phone) : maskedPhone(customer.phone_last4),
              ),
              customer.kind === "BUSINESS"
                ? statusPill({ state: "info", text: enumVi("BUSINESS"), token: "BUSINESS" })
                : null,
            ),
      }),
    );
    render(contactHost, erased ? null : contactActions(customer));
    const openIds = new Set((detail.open_orders || []).map((item) => item.order_id));
    const history = (detail.recent_orders || []).filter((item) => !openIds.has(item.order_id));
    render(
      bodyHost,
      erased
        ? inlineAlert({
            state: "info",
            title: "Thông tin cá nhân của khách đã được xoá.",
            body: "Các đơn bên dưới vẫn giữ nguyên; không còn tên hay số điện thoại.",
          })
        : null,
      ordersSection("Đơn đang mở", detail.open_orders || [], "customer-open", {
        empty: "Không có đơn nào đang mở.",
        truncated: detail.open_orders_truncated
          ? "Chỉ hiện 20 đơn mở mới nhất."
          : "",
      }),
      ordersSection("Lịch sử", history, "customer-history", {
        empty: detail.open_orders?.length ? "" : "Chưa có đơn nào.",
        truncated: "",
      }),
      creditsSection(),
      infoSection(customer),
      linksSection(),
    );
    drawActions(customer);
    render(
      techHost,
      techDetails([
        ["Mã khách", String(customer.customer_id), { copy: String(customer.customer_id) }],
        ["Phiên bản", String(customer.row_version)],
        ["Bản thông báo khách đã nghe", String(customer.service_consent_notice_version)],
      ]),
    );
  }

  /** @param {any} customer */
  function contactActions(customer) {
    const tel = telHref(customer.phone);
    const zalo = zaloHref(customer.phone);
    if (!tel || !zalo) {
      return h("p", { class: "hint", id: "customer-masked" }, "Vai trò của bạn không xem được số điện thoại đầy đủ.");
    }
    return h(
      "div",
      { class: "customer__contact" },
      h(
        "a",
        { class: ["button", "btn"], href: tel, dataVariant: "primary", id: "customer-call" },
        icon("phone"),
        h("span", null, "Gọi"),
      ),
      h(
        "a",
        {
          class: ["button", "btn"],
          href: zalo,
          target: "_blank",
          rel: "noopener noreferrer",
          id: "customer-zalo",
        },
        icon("message"),
        h("span", null, "Zalo"),
      ),
    );
  }

  /**
   * @param {string} title
   * @param {any[]} orders
   * @param {string} listId
   * @param {{empty: string, truncated: string}} words
   */
  function ordersSection(title, orders, listId, words) {
    if (!orders.length && !words.empty) return null;
    return section({
      title,
      id: `${listId}-section`,
      card: false,
      children: h(
        "div",
        { class: "stack stack--tight" },
        orders.length
          ? list(
              orders.map((order) => {
                const status = orderStatus(order);
                return listRow({
                  href: `#/orders/${encodeURIComponent(String(order.order_id))}`,
                  leading: "order",
                  title: orderName(order),
                  meta: `${status.text} · ${dateOnly(order.created_at)}`,
                  trailing: money(order.payable_total_vnd, "Chưa có tổng"),
                  data: { order: String(order.order_id) },
                });
              }),
              { label: title, id: listId },
            )
          : h("p", { class: "muted" }, words.empty),
        words.truncated ? h("p", { class: "hint" }, words.truncated) : null,
      ),
    });
  }

  function creditsSection() {
    const credits = detail.credits || [];
    if (!credits.length) return null;
    return section({
      title: "Khoản giảm trừ chưa dùng",
      id: "customer-credits-section",
      card: false,
      info: infoButton(
        "Dùng khoản này thế nào?",
        h(
          "p",
          { class: "hint" },
          "Ở bước “Đồ & giá” của Nhận đồ, bấm “Dùng khoản giảm trừ” và chọn khoản này. Khoản giảm " +
            "trừ được trừ vào đơn mới, không đổi ra tiền mặt.",
        ),
      ),
      children: h(
        "div",
        { class: "stack stack--tight" },
        list(
          credits.map((credit) =>
            listRow({
              leading: "tag",
              title: CREDIT_KIND[credit.kind] || enumVi(credit.kind),
              meta: Number.isInteger(credit.ticket_number)
                ? `Từ Phiếu ${credit.ticket_number} · ${ticketDay(credit.ticket_issued_on)}`
                : `Cấp ngày ${dateOnly(credit.issued_at)}`,
              trailing: money(credit.amount_vnd),
              chevron: false,
              data: { credit: String(credit.credit_id) },
            }),
          ),
          { label: "Khoản giảm trừ chưa dùng", id: "customer-credits" },
        ),
        detail.credits_truncated ? h("p", { class: "hint" }, "Chỉ hiện 20 khoản mới nhất.") : null,
      ),
    });
  }

  /** @param {any} customer */
  function infoSection(customer) {
    if (customer.erased_at) return null;
    return section({
      title: "Thông tin đã lưu",
      id: "customer-info",
      children: keyValues([
        ["Địa chỉ giao đồ", customer.phone_visible ? customer.delivery_address || "—" : "Ẩn với vai trò của bạn"],
        ["Ghi chú", customer.note || "—"],
        ["Loại khách", enumVi(customer.kind)],
        ["Nhận tin ưu đãi", customer.marketing_consent ? "Có" : "Không"],
        ["Đồng ý lưu thông tin", dateTime(customer.service_consent_at)],
      ]),
    });
  }

  function linksSection() {
    const links = detail.links || [];
    if (!links.length) return null;
    return section({
      title: "Phiếu và kênh đã gắn",
      card: false,
      children: list(
        links.map((link) =>
          listRow({
            leading: link.link_kind === "COUNTER_TICKET" ? "tag" : "message",
            title:
              link.link_kind === "COUNTER_TICKET" && Number.isInteger(link.ticket_number)
                ? `Phiếu ${link.ticket_number} · ${ticketDay(link.ticket_issued_on)}`
                : `Khách nhắn qua ${(link.channels || []).map((token) => enumVi(token)).join(", ") || "kênh chat"}`,
            meta: `Gắn ngày ${dateOnly(link.linked_at)}`,
            chevron: false,
          }),
        ),
        { label: "Phiếu và kênh đã gắn", id: "customer-links" },
      ),
    });
  }

  /** @param {any} customer */
  function drawActions(customer) {
    if (customer.erased_at) {
      render(actionHost);
      return;
    }
    const editButton = gated(
      button({
        label: "Sửa",
        icon: "draft",
        id: "customer-edit",
        onClick: () => openEdit(customer),
      }),
      writeVerdict,
    );
    const eraseButton = confirmButton({
      label: "Xoá thông tin (khách yêu cầu)",
      confirmLabel: "Bấm lần nữa để xoá",
      variant: "danger",
      onConfirm: () => void eraseNow(customer, eraseButton),
    });
    eraseButton.id = "customer-erase";
    render(
      actionHost,
      section({
        card: false,
        children: h(
          "div",
          { class: "stack stack--tight customer__actions" },
          editButton,
          gated(eraseButton, eraseVerdict),
          h(
            "p",
            { class: "hint" },
            "Xoá tên, số điện thoại, địa chỉ và ghi chú. Đơn hàng và tiền vẫn giữ.",
          ),
          alertHost,
        ),
      }),
    );
  }

  /**
   * @param {any} customer
   * @param {HTMLButtonElement} control
   */
  async function eraseNow(customer, control) {
    control.setAttribute("aria-busy", "true");
    control.disabled = true;
    render(alertHost);
    try {
      await request(`/internal/v1/stores/${encodeURIComponent(store)}/customers/${id}/erase`, {
        method: "POST",
        body: { reason: "CUSTOMER_REQUEST" },
        idempotencyKey: erase.key(),
        ifMatch: customer.row_version,
      });
      erase.reset();
      toast("Đã xoá thông tin khách · đơn hàng vẫn giữ");
      await load();
    } catch (error) {
      control.removeAttribute("aria-busy");
      control.disabled = false;
      show(alertHost, errorNotice(error, { title: customerRefusalText(error) || undefined }));
    }
  }

  /** @param {any} customer */
  function openEdit(customer) {
    const draft = {
      display_name: customer.display_name || "",
      phone: customer.phone || "",
      delivery_address: customer.delivery_address || "",
      note: customer.note || "",
      kind: customer.kind,
      marketing_consent: Boolean(customer.marketing_consent),
    };
    edit.reset();
    const sheetAlert = h("div");
    /**
     * @param {string} key
     * @param {string} label
     * @param {Record<string, string>} [extra]
     */
    const field = (key, label, extra = {}) => {
      const input = h(key === "note" ? "textarea" : "input", {
        type: key === "note" ? null : "text",
        id: `customer-edit-${key}`,
        value: draft[key],
        autocomplete: "off",
        ...extra,
        onInput: (event) => {
          draft[key] = event.target.value;
          edit.reset();
        },
      });
      return h("div", { class: "stack stack--tight" }, h("label", { for: `customer-edit-${key}` }, label), input);
    };
    const kind = segmented({
      label: "Loại khách",
      id: "customer-edit-kind",
      value: draft.kind,
      options: [
        { value: "RETAIL", label: enumVi("RETAIL") },
        { value: "BUSINESS", label: enumVi("BUSINESS") },
      ],
      onChange: (value) => {
        draft.kind = value;
        edit.reset();
      },
    });
    const marketing = h("input", {
      type: "checkbox",
      id: "customer-edit-marketing",
      checked: draft.marketing_consent,
      onChange: (event) => {
        draft.marketing_consent = event.target.checked;
        edit.reset();
      },
    });
    const save = button({
      label: "Lưu",
      variant: "primary",
      network: true,
      id: "customer-edit-save",
      onClick: () => void submit(),
    });
    const made = sheet({
      id: "customer-edit-sheet",
      title: "Sửa thông tin khách",
      body: h(
        "div",
        { class: "stack" },
        field("display_name", "Tên gọi", { maxlength: "80" }),
        customer.phone_visible ? field("phone", "Số điện thoại", { inputmode: "tel" }) : null,
        customer.phone_visible ? field("delivery_address", "Địa chỉ giao đồ", { maxlength: "300" }) : null,
        field("note", "Ghi chú cách giặt", { maxlength: "200" }),
        h("div", { class: "stack stack--tight" }, h("p", { class: "label" }, "Loại khách"), kind),
        h(
          "div",
          { class: "check-row" },
          h(
            "label",
            { class: "check-row__label", for: "customer-edit-marketing" },
            marketing,
            h("span", null, "Khách muốn nhận tin ưu đãi của tiệm"),
          ),
        ),
        sheetAlert,
      ),
      actions: save,
      onClose: () => made.node.remove(),
    });

    async function submit() {
      /** @type {Record<string, unknown>} */
      const changes = {};
      const text = (value) => String(value || "").trim();
      if (text(draft.display_name) !== text(customer.display_name)) {
        changes.display_name = text(draft.display_name) || null;
      }
      if (customer.phone_visible && text(draft.phone) !== text(customer.phone)) {
        changes.phone = text(draft.phone);
      }
      if (customer.phone_visible && text(draft.delivery_address) !== text(customer.delivery_address)) {
        changes.delivery_address = text(draft.delivery_address) || null;
      }
      if (text(draft.note) !== text(customer.note)) changes.note = text(draft.note) || null;
      if (draft.kind !== customer.kind) changes.kind = draft.kind;
      if (draft.marketing_consent !== Boolean(customer.marketing_consent)) {
        changes.marketing_consent = draft.marketing_consent;
      }
      if (!Object.keys(changes).length) {
        made.close();
        return;
      }
      save.disabled = true;
      save.setAttribute("aria-busy", "true");
      try {
        await request(`/internal/v1/stores/${encodeURIComponent(store)}/customers/${id}`, {
          method: "PATCH",
          body: changes,
          idempotencyKey: edit.key(),
          ifMatch: customer.row_version,
        });
        edit.reset();
        made.close();
        toast("Đã lưu thông tin khách");
        await load();
      } catch (error) {
        save.disabled = false;
        save.removeAttribute("aria-busy");
        show(sheetAlert, errorNotice(error, { title: customerRefusalText(error) || undefined }));
      }
    }

    render(sheetsHost, made.node);
    made.open();
  }

  if (UUID.test(customerId)) {
    void load();
  } else {
    render(
      bodyHost,
      inlineAlert({ state: "danger", title: "Địa chỉ này không chứa một mã khách hợp lệ" }),
    );
  }

  return h(
    "section",
    { class: "screen customer" },
    headHost,
    contactHost,
    bodyHost,
    actionHost,
    techHost,
    sheetsHost,
  );
}

/** @type {import("../core/router.js").Route} */
export const detailScreen = {
  path: "/customers/:customerId",
  title: "Khách hàng",
  capability: "CUSTOMERS_READ",
  needsStore: true,
  render: renderDetail,
};
