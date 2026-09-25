/**
 * Báo giá: every price the server has written for this store, and one revision read back.
 *
 * Pricing a bag happens on `＋ Nhận đồ` now (`CONSOLE-REDESIGN-001`), which carries the intake,
 * the quote, its revision and its seal forward by itself. This screen is the history and the way
 * back in:
 *
 *   - **The list.** One row per quote: whose ticket it is (joined from the intake list, which
 *     carries `ticket_number` and `order_id` since `READ-ENRICH-001`), its total as the server
 *     stated it, how certain that price is, and its status. Nothing is summed or recomputed.
 *   - **One revision, read-only** (`#/quotes?quote=<id>[&revision=<n>]`). This is where an approver
 *     lands from `#/approvals` before deciding a `QUOTE_REVISION` envelope: the lines and the bands
 *     behind a digest, drawn as a receipt. With `&revision=` it is strictly a read and carries no
 *     control, because a revision opened from the queue is being reviewed, not priced — and since
 *     `DEC-029` a counter-chosen price is attested by the person who chose it, never by whoever
 *     opens it here. A malformed revision opens nothing: reading the wrong revision is worse than
 *     reading none.
 *   - **Resume.** Opened from the list, an unconverted quote offers "Tiếp tục" (back into
 *     `#/new` at step 2 or 3, restored from the reads) and "Thêm bản sửa đổi".
 *
 * @module screens/quotes
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UUID, matchesFilter, moneyRange } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { storeId } from "../core/session.js";
import { errorNotice, listView } from "../ui/components.js";
import {
  actionBar,
  emptyState,
  infoButton,
  linkButton,
  list,
  listRow,
  page,
  segmented,
  skeletonRows,
} from "../ui/kit.js";
import { customerLabel, finalityPill, modeLabel, receipt } from "../ui/quoting.js";

const LIST_LIMIT = 100;

/** The intake list the rows are joined to for their ticket number; the route caps at 100. */
const REQUEST_LIMIT = 100;

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const openId = String(context?.query?.get("quote") || "").trim();
  if (openId) return detailView(openId, String(context?.query?.get("revision") || "").trim());
  return listScreen();
}

// ---------------------------------------------------------------------------------------------
// The list
// ---------------------------------------------------------------------------------------------

function listScreen() {
  const store = storeId();
  /** @type {Map<string, any>} order_request_id → the intake, for "Phiếu 17" and "đã thành đơn" */
  let intakes = new Map();
  let intakesUnread = false;
  let scope = "open";
  const scopeHost = h("div");
  const intakeNote = h("div");

  const converted = (item) => Boolean(intakes.get(String(item.order_request_id))?.order_id);

  function drawScopes(items) {
    const open = items.filter((item) => !converted(item)).length;
    render(
      scopeHost,
      segmented({
        label: "Lọc báo giá",
        id: "quote-scope",
        value: scope,
        options: [
          { value: "open", label: "Chưa thành đơn", count: String(open) },
          { value: "all", label: "Tất cả", count: String(items.length) },
        ],
        onChange: (value) => {
          scope = value;
          view.rerender();
        },
      }),
    );
    render(
      intakeNote,
      intakesUnread
        ? h(
            "p",
            { class: "hint" },
            "Chưa đọc được danh sách tiếp nhận, nên chưa biết số phiếu và báo giá nào đã thành đơn.",
          )
        : null,
    );
  }

  /** @param {any} item a `QuoteSummaryResponse` */
  function quoteRow(item) {
    const intake = intakes.get(String(item.order_request_id));
    const total = moneyRange(item.display_total_min_vnd, item.display_total_max_vnd, "chưa có tổng");
    return listRow({
      href: `#/quotes?quote=${encodeURIComponent(String(item.quote_id))}`,
      leading: "quote",
      title: intake ? customerLabel(intake) : "Báo giá",
      meta: [
        converted(item) ? "Đã thành đơn" : enumVi(item.status),
        item.fulfillment_mode ? modeLabel(item.fulfillment_mode) : null,
        `bản ${item.revision}`,
      ]
        .filter(Boolean)
        .join(" · "),
      trailing: h("span", { class: total.isKnown ? "money" : "muted" }, total.text),
      trailingMeta: finalityPill(item.finality),
      data: { quote: String(item.quote_id) },
    });
  }

  const view = listView({
    limit: LIST_LIMIT,
    fetch: async () => {
      const [quotes, requests] = await Promise.all([
        request(`/internal/v1/stores/${encodeURIComponent(store)}/quotes?limit=${LIST_LIMIT}`),
        request(
          `/internal/v1/stores/${encodeURIComponent(store)}/order-requests?limit=${REQUEST_LIMIT}`,
        ).catch(() => null),
      ]);
      intakesUnread = !Array.isArray(requests);
      intakes = new Map(
        (Array.isArray(requests) ? requests : []).map((item) => [String(item.order_request_id), item]),
      );
      return quotes;
    },
    renderItem: quoteRow,
    container: (rows) => list(rows, { label: "Báo giá", id: "quote-list" }),
    scope: () => (scope === "open" ? (item) => !converted(item) : null),
    emptyText: "Chưa có báo giá nào trong cửa hàng này.",
    emptyNode: (filtered) =>
      emptyState({
        icon: "quote",
        title: filtered ? "Không có báo giá nào đang chờ" : "Chưa có báo giá nào",
        body: filtered ? null : "Tính giá cho khách ở Nhận đồ.",
        action: filtered
          ? null
          : linkButton({ href: "#/new", label: "Nhận đồ", icon: "plus", variant: "primary" }),
      }),
    skeletonRows: 3,
    filterStatusHiddenWhenInactive: true,
    onLoaded: drawScopes,
    filter: {
      placeholder: "Tìm số phiếu…",
      label: "Tìm báo giá theo số phiếu",
      noun: "báo giá",
      matches: (item, needle) => {
        const intake = intakes.get(String(item.order_request_id));
        return matchesFilter(
          [intake ? customerLabel(intake) : "", item.revision, enumVi(item.status), item.finality],
          needle,
        );
      },
    },
  });
  void view.reload();

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Báo giá",
      subtitle: "Giá máy chủ đã tính, mới nhất trước.",
      action: linkButton({ href: "#/new", label: "Nhận đồ", icon: "plus", variant: "primary" }),
      info: infoButton(
        "Giá ở đây do ai tính?",
        h(
          "p",
          { class: "hint" },
          "Máy chủ tính theo bảng giá đã chốt. Món niêm yết theo khoảng giá thì nhân viên trực quầy " +
            "chốt một số trong khoảng, có ghi tên. Màn hình này không tự cộng tiền, không làm tròn, và " +
            "không tự chọn số nào trong khoảng.",
        ),
        h(
          "p",
          { class: "hint" },
          "Thiếu dữ kiện thì máy chủ từ chối đoán và không ghi gì cả — người quyết, không phải máy.",
        ),
      ),
    }),
    h(
      "div",
      { class: "stack" },
      view.bar.node,
      scopeHost,
      intakeNote,
      view.filterStatus,
      view.truncation,
      view.host,
    ),
  );
}

// ---------------------------------------------------------------------------------------------
// One revision, read back
// ---------------------------------------------------------------------------------------------

/**
 * @param {string} id
 * @param {string} revision the revision an envelope binds, when the caller arrived from one
 * @returns {HTMLElement}
 */
function detailView(id, revision) {
  const store = storeId();
  const host = h("div", { class: "stack" }, skeletonRows(3));
  const titleHost = h("div", null, page({ title: "Báo giá", back: { href: "#/quotes", label: "Báo giá" } }));
  // Arrived from an envelope (or from a credit's record): a read, and nothing else.
  const readOnly = Boolean(revision);

  async function load() {
    if (!UUID.test(id)) {
      render(
        host,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Mã báo giá trong đường dẫn không hợp lệ"),
          h("p", null, "Không mở được bản nào; chọn báo giá từ danh sách bên dưới."),
        ),
        linkButton({ href: "#/quotes", label: "Danh sách báo giá", variant: "quiet" }),
      );
      return;
    }
    // Refused rather than silently dropped: a caller that asked for a revision and got the newest
    // one instead is the exact failure this parameter exists to prevent.
    if (revision && !/^[1-9][0-9]{0,8}$/.test(revision)) {
      render(
        host,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Số bản sửa đổi trong đường dẫn không hợp lệ"),
          h("p", null, "Không mở bản nào, vì mở nhầm bản còn tệ hơn không mở. Kiểm lại đường dẫn."),
        ),
      );
      return;
    }
    render(host, skeletonRows(3));
    try {
      // The catalog names the lines; without it they read as codes, which is honest, not fatal.
      const [detail, catalog] = await Promise.all([
        request(
          `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(id)}` +
            (revision ? `?revision=${encodeURIComponent(revision)}` : ""),
        ),
        request("/internal/v1/pricebook/services").catch(() => null),
      ]);
      let intake = null;
      if (detail.order_request_id) {
        intake = await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/order-requests/${encodeURIComponent(String(detail.order_request_id))}`,
        ).catch(() => null);
      }
      draw(detail, Array.isArray(catalog) ? catalog : null, intake);
    } catch (error) {
      // A 404 here is most often store scope: the approvals queue spans every store the approver
      // is assigned to, while this read is scoped to the one selected in the app bar. The server
      // answers the same for "another store's quote" and "no such quote", so neither is guessed.
      render(
        host,
        /** @type {any} */ (error).kind === "MISSING"
          ? h(
              "div",
              { class: "notice", dataState: "warn" },
              h(
                "p",
                { class: "notice__title" },
                "Không tìm thấy bản báo giá này trong cửa hàng đang chọn",
              ),
              h(
                "p",
                null,
                "Báo giá có thể thuộc cửa hàng khác, hoặc mã đã sai — máy chủ trả lời giống nhau " +
                  "cho cả hai nên màn hình này cũng không đoán. Đổi cửa hàng ở thanh trên rồi mở " +
                  "lại đường dẫn. Đừng duyệt một phiếu mà bạn chưa xem được nội dung.",
              ),
            )
          : errorNotice(/** @type {any} */ (error), { onRetry: () => void load() }),
      );
    }
  }

  /**
   * @param {any} detail
   * @param {any[]|null} catalog
   * @param {any|null} intake
   */
  function draw(detail, catalog, intake) {
    const isConverted = Boolean(intake?.order_id);
    render(
      titleHost,
      page({
        title: intake ? customerLabel(intake) : "Báo giá",
        subtitle: [
          `Bản sửa đổi ${detail.revision}`,
          isConverted ? "Đã thành đơn" : enumVi(detail.status),
          detail.fulfillment_mode ? modeLabel(detail.fulfillment_mode) : null,
        ]
          .filter(Boolean)
          .join(" · "),
        back: { href: "#/quotes", label: "Báo giá" },
      }),
    );
    const actions = readOnly
      ? null
      : isConverted
        ? leadBar(
            linkButton({
              href: `#/orders/${encodeURIComponent(String(intake.order_id))}`,
              label: "Mở đơn",
              variant: "primary",
            }),
          )
        : leadBar(
            linkButton({
              href: `#/new?quote=${encodeURIComponent(String(detail.quote_id))}&revise=1`,
              label: "Thêm bản sửa đổi",
              variant: "quiet",
            }),
            linkButton({
              href: `#/new?quote=${encodeURIComponent(String(detail.quote_id))}`,
              label: "Tiếp tục",
              variant: "primary",
            }),
          );
    render(
      host,
      readOnly
        ? h(
            "div",
            { class: "fact-line" },
            h(
              "p",
              { class: "hint" },
              "Chỉ để đọc: đúng những dòng và khoảng giá mà phiếu duyệt niêm phong.",
            ),
            infoButton(
              "Vì sao bảng này chỉ để đọc?",
              h(
                "p",
                { class: "hint" },
                "Bảng này chỉ để đọc. Nó cho thấy đúng những dòng và những khoảng giá mà một phiếu " +
                  "duyệt niêm phong, để không ai phải duyệt một mã băm mà chưa nhìn thấy nội dung.",
              ),
            ),
          )
        : null,
      receipt({
        revision: detail,
        detail,
        catalog,
        mode: detail.fulfillment_mode,
        title: `Bản sửa đổi ${detail.revision}`,
      }),
      actions,
    );
  }

  void load();
  return h("section", { class: "screen" }, titleHost, host);
}

/**
 * @param {...unknown} children
 * @returns {HTMLElement}
 */
function leadBar(...children) {
  const node = actionBar(...children);
  node.classList.add("action-bar--lead");
  return node;
}

export const screen = {
  path: "/quotes",
  title: "Báo giá",
  capability: "QUOTES_READ",
  needsStore: true,
  render: render_,
};
