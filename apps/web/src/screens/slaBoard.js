/**
 * Bảng trễ hạn: which order needs a person right now, and how long is left.
 *
 * `OPS-BOARD-001`, rebuilt on the V2 kit (spec V2 §5.1/§5.5 sibling, `CONSOLE-REDESIGN-003`). One
 * row per order in production: the ticket, the outcome as a pill, the time left or past the mark
 * large on the right, and the order page behind the tap. The rule behind the numbers, the reason
 * codes and the versions are one tap away (ⓘ and each row's "Chi tiết"), never gone.
 *
 * Four choices, and each has a reason that outlives the person who made it:
 *
 *   - **Nothing here computes risk, and nothing here computes the order.** The list is rendered in
 *     the order the server returned it, which is acceptance order — oldest accepted first — and
 *     the screen says exactly that rather than promising urgency order. It is nearly the same thing
 *     and deliberately not called the same thing: an order already washed and waiting on the shelf
 *     stopped its clock at `production_ready_at`, so its remaining time is frozen and it can sit
 *     above an order that is about to pass the mark. What each row needs is on the row — the
 *     outcome pill, the time left, the time past — so a shift ranks by the figure rather than by
 *     the position. Sorting again in the browser would rank one page against itself and would
 *     break the server's keyset paging.
 *   - **The rule that produced the numbers is on the screen, in the server's own words.** The
 *     sentence comes down in `policy_notice_vi` and is rendered verbatim behind the ⓘ. Per-order
 *     SLA policy is an unresolved business decision: one stated rule is applied to every order
 *     because there is no other rule to apply, and the board says so rather than letting a red row
 *     imply the shop broke a promise it never made.
 *   - **"Trễ mốc" is an internal mark, never a promise to a customer.** The mark is the shop's own
 *     eight-hour risk line, and the page's subtitle says so in one line (tier 1). `GUIDANCE_DOES_
 *     NOT_CREATE_BREACH` exists in the domain for exactly this reason.
 *   - **Durations are the server's integers.** `remaining_microseconds` and `breach_microseconds`
 *     are produced by `evaluate_production_sla`, which knows where the clock stopped — an order
 *     washed and waiting overnight for its owner is not still accruing time, and a browser
 *     subtracting a due date from its own clock would say it is. `format.duration` converts units
 *     and decides nothing.
 *
 * The ticket: the SLA read does not yet carry `ticket_number` (`READ-ENRICH-001` adds it). A row
 * shows "Phiếu N" when the field is present and the order's short id until then — never a guess.
 *
 * @module screens/slaBoard
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { TIMEZONE, UNKNOWN, dateTime, duration, parseInstant, shortId } from "../core/format.js";
import { enumLabel, enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, markUpdated, reasonCodeList, toolbar } from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  list,
  listRow,
  page,
  section,
  skeletonRows,
  statusPill,
  techDetails,
} from "../ui/kit.js";

/** One page of the board. The server's own ceiling is 200; this is a screenful. */
const PAGE_LIMIT = 50;

/**
 * The V1 panel's guardrail, now inside the ⓘ. Kept as a keyed property so the sentence keeps its
 * slot in the disclosure registry (`specs/contracts/console-disclosures-v1.yaml`).
 */
const BOARD_LIMITS = {
  guardrail:
    "Bảng này chỉ đọc. Không có nút nào ở đây thay đổi trạng thái đơn, và mốc hiển thị là mốc " +
    "nội bộ của tiệm chứ không phải giờ đã hẹn với khách.",
};

/**
 * How each SLA outcome is shown. The word carries the meaning, the colour speeds up scanning, and
 * the server's token stays in the pill's `title` and in each row's "Chi tiết" (spec V2 §4.1).
 *
 * Only the three outcomes `SlaOutcome` defines are here. An outcome this table has not been taught
 * falls through to a warn pill carrying the raw value, because a new value from the server must
 * look unfamiliar rather than be absorbed into a plausible-looking one. "Chưa tới mốc" rather than
 * "sắp tới mốc": PENDING says the mark has not passed, not that it is near — the time on the row
 * says how near.
 */
const OUTCOME_PILL = {
  BREACHED: { text: "Quá mốc", state: "danger" },
  PENDING: { text: "Chưa tới mốc", state: "info" },
  MET: { text: "Xong trước mốc", state: "ok" },
};

/**
 * @param {string} outcome
 * @returns {HTMLElement}
 */
function outcomePill(outcome) {
  const known = OUTCOME_PILL[outcome];
  return statusPill({
    text: known ? known.text : String(outcome || UNKNOWN),
    state: known ? known.state : "warn",
    token: String(outcome || UNKNOWN),
  });
}

/**
 * The time figure: one line, never two, and never a signed number.
 *
 * The server sends two non-negative durations and at most one of them is non-zero, which is how a
 * magnitude avoids carrying a direction. So the figure reads either "còn X" or "quá mốc Y", and the
 * two can never be confused for each other the way "-3 giờ" and "3 giờ" can.
 *
 * `null` in both means the policy set no mark at all — guidance, or an item that needs a person to
 * give it a time. That is `—`, which is not the same as "no time left"; the row says why.
 *
 * @param {any} item
 * @returns {{text: string, state: "danger"|"ok"|"neutral", noMark: boolean}}
 */
function timeFigure(item) {
  if (item.breach_microseconds > 0) {
    return {
      text: `quá mốc ${duration(item.breach_microseconds)}`,
      state: "danger",
      noMark: false,
    };
  }
  if (!Number.isInteger(item.remaining_microseconds)) {
    return { text: UNKNOWN, state: "neutral", noMark: true };
  }
  return {
    text: `còn ${duration(item.remaining_microseconds)}`,
    state: item.sla_outcome === "MET" ? "ok" : "neutral",
    noMark: false,
  };
}

/**
 * "Phiếu 17" when the read carries the ticket (`READ-ENRICH-001`), else the order's short id.
 *
 * @param {any} item
 * @returns {string}
 */
function orderLabel(item) {
  return Number.isInteger(item.ticket_number)
    ? `Phiếu ${item.ticket_number}`
    : `Đơn ${String(item.order_id || UNKNOWN).slice(0, 8)}`;
}

/** "01:22 26/09": the mark to the minute, with its day, in the shop's time zone. */
const MARK = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TIMEZONE,
  hour: "2-digit",
  minute: "2-digit",
  day: "2-digit",
  month: "2-digit",
});

/**
 * @param {string|null|undefined} value
 * @returns {string}
 */
function markTime(value) {
  const parsed = parseInstant(value);
  return parsed ? MARK.format(parsed) : UNKNOWN;
}

/**
 * One order on the board: the row that opens it, and its record one tap below.
 *
 * `data-order-id` is what lets the browser verification assert the *sequence* the server sent
 * rather than only that a breached row is on top. The board orders by acceptance, so "breached
 * first" is not a property it has, and a check that asserted it would pin a coincidence.
 *
 * @param {any} item
 * @returns {HTMLElement[]}
 */
function riskRow(item) {
  const figure = timeFigure(item);
  const meta = [
    // The mark, and what production is doing: the two facts a shift acts on.
    item.internal_risk_due_at ? `Mốc ${markTime(item.internal_risk_due_at)}` : null,
    enumVi(item.production_status),
  ]
    .filter(Boolean)
    .join(" · ");
  return [
    listRow({
      href: `#/orders/${item.order_id}`,
      title: [h("span", { title: item.order_id }, orderLabel(item)), outcomePill(item.sla_outcome)],
      meta: figure.noMark
        ? [
            meta,
            h(
              "span",
              { class: "hint" },
              "Quy tắc áp cho đơn này không đặt mốc nào, nên không có thời gian còn lại để đếm.",
            ),
          ]
        : meta,
      trailing: h("span", { class: "sla__time", dataState: figure.state }, figure.text),
      data: { slaOutcome: String(item.sla_outcome), orderId: String(item.order_id) },
    }),
    h(
      "div",
      { class: "sla__more" },
      techDetails(
        [
          ["Mốc rủi ro nội bộ", dateTime(item.internal_risk_due_at), { mono: false }],
          ["Nhận vào sản xuất", dateTime(item.production_accepted_at), { mono: false }],
          // Named "báo xong" rather than "xong": this is the moment production reported the work
          // finished, which is when the clock stops. It is not when the customer took the bag.
          ["Sản xuất báo xong", dateTime(item.production_ready_at), { mono: false }],
          ["Trạng thái sản xuất", enumLabel(item.production_status)],
          ["Trạng thái đơn", enumLabel(item.commercial_status)],
          ["Kết quả SLA", String(item.sla_outcome || UNKNOWN)],
          [
            "Vì sao đơn này nằm ở đây?",
            reasonCodeList(item.reason_codes, "Máy chủ nêu các căn cứ sau"),
            { mono: false },
          ],
          ["Mã đơn", shortId(item.order_id), { copy: String(item.order_id) }],
        ],
        { summary: "Chi tiết" },
      ),
    ),
  ];
}

/**
 * What produced the numbers, in the server's own words — behind the ⓘ on the page title.
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
  const host = h("div", { class: "stack" }, skeletonRows(4));
  const ruleHost = h("div", { class: "stack" }, h("p", { class: "hint" }, "Đang đọc quy tắc…"));
  const techHost = h("div");
  const countNode = h("span", { class: "sla__count" });
  const moreHost = h("div", { class: "sla__paging" });

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
    countNode.textContent = total ? `${total} đơn` : "";
    render(
      host,
      total
        ? pages.map((page, index) =>
            h(
              "div",
              { class: "stack stack--tight" },
              index === 0
                ? null
                : h(
                    "p",
                    { class: "hint" },
                    `Các dòng dưới đây đọc lúc ${dateTime(page.evaluatedAt)}, muộn hơn phần ở trên.`,
                  ),
              list(page.items.map(riskRow), { label: "Đơn đang trong sản xuất" }),
            ),
          )
        : emptyState({
            icon: "check",
            title: "Không có đơn nào đang giặt",
            body: h(
              "span",
              { class: "hint" },
              "Không có đơn nào đang trong sản xuất ở cửa hàng này. Bảng này chỉ hiện đơn đã nhận " +
                "vào sản xuất và chưa trả ra, nên trống là một câu trả lời thật.",
            ),
          }),
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
    if (!after) render(host, skeletonRows(4));
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
            h(
              "p",
              null,
              "Không đọc được danh sách, và hiện một bảng trống ở đây sẽ là sai sự thật.",
            ),
          ),
        );
        return;
      }
      pages = after
        ? pages.concat([{ items, evaluatedAt: page.evaluated_at }])
        : [{ items, evaluatedAt: page.evaluated_at }];
      // The rule notice belongs to the first page and is not overwritten by a later one: its stamp
      // is when the board was opened, and the later pages carry their own above their own rows.
      if (!after) {
        render(ruleHost, ruleNotice(page));
        render(
          techHost,
          techDetails([
            ["Quy tắc", `${page.policy_id} (${page.policy_type})`],
            ["Truy vấn", page.query_version],
            ["Máy chủ tính lúc", dateTime(page.evaluated_at), { mono: false }],
          ]),
        );
      }
      paint();
      markUpdated(bar.stamp);
      if (page.next_accepted_at && page.next_order_id) {
        render(
          moreHost,
          button({
            label: "Tải thêm",
            variant: "quiet",
            block: true,
            onClick: () =>
              void load({
                accepted_at: page.next_accepted_at,
                order_id: page.next_order_id,
              }),
          }),
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
      countNode.textContent = "";
    }
  }

  const bar = toolbar({ onReload: () => void load(null) });

  const verdict = can(principal(), "SLA_BOARD_READ");
  if (!verdict.allowed) {
    render(
      host,
      inlineAlert({
        state: "warn",
        title: "Không đủ quyền — không gọi máy chủ",
        body: verdict.reason,
      }),
    );
  } else {
    void load(null);
  }

  return h(
    "section",
    { class: "screen sla" },
    page({
      title: "Bảng trễ hạn",
      subtitle: "Mốc nội bộ của tiệm, không phải hẹn với khách.",
      info: infoButton(
        "Mốc này là gì, và bảng xếp theo thứ tự nào?",
        ruleHost,
        h(
          "p",
          { class: "screen__lede" },
          "Đơn đang trong sản xuất, xếp theo thời gian nhận vào sản xuất — đơn nhận sớm nhất nằm " +
            "trên cùng. Đây không phải thứ tự gấp: đơn đã giặt xong đã dừng đồng hồ, nên hãy đọc " +
            "mốc ghi trên từng dòng. Thứ tự là của máy chủ, màn hình này không tự sắp lại.",
        ),
        h("p", { class: "hint" }, BOARD_LIMITS.guardrail),
      ),
    }),
    section({
      title: "Đang giặt",
      action: countNode,
      card: false,
      children: h(
        "div",
        { class: "stack stack--tight" },
        bar.node,
        // Tier 1: the one fact that changes how the list is read. Position is not urgency.
        h(
          "p",
          { class: "hint sla__order" },
          "Xếp theo lúc nhận vào giặt, không theo độ gấp — hãy xem giờ trên từng dòng.",
        ),
        host,
        moreHost,
        techHost,
      ),
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
