/**
 * Đồ chờ lấy: finished laundry the customer has not collected (`UNCLAIMED-001`, `DEC-036`).
 *
 * The list the counter works through when the shelf fills up: longest-waiting first, as the server
 * orders it. Each row is the ticket or the customer, how long it has waited, how often the shop has
 * tried to reach them, and the storage fee owed so far — every figure the server's. Under it, the
 * two things a person does about it: **Gọi** (only when a phone is on the customer's record; the
 * number rides inside the `tel:` link and is never printed here) and **Ghi lần liên hệ**. Tapping
 * the row opens the order, where the fee can be waived and, when legal, the owner disposes of it.
 *
 * Before the owner publishes the storage policy the list and the attempts work, and the screen says
 * in one line that no fee is charged and nothing can be disposed of until then.
 *
 * @module screens/pickup
 */

import { request } from "../core/api.js";
import { customerTitle, maskedPhone, telHref } from "../core/customers.js";
import { h, render } from "../core/dom.js";
import { calendarDay, money } from "../core/format.js";
import { CONTACT_OUTCOME_VI } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated, icon } from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  list,
  listRow,
  page,
  show,
  skeletonRows,
  statusPill,
} from "../ui/kit.js";
import { contactSheet, feeText, shelfSwitch, waitingText } from "../ui/unclaimed.js";
import { orderName } from "./orders.js";

/** One page of the waiting list; the server says when there are more (`truncated`). */
const LIMIT = 100;

/**
 * What a row calls the order: the customer's name, or the ticket.
 *
 * @param {any} item an `AwaitingPickupItemResponse`
 * @returns {string}
 */
function rowTitle(item) {
  if (item.customer_name) return String(item.customer_name);
  if (item.customer_id && item.phone_last4) {
    return customerTitle({ phone_last4: item.phone_last4 });
  }
  return orderName(item);
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const who = principal();
  const readVerdict = can(who, "PICKUP_READ");
  const contactVerdict = can(who, "PICKUP_CONTACT");
  const noticeHost = h("div");
  const listHost = h("div", { class: "stack stack--tight" }, skeletonRows(4));
  const sheetsHost = h("div");
  const subtitle = h("span", { dataField: "pickup-count" });
  const infoHost = h("span");

  async function load() {
    try {
      const payload = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders/awaiting-pickup?limit=${LIMIT}`,
      );
      draw(payload);
    } catch (error) {
      show(listHost, errorNotice(/** @type {any} */ (error), { onRetry: () => void load() }));
    }
  }

  /** @param {any} payload */
  function draw(payload) {
    const orders = Array.isArray(payload?.orders) ? payload.orders : [];
    const policy = payload?.policy || null;
    subtitle.textContent = payload.total_count
      ? `${payload.total_count} đơn · lâu nhất ${waitingText(orders[0]?.days_waiting).toLowerCase()}`
      : "Không có đơn nào";
    render(
      infoHost,
      infoButton(
        "Đồ chờ lấy là gì?",
        h(
          "p",
          null,
          "Đơn đã giặt xong, đang ở tiệm, khách chưa lấy. Đơn giao tận nơi không nằm ở đây — người " +
            "giao mang đi.",
        ),
        policy
          ? h("p", null, policy.receipt_line_vi)
          : h(
              "p",
              null,
              "Chủ tiệm chưa công bố phí lưu kho (DEC-036): chưa tính phí, chưa thanh lý. Liên hệ " +
                "khách vẫn ghi được.",
            ),
        policy ? h("p", { class: "hint" }, policy.disposal_rule_vi) : null,
      ),
    );
    render(
      noticeHost,
      policy
        ? null
        : inlineAlert({
            state: "info",
            title: "Chủ tiệm chưa công bố phí lưu kho: chưa tính phí, chưa thanh lý được.",
          }),
    );
    if (!orders.length) {
      render(
        listHost,
        emptyState({
          icon: "store",
          title: "Không có đồ nào chờ lấy",
          body: "Đồ đã xong mà khách chưa lấy sẽ hiện ở đây.",
        }),
      );
      return;
    }
    render(
      listHost,
      list(orders.map((item) => pickupRow(item, policy)), { label: "Đồ chờ lấy", id: "pickup-list" }),
      payload.truncated
        ? h(
            "p",
            { class: "hint", dataTruncated: "true" },
            `Chỉ hiện ${orders.length} đơn chờ lâu nhất trong ${payload.total_count} đơn.`,
          )
        : null,
    );
  }

  /**
   * @param {any} item
   * @param {any|null} policy
   */
  function pickupRow(item, policy) {
    const fee = item.storage_fee || {};
    const tel = telHref(item.phone);
    const title = rowTitle(item);
    // The title is the customer's name, or the ticket; the meta line never repeats the title.
    const day = item.ticket_issued_on ? `Nhận ${calendarDay(item.ticket_issued_on, { weekday: false })}` : null;
    const label = [title === orderName(item) ? null : orderName(item), day]
      .filter(Boolean)
      .join(" · ");
    const attempts = item.attempts_count
      ? `${item.attempts_count} lần liên hệ${
          item.last_attempt_outcome
            ? ` · lần cuối: ${(CONTACT_OUTCOME_VI[item.last_attempt_outcome] || item.last_attempt_outcome).toLowerCase()}`
            : ""
        }`
      : "Chưa liên hệ";
    const openContact = (channel) => {
      const made = contactSheet({
        orderId: String(item.order_id),
        title: title === orderName(item) ? title : `${orderName(item)} · ${title}`,
        channel,
        onRecorded: () => void load(),
      });
      render(sheetsHost, made.node);
      made.open();
    };
    const call = tel
      ? h(
          "a",
          {
            class: ["button", "btn"],
            href: tel,
            dataVariant: "primary",
            dataCall: String(item.order_id),
            // The dialler opens; the sheet waits here for what came of the call.
            onClick: () => openContact("CALL"),
          },
          icon("phone"),
          h("span", null, "Gọi"),
        )
      : null;
    const record = gated(
      button({
        label: "Ghi lần liên hệ",
        variant: "secondary",
        network: true,
        data: { recordAttempt: String(item.order_id) },
        onClick: () => openContact(tel ? "CALL" : "ZALO"),
      }),
      contactVerdict,
    );
    return h(
      "div",
      { class: "pickup-row", dataOrder: String(item.order_id) },
      listRow({
        href: `#/orders/${encodeURIComponent(String(item.order_id))}`,
        leading: "store",
        title,
        meta: [
          h("span", null, label),
          h("span", { dataField: "waiting" }, `${waitingText(item.days_waiting)} · ${attempts}`),
          item.disposal?.allowed
            ? statusPill({ state: "danger", text: "Thanh lý được", token: "DISPOSAL_ALLOWED" })
            : item.balance === "PAID"
              ? statusPill({ state: "ok", text: "Đã trả tiền", token: "PAID" })
              : null,
        ],
        trailing: fee.amount_vnd ? money(fee.amount_vnd) : null,
        trailingMeta: fee.amount_vnd ? "phí lưu kho" : policy ? feeText(fee, policy) : null,
        data: { pickup: String(item.order_id), feeStatus: String(fee.status || "") },
      }),
      h(
        "div",
        { class: "pickup-row__actions" },
        call,
        !tel && item.customer_id && !item.phone && item.has_phone
          ? h("span", { class: "hint" }, maskedPhone(item.phone_last4))
          : null,
        record,
      ),
    );
  }

  if (!readVerdict.allowed) {
    render(listHost, h("p", { class: "hint" }, readVerdict.reason));
  } else if (!store) {
    render(listHost, h("p", { class: "hint" }, "Chọn một cửa hàng để xem đồ chờ lấy."));
  } else {
    void load();
  }

  return h(
    "section",
    { class: "screen pickup" },
    page({
      title: "Đồ chờ lấy",
      subtitle,
      info: infoHost,
      action: button({ label: "Tải lại", icon: "refresh", variant: "quiet", onClick: () => void load() }),
    }),
    shelfSwitch("/pickup"),
    noticeHost,
    listHost,
    sheetsHost,
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/pickup",
  title: "Đồ chờ lấy",
  capability: "PICKUP_READ",
  needsStore: true,
  render: render_,
};
