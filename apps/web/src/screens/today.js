/**
 * Hôm nay: the owner's morning. What needs a person right now — and nothing else.
 *
 * This is the landing screen, which makes it the most dangerous place in the console to waste the
 * reader's attention. The redesign principle, from the owner's own review: the screen answers one
 * question — "có việc gì cần tôi không?" — in a glance, and everything that is not that answer
 * stays out of the way. Four decisions shape it:
 *
 *   - **An empty queue takes no space.** A tile whose count is zero is hidden, and one calm line
 *     says what is clear. A wall of zero-cards with footnotes is how the old screen read; an owner
 *     with nothing pending should see *that*, not five descriptions of nothing.
 *   - **A tile the role cannot call is not called, and not hidden.** `core/rbac` predicts what the
 *     server would answer; a predicted refusal turns into a visible "không đủ quyền" note with the
 *     server's own rule as the reason. Hiding it would teach staff the queue does not exist — so a
 *     refusal is the one non-count that always keeps its tile.
 *   - **One refusal does not blank the board.** The tiles load through `Promise.allSettled`, and a
 *     failure is rendered inside its own tile. An approver who is refused the incident list still
 *     sees the approval queue.
 *   - **Scope is stated only when it can surprise.** Two endpoints are not scoped to the selected
 *     store. An operator with exactly one store cannot misread that scope, so the note appears only
 *     when the session actually carries more than one.
 *
 * A shared toolbar tops the tile panel. Its reload button re-runs every permitted read, and the
 * stamp beside it records when the last successful fetch landed. There is no filter: these are
 * per-queue counts, not a searchable list, and narrowing them would compute nothing.
 *
 * Nothing on this screen writes, so there is no `Submission` and no result line.
 *
 * @module screens/today
 */

import { isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, count, money } from "../core/format.js";
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
 * @property {string} [hint] scope note, rendered only for a multi-store session
 * @property {string} zero what an empty list means
 * @property {boolean} [alwaysVisible] the day's volume is shown even at zero (the orders tile)
 * @property {(store: string) => string} url
 */

/** @type {Tile[]} */
const TILES = [
  {
    id: "approvals",
    eyebrow: "Cần bạn duyệt",
    title: "Hàng chờ duyệt",
    capability: "APPROVALS_READ",
    needsStore: false,
    limit: 100,
    href: "/approvals",
    action: "Mở hàng chờ duyệt",
    hint: "Mọi cửa hàng bạn được gán.",
    zero: "Không có việc nào đang chờ duyệt.",
    url: () => "/internal/v1/approvals?limit=100",
  },
  {
    id: "drafts",
    eyebrow: "AI soạn sẵn",
    title: "Tin AI soạn chờ duyệt",
    capability: "SHADOW_READ",
    needsStore: true,
    limit: 50,
    href: "/shadow",
    action: "Mở bản nháp AI",
    zero: "Không có tin AI soạn nào đang chờ.",
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/shadow/drafts?limit=50`,
  },
  {
    id: "unknown-sends",
    eyebrow: "Cần đối soát",
    title: "Gửi chưa rõ kết quả",
    capability: "SHADOW_READ",
    needsStore: false,
    limit: 50,
    href: "/exceptions",
    action: "Mở ngoại lệ",
    hint: "Toàn hệ thống.",
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
    zero: "Chưa có đơn nào trong cửa hàng này.",
    alwaysVisible: true,
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/orders?limit=100`,
  },
];

/**
 * "Tiền hôm nay" — the fourth question of the owner's morning, and the only money on this screen.
 *
 * The reason a figure may be shown here at all is what it is a sum *of*. `order_settlements` is an
 * append-only ledger with one row per order, and the database itself constrains
 * `paid_amount_vnd = expected_total_vnd`, so every row is a customer who paid the quoted total in
 * full at the counter. Postgres does the addition over a BIGINT column; nothing in this console
 * adds, rounds or converts, and no model is involved at any point.
 *
 * What it is not is stated on the card, not buried: this is money taken today, not doanh thu.
 * Orders still in the wash, goods delivered but unpaid, and every question about refunds, deposits
 * and B2B accounts are outside it — those are DEC-010 and unanswered. An owner who reads this as
 * revenue would be wrong in a direction that matters, so the card names the boundary itself and
 * the assistant still refuses revenue questions.
 *
 * @returns {{card: HTMLElement, amountNode: HTMLElement, body: HTMLElement}}
 */
function takingsCard() {
  const amountNode = h("p", { class: "takings__amount" }, "…");
  const body = h("div", { class: "stack stack--tight" }, skeleton(1));

  const card = h(
    "article",
    { class: "card takings" },
    h("p", { class: "eyebrow" }, "Tiền hôm nay"),
    h("h2", null, "Đã thu tại quầy"),
    amountNode,
    body,
    explain(
      "Con số này gồm những gì, và không gồm những gì?",
      h(
        "p",
        null,
        "Gồm: các đơn đã tất toán tại quầy hôm nay — khách trả đủ số tiền trên báo giá và nhận " +
          "đồ về. Máy chủ cộng trực tiếp từ sổ ghi tất toán; màn hình này không tự cộng.",
      ),
      h(
        "p",
        null,
        "Không gồm: đơn đang giặt, đơn đã giao nhưng chưa thu tiền, và mọi khoản trả trước, trả " +
          "một phần hay công nợ doanh nghiệp — hệ thống chưa hỗ trợ những hình thức đó. " +
          "Vì vậy đây không phải doanh thu.",
      ),
      h("p", null, h("a", { href: "#/gaps" }, "Xem danh sách năng lực chưa hỗ trợ")),
    ),
  );

  return { card, amountNode, body };
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
 * @returns {{card: HTMLElement, countNode: HTMLElement, body: HTMLElement, link: HTMLElement, hintNode: HTMLElement|null}}
 */
function tileCard(tile) {
  const countNode = h("span", { class: "count" }, "…");
  const body = h("div", { class: "stack stack--tight" }, skeleton(1));
  const link = h("a", { class: "button", href: `#${tile.href}` }, tile.action);
  const hintNode = tile.hint ? h("p", { class: "hint", hidden: true }, tile.hint) : null;

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
    hintNode,
    h("div", { class: "form__actions" }, link),
  );

  return { card, countNode, body, link, hintNode };
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const cards = TILES.map((tile) => ({ tile, ...tileCard(tile) }));
  const clearLine = h("p", { class: "hint", hidden: true });
  const takings = takingsCard();

  /**
   * Load today's counter takings.
   *
   * Separate from `loadTile` because this is not a queue: it has no limit, no screen that owns it,
   * and zero is a real answer rather than an empty list to hide. It fails on its own too — a store
   * whose takings cannot be read still shows its queues.
   *
   * @param {string} store
   * @returns {Promise<void>}
   */
  async function loadTakings(store) {
    render(takings.body, skeleton(1));
    try {
      const result = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/settlements/today`,
      );
      takings.amountNode.textContent = money(result.collected_vnd);
      markUpdated(bar.stamp);
      render(
        takings.body,
        h(
          "p",
          { class: "hint" },
          result.settlement_count === 0
            ? "Chưa có đơn nào tất toán hôm nay."
            : `${result.settlement_count} đơn đã tất toán hôm nay (giờ Việt Nam).`,
        ),
      );
    } catch (error) {
      // The amount is blanked, never left showing the previous store's figure under an error
      // notice — a stale number beside a red box reads as "still roughly right".
      takings.amountNode.textContent = UNKNOWN;
      render(takings.body, errorNotice(error, { onRetry: () => void loadTakings(store) }));
    }
  }

  /**
   * Load one tile.
   *
   * A failure is written into the tile, never thrown upward, so the page-level layout survives a
   * 403 on one queue. `errorNotice` decides for itself whether a retry button is honest — a read
   * that timed out may be retried, a read that was refused may not.
   *
   * @param {{tile: Tile, countNode: HTMLElement, body: HTMLElement, card: HTMLElement}} entry
   * @param {string} store
   * @returns {Promise<"zero" | "pending" | "blocked">}
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
        return "blocked";
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
              `Máy chủ trả tối đa ${entry.tile.limit} bản ghi; có thể còn nữa.`,
            )
          : null,
      );
      if (items.length === 0 && !entry.tile.alwaysVisible) {
        // An empty queue takes no space; the clear line below names what was checked.
        entry.card.classList.add("tile--clear");
        return "zero";
      }
      return "pending";
    } catch (error) {
      entry.countNode.textContent = UNKNOWN;
      render(entry.body, errorNotice(error, { onRetry: () => void loadTile(entry, store) }));
      return "blocked";
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
    // Scope notes exist for the one reader they can matter to: somebody with more than one store.
    const multiStore = state.memberStoreIds.length > 1;
    for (const entry of cards) {
      if (entry.hintNode) entry.hintNode.hidden = !multiStore;
    }
    // The takings card follows the same rule as a tile: a predicted refusal is shown, not hidden,
    // and no call is made. It needs a store, because money is never summed across stores.
    const takingsVerdict = can(state.principal, "SETTLEMENTS_READ");
    if (!takingsVerdict.allowed) {
      takings.amountNode.textContent = UNKNOWN;
      render(
        takings.body,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Không đủ quyền — không gọi máy chủ"),
          h("p", null, takingsVerdict.reason),
        ),
      );
    } else if (!store) {
      takings.amountNode.textContent = UNKNOWN;
      render(takings.body, h("p", { class: "hint" }, "Chọn một cửa hàng để xem số tiền đã thu."));
    } else {
      void loadTakings(store);
    }

    /** @type {Array<{tile: Tile, countNode: HTMLElement, body: HTMLElement, card: HTMLElement}>} */
    const runnable = [];

    for (const entry of cards) {
      entry.card.classList.remove("tile--clear");
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
    const outcomes = await Promise.allSettled(runnable.map((entry) => loadTile(entry, store || "")));

    // The one calm line. A queue that came back empty is named here, once, in passing — it is
    // information, not five cards worth of furniture. It says "the queues that were checked are
    // empty", never "there is nothing waiting for you": a tile the role could not read was never
    // checked, and the approvals list itself drops every envelope whose resource is not an order
    // (`#/approvals` states that caveat). An all-clear this screen cannot prove is not offered.
    const cleared = runnable
      .filter((_, index) => outcomes[index].status === "fulfilled" && outcomes[index].value === "zero")
      .map((entry) => entry.tile.title.toLowerCase());
    clearLine.hidden = cleared.length === 0;
    if (cleared.length) {
      const queueTiles = runnable.filter((entry) => !entry.tile.alwaysVisible);
      clearLine.textContent =
        queueTiles.length > 0 && cleared.length === queueTiles.length
          ? "Các hàng đợi đã kiểm đều đang trống."
          : `Đang trống: ${cleared.join(" · ")}.`;
    }
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
      h("p", { class: "eyebrow" }, "Ca làm việc hôm nay"),
      h("h1", null, "Hôm nay"),
      h(
        "p",
        { class: "screen__lede" },
        "Việc đang chờ bạn quyết định và tình hình cửa hàng — chỉ những gì cần một người xử lý.",
      ),
    ),
    takings.card,
    panel({
      eyebrow: "Hàng đợi",
      title: "Đang chờ bạn xử lý",
      children: [
        bar.node,
        h("div", { class: "panels" }, cards.map((entry) => entry.card)),
        clearLine,
      ],
    }),
  );
}

export const screen = {
  path: "/",
  title: "Hôm nay",
  render: render_,
};
