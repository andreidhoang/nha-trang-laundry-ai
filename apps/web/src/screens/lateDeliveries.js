/**
 * Giao trễ cần xử lý: deliveries the server measured as late, and one tap each way
 * (`LATE-CREDIT-002`, `DEC-042`).
 *
 * The server measures every figure on this screen: the deadline (the first promise, moved only by
 * a *Hẹn lại* the customer asked for), the arrival (the successful delivery), the minutes late and
 * the would-be credit (the remedy rules' own 10%). Nothing is typed and nothing is computed here —
 * the screen prints what it was sent.
 *
 * Per row, two buttons:
 *
 *   - **Lỗi của tiệm — giảm {credit}** records the shop's fault. The server opens the complaint and
 *     the late-delivery credit at its own minutes, in one transaction. The credit then follows the
 *     remedy path it always did: staff give it on the complaint's page up to the published limit,
 *     the owner approves above it. The row moves to **Đã ghi lỗi của tiệm** with the next step:
 *     **Cấp giảm trừ** (the complaint's page) or **Chờ chủ tiệm duyệt** (the approvals screen).
 *   - **Không phải lỗi tiệm** opens a reason picker; the row leaves the list and the reason is kept.
 *
 * On a desk the list is a table (ticket, hẹn → giao, trễ, the two buttons); on a phone each row is
 * a card with the same facts in the same order.
 *
 * @module screens/lateDeliveries
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { calendarDay, duration, money, promiseTime } from "../core/format.js";
import { LATE_REASON_VI } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated } from "../ui/components.js";
import {
  button,
  choiceChips,
  emptyState,
  infoButton,
  inlineAlert,
  linkButton,
  page,
  section,
  sheet,
  show,
  skeletonRows,
  statusPill,
  toast,
} from "../ui/kit.js";
import { orderName } from "./orders.js";

/** One page of the list; the server says when there are more (`truncated`). */
const LIMIT = 50;
/** The longest note the server keeps (`NOTE_TOO_LONG` above it). */
const NOTE_LIMIT = 120;

/**
 * "trễ 3 giờ 10 phút": the server's whole minutes, written in hours and minutes.
 *
 * @param {unknown} minutes
 * @returns {string}
 */
export function lateText(minutes) {
  return Number.isInteger(minutes) ? duration(/** @type {number} */ (minutes) * 60_000_000) : "—";
}

/**
 * "27/09": the ticket's day, in the one day convention (`format.js`).
 *
 * @param {string} day
 * @returns {string}
 */
function shortDay(day) {
  return calendarDay(day, { weekday: false });
}

/**
 * What a row calls the order: the ticket, and the customer's name when the order has one.
 *
 * @param {any} item
 * @returns {string}
 */
function rowTitle(item) {
  return item.customer_name ? `${orderName(item)} · ${item.customer_name}` : orderName(item);
}

/**
 * "Giao 14:05 không gặp khách": each failed attempt before the deadline, which points to the
 * customer's side. Shown beside the lateness; it decides nothing.
 *
 * @param {any} item
 * @returns {string|null}
 */
function attemptsHint(item) {
  const times = Array.isArray(item.failed_attempts_before_deadline)
    ? item.failed_attempts_before_deadline
    : [];
  if (!times.length) return null;
  // "14:05" on the deadline's own day, "14:05 27/9" on another: the shop's clock, no seconds.
  const deadlineDay = promiseTime(item.deadline_at, { short: true }).split(" ")[1];
  return times
    .map((at) => {
      const [clock, day] = promiseTime(at, { short: true }).split(" ");
      return `Giao ${day === deadlineDay ? clock : `${clock} ${day}`} không gặp khách`;
    })
    .join(" · ");
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const who = principal();
  const readVerdict = can(who, "INCIDENTS_READ");
  const decideVerdict = can(who, "INCIDENTS_WRITE");
  const noticeHost = h("div");
  const listHost = h("div", { class: "stack stack--tight" }, skeletonRows(3));
  const followHost = h("div");
  const sheetsHost = h("div");
  const subtitle = h("span", { dataField: "late-count" });
  /** @type {Map<string, Submission>} one intent per order, kept across a retry */
  const submissions = new Map();

  function submissionFor(orderId, intent) {
    const scope = `late-${intent}-${orderId}`;
    if (!submissions.has(scope)) submissions.set(scope, new Submission("late-delivery-decision"));
    return /** @type {Submission} */ (submissions.get(scope));
  }

  async function load() {
    try {
      const payload = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/late-deliveries?limit=${LIMIT}`,
      );
      draw(payload);
    } catch (error) {
      show(listHost, errorNotice(/** @type {any} */ (error), { onRetry: () => void load() }));
    }
  }

  /** @param {any} payload */
  function draw(payload) {
    const orders = Array.isArray(payload?.orders) ? payload.orders : [];
    const follow = Array.isArray(payload?.follow_up) ? payload.follow_up : [];
    const threshold = payload?.threshold_minutes;
    subtitle.textContent = !payload?.policy_published
      ? "Chưa đo được"
      : orders.length
        ? `${orders.length}${payload.truncated ? "+" : ""} chuyến trễ quá ${lateText(threshold)}`
        : `Không có chuyến nào trễ quá ${lateText(threshold)}`;
    render(
      noticeHost,
      payload?.policy_published
        ? null
        : inlineAlert({
            state: "info",
            title: "Chủ tiệm chưa công bố mức bồi hoàn, nên chưa đo được chuyến nào giao trễ.",
          }),
    );
    if (!payload?.policy_published) {
      render(listHost);
    } else if (!orders.length) {
      render(
        listHost,
        emptyState({
          icon: "truck",
          title: "Không có chuyến giao nào trễ",
          body: `Chuyến giao trễ quá ${lateText(threshold)} so với giờ hẹn sẽ hiện ở đây.`,
        }),
      );
    } else {
      render(
        listHost,
        // A real table: on a desk its columns line up across rows; on a phone the stylesheet lays
        // each row out as a card (`kit.css`, LATE-CREDIT-002 block).
        h(
          "div",
          { class: "late-table" },
          h(
            "table",
            { id: "late-list", "aria-label": "Giao trễ cần xử lý" },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h("th", { scope: "col" }, "Đơn"),
                h("th", { scope: "col" }, "Hẹn → giao"),
                h("th", { scope: "col" }, "Trễ"),
                h("th", { scope: "col", class: "late-table__actions-head" }, "Xử lý"),
              ),
            ),
            h(
              "tbody",
              null,
              orders.map((item) => lateRow(item)),
            ),
          ),
        ),
        payload.truncated
          ? h(
              "p",
              { class: "hint", dataTruncated: "true" },
              `Chỉ hiện ${orders.length} chuyến giao trễ lâu nhất; xử lý xong sẽ hiện tiếp.`,
            )
          : null,
      );
    }
    render(
      followHost,
      follow.length
        ? section({
            title: "Đã ghi lỗi của tiệm",
            id: "late-follow-up",
            children: h(
              "div",
              { class: "stack stack--tight" },
              follow.map((item) => followRow(item)),
              payload.follow_up_truncated
                ? h("p", { class: "hint", dataTruncated: "true" }, "Còn khoản khác chưa hiện ở đây.")
                : null,
            ),
          })
        : null,
    );
  }

  /** @param {any} item */
  function lateRow(item) {
    const orderId = String(item.order_id);
    const alertHost = h("div", { class: "late-row__alert" });
    const hint = attemptsHint(item);
    const credit = item.credit_vnd;
    const fault = gated(
      button({
        label:
          credit === null || credit === undefined
            ? "Lỗi của tiệm"
            : `Lỗi của tiệm — giảm ${money(credit)}`,
        variant: "primary",
        network: true,
        disabled: credit === null || credit === undefined,
        data: { lateFault: orderId },
        onClick: (event) => void storeFault(item, /** @type {any} */ (event.currentTarget), alertHost),
      }),
      decideVerdict,
    );
    const notFault = gated(
      button({
        label: "Không phải lỗi tiệm",
        variant: "secondary",
        network: true,
        data: { lateNotFault: orderId },
        onClick: () => openReasons(item),
      }),
      decideVerdict,
    );
    return h(
      "tr",
      { class: "late-row", dataLate: orderId },
      h(
        "td",
        { class: "late-row__order" },
        h(
          "a",
          { href: `#/orders/${encodeURIComponent(orderId)}` },
          h("span", { class: "late-row__title" }, rowTitle(item)),
          item.ticket_issued_on
            ? h("span", { class: "late-row__meta" }, `Nhận ${shortDay(item.ticket_issued_on)}`)
            : null,
        ),
      ),
      h(
        "td",
        { class: "late-row__times" },
        h("span", null, `Hẹn ${promiseTime(item.deadline_at, { short: true })}`),
        " ",
        h("span", null, `→ giao ${promiseTime(item.delivered_at, { short: true })}`),
        item.deadline_basis === "CUSTOMER_REQUEST"
          ? h("span", { class: "late-row__meta" }, "Giờ khách hẹn lại")
          : null,
        hint ? h("span", { class: "late-row__hint", dataField: "late-attempts" }, hint) : null,
      ),
      h(
        "td",
        { class: "late-row__late" },
        statusPill({
          state: "danger",
          text: `Trễ ${lateText(item.late_by_minutes)}`,
          token: "LATE_DELIVERY",
        }),
      ),
      h(
        "td",
        { class: "late-row__actions" },
        h("span", { class: "late-row__buttons" }, fault, notFault),
        credit === null || credit === undefined
          ? h(
              "span",
              { class: "hint", dataField: "late-no-credit" },
              item.refunded ? "Đơn đã hoàn tiền: không có giảm trừ." : "Chưa tính được giảm trừ.",
            )
          : item.credit_requires_owner
            ? h("span", { class: "hint" }, "Trên mức nhân viên: chủ tiệm duyệt.")
            : null,
        alertHost,
      ),
    );
  }

  /** @param {any} item */
  function followRow(item) {
    const label = `${orderName(item)} · trễ ${lateText(item.late_by_minutes)}`;
    const amount = Number.isInteger(item.amount_vnd) ? `giảm ${money(item.amount_vnd)}` : "";
    const action =
      item.next_step === "AWAIT_OWNER"
        ? linkButton({
            href: "#/approvals",
            label: "Chờ chủ tiệm duyệt",
            variant: "quiet",
            icon: "approval",
          })
        : linkButton({
            href: `#/incidents/${encodeURIComponent(String(item.incident_id))}`,
            label: "Cấp giảm trừ",
            variant: "secondary",
          });
    return h(
      "div",
      { class: "late-follow", dataLateFollow: String(item.order_id), dataNextStep: item.next_step },
      h(
        "span",
        { class: "late-follow__main" },
        h("span", { class: "late-row__title" }, label),
        amount ? h("span", { class: "late-row__meta" }, amount) : null,
      ),
      action,
    );
  }

  /**
   * @param {any} item
   * @param {HTMLButtonElement} control
   * @param {HTMLElement} alertHost
   */
  async function storeFault(item, control, alertHost) {
    const orderId = String(item.order_id);
    const submission = submissionFor(orderId, "fault");
    control.disabled = true;
    control.setAttribute("aria-busy", "true");
    render(alertHost);
    try {
      const done = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/late-deliveries/${encodeURIComponent(orderId)}/decision`,
        { method: "POST", body: { decision: "STORE_FAULT" }, idempotencyKey: submission.key() },
      );
      submissions.delete(`late-fault-${orderId}`);
      toast(
        done.proposal_status === "OWNER_APPROVAL_REQUIRED"
          ? `Đã ghi lỗi của tiệm · ${orderName(item)} · chờ chủ tiệm duyệt`
          : `Đã ghi lỗi của tiệm · ${orderName(item)}`,
      );
      await load();
    } catch (error) {
      if (control.isConnected) {
        control.disabled = false;
        control.removeAttribute("aria-busy");
      }
      show(alertHost, errorNotice(/** @type {any} */ (error)));
    }
  }

  /** @param {any} item */
  function openReasons(item) {
    const orderId = String(item.order_id);
    let reason = "";
    let note = "";
    let intent = "";
    const alertHost = h("div");
    const submission = submissionFor(orderId, "not-fault");
    const noteHost = h("div", { class: "stack stack--tight", hidden: true });
    const noteInput = h("input", {
      id: "late-note",
      type: "text",
      class: "input",
      autocomplete: "off",
      maxlength: String(NOTE_LIMIT),
      placeholder: "Ví dụ: khách dặn giao sau 18 giờ",
      onInput: (event) => {
        note = /** @type {HTMLInputElement} */ (event.target).value;
      },
    });
    render(
      noteHost,
      h("label", { class: "field-label", for: "late-note" }, "Vì sao"),
      noteInput,
      h("p", { class: "hint" }, "Tối đa 120 ký tự. Không ghi số điện thoại khách."),
    );
    const submit = button({
      label: "Lưu: không phải lỗi tiệm",
      variant: "primary",
      block: true,
      network: true,
      id: "late-reason-submit",
      onClick: () => void send(),
    });

    async function send() {
      if (!reason) {
        show(alertHost, inlineAlert({ state: "danger", title: "Chọn một lý do." }));
        return;
      }
      const body = {
        decision: "NOT_STORE_FAULT",
        reason_code: reason,
        note: reason === "OTHER" && note.trim() ? note.trim() : null,
      };
      const next = JSON.stringify(body);
      if (next !== intent) {
        submission.reset();
        intent = next;
      }
      render(alertHost);
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
      try {
        await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/late-deliveries/${encodeURIComponent(orderId)}/decision`,
          { method: "POST", body, idempotencyKey: submission.key() },
        );
        submissions.delete(`late-not-fault-${orderId}`);
        made.close();
        toast(`Đã ghi: không phải lỗi tiệm · ${orderName(item)}`);
        await load();
      } catch (error) {
        show(alertHost, errorNotice(/** @type {any} */ (error)));
      } finally {
        if (submit.isConnected) {
          submit.disabled = false;
          submit.removeAttribute("aria-busy");
        }
      }
    }

    const made = sheet({
      id: "late-reason",
      title: "Không phải lỗi tiệm",
      body: h(
        "div",
        { class: "stack" },
        h(
          "p",
          { class: "hint" },
          `${rowTitle(item)} · trễ ${lateText(item.late_by_minutes)}`,
        ),
        h("p", { class: "field-label" }, "Lý do"),
        choiceChips({
          label: "Lý do",
          name: "late-reason",
          options: Object.entries(LATE_REASON_VI).map(([value, label]) => ({ value, label })),
          onChange: (value) => {
            reason = value;
            noteHost.hidden = value !== "OTHER";
            if (value === "OTHER") noteInput.focus();
          },
        }),
        noteHost,
        alertHost,
      ),
      actions: submit,
      onClose: () => made.node.remove(),
    });
    render(sheetsHost, made.node);
    made.open();
  }

  const info = infoButton(
    "Giao trễ được đo thế nào?",
    h(
      "p",
      null,
      "Máy chủ tự đo: giờ hẹn là giờ hẹn trả đầu tiên; chỉ khi khách xin hẹn lại thì mới tính theo " +
        "giờ khách hẹn. Hẹn lại vì máy, vì đông việc hay vì thời tiết không dời giờ hẹn. Giờ giao là " +
        "lúc ghi giao thành công.",
    ),
    h(
      "p",
      null,
      "Lỗi của tiệm: máy chủ mở khiếu nại và khoản giảm 10% tổng đã thu vào đơn sau, đúng số phút " +
        "đã đo. Tới mức nhân viên được duyệt thì cấp ở trang khiếu nại; trên mức đó chờ chủ tiệm " +
        "duyệt. Mỗi đơn chỉ giảm một lần; đơn đã hoàn tiền thì không giảm.",
    ),
    h(
      "p",
      null,
      "Không phải lỗi tiệm: chọn lý do (khách vắng nhà, sai địa chỉ, khách hẹn muộn hơn, khác). Đơn " +
        "rời danh sách và lý do được giữ lại.",
    ),
    h(
      "p",
      { class: "hint" },
      "Mức trễ (hơn 2 giờ) và 10% là số chủ tiệm công bố trong chính sách bồi hoàn (DEC-004, " +
        "DEC-042). Chưa công bố thì chưa đo được đơn nào.",
    ),
  );

  if (!readVerdict.allowed) {
    render(listHost, h("p", { class: "hint" }, readVerdict.reason));
  } else if (!store) {
    render(listHost, h("p", { class: "hint" }, "Chọn một cửa hàng để xem giao trễ."));
  } else {
    void load();
  }

  return h(
    "section",
    { class: "screen screen--wide late-deliveries" },
    page({
      title: "Giao trễ cần xử lý",
      subtitle,
      info,
      action: button({ label: "Tải lại", icon: "refresh", variant: "quiet", onClick: () => void load() }),
    }),
    noticeHost,
    listHost,
    followHost,
    sheetsHost,
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/late-deliveries",
  title: "Giao trễ cần xử lý",
  capability: "INCIDENTS_READ",
  needsStore: true,
  render: render_,
};
