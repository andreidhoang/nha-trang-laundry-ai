/**
 * Hôm nay: the owner's morning and the counter's first screen. What needs a person right now, what
 * the drawer took, and the two things a counter does most — and nothing else.
 *
 * Spec V2 §5.1. The screen answers one question — "có việc gì cần tôi không?" — in a glance:
 *
 *   - **Tiền hôm nay** is one large number (`moneyHero`). What it includes and excludes is one tap
 *     away (ⓘ), verbatim from the V1 card; the query version that produced it is in the technical
 *     drawer (tier 3), so a figure photographed off this screen still names its rule.
 *   - **Cần làm** is one row per queue that has something in it, with the count at the right and
 *     the queue's own screen behind the tap. A queue that came back empty takes no row: the empty
 *     ones are named together in one calm line. A queue the role cannot read keeps its row,
 *     disabled, with the server's rule as the reason — hiding it would teach staff the queue does
 *     not exist (V1 invariant 5). A queue that failed to load keeps its row too, with the failure.
 *   - **One refusal does not blank the board.** Every read runs through `Promise.allSettled` and
 *     writes only its own row, so an approver refused the incident list still sees the approvals.
 *   - **The all-clear claims only what was checked.** "Không có gì chờ bạn" is said only when every
 *     queue on this screen was actually read and came back empty, and the line names the queues it
 *     checked. A queue the role could not read, or that failed, was never checked — then the line
 *     says only which queues are empty.
 *   - **Scope is stated only when it can surprise.** The approvals queue is not scoped to the
 *     selected store; the note says so only to a session that holds more than one store.
 *   - **Đơn hôm nay** is the server's own count by status (`today_status_counts`), one chip per
 *     status linking to the order list filtered to it. The total comes down already summed.
 *
 * Nothing on this screen writes, so there is no `Submission`.
 *
 * @module screens/today
 */

import { isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { TIMEZONE, UNKNOWN, count, money } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { snapshot } from "../core/session.js";
import { errorNotice, gated, icon, markUpdated } from "../ui/components.js";
import {
  button,
  infoButton,
  inlineAlert,
  linkButton,
  list,
  listRow,
  moneyHero,
  page,
  section,
  skeletonRows,
  techDetails,
} from "../ui/kit.js";

/**
 * One queue on the "Cần làm" list.
 *
 * `limit` is per-endpoint and is not a house style: the server defaults differ (100 for approvals
 * and incidents; 50 for the two Shadow lists; the SLA board allows 200) and passing the wrong one
 * to `count()` would turn a full page into a plain number and hide the truncation.
 *
 * @typedef {object} Tile
 * @property {string} id
 * @property {string} title
 * @property {string} short how the calm line names it when it is empty
 * @property {string} icon
 * @property {keyof import("../core/rbac.js").CAPABILITIES} capability
 * @property {boolean} needsStore
 * @property {number} limit
 * @property {string} href hash path of the screen that owns this queue
 * @property {string} [hint] scope note, rendered only for a multi-store session
 * @property {(store: string) => string} url
 * @property {(payload: any) => {items: any[]|null, more: boolean}} [pick] how to count the answer
 */

/** @type {Tile[]} */
const TILES = [
  {
    id: "approvals",
    title: "Chờ duyệt",
    short: "chờ duyệt",
    icon: "approval",
    capability: "APPROVALS_READ",
    needsStore: false,
    limit: 100,
    href: "/approvals",
    hint: "Mọi cửa hàng bạn được gán.",
    url: () => "/internal/v1/approvals?limit=100",
  },
  {
    id: "drafts",
    title: "Tin AI chờ duyệt",
    short: "tin AI",
    icon: "draft",
    capability: "SHADOW_READ",
    needsStore: true,
    limit: 50,
    href: "/shadow",
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/shadow/drafts?limit=50`,
  },
  {
    id: "unknown-sends",
    title: "Gửi chưa rõ kết quả",
    short: "gửi chưa rõ",
    icon: "message",
    capability: "UNKNOWN_SENDS_READ",
    needsStore: true,
    limit: 50,
    href: "/exceptions",
    url: (store) =>
      `/internal/v1/stores/${encodeURIComponent(store)}/shadow/unknown-sends?limit=50`,
  },
  {
    id: "incidents",
    title: "Sự cố đang mở",
    short: "sự cố",
    icon: "incident",
    capability: "INCIDENTS_READ",
    needsStore: true,
    limit: 100,
    href: "/incidents",
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/incidents?limit=100`,
  },
  {
    // The SLA board's own read, one page of its ceiling, counted by the outcome the server gave
    // each row. Nothing here decides whether an order is late: `sla_outcome` is the domain
    // engine's, and a page that has a next page is shown as a floor ("3+"), never as a total.
    id: "sla",
    title: "Đơn quá mốc nội bộ",
    short: "đơn quá mốc",
    icon: "clock",
    capability: "SLA_BOARD_READ",
    needsStore: true,
    limit: 200,
    href: "/sla-board",
    url: (store) => `/internal/v1/stores/${encodeURIComponent(store)}/sla-board?limit=200`,
    pick: (payload) => {
      const items = Array.isArray(payload?.items) ? payload.items : null;
      return {
        items: items ? items.filter((item) => item.sla_outcome === "BREACHED") : null,
        more: Boolean(payload?.next_order_id),
      };
    },
  },
];

/**
 * The drawer's movement for the day, in words. `net_vnd` and `net_direction` arrive already
 * computed by the server; this only picks the word, so no minus sign and no arithmetic reach the
 * screen.
 *
 * @param {number} amount non-negative VND
 * @param {"IN" | "OUT"} direction
 * @returns {string}
 */
function drawerLine(amount, direction) {
  if (amount === 0) return "Tiền trong két hôm nay: không đổi.";
  return `Tiền trong két hôm nay: ${direction === "OUT" ? "giảm" : "tăng"} ${money(amount)}.`;
}

/**
 * "Con số này gồm những gì…" — the V1 takings card's explanation, verbatim, now behind the ⓘ beside
 * the figure (tier 2).
 *
 * The reason a figure may be shown here at all is what it is a sum *of*. `order_settlements` is an
 * append-only ledger with one row per order, and the database itself constrains
 * `paid_amount_vnd = expected_total_vnd`, so every row is a customer who paid the quoted total in
 * full at the counter. Postgres does the addition over a BIGINT column; nothing in this console
 * adds, rounds or converts, and no model is involved at any point. What it is not — doanh thu — is
 * the last sentence of the explanation and the reason the hero is labelled "Đã thu tại quầy".
 *
 * @returns {HTMLElement}
 */
function takingsInfo() {
  return infoButton(
    "Con số này gồm những gì, và không gồm những gì?",
    h(
      "p",
      null,
      "Gồm: các đơn đã tất toán tại quầy hôm nay — khách trả đủ số tiền trên báo giá, một lần, " +
        // DEC-032 (2026-09-25) added the third case: paid at drop-off, collected later. Still
        // the exact total in one payment, counted on the day it was taken.
        "tại quầy. Có ba trường hợp và cả ba đều đã trả đủ: khách trả lúc lấy đồ, khách trả " +
        "trước khi gửi đồ rồi lấy sau, hoặc trả trước rồi tiệm giao tận nơi. Nên tiền đã thu " +
        "không có nghĩa là đồ đã ra khỏi tiệm. " +
        "Máy chủ cộng trực tiếp từ sổ ghi tất toán; màn hình này không tự cộng.",
    ),
    h(
      "p",
      null,
      // DEC-024: refunds are shown beside the takings rather than taken out of them, on the day
      // the money went back. Said here so a drawer that "giảm" reads as the truth, not a fault.
      "Hoàn tiền ghi riêng: khi đơn đã trả tiền bị huỷ (trả đồ chưa giặt, hoặc lỗi của tiệm " +
        "nên không thu tiền), số tiền hoàn lại hiện bên dưới, tính vào ngày hoàn, kèm tiền " +
        "trong két hôm nay tăng hay giảm bao nhiêu. Máy chủ tính cả hai con số.",
    ),
    h(
      "p",
      null,
      // Both supported settlements are paid IN FULL at the counter, and what differs is only
      // where the goods go afterwards. What is refused is a part payment -- a deposit,
      // instalments, or shop credit -- in the same words the settlement panel itself uses.
      "Không gồm: đơn đang giặt, đơn đã giao nhưng chưa thu tiền, và mọi hình thức đặt cọc, " +
        "trả thiếu, trả thừa, trả góp hay ghi nợ doanh nghiệp — hệ thống chưa hỗ trợ những " +
        "hình thức đó. Vì vậy đây không phải doanh thu.",
    ),
    h("p", null, h("a", { href: "#/gaps" }, "Xem danh sách năng lực chưa hỗ trợ")),
  );
}

/**
 * A capability the role does not hold, as a small visible alert. The request is never made.
 *
 * @param {{reason: string}} verdict
 * @returns {HTMLElement}
 */
function refusal(verdict) {
  return inlineAlert({
    state: "warn",
    title: "Không đủ quyền — không gọi máy chủ",
    body: verdict.reason,
  });
}

/**
 * "Chào buổi sáng" and today's date, both in the shop's time zone rather than the device's.
 *
 * @param {Date} [now]
 * @returns {{greeting: string, date: string}}
 */
function greeting(now = new Date()) {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", { timeZone: TIMEZONE, hour: "2-digit", hourCycle: "h23" })
      .format(now)
      .slice(0, 2),
  );
  const date = new Intl.DateTimeFormat("vi-VN", {
    timeZone: TIMEZONE,
    weekday: "long",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(now);
  const part =
    hour < 11 ? "buổi sáng" : hour < 13 ? "buổi trưa" : hour < 18 ? "buổi chiều" : "buổi tối";
  return { greeting: `Chào ${part}`, date: date.charAt(0).toUpperCase() + date.slice(1) };
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const hello = greeting();
  const stamp = h("span", { class: "updated", role: "status" });

  // --- Tiền hôm nay -------------------------------------------------------------------------
  const takingsHost = h("div", { class: "stack stack--tight" }, skeletonRows(1));

  /**
   * Load today's counter takings.
   *
   * Not a queue: it has no limit, no screen that owns it, and zero is a real answer rather than
   * an empty list to hide. It fails on its own too — a store whose takings cannot be read still
   * shows its queues.
   *
   * @param {string} store
   * @returns {Promise<void>}
   */
  async function loadTakings(store) {
    render(takingsHost, skeletonRows(1));
    try {
      const result = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/settlements/today`,
      );
      markUpdated(stamp);
      render(
        takingsHost,
        moneyHero({
          label: "Đã thu tại quầy",
          amount: money(result.collected_vnd),
          state: "ok",
          caption:
            result.settlement_count === 0
              ? "Chưa có đơn nào tất toán hôm nay."
              : `${result.settlement_count} đơn đã tất toán hôm nay (giờ Việt Nam).`,
        }),
        // `collected-today-v2` (DEC-024): the headline stays what was collected. When money also
        // went back, the refund and the drawer's movement are shown in words, both exactly as the
        // server computed them -- every amount non-negative, the direction a word, and no
        // arithmetic on this screen.
        result.refunded_vnd > 0
          ? h(
              "div",
              { class: "today__refunds" },
              h("p", null, `Đã hoàn lại ${money(result.refunded_vnd)}.`),
              h("p", null, drawerLine(result.net_vnd, result.net_direction)),
            )
          : null,
        // The rule that produced the figure travels with the figure (invariant 18), in tier 3: a
        // total printed or photographed off this screen can still say which rule made it.
        techDetails([
          ["Truy vấn", result.query_version],
          ["Múi giờ", result.business_timezone || TIMEZONE],
        ]),
      );
    } catch (error) {
      // The amount is blanked, never left showing the previous store's figure under an error
      // notice — a stale number beside a red box reads as "still roughly right".
      render(
        takingsHost,
        moneyHero({ label: "Đã thu tại quầy", amount: UNKNOWN }),
        errorNotice(error, { onRetry: () => void loadTakings(store) }),
      );
    }
  }

  // --- Đơn hôm nay ---------------------------------------------------------------------------
  const daySummaryHost = h("div", { class: "stack stack--tight" }, skeletonRows(1));
  const daySummaryNote = h("div", { class: "stack stack--tight" });

  /**
   * Load the day's order counts: `today_status_counts`, one chip per status the server recorded.
   *
   * Two rules it keeps. The counts and their total are the server's: `total_orders` comes down
   * already summed, because a console that added the rows would be a second opinion about the
   * day's volume. And there is no money here — the takings figure keeps its own route and its own
   * `DEC-014` gate above.
   *
   * @param {string} store
   * @returns {Promise<void>}
   */
  async function loadDaySummary(store) {
    render(daySummaryHost, skeletonRows(1));
    try {
      const summary = await request(`/internal/v1/stores/${encodeURIComponent(store)}/day-summary`);
      markUpdated(stamp);
      const counts = Array.isArray(summary.counts) ? summary.counts : [];
      // What the figures are (and are not) is one tap away, in the V1 card's own words.
      render(
        daySummaryNote,
        h(
          "p",
          { class: "hint" },
          `Tổng ${summary.total_orders} đơn tạo trong ngày hôm nay (giờ Việt Nam). Đây là số đếm ` +
            "theo trạng thái máy chủ ghi nhận, không phải chỉ số hiệu suất.",
        ),
        techDetails([["Truy vấn", summary.query_version]]),
      );
      render(
        daySummaryHost,
        counts.length === 0
          ? h("p", { class: "hint" }, "Hôm nay cửa hàng này chưa có đơn nào (theo giờ Việt Nam).")
          : h(
              "div",
              { class: "today__chips", role: "list", "aria-label": "Đơn hôm nay theo trạng thái" },
              counts.map(([statusName, total]) =>
                h(
                  "a",
                  {
                    class: "today__chip",
                    role: "listitem",
                    href: `#/orders?status=${encodeURIComponent(String(statusName))}`,
                    title: String(statusName),
                    dataStatus: String(statusName),
                  },
                  h("span", { class: "today__chip-count" }, String(total)),
                  h("span", { class: "today__chip-label" }, enumVi(statusName)),
                ),
              ),
            ),
        h("p", { class: "today__total" }, `Tổng ${summary.total_orders} đơn`),
      );
    } catch (error) {
      render(daySummaryNote);
      render(daySummaryHost, errorNotice(error, { onRetry: () => void loadDaySummary(store) }));
    }
  }

  // --- Cần làm ------------------------------------------------------------------------------
  const queueHost = h("div", { class: "stack stack--tight" }, skeletonRows(3));

  /**
   * What one queue came back as. `row` is what the list shows; `zero` marks an empty queue, which
   * takes no row and is named in the calm line instead.
   *
   * @typedef {{tile: Tile, zero: boolean, checked: boolean, row: HTMLElement|null, alert?: HTMLElement}} Outcome
   */

  /**
   * Read one queue and describe it as a row. A failure is described, never thrown upward, so the
   * list survives a 403 on one queue.
   *
   * @param {Tile} tile
   * @param {string} store
   * @param {boolean} multiStore
   * @returns {Promise<Outcome>}
   */
  async function loadTile(tile, store, multiStore) {
    try {
      const payload = await request(tile.url(store));
      const picked = tile.pick
        ? tile.pick(payload)
        : { items: Array.isArray(payload) ? payload : null, more: false };
      // Every one of these routes has a declared shape. A response that is not one means the
      // contract moved, and `undefined.length` counted as zero would report an empty queue to a
      // shift that in fact has work waiting — so it is reported as unknown instead.
      if (!picked.items) {
        return {
          tile,
          zero: false,
          checked: false,
          row: listRow({
            href: `#${tile.href}`,
            leading: tile.icon,
            title: tile.title,
            meta: "Không đếm được",
            trailing: UNKNOWN,
            data: { queue: tile.id },
          }),
          alert: h(
            "div",
            { class: "notice", dataState: "danger" },
            h("p", { class: "notice__title" }, "Máy chủ trả về hình dạng lạ cho danh sách này"),
            h(
              "p",
              null,
              "Không đếm được, và hiển thị số 0 ở đây sẽ là sai sự thật. Báo cho kỹ thuật.",
            ),
          ),
        };
      }
      markUpdated(stamp);
      const items = picked.items;
      if (items.length === 0 && !picked.more) return { tile, zero: true, checked: true, row: null };
      const shown = picked.more ? `${items.length}+` : count(items, tile.limit);
      const truncated = picked.more || isTruncated(items, tile.limit);
      return {
        tile,
        zero: false,
        checked: true,
        row: listRow({
          href: `#${tile.href}`,
          leading: tile.icon,
          title: tile.title,
          meta: [
            multiStore && tile.hint ? h("span", { class: "hint" }, tile.hint) : null,
            truncated
              ? h("span", null, `Máy chủ trả tối đa ${tile.limit} bản ghi; có thể còn nữa.`)
              : null,
          ].filter(Boolean),
          trailing: h("span", { class: "today__count" }, shown),
          data: { queue: tile.id },
        }),
      };
    } catch (error) {
      return {
        tile,
        zero: false,
        checked: false,
        row: listRow({
          href: `#${tile.href}`,
          leading: tile.icon,
          title: tile.title,
          meta: h("span", { class: "row-item__reason" }, `Không tải được: ${error.message}`),
          trailing: UNKNOWN,
          data: { queue: tile.id, failed: "true" },
        }),
      };
    }
  }

  /**
   * Decide, per queue, whether to ask the server at all — then ask every permitted one at once.
   *
   * The session is read here rather than at build time because this is the one route the shell's
   * guard lets through while the session is still being fetched. Gating on a principal that has not
   * arrived yet would flash "không đủ quyền" on every row of every cold load; the shell re-renders
   * the screen when the session settles.
   */
  async function loadAll() {
    const state = snapshot();
    if (state.status === "unknown") return; // still asking who we are; skeletons stay up

    const store = state.storeId;
    // Scope notes exist for the one reader they can matter to: somebody with more than one store.
    const multiStore = state.memberStoreIds.length > 1;
    const noStoreReason = state.memberStoreIds.length
      ? "Chọn cửa hàng ở thanh phía trên rồi mở lại màn hình này."
      : "Tài khoản này chưa được gán cửa hàng nào, nên danh sách theo cửa hàng chưa gọi được.";

    // The takings follow the same three-way gate as a queue: a predicted refusal is shown, not
    // hidden, and no call is made. It needs a store, because money is never summed across stores.
    const takingsVerdict = can(state.principal, "SETTLEMENTS_READ");
    if (!takingsVerdict.allowed) {
      render(
        takingsHost,
        moneyHero({ label: "Đã thu tại quầy", amount: UNKNOWN }),
        refusal(takingsVerdict),
      );
    } else if (!store) {
      render(
        takingsHost,
        moneyHero({ label: "Đã thu tại quầy", amount: UNKNOWN }),
        h("p", { class: "hint" }, "Chọn một cửa hàng để xem số tiền đã thu."),
      );
    } else {
      void loadTakings(store);
    }

    const summaryVerdict = can(state.principal, "DAY_SUMMARY_READ");
    if (!summaryVerdict.allowed) {
      render(daySummaryNote);
      render(daySummaryHost, refusal(summaryVerdict));
    } else if (!store) {
      render(daySummaryNote);
      render(
        daySummaryHost,
        h("p", { class: "hint" }, "Chọn một cửa hàng để xem số đơn trong ngày."),
      );
    } else {
      void loadDaySummary(store);
    }

    /** @type {Outcome[]} */
    const fixed = [];
    /** @type {Tile[]} */
    const runnable = [];
    let missingStore = false;

    for (const tile of TILES) {
      const verdict = can(state.principal, tile.capability);
      if (!verdict.allowed) {
        // Refused: the row stays, disabled, with the server's rule as the reason.
        fixed.push({
          tile,
          zero: false,
          checked: false,
          row: listRow({
            leading: tile.icon,
            title: tile.title,
            disabled: true,
            reason: verdict.reason,
            trailing: UNKNOWN,
            data: { queue: tile.id, refused: "true" },
          }),
        });
        continue;
      }

      if (tile.needsStore && !store) {
        missingStore = true;
        fixed.push({
          tile,
          zero: false,
          checked: false,
          row: listRow({
            leading: tile.icon,
            title: tile.title,
            disabled: true,
            reason: "Chưa chọn cửa hàng",
            trailing: UNKNOWN,
            data: { queue: tile.id },
          }),
        });
        continue;
      }

      runnable.push(tile);
    }

    render(queueHost, skeletonRows(Math.max(1, runnable.length)));

    // Concurrent on purpose: sequential reads would make the slowest one the shift's first
    // impression of the console. `allSettled` because a rejection here must not stop the others.
    const settled = await Promise.allSettled(
      runnable.map((tile) => loadTile(tile, store || "", multiStore)),
    );
    /** @type {Outcome[]} */
    const outcomes = settled.map((result, index) =>
      result.status === "fulfilled"
        ? result.value
        : { tile: runnable[index], zero: false, checked: false, row: null },
    );

    // Rows in the order of TILES, whatever order the reads landed in.
    const byTile = new Map([...fixed, ...outcomes].map((outcome) => [outcome.tile.id, outcome]));
    const ordered = TILES.map((tile) => byTile.get(tile.id)).filter(Boolean);
    const rows = ordered.map((outcome) => outcome?.row).filter(Boolean);
    const alerts = ordered.map((outcome) => outcome?.alert).filter(Boolean);
    const cleared = ordered.filter((outcome) => outcome?.zero).map((outcome) => outcome?.tile);

    // The one calm line, and it claims only what was checked:
    //   - every queue this screen tracks was read and is empty → "Không có gì chờ bạn", naming
    //     the queues it checked;
    //   - every queue that was read is empty, but some were refused or not read → "Các hàng đợi
    //     đã kiểm đều đang trống.", naming the ones checked (the others keep their rows);
    //   - some read queues have work → the empty ones are named in passing, nothing more.
    const names = cleared.map((tile) => tile?.short).join(" · ");
    const pending = ordered.filter((outcome) => outcome?.checked && !outcome.zero).length;
    const clearState = cleared.length === TILES.length ? "all" : pending === 0 ? "checked" : "some";
    const calm = cleared.length
      ? clearState === "some"
        ? h(
            "p",
            { class: "today__clear-meta", dataQueueClear: clearState },
            `Đang trống: ${names}.`,
          )
        : h(
            "div",
            { class: "today__clear", dataQueueClear: clearState },
            h("span", { class: "today__clear-icon" }, icon("check")),
            h(
              "div",
              null,
              h(
                "p",
                { class: "today__clear-title" },
                clearState === "all"
                  ? "Không có gì chờ bạn"
                  : "Các hàng đợi đã kiểm đều đang trống.",
              ),
              h("p", { class: "today__clear-meta" }, `Đã kiểm: ${names}.`),
            ),
          )
      : null;

    render(
      queueHost,
      missingStore
        ? inlineAlert({ state: "warn", title: "Chưa chọn cửa hàng", body: noStoreReason })
        : null,
      rows.length ? list(rows, { label: "Việc đang chờ" }) : null,
      calm,
      alerts,
    );
  }

  // --- Quick actions ---------------------------------------------------------------------------
  const state = snapshot();
  const newOrderVerdict = can(state.principal, "QUOTES_WRITE");
  const ordersVerdict = can(state.principal, "ORDERS_READ");
  const quick = h(
    "div",
    { class: "today__quick" },
    newOrderVerdict.allowed || state.status === "unknown"
      ? linkButton({ href: "#/new", label: "＋ Nhận đồ", variant: "primary", block: true })
      : gated(button({ label: "＋ Nhận đồ", variant: "primary", block: true }), newOrderVerdict),
    ordersVerdict.allowed || state.status === "unknown"
      ? linkButton({
          href: "#/orders?lookup=1",
          label: "Khách tới lấy đồ",
          block: true,
        })
      : gated(button({ label: "Khách tới lấy đồ", block: true }), ordersVerdict),
  );

  void loadAll();

  return h(
    "section",
    { class: "screen today" },
    page({
      title: hello.greeting,
      subtitle: hello.date,
      action: h(
        "div",
        { class: "today__refresh" },
        stamp,
        button({
          label: "Tải lại",
          icon: "refresh",
          variant: "quiet",
          onClick: () => void loadAll(),
        }),
      ),
    }),
    quick,
    section({ title: "Tiền hôm nay", info: takingsInfo(), children: takingsHost }),
    section({ title: "Cần làm", card: false, children: queueHost }),
    section({
      title: "Đơn hôm nay",
      info: infoButton("Những con số này là gì?", daySummaryNote),
      action: h("a", { class: "group__link", href: "#/orders" }, "Xem tất cả"),
      children: daySummaryHost,
    }),
  );
}

export const screen = {
  path: "/",
  title: "Hôm nay",
  render: render_,
};
