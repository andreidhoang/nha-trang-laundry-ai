/**
 * One order: its four states, and everything the audit log recorded against its identifier.
 *
 * This screen exists in a slightly awkward position, and the awkwardness is the point rather than
 * something to paper over:
 *
 *   - **There is no route that reads one order.** The API offers a store-scoped board and nothing
 *     else, so the "detail" here is the board fetched again and filtered in the browser. That means
 *     an order outside the newest hundred rows cannot be shown at all, and the screen says exactly
 *     that instead of rendering "không tìm thấy", which would be a claim about the order's
 *     existence that this console is in no position to make.
 *   - **The timeline is a Shadow surface wearing an order's clothes.** `GET …/shadow/audit/{id}` is
 *     the only audit read in the API, and the repository gates it on `SHADOW_READ` regardless of
 *     what the caller thinks they are looking at. An `AUDITOR` or an `OPERATOR` reaches it; a role
 *     that does not hold it is told so here rather than shown an empty list it would misread as
 *     "nothing ever happened".
 *   - **An empty timeline is genuinely ambiguous.** The server answers an unknown aggregate with
 *     `200 []`, not a `404`. So "no rows" means "no recorded events for this identifier" and
 *     nothing more — not that the order is new, not that the order is absent. The empty state says
 *     the ambiguous thing because the ambiguous thing is what is true.
 *   - **Nothing is re-sorted and nothing is totalled.** The server orders by `(occurred_at, id)`,
 *     oldest first, and that order is preserved; there is no money on this screen to add up.
 *
 * @module screens/orderDetail
 */

import { isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, count, dateTime, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  empty,
  errorNotice,
  facts,
  panel,
  skeleton,
} from "../ui/components.js";

const BOARD_LIMIT = 100;

/**
 * The server's fixed audit page size.
 *
 * `ShadowConsoleRepository.audit_timeline` takes a `limit` with a default of 100, and the route
 * does not expose it. So this is not a request the console makes; it is a ceiling the console can
 * only disclose.
 */
const AUDIT_LIMIT = 100;

/** Loose UUID shape — the route parameter is user-controlled text until proven otherwise. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * One state dimension, as a neutral badge.
 *
 * Uncoloured for the same reason as on the board: ranking the severity of a domain state is a
 * domain judgement, and this console does not hold one. The enum value carries the meaning.
 *
 * @param {string|null|undefined} value
 * @returns {HTMLElement}
 */
function dimensionBadge(value) {
  return badge({ token: enumLabel(value), gloss: "", state: "neutral" });
}

/**
 * One audit row.
 *
 * @param {any} entry an `AuditEntryResponse`
 * @returns {HTMLElement}
 */
function auditRow(entry) {
  return h(
    "li",
    { class: "card" },
    facts([
      ["Thời điểm", dateTime(entry.occurred_at)],
      ["Hành động", entry.action || UNKNOWN, { mono: true }],
      [
        "Tác nhân",
        h(
          "span",
          { class: "row" },
          badge({ token: enumLabel(entry.actor_type), gloss: "", state: "neutral" }),
          h(
            "span",
            { class: "mono", title: entry.actor_id || "" },
            // `null` here means the server recorded no staff user for this row — the outbox worker
            // and the bootstrap have no person behind them. It is shown as unknown, never as a
            // blank that reads like a missing field.
            shortId(entry.actor_id),
          ),
        ),
        { span: true },
      ],
      ["Đối tượng", enumLabel(entry.aggregate_type), { mono: true }],
    ]),
  );
}

/**
 * @param {any[]} entries
 * @returns {HTMLElement}
 */
function auditTimeline(entries) {
  if (!entries.length) {
    return empty(
      "Không có sự kiện nào được ghi cho định danh này. Máy chủ trả mảng rỗng cả khi định danh " +
        "không tồn tại lẫn khi đơn chưa phát sinh sự kiện — hai trường hợp đó không phân biệt " +
        "được từ đây.",
    );
  }
  return h("ol", { class: "stack" }, entries.map(auditRow));
}

/**
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
export function render_(context) {
  const orderId = String(context?.params?.orderId || "").trim();
  const store = storeId();
  const shadowVerdict = can(principal(), "SHADOW_READ");
  const wellFormed = UUID.test(orderId);

  const orderHost = h("div", null, skeleton(1));
  const timelineHost = h("div", null, skeleton(3));
  const timelineCount = h("span", { class: "count" }, "…");
  const timelineTruncation = h("p", { class: "hint" });

  /**
   * Fetch the board and pick this order out of it.
   *
   * The filter is done here rather than server-side because there is no server-side filter to use.
   * That has a consequence worth stating plainly on screen: absence from the response is absence
   * from the newest hundred rows of one store, not absence from the system.
   */
  async function loadOrder() {
    render(orderHost, skeleton(1));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?limit=${BOARD_LIMIT}`,
      );
      const found = (Array.isArray(items) ? items : []).find(
        (item) => String(item.order_id).toLowerCase() === orderId.toLowerCase(),
      );

      if (!found) {
        const full = isTruncated(items, BOARD_LIMIT);
        render(
          orderHost,
          h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Không thấy đơn này trong bảng đơn"),
            h(
              "p",
              null,
              full
                ? `Bảng chỉ đọc được ${BOARD_LIMIT} đơn mới nhất và đã trả đủ ${BOARD_LIMIT} dòng, ` +
                  "nên đơn này có thể nằm ngoài phạm vi đọc được. Đây không phải bằng chứng đơn " +
                  "không tồn tại."
                : "Cửa hàng đang chọn không có đơn nào mang định danh này. Đơn có thể thuộc cửa " +
                  "hàng khác — API không có route đọc một đơn theo định danh, nên không kiểm tra " +
                  "được điều đó từ đây.",
            ),
            h("p", { class: "hint mono" }, orderId),
          ),
        );
        return;
      }

      render(
        orderHost,
        h(
          "div",
          { class: "stack" },
          facts([
            ["Mã đơn", orderId, { mono: true, span: true }],
            ["Cửa hàng", found.store_id, { mono: true, span: true }],
            ["Thương mại", dimensionBadge(found.commercial)],
            ["Tiếp nhận", dimensionBadge(found.intake)],
            ["Sản xuất", dimensionBadge(found.production)],
            ["Công nợ", dimensionBadge(found.balance)],
            ["Phiên bản dòng", `v${found.row_version}`],
          ]),
          h(
            "p",
            { class: "hint" },
            "Bốn trục chuyển động độc lập với nhau; đừng đọc chúng như một chuỗi tuần tự. Lệnh " +
              "chuyển trạng thái nằm ở bảng đơn, vì lệnh đó cần phiên bản dòng vừa đọc được.",
          ),
        ),
      );
    } catch (error) {
      render(orderHost, errorNotice(error, { onRetry: () => void loadOrder() }));
    }
  }

  async function loadTimeline() {
    // The repository gates this on SHADOW_READ even though the screen is an order screen. A role
    // without it gets the reason, not an empty list that would read as "nothing ever happened".
    if (!shadowVerdict.allowed) {
      timelineCount.textContent = "—";
      render(
        timelineHost,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Vai trò này không đọc được dòng thời gian"),
          h("p", null, shadowVerdict.reason),
        ),
      );
      return;
    }

    render(timelineHost, skeleton(3));
    try {
      const entries = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/audit/${encodeURIComponent(orderId)}`,
      );
      const rows = Array.isArray(entries) ? entries : [];
      timelineCount.textContent = count(rows, AUDIT_LIMIT);
      timelineTruncation.textContent =
        isTruncated(rows, AUDIT_LIMIT)
          ? `Máy chủ cố định ${AUDIT_LIMIT} dòng và đã trả đủ; có thể còn nữa. Route này không ` +
            "nhận tham số limit và không có phân trang, nên phần còn lại chỉ đọc được ở cơ sở dữ liệu."
          : "";
      render(timelineHost, auditTimeline(rows));
    } catch (error) {
      timelineCount.textContent = "—";
      timelineTruncation.textContent = "";
      render(timelineHost, errorNotice(error, { onRetry: () => void loadTimeline() }));
    }
  }

  if (wellFormed) {
    void loadOrder();
    void loadTimeline();
  } else {
    // A malformed identifier would be a 422 from the server and a confusing one, because the
    // failure is in the address bar rather than in anything the operator typed on this screen.
    const malformed = h(
      "div",
      { class: "notice", dataState: "danger" },
      h("p", { class: "notice__title" }, "Địa chỉ này không chứa một mã đơn hợp lệ"),
      h("p", null, "Mã đơn phải là một UUID. Không có yêu cầu nào được gửi đi."),
      h("p", { class: "hint mono" }, orderId || UNKNOWN),
    );
    render(orderHost, malformed);
    timelineCount.textContent = "—";
    render(
      timelineHost,
      empty("Chưa đọc dòng thời gian vì mã đơn trong địa chỉ không hợp lệ."),
    );
  }

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "GHÉP TỪ BẢNG ĐƠN · API KHÔNG CÓ ROUTE ĐỌC MỘT ĐƠN"),
      h("h1", null, "Chi tiết đơn"),
      h(
        "p",
        { class: "screen__lede" },
        h("span", { class: "mono" }, orderId || UNKNOWN),
      ),
      h("p", null, h("a", { href: "#/orders" }, "← Về bảng đơn")),
    ),
    panel({
      eyebrow: "TRẠNG THÁI",
      title: "Đơn này",
      guardrail:
        "API không có route đọc một đơn. Màn hình này gọi lại bảng đơn của cửa hàng và lọc theo " +
        `mã trong địa chỉ, nên chỉ thấy được ${BOARD_LIMIT} đơn mới nhất. Ngoài bốn trạng thái và ` +
        "phiên bản dòng, máy chủ không trả thêm gì về một đơn — không khách hàng, không mốc thời " +
        "gian, không tiền.",
      children: h(
        "div",
        { class: "stack" },
        orderHost,
        h("p", null, h("a", { href: "#/gaps" }, "Xem khoảng trống: read model chi tiết đơn")),
      ),
    }),
    panel({
      eyebrow: "KIỂM TOÁN",
      title: "Dòng thời gian",
      count: timelineCount,
      guardrail:
        "Đây là bản ghi kiểm toán theo định danh, không phải lịch sử đơn hàng được biên tập. Thứ " +
        "tự do máy chủ quyết định (occurred_at, id) — cũ nhất trước — và màn hình này không sắp " +
        "xếp lại. Định danh không tồn tại cũng trả về danh sách rỗng chứ không phải lỗi 404.",
      children: h(
        "div",
        { class: "stack" },
        h(
          "p",
          { class: "hint" },
          "Tác nhân không phải STAFF — OUTBOX_WORKER, AGENT_RUNNER, BOOTSTRAP — không gắn với " +
            "người dùng nào, nên cột định danh người thực hiện để trống.",
        ),
        timelineTruncation,
        timelineHost,
      ),
    }),
  );
}

export const screen = {
  path: "/orders/:orderId",
  title: "Chi tiết đơn",
  capability: "ORDERS_READ",
  needsStore: true,
  render: render_,
};
