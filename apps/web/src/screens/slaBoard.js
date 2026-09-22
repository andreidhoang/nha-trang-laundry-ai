/**
 * Bảng trễ hạn: which order needs a person right now, and how long is left.
 *
 * `OPS-BOARD-001`. The SLA engine and its board query already existed and the only way to reach
 * them was to ask the assistant, which answered with two counts: how many orders are in production
 * and how many passed the internal risk mark. A count is not something a shift can act on — nobody
 * can pick up a count, find the bag and wash it first. This screen is the same query, listed.
 *
 * Four choices, and each has a reason that outlives the person who made it:
 *
 *   - **Nothing here computes risk, and nothing here computes the order.** The list is rendered in
 *     the order the server returned it, which is acceptance order — oldest accepted first — and
 *     the lede says exactly that rather than promising urgency order. It is nearly the same thing
 *     and deliberately not called the same thing: an order already washed and waiting on the shelf
 *     stopped its clock at `production_ready_at`, so its remaining time is frozen and it can sit
 *     above an order that is about to pass the mark. What each row needs is on the row — the
 *     outcome badge, the time left, the time past — so a shift ranks by the figure rather than by
 *     the position. Sorting again in the browser would rank one page against itself and would
 *     break the server's keyset paging.
 *   - **The rule that produced the numbers is on the screen, in the server's own words.** The
 *     sentence comes down in `policy_notice_vi` and is rendered verbatim. Per-order SLA policy is
 *     an unresolved business decision: one stated rule is applied to every order because there is
 *     no other rule to apply, and the board says so rather than letting a red row imply the shop
 *     broke a promise it never made.
 *   - **"Trễ mốc" is an internal mark, never a promise to a customer.** The mark is the shop's own
 *     eight-hour risk line. `GUIDANCE_DOES_NOT_CREATE_BREACH` exists in the domain for exactly this
 *     reason, and the wording here keeps the distinction: quá mốc rủi ro nội bộ, not trễ hẹn với
 *     khách.
 *   - **Durations are the server's integers.** `remaining_microseconds` and `breach_microseconds`
 *     are produced by `evaluate_production_sla`, which knows where the clock stopped — an order
 *     washed and waiting overnight for its owner is not still accruing time, and a browser
 *     subtracting a due date from its own clock would say it is. `format.duration` converts units
 *     and decides nothing.
 *
 * @module screens/slaBoard
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, dateTime, duration, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  errorNotice,
  explain,
  facts,
  markUpdated,
  panel,
  reasonCodeList,
  skeleton,
  toolbar,
} from "../ui/components.js";

/** One page of the board. The server's own ceiling is 200; this is a screenful. */
const PAGE_LIMIT = 50;

/**
 * How each SLA outcome is shown. The server's token is always displayed beside the Vietnamese, per
 * `core/i18n`'s rule: the operator reads the gloss, the engineer greps the token.
 *
 * Only the three outcomes `SlaOutcome` defines are here. An outcome this table has not been taught
 * falls through to a neutral badge carrying the raw value, because a new value from the server must
 * look unfamiliar rather than be absorbed into a plausible-looking one.
 */
const OUTCOME_BADGE = {
  BREACHED: { gloss: "Quá mốc nội bộ", state: "danger" },
  PENDING: { gloss: "Đang trong mốc", state: "info" },
  MET: { gloss: "Xong trước mốc", state: "ok" },
};

/**
 * @param {string} outcome
 * @returns {HTMLElement}
 */
function outcomeBadge(outcome) {
  const known = OUTCOME_BADGE[outcome];
  return badge({
    token: String(outcome || UNKNOWN),
    gloss: known ? known.gloss : "trạng thái SLA lạ",
    state: known ? known.state : "warn",
  });
}

/**
 * The time cell: one row, never two, and never a signed number.
 *
 * The server sends two non-negative durations and at most one of them is non-zero, which is how a
 * magnitude avoids carrying a direction. So the cell reads either "còn X" or "quá mốc Y", and the
 * two can never be confused for each other the way "-3 giờ" and "3 giờ" can.
 *
 * `null` in both means the policy set no mark at all — guidance, or an item that needs a person to
 * give it a time. That is `—`, which is not the same as "no time left".
 *
 * @param {any} item
 * @returns {HTMLElement|string}
 */
function timeCell(item) {
  if (item.breach_microseconds > 0) {
    return h("span", null, `quá mốc ${duration(item.breach_microseconds)}`);
  }
  if (!Number.isInteger(item.remaining_microseconds)) {
    return h(
      "span",
      null,
      `${UNKNOWN} `,
      h(
        "span",
        { class: "hint" },
        "Quy tắc áp cho đơn này không đặt mốc nào, nên không có thời gian còn lại để đếm.",
      ),
    );
  }
  return h("span", null, `còn ${duration(item.remaining_microseconds)}`);
}

/**
 * One order on the board.
 *
 * @param {any} item
 * @returns {HTMLElement}
 */
function riskCard(item) {
  return h(
    "article",
    // `dataOrderId` is what lets the browser verification assert the *sequence* the server sent
    // rather than only that a breached row is on top. The board orders by acceptance, so "breached
    // first" is not a property it has, and a check that asserted it would pin a coincidence.
    { class: "card", dataSlaOutcome: item.sla_outcome, dataOrderId: item.order_id },
    h(
      "div",
      { class: "spread" },
      h("strong", { class: "mono", title: item.order_id }, shortId(item.order_id)),
      outcomeBadge(item.sla_outcome),
    ),
    facts([
      ["Thời gian", timeCell(item), { span: true }],
      ["Mốc rủi ro nội bộ", dateTime(item.internal_risk_due_at)],
      ["Nhận vào sản xuất", dateTime(item.production_accepted_at)],
      // Named "báo xong" rather than "xong": this is the moment production reported the work
      // finished, which is when the clock stops. It is not when the customer took the bag.
      ["Sản xuất báo xong", dateTime(item.production_ready_at)],
      ["Trạng thái sản xuất", enumLabel(item.production_status), { mono: true }],
      ["Trạng thái đơn", enumLabel(item.commercial_status), { mono: true }],
    ]),
    explain(
      "Vì sao đơn này nằm ở đây?",
      reasonCodeList(item.reason_codes, "Máy chủ nêu các căn cứ sau"),
    ),
    h(
      "div",
      { class: "action-bar" },
      h("a", { class: "button", href: `#/orders/${item.order_id}` }, "Mở đơn hàng"),
    ),
  );
}

/**
 * What produced the numbers above, stated on the screen rather than in a commit message.
 *
 * `policy_notice_vi` is the server's sentence and is rendered as it arrived. It is the same
 * sentence `#/assistant` says, shared rather than copied, so the two surfaces cannot end up telling
 * one shift two different things about what the shop owes.
 *
 * @param {any} page
 * @returns {HTMLElement}
 */
function ruleNotice(page) {
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Mốc này là mốc nội bộ của tiệm, không phải hẹn với khách"),
    h("p", null, page.policy_notice_vi),
    h(
      "p",
      { class: "hint" },
      "Quy tắc: ",
      h("span", { class: "mono" }, `${page.policy_id} (${page.policy_type})`),
      " · Truy vấn: ",
      h("span", { class: "mono" }, page.query_version),
      " · Đọc lúc ",
      dateTime(page.evaluated_at),
    ),
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const host = h("div", { class: "stack" }, skeleton(3));
  const ruleHost = h("div", { class: "stack" });
  const countNode = h("span", { class: "count" }, "…");
  const moreHost = h("div", { class: "action-bar" });

  /**
   * The pages fetched so far, each with the instant the server evaluated it against.
   *
   * Pages rather than a flat list of rows, and the reason is the one thing this screen must not get
   * wrong. Every figure on a page comes from one read at one instant; the next page is a second
   * read, seconds later, and an order can cross its mark in between. Collapsing both into one list
   * under one "đọc lúc" stamp would put two instants behind one timestamp — a small lie on the one
   * screen whose whole claim is that its numbers were all true at the same moment. So each page
   * after the first carries its own stamp.
   *
   * @type {Array<{items: any[], evaluatedAt: string}>}
   */
  let pages = [];

  /**
   * Render whatever has been fetched. Separated from fetching so that appending a page does not
   * re-request the pages already on screen.
   */
  function paint() {
    const total = pages.reduce((sum, page) => sum + page.items.length, 0);
    countNode.textContent = String(total);
    render(
      host,
      total
        ? h(
            "div",
            { class: "stack" },
            pages.map((page, index) =>
              h(
                "div",
                { class: "stack" },
                index === 0
                  ? null
                  : h(
                      "p",
                      { class: "hint" },
                      `Các dòng dưới đây đọc lúc ${dateTime(page.evaluatedAt)}, muộn hơn phần ở trên.`,
                    ),
                page.items.map(riskCard),
              ),
            ),
          )
        : h(
            "p",
            { class: "hint" },
            "Không có đơn nào đang trong sản xuất ở cửa hàng này. Bảng này chỉ hiện đơn đã nhận " +
              "vào sản xuất và chưa trả ra, nên trống là một câu trả lời thật.",
          ),
    );
  }

  /**
   * Fetch one page.
   *
   * `after` is the server's keyset, handed back exactly as it was given. A page is requested by the
   * row it follows rather than by a row number: the board's population changes while a shift reads
   * it, and an offset would silently skip an order the moment one was released.
   *
   * @param {{accepted_at: string, order_id: string}|null} after
   * @returns {Promise<void>}
   */
  async function load(after) {
    if (!after) render(host, skeleton(3));
    render(moreHost);
    const query = new URLSearchParams({ limit: String(PAGE_LIMIT) });
    if (after) {
      query.set("after_accepted_at", after.accepted_at);
      query.set("after_order_id", after.order_id);
    }
    try {
      const page = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/sla-board?${query.toString()}`,
      );
      const items = Array.isArray(page?.items) ? page.items : null;
      if (!items) {
        render(
          host,
          h(
            "div",
            { class: "notice", dataState: "danger" },
            h("p", { class: "notice__title" }, "Máy chủ trả về hình dạng lạ cho bảng này"),
            h("p", null, "Không đọc được danh sách, và hiện một bảng trống ở đây sẽ là sai sự thật."),
          ),
        );
        return;
      }
      pages = after
        ? pages.concat([{ items, evaluatedAt: page.evaluated_at }])
        : [{ items, evaluatedAt: page.evaluated_at }];
      // The rule notice belongs to the first page and is not overwritten by a later one: its stamp
      // is when the board was opened, and the later pages carry their own above their own rows.
      if (!after) render(ruleHost, ruleNotice(page));
      paint();
      markUpdated(bar.stamp);
      if (page.next_accepted_at && page.next_order_id) {
        render(
          moreHost,
          h(
            "button",
            {
              type: "button",
              dataVariant: "quiet",
              onClick: () =>
                void load({
                  accepted_at: page.next_accepted_at,
                  order_id: page.next_order_id,
                }),
            },
            "Tải thêm",
          ),
        );
      }
    } catch (error) {
      // A failed page never blanks the rows already on screen: they were read from the server and
      // are still the last true answer. The notice goes where the "Tải thêm" button was.
      if (after) {
        render(moreHost, errorNotice(error, { onRetry: () => void load(after) }));
        return;
      }
      render(host, errorNotice(error, { onRetry: () => void load(null) }));
      countNode.textContent = UNKNOWN;
    }
  }

  const bar = toolbar({ onReload: () => void load(null) });

  const verdict = can(principal(), "SLA_BOARD_READ");
  if (!verdict.allowed) {
    countNode.textContent = UNKNOWN;
    render(
      host,
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Không đủ quyền — không gọi máy chủ"),
        h("p", null, verdict.reason),
      ),
    );
  } else {
    void load(null);
  }

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Việc cần làm trước"),
      h("h1", null, "Bảng trễ hạn"),
      h(
        "p",
        { class: "screen__lede" },
        "Đơn đang trong sản xuất, xếp theo thời gian nhận vào sản xuất — đơn nhận sớm nhất nằm " +
          "trên cùng. Đây không phải thứ tự gấp: đơn đã giặt xong đã dừng đồng hồ, nên hãy đọc " +
          "mốc ghi trên từng dòng. Thứ tự là của máy chủ, màn hình này không tự sắp lại.",
      ),
    ),
    panel({
      eyebrow: "Đang sản xuất",
      title: "Đơn theo mốc rủi ro nội bộ",
      count: countNode,
      guardrail:
        "Bảng này chỉ đọc. Không có nút nào ở đây thay đổi trạng thái đơn, và mốc hiển thị là mốc " +
        "nội bộ của tiệm chứ không phải giờ đã hẹn với khách.",
      children: h("div", { class: "stack" }, bar.node, ruleHost, host, moreHost),
    }),
  );
}

export const screen = {
  path: "/sla-board",
  title: "Bảng trễ hạn",
  capability: "SLA_BOARD_READ",
  needsStore: true,
  render: render_,
};
