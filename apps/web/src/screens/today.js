/**
 * Hôm nay: what needs a person right now, and nothing that pretends to be more than that.
 *
 * This is the landing screen, which makes it the most dangerous place in the console to overstate
 * something — whatever is on it is what a shift believes about the day. Four decisions therefore
 * shape it, and three of them are refusals:
 *
 *   - **These are row counts, not indicators.** `FR-RPT-005` requires a KPI to carry a numerator, a
 *     denominator, a time window and a data-quality status. Every number here is the length of one
 *     page of one list, bounded by that list's own limit, and the API publishes no denominator for
 *     any of them. So the screen names them for what they are — bản ghi đang chờ — and links to the
 *     gap register rather than dressing a `length` up as a measurement.
 *   - **A tile the role cannot call is not called, and not hidden.** `core/rbac` predicts what the
 *     server would answer; a predicted refusal turns into a visible "không đủ quyền" note with the
 *     server's own rule as the reason. Hiding it would teach staff the queue does not exist.
 *   - **One refusal does not blank the board.** The tiles load through `Promise.allSettled`, and a
 *     failure is rendered inside its own tile. An approver who is refused the incident list still
 *     sees the approval queue.
 *   - **Scope is stated per tile, because it differs per tile.** Two of these endpoints are not
 *     scoped to the selected store at all, and a count whose scope the reader guesses wrong is
 *     worse than no count.
 *
 * A shared toolbar tops the tile panel. Its reload button re-runs every permitted read, and the
 * stamp beside it records when the last successful fetch landed, so "these counts look stale" is
 * a checkable fact rather than a feeling. There is no filter: these are per-queue counts, not a
 * searchable list, and narrowing them would compute nothing.
 *
 * Nothing on this screen writes, so there is no `Submission` and no result line.
 *
 * @module screens/today
 */

import { isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, count } from "../core/format.js";
import { can } from "../core/rbac.js";
import { snapshot } from "../core/session.js";
import { errorNotice, explain, markUpdated, panel, skeleton, toolbar } from "../ui/components.js";

/**
 * One tile.
 *
 * `limit` is per-endpoint and is not a house style: the server defaults differ (100 for approvals,
 * orders and incidents; 50 for the two Shadow lists) and passing the wrong one to `count()` would
 * turn a full page into a plain number and hide the truncation.
 *
 * @typedef {object} Tile
 * @property {string} id
 * @property {string} eyebrow
 * @property {string} title
 * @property {keyof import("../core/rbac.js").CAPABILITIES} capability
 * @property {boolean} needsStore
 * @property {number} limit
 * @property {string} href hash path of the screen that owns this queue
 * @property {string} action label of the link into that screen
 * @property {string} hint the count's scope in a few words, always visible
 * @property {string} scope who and what the count covers, in one sentence — inside an explain()
 * @property {string} zero what an empty list means
 * @property {(store: string) => string} url
 */

/** @type {Tile[]} */
const TILES = [
  {
    id: "approvals",
    eyebrow: "Cần người duyệt",
    title: "Hàng chờ duyệt",
    capability: "APPROVALS_READ",
    needsStore: false,
    limit: 100,
    href: "/approvals",
    action: "Mở hàng chờ duyệt",
    hint: "Mọi cửa hàng bạn được gán, không chỉ cửa hàng đang chọn.",
    scope:
      "Đếm phong bì duyệt đang ở trạng thái REQUESTED trên mọi cửa hàng bạn được gán, không chỉ " +
      "cửa hàng đang chọn. Phong bì duyệt có hạn ngắn và không tự gia hạn.",
    zero: "Không có phong bì duyệt nào đang chờ.",
    url: () => "/internal/v1/approvals?limit=100",
  },
  {
    id: "drafts",
    eyebrow: "AI soạn · người quyết",
    title: "Bản nháp AI chờ duyệt",
    capability: "SHADOW_READ",
    needsStore: true,
    limit: 50,
    href: "/shadow",
    action: "Mở bản nháp AI",
    hint: "Cửa hàng đang chọn; nháp chưa có quyết định.",
    scope: "Đếm bản nháp chưa có quyết định của cửa hàng đang chọn. Không có bản nháp nào tự gửi đi.",
    zero: "Không có bản nháp nào đang chờ quyết định.",
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/shadow/drafts?limit=50`,
  },
  {
    id: "unknown-sends",
    eyebrow: "Chưa đối soát",
    title: "Gửi chưa rõ kết quả",
    capability: "SHADOW_READ",
    needsStore: false,
    limit: 50,
    href: "/exceptions",
    action: "Mở ngoại lệ",
    hint: "Toàn hệ thống, không theo cửa hàng.",
    scope:
      "Đếm biên nhận gửi ở trạng thái UNKNOWN hoặc UNKNOWN_REQUIRES_HUMAN trên toàn hệ thống, " +
      "không theo cửa hàng đang chọn. Chưa rõ nghĩa là không được gửi lại cho tới khi có người đối soát.",
    zero: "Không có lần gửi nào chưa rõ kết quả.",
    url: () => "/internal/v1/shadow/unknown-sends?limit=50",
  },
  {
    id: "incidents",
    eyebrow: "Đang mở",
    title: "Sự cố đang mở",
    capability: "INCIDENTS_READ",
    needsStore: true,
    limit: 100,
    href: "/incidents",
    action: "Mở sự cố",
    hint: "Cửa hàng đang chọn, theo đúng trang máy chủ trả.",
    scope:
      "Đếm sự cố của cửa hàng đang chọn theo đúng những gì máy chủ trả về cho danh sách này. " +
      "Lỗi thuộc về ai và bồi thường thế nào là hai quyết định riêng, không suy ra từ con số này.",
    zero: "Không có sự cố nào trong danh sách.",
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/incidents?limit=100`,
  },
  {
    id: "orders",
    eyebrow: "Bảng đơn",
    title: "Đơn hàng",
    capability: "ORDERS_READ",
    needsStore: true,
    limit: 100,
    href: "/orders",
    action: "Mở bảng đơn",
    hint: "Cửa hàng đang chọn; không lọc theo trạng thái.",
    scope:
      "Đếm đơn của cửa hàng đang chọn mà máy chủ trả về trong một trang. Đây là số dòng, không " +
      "phải số đơn đang cần xử lý — danh sách này không lọc theo trạng thái.",
    zero: "Chưa có đơn nào trong cửa hàng này.",
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/orders?limit=100`,
  },
];

/**
 * The standing note that keeps every number on this screen honest. It is education, not a
 * point-of-action warning, so WS2 collapses it into an explain() — collapsed by default,
 * flattened by print.css, never deleted.
 *
 * @returns {HTMLElement}
 */
function notKpiNotice() {
  return explain(
    "Vì sao đây là số bản ghi đang chờ, không phải chỉ số KPI?",
    h(
      "p",
      null,
      "Mỗi con số bên dưới là số dòng máy chủ trả về trong một trang của một danh sách. Không có " +
        "mẫu số, không có khung thời gian và không có đánh giá chất lượng dữ liệu đi kèm, nên " +
        "không được dùng để báo cáo hiệu suất hay so sánh giữa các ngày.",
    ),
    h(
      "p",
      null,
      "Bảng điều hành vận hành thật — có tử số, mẫu số, khung thời gian và tình trạng dữ liệu " +
        "theo FR-RPT-005 — chưa tồn tại trong hệ thống này. ",
      h("a", { href: "#/gaps" }, "Xem danh sách năng lực chưa hỗ trợ"),
      ".",
    ),
  );
}

/**
 * Build one tile's DOM and hand back the handles the loader writes into.
 *
 * The link is never removed and never turned into plain text. A role that cannot read the queue can
 * still reach the screen that owns it, where the guard states the same refusal in full — so the
 * anchor carries `aria-disabled` the way the shell's navigation does, rather than a `disabled`
 * attribute, which does nothing on an anchor.
 *
 * @param {Tile} tile
 * @returns {{card: HTMLElement, countNode: HTMLElement, body: HTMLElement, link: HTMLElement}}
 */
function tileCard(tile) {
  const countNode = h("span", { class: "count" }, "…");
  const body = h("div", { class: "stack stack--tight" }, skeleton(1));
  const link = h("a", { class: "button", href: `#${tile.href}` }, tile.action);

  const card = h(
    "article",
    { class: "card stack", dataTile: tile.id },
    h(
      "div",
      { class: "spread" },
      h("div", null, h("p", { class: "eyebrow" }, tile.eyebrow), h("h3", null, tile.title)),
      countNode,
    ),
    body,
    h("p", { class: "hint" }, tile.hint),
    explain("Con số này đếm gì, ở phạm vi nào?", h("p", null, tile.scope)),
    h("div", { class: "form__actions" }, link),
  );

  return { card, countNode, body, link };
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const cards = TILES.map((tile) => ({ tile, ...tileCard(tile) }));

  /**
   * Load one tile.
   *
   * A failure is written into the tile, never thrown upward, so the page-level layout survives a
   * 403 on one queue. `errorNotice` decides for itself whether a retry button is honest — a read
   * that timed out may be retried, a read that was refused may not.
   *
   * @param {{tile: Tile, countNode: HTMLElement, body: HTMLElement}} entry
   * @param {string} store
   * @returns {Promise<void>}
   */
  async function loadTile(entry, store) {
    render(entry.body, skeleton(1));
    try {
      const payload = await request(entry.tile.url(store));
      // Every one of these routes is declared `list[...]`. A response that is not an array means
      // the contract moved, and `undefined.length` counted as zero would report an empty queue to
      // a shift that in fact has work waiting — so it is reported as unknown instead.
      const items = Array.isArray(payload) ? payload : null;
      if (!items) {
        entry.countNode.textContent = UNKNOWN;
        render(
          entry.body,
          h(
            "div",
            { class: "notice", dataState: "danger" },
            h("p", { class: "notice__title" }, "Máy chủ trả về hình dạng lạ cho danh sách này"),
            h(
              "p",
              null,
              "Không đếm được, và hiển thị số 0 ở đây sẽ là sai sự thật. Báo cho kỹ thuật.",
            ),
          ),
        );
        return;
      }
      entry.countNode.textContent = count(items, entry.tile.limit);
      // Each tile fetches on its own clock; the stamp records the most recent one that landed.
      markUpdated(bar.stamp);
      render(
        entry.body,
        items.length === 0 ? h("p", { class: "hint" }, entry.tile.zero) : null,
        isTruncated(items, entry.tile.limit)
          ? h(
              "p",
              { class: "hint" },
              `Máy chủ trả tối đa ${entry.tile.limit} bản ghi và đã trả đủ; có thể còn nữa. ` +
                "API này không có phân trang, nên con số hiển thị là mức sàn.",
            )
          : null,
      );
    } catch (error) {
      entry.countNode.textContent = UNKNOWN;
      render(entry.body, errorNotice(error, { onRetry: () => void loadTile(entry, store) }));
    }
  }

  /**
   * Decide, per tile, whether to ask the server at all — then ask every permitted tile at once.
   *
   * The session is read here rather than at build time because this is the one route the shell's
   * guard lets through while the session is still being fetched. Gating on a principal that has not
   * arrived yet would flash "không đủ quyền" on every tile of every cold load; the shell re-renders
   * the screen when the session settles.
   */
  async function loadAll() {
    const state = snapshot();
    if (state.status === "unknown") return; // still asking who we are; skeletons stay up

    const store = state.storeId;
    /** @type {Array<{tile: Tile, countNode: HTMLElement, body: HTMLElement}>} */
    const runnable = [];

    for (const entry of cards) {
      const verdict = can(state.principal, entry.tile.capability);
      if (!verdict.allowed) {
        entry.countNode.textContent = UNKNOWN;
        entry.link.setAttribute("aria-disabled", "true");
        render(
          entry.body,
          h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Không đủ quyền — không gọi máy chủ"),
            h("p", null, verdict.reason),
          ),
        );
        continue;
      }

      if (entry.tile.needsStore && !store) {
        entry.countNode.textContent = UNKNOWN;
        render(
          entry.body,
          h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Chưa chọn cửa hàng"),
            h(
              "p",
              null,
              state.memberStoreIds.length
                ? "Chọn cửa hàng ở thanh phía trên rồi mở lại màn hình này."
                : "Tài khoản này chưa được gán cửa hàng nào, nên danh sách theo cửa hàng chưa gọi được.",
            ),
          ),
        );
        continue;
      }

      runnable.push(entry);
    }

    // Concurrent on purpose: five sequential reads would make the slowest one the shift's first
    // impression of the console. `allSettled` because a rejection here must not stop the others —
    // `loadTile` already renders its own failure, so nothing is lost by ignoring the results.
    await Promise.allSettled(runnable.map((entry) => loadTile(entry, store || "")));
  }

  /**
   * Reload re-runs the whole gate-and-fetch pass, not just the tiles that succeeded: a tile that
   * was refused or missing a store is re-checked against the session, so the button stays honest
   * after a store switch. Per-tile error notices keep their own retry for the single-queue case.
   */
  const bar = toolbar({ onReload: loadAll });

  void loadAll();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Số bản ghi đang chờ"),
      h("h1", null, "Hôm nay"),
      h(
        "p",
        { class: "screen__lede" },
        "Những hàng đợi đang cần một người quyết định. Màn hình này chỉ đếm và dẫn đường; mọi " +
          "quyết định nằm ở màn hình sở hữu hàng đợi đó.",
      ),
    ),
    notKpiNotice(),
    panel({
      eyebrow: "Hàng đợi",
      title: "Đang chờ người xử lý",
      guardrail:
        "Mỗi ô chỉ hỏi máy chủ khi vai trò hiện tại được phép. Ô bị từ chối hoặc thiếu cửa hàng " +
        "nêu rõ lý do và không gọi API, chứ không hiện số 0.",
      children: [bar.node, h("div", { class: "panels" }, cards.map((entry) => entry.card))],
    }),
  );
}

export const screen = {
  path: "/",
  title: "Hôm nay",
  render: render_,
};
