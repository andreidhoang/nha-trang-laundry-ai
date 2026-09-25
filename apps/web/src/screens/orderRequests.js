/**
 * Tiếp nhận: every customer the counter has taken in, and where each one stands.
 *
 * Taking a customer in happens on `＋ Nhận đồ` now (`CONSOLE-REDESIGN-001`): one press issues the
 * ticket and opens the intake. This screen is the history of those intakes and the way back into
 * one: an intake that has not become an order resumes the flow (`#/new?request=<id>`, every value
 * restored from the server's reads), and one that has opens its order.
 *
 * What it still refuses to be, from its V1 form:
 *
 *   - **It stores and shows nothing about the person.** `DEC-013`: a walk-in is a number the counter
 *     issued. A row reads "Phiếu 17", never a name, and the contact reference itself is tier 3.
 *   - **It edits and cancels nothing.** No route mutates an intake; a wrong one is outlived by its
 *     quote, not rewritten.
 *   - **Truncation is disclosed.** The route caps at 100 and has no cursor, so a full page says so.
 *
 * "Đang chờ báo giá" / "Đã thành đơn" is read from `order_id` (`READ-ENRICH-001`), which is null
 * until the intake's quote is converted. The console decides nothing about it.
 *
 * @module screens/orderRequests
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { dateTime, matchesFilter } from "../core/format.js";
import { storeId } from "../core/session.js";
import { listView } from "../ui/components.js";
import { emptyState, linkButton, list, listRow, page, segmented, statusPill } from "../ui/kit.js";
import { customerLabel } from "../ui/quoting.js";

const LIST_LIMIT = 100;

/** @type {Array<{value: string, label: string}>} */
const SCOPES = [
  { value: "waiting", label: "Đang chờ" },
  { value: "converted", label: "Đã thành đơn" },
  { value: "all", label: "Tất cả" },
];

/**
 * @param {any} item an `OrderRequestSummaryResponse`
 * @returns {HTMLElement}
 */
function requestRow(item) {
  const converted = Boolean(item.order_id);
  return listRow({
    href: converted
      ? `#/orders/${encodeURIComponent(String(item.order_id))}`
      : `#/new?request=${encodeURIComponent(String(item.order_request_id))}`,
    leading: "intake",
    title: customerLabel(item),
    meta: `lúc ${dateTime(item.created_at)}`,
    trailing: statusPill({
      state: converted ? "ok" : "warn",
      text: converted ? "Đã thành đơn" : "Đang chờ báo giá",
    }),
    data: { request: String(item.order_request_id) },
  });
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  let scope = "waiting";
  const scopeHost = h("div");

  /** @param {any[]} items */
  function drawScopes(items) {
    const waiting = items.filter((item) => !item.order_id).length;
    const counts = { waiting, converted: items.length - waiting, all: items.length };
    const control = segmented({
      label: "Lọc lượt tiếp nhận",
      id: "intake-scope",
      value: scope,
      options: SCOPES.map((option) => ({ ...option, count: String(counts[option.value]) })),
      onChange: (value) => {
        scope = value;
        view.rerender();
      },
    });
    control.classList.add("segmented--fit");
    render(scopeHost, control);
  }

  const view = listView({
    limit: LIST_LIMIT,
    fetch: () =>
      request(`/internal/v1/stores/${encodeURIComponent(store)}/order-requests?limit=${LIST_LIMIT}`),
    renderItem: requestRow,
    container: (rows) => list(rows, { label: "Lượt tiếp nhận", id: "intake-list" }),
    scope: () =>
      scope === "waiting"
        ? (item) => !item.order_id
        : scope === "converted"
          ? (item) => Boolean(item.order_id)
          : null,
    emptyText: "Chưa có lượt tiếp nhận nào trong cửa hàng này.",
    emptyNode: (filtered) =>
      emptyState({
        icon: "intake",
        title: filtered ? "Không có lượt nào ở mục này" : "Chưa có lượt tiếp nhận nào",
        body: filtered ? null : "Khách gửi đồ thì bắt đầu ở Nhận đồ.",
        action: filtered
          ? null
          : linkButton({ href: "#/new", label: "Nhận đồ", icon: "plus", variant: "primary" }),
      }),
    skeletonRows: 3,
    filterStatusHiddenWhenInactive: true,
    truncationText: (n) => `Đang hiện ${n} lượt gần nhất; có thể còn nữa ở phía trước.`,
    onLoaded: drawScopes,
    filter: {
      placeholder: "Tìm số phiếu…",
      label: "Tìm lượt tiếp nhận theo số phiếu",
      noun: "lượt tiếp nhận",
      matches: (item, needle) =>
        matchesFilter([customerLabel(item), item.ticket_number, dateTime(item.created_at)], needle),
    },
  });
  void view.reload();

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Tiếp nhận",
      subtitle: "Khách đã nhận phiếu, mới nhất trước.",
      action: linkButton({ href: "#/new", label: "Nhận đồ", icon: "plus", variant: "primary" }),
    }),
    h(
      "div",
      { class: "stack" },
      view.bar.node,
      scopeHost,
      view.filterStatus,
      view.truncation,
      view.host,
    ),
  );
}

export const screen = {
  path: "/order-requests",
  title: "Tiếp nhận",
  capability: "QUOTES_READ",
  needsStore: true,
  render: render_,
};
