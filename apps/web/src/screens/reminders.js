/**
 * Nhắc khách lấy đồ: the pickup reminders due today (`PICKUP-REMIND-001`, `DEC-043`).
 *
 * The server decides who, when and what; a person sends it from the shop's own Zalo or phone, in
 * two taps. Each row is one waiting order whose reminder is due — day 0 "đồ đã xong", day 3, 7,
 * 14, and the last day before the published storage fee — oldest ready first, as the server orders
 * it. On a desk it is a table; on a phone each row is a card. Per row:
 *
 *   - **Mở Zalo** — the `zalo.me` link the server built from the customer's number (the number
 *     itself is never printed here);
 *   - **Chép tin nhắn** — asks the server for the fixed `pickup-reminder-v2` text for that step,
 *     which it gives only after the egress guard allows it, and puts it on the clipboard;
 *   - **Gọi** — a `tel:` link, then what came of the call;
 *   - **Đã nhắc** — records a contact attempt for that step, one tap on the outcome; the first is
 *     *Đã gửi tin*.
 *
 * Refusals are said in words: before the owner publishes the messaging policy nothing can be
 * copied (one line for the page, the owner's switch behind ⓘ); a customer who wrote STOP gets no
 * message (one line on the row). A call stays available. Orders with no phone and no chat channel
 * are counted at the bottom, not hidden.
 *
 * What this screen never does: decide which step is due, count days, build the text, or keep
 * anything on the device. Every word of the message and every verdict is the server's.
 *
 * @module screens/reminders
 */

import { Submission, request } from "../core/api.js";
import { customerTitle, telHref } from "../core/customers.js";
import { h, render } from "../core/dom.js";
import { dateOnly, money } from "../core/format.js";
import { REMINDER_STEP_VI } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated } from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  page,
  sheet,
  show,
  skeletonRows,
  statusPill,
  toast,
} from "../ui/kit.js";
import { heldDaysText, shelfSwitch, waitingText } from "../ui/unclaimed.js";
import { orderName } from "./orders.js";

/** One page of the due list; the server says when there are more (`truncated`). */
const LIMIT = 100;

/**
 * The refusal, in the counter's words, next to the control (tier 1). Why the owner's switch and a
 * customer's STOP stop a message is said once, behind the page's ⓘ (tier 2).
 *
 * @type {Readonly<Record<string, string>>}
 */
const REFUSAL_VI = {
  NO_CONTACT: "Không có số điện thoại hay kênh chat của khách.",
  MESSAGING_POLICY_UNPUBLISHED: "Chủ tiệm chưa công bố chính sách tin dịch vụ nên chưa chép tin được.",
  SUPPRESSED: "Khách đã nhắn dừng nhận tin. Không gửi tin; gọi nếu cần.",
  PENDING_REVIEW: "Khách có tin giống yêu cầu dừng nhận tin, đang chờ người xem. Chưa gửi tin.",
  SUPPRESSION_UNKNOWN: "Chưa rõ khách có nhận tin không, nên chưa gửi tin.",
  NO_SERVICE_BASIS: "Chưa đủ căn cứ gửi tin dịch vụ cho khách này.",
  NO_REMINDER_DUE: "Đơn này không còn lần nhắc nào đến hạn. Tải lại danh sách.",
  REMINDER_STEP_NOT_DUE: "Lần nhắc này vừa đổi (sang ngày mới hoặc người khác vừa ghi). Tải lại.",
  NOT_AWAITING_PICKUP: "Đơn không còn chờ lấy (khách đã lấy hoặc đơn đã đổi). Tải lại.",
};

/**
 * @param {string|null|undefined} code
 * @returns {string}
 */
export function reminderRefusalText(code) {
  return (code && REFUSAL_VI[code]) || "";
}

/**
 * The outcomes *Đã nhắc* offers, in order; the first is the default. A message needs the server's
 * allowance, a call does not (DEC-043: "Gọi stays available").
 */
const OUTCOMES = [
  { id: "zalo", label: "Đã gửi tin Zalo", channel: "ZALO", outcome: "MESSAGE_SENT", message: true },
  { id: "sms", label: "Đã gửi SMS", channel: "SMS", outcome: "MESSAGE_SENT", message: true },
  { id: "reached", label: "Gọi: đã nói chuyện", channel: "CALL", outcome: "REACHED" },
  { id: "promised", label: "Gọi: hẹn tới lấy", channel: "CALL", outcome: "PROMISED_TO_COME" },
  { id: "no-answer", label: "Gọi: không nghe máy", channel: "CALL", outcome: "NO_ANSWER" },
  { id: "wrong", label: "Gọi: sai số", channel: "CALL", outcome: "WRONG_NUMBER" },
];

/**
 * What a row calls the order: "Phiếu 17 · chị Lan", or the ticket alone.
 *
 * @param {any} item a `PickupReminderItemResponse`
 * @returns {string}
 */
function rowTitle(item) {
  const name = item.customer_name
    ? String(item.customer_name)
    : item.customer_id && item.phone_last4
      ? customerTitle({ phone_last4: item.phone_last4 })
      : "";
  return name ? `${orderName(item)} · ${name}` : orderName(item);
}

/**
 * @param {unknown} error
 * @returns {string}
 */
function codeOf(error) {
  const codes = /** @type {any} */ (error)?.reasonCodes;
  return Array.isArray(codes) && codes.length ? String(codes[0]) : "";
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
  const subtitle = h("span", { dataField: "reminder-count" });

  async function load() {
    try {
      const payload = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/pickup-reminders?limit=${LIMIT}`,
      );
      draw(payload);
    } catch (error) {
      show(listHost, errorNotice(/** @type {any} */ (error), { onRetry: () => void load() }));
    }
  }

  /** @param {any} payload */
  function draw(payload) {
    const orders = Array.isArray(payload?.orders) ? payload.orders : [];
    const reachable = orders.filter((item) => item.reachable !== "NONE");
    const unreachable = orders.filter((item) => item.reachable === "NONE");
    // Round 8 desk review: the heading counts what the counter can act on; the ones nobody can be
    // reached on are counted in their own line under the table, not folded into this number.
    const actionable = Number(payload.total_count || 0) - Number(payload.unreachable_count || 0);
    subtitle.textContent =
      actionable > 0
        ? `${actionable} đơn cần nhắc`
        : payload.total_count
          ? "Không có đơn nào nhắc được"
          : "Không có đơn nào cần nhắc";
    render(
      noticeHost,
      payload.messaging_policy_published === false && reachable.length
        ? inlineAlert({
            state: "info",
            title: "Chủ tiệm chưa công bố chính sách tin dịch vụ: chưa chép tin được. Gọi vẫn dùng được.",
          })
        : null,
    );
    if (!orders.length) {
      render(
        listHost,
        emptyState({
          icon: "check",
          title: "Không có ai cần nhắc hôm nay",
          body: "Đồ xong vào ngày 0, 3, 7, 14 và ngày trước khi tính phí lưu kho sẽ hiện ở đây.",
        }),
      );
      return;
    }
    render(
      listHost,
      reachable.length
        ? h(
            "div",
            { class: "reminders__scroll" },
            h(
              "table",
              { class: "reminders", id: "reminder-list", "aria-label": "Nhắc khách lấy đồ" },
              h(
                "thead",
                null,
                h(
                  "tr",
                  null,
                  h("th", { scope: "col" }, "Đơn"),
                  h("th", { scope: "col" }, "Lần nhắc"),
                  h("th", { scope: "col", class: "numeric" }, "Còn lại"),
                  h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Việc cần làm")),
                ),
              ),
              h("tbody", null, reachable.map((item) => reminderRow(item, payload))),
            ),
          )
        : null,
      unreachable.length ? unreachableBlock(unreachable, payload) : null,
      payload.truncated
        ? h(
            "p",
            { class: "hint", dataTruncated: "true" },
            `Chỉ hiện ${orders.length} đơn chờ lâu nhất trong ${payload.total_count} đơn cần nhắc.`,
          )
        : null,
    );
  }

  /**
   * Orders nobody can be messaged about, counted and listed small: the reminder waits for the
   * customer to come or call.
   *
   * @param {any[]} items
   * @param {any} payload
   */
  function unreachableBlock(items, payload) {
    const counted = Number(payload.unreachable_count || items.length);
    return h(
      "details",
      { class: "reminders__unreachable", dataUnreachable: String(counted) },
      h(
        "summary",
        null,
        `${counted} đơn đến lúc nhắc nhưng không có số điện thoại hay kênh chat của khách`,
      ),
      h(
        "ul",
        { class: "reminders__unreachable-list" },
        items.map((item) =>
          h(
            "li",
            { dataReminderUnreachable: String(item.order_id) },
            h(
              "a",
              { href: `#/orders/${encodeURIComponent(String(item.order_id))}` },
              rowTitle(item),
            ),
            h(
              "span",
              { class: "hint" },
              ` · ${REMINDER_STEP_VI[item.step] || item.step} · ${waitingText(item.days_waiting)}`,
            ),
          ),
        ),
      ),
    );
  }

  /**
   * @param {any} item
   * @param {any} payload
   */
  function reminderRow(item, payload) {
    const id = String(item.order_id);
    const title = rowTitle(item);
    const refusal = item.message_refusal ? String(item.message_refusal) : "";
    const statusHost = h("div", { class: "reminders__status", role: "status" });
    const tel = telHref(item.phone);
    // The page already says the policy is unpublished; a row repeats only what is its own.
    const rowReason =
      refusal && refusal !== "MESSAGING_POLICY_UNPUBLISHED" ? reminderRefusalText(refusal) : "";

    /** @param {HTMLElement} control @param {string} reason */
    const refuse = (control, reason) => {
      control.setAttribute("disabled", "");
      control.setAttribute("aria-disabled", "true");
      control.setAttribute("data-denied", "true");
      control.setAttribute("title", reason);
      return control;
    };

    const copy = button({
      label: "Chép tin nhắn",
      variant: "secondary",
      network: true,
      data: { reminderCopy: id },
      onClick: () => void copyText(),
    });
    if (refusal) refuse(copy, reminderRefusalText(refusal) || refusal);

    const zalo = item.zalo_url
      ? refusal
        ? refuse(
            button({ label: "Mở Zalo", variant: "secondary", data: { reminderZalo: id } }),
            reminderRefusalText(refusal) || refusal,
          )
        : h(
            "a",
            {
              class: ["button", "btn"],
              href: String(item.zalo_url),
              target: "_blank",
              rel: "noopener noreferrer",
              dataReminderZalo: id,
            },
            h("span", null, "Mở Zalo"),
          )
      : null;

    const call = tel
      ? h(
          "a",
          {
            class: ["button", "btn"],
            href: tel,
            dataReminderCall: id,
            // The dialler opens; *Đã nhắc* waits here for what came of the call.
            onClick: () => openDone("call"),
          },
          h("span", null, "Gọi"),
        )
      : null;

    const done = button({
      label: "Đã nhắc",
      variant: "primary",
      network: true,
      data: { reminderDone: id },
      onClick: () => openDone(refusal ? "call" : "message"),
    });

    async function copyText() {
      render(statusHost);
      copy.disabled = true;
      try {
        const message = await request(
          `/internal/v1/orders/${encodeURIComponent(id)}/pickup-reminder?step=${encodeURIComponent(String(item.step))}`,
        );
        const text = String(message?.text || "");
        const clipboard = navigator.clipboard;
        try {
          if (!clipboard || typeof clipboard.writeText !== "function") throw new Error("no clipboard");
          await clipboard.writeText(text);
          render(statusHost, h("p", { class: "hint", dataReminderCopied: id }, "Đã chép. Dán vào Zalo, gửi, rồi bấm “Đã nhắc”."));
          toast(`Đã chép tin nhắc · ${orderName(item)}`);
        } catch {
          // No clipboard (an insecure page, an old WebView): the whole text, selectable.
          render(
            statusHost,
            h("p", { class: "hint" }, "Chép không được — bôi đen đoạn dưới và tự chép."),
            h("textarea", {
              class: "reminders__text",
              readonly: true,
              rows: "6",
              value: text,
              "aria-label": "Tin nhắn nhắc khách",
              dataReminderText: id,
            }),
          );
        }
      } catch (error) {
        const code = codeOf(error);
        const words = reminderRefusalText(code);
        render(
          statusHost,
          words
            ? inlineAlert({ state: "warn", title: words })
            : errorNotice(/** @type {any} */ (error)),
        );
      } finally {
        if (copy.isConnected && !refusal) copy.disabled = false;
      }
    }

    /** @param {"message"|"call"} focus */
    function openDone(focus) {
      const made = doneSheet(item, title, refusal, focus, () => void load());
      render(sheetsHost, made.node);
      made.open();
    }

    const stepText = REMINDER_STEP_VI[item.step] || String(item.step);
    const remaining =
      item.remaining_vnd === null || item.remaining_vnd === undefined
        ? "Báo khi lấy"
        : item.remaining_vnd === 0
          ? "Đã trả đủ"
          : money(item.remaining_vnd);
    return h(
      "tr",
      { dataReminder: id, dataStep: String(item.step), dataReachable: String(item.reachable) },
      h(
        "td",
        { class: "reminders__order" },
        h("a", { href: `#/orders/${encodeURIComponent(id)}`, class: "reminders__title" }, title),
        h(
          "span",
          { class: "hint" },
          h("span", { dataField: "waiting" }, waitingText(item.days_waiting)),
          item.days_waiting === 0 ? null : ` · xong ${dateOnly(item.ready_at)}`,
          // DEC-050: the count leaves the held days out; said beside the ready day (P2).
          item.held_days
            ? h("span", { dataField: "held-days" }, ` · ${heldDaysText(item.held_days)}`)
            : null,
          item.reachable === "CHAT" ? " · khách nhắn qua kênh chat" : null,
        ),
      ),
      h(
        "td",
        { class: "reminders__step" },
        statusPill({
          state: item.step === "BEFORE_FEE" ? "warn" : "info",
          text: stepText,
          token: String(item.step),
        }),
      ),
      h("td", { class: "reminders__money numeric" }, remaining),
      h(
        "td",
        { class: "reminders__actions" },
        h(
          "div",
          { class: "reminders__buttons" },
          contactVerdict.allowed ? [zalo, copy, call, done] : gated(done, contactVerdict),
        ),
        rowReason ? h("p", { class: "hint reminders__reason", dataReminderReason: refusal }, rowReason) : null,
        statusHost,
      ),
    );
  }

  /**
   * Đã nhắc: one tap on what happened. `POST /orders/{id}/contact-attempts` with the step; no
   * `If-Match` (an attempt changes nothing on the order) and one `Idempotency-Key` per outcome, so a
   * resend after a timeout replays and a different answer is a new attempt.
   *
   * @param {any} item
   * @param {string} title
   * @param {string} refusal the row's `message_refusal`
   * @param {"message"|"call"} focus
   * @param {() => void} onRecorded
   */
  function doneSheet(item, title, refusal, focus, onRecorded) {
    const id = encodeURIComponent(String(item.order_id));
    const alertHost = h("div");
    /** @type {Record<string, Submission>} */
    const submissions = {};
    const buttons = OUTCOMES.map((choice, index) => {
      const control = button({
        label: choice.label,
        variant: index === 0 && !refusal ? "primary" : "secondary",
        block: true,
        network: true,
        data: { reminderOutcome: choice.id },
        onClick: () => void record(choice, control),
      });
      if (choice.message && refusal) {
        control.setAttribute("disabled", "");
        control.setAttribute("aria-disabled", "true");
        control.setAttribute("data-denied", "true");
      }
      return control;
    });

    /**
     * @param {typeof OUTCOMES[number]} choice
     * @param {HTMLButtonElement} control
     */
    async function record(choice, control) {
      submissions[choice.id] ||= new Submission(`reminder-${choice.id}`);
      render(alertHost);
      control.disabled = true;
      control.setAttribute("aria-busy", "true");
      try {
        await request(`/internal/v1/orders/${id}/contact-attempts`, {
          method: "POST",
          body: {
            channel: choice.channel,
            outcome: choice.outcome,
            note: null,
            reminder_step: String(item.step),
          },
          idempotencyKey: submissions[choice.id].key(),
        });
        made.close();
        toast(`Đã ghi nhắc · ${orderName(item)}`);
        onRecorded();
      } catch (error) {
        const code = codeOf(error);
        const words = reminderRefusalText(code);
        show(
          alertHost,
          words
            ? inlineAlert({ state: "warn", title: words })
            : errorNotice(/** @type {any} */ (error)),
        );
      } finally {
        if (control.isConnected) {
          control.disabled = false;
          control.removeAttribute("aria-busy");
        }
      }
    }

    const messageGroup = buttons.slice(0, 2);
    const callGroup = buttons.slice(2);
    const made = sheet({
      id: "reminder-done",
      title: "Đã nhắc",
      body: h(
        "div",
        { class: "stack" },
        h("p", { class: "hint" }, `${title} · ${REMINDER_STEP_VI[item.step] || item.step}`),
        focus === "call"
          ? [h("div", { class: "reminders__choices" }, callGroup), h("div", { class: "reminders__choices" }, messageGroup)]
          : [h("div", { class: "reminders__choices" }, messageGroup), h("div", { class: "reminders__choices" }, callGroup)],
        refusal && refusal !== "NO_CONTACT"
          ? h("p", { class: "hint" }, reminderRefusalText(refusal))
          : null,
        alertHost,
      ),
      onClose: () => made.node.remove(),
    });
    return made;
  }

  if (!readVerdict.allowed) {
    render(listHost, h("p", { class: "hint" }, readVerdict.reason));
  } else if (!store) {
    render(listHost, h("p", { class: "hint" }, "Chọn một cửa hàng để xem ai cần nhắc."));
  } else {
    void load();
  }

  return h(
    "section",
    { class: "screen reminders-screen" },
    page({
      title: "Nhắc khách lấy đồ",
      subtitle,
      info: infoButton(
        "Nhắc khách lấy đồ là gì?",
        h(
          "p",
          null,
          "Máy chủ chọn ai cần nhắc và nhắc gì: ngày đồ xong, ngày 3, ngày 7, ngày 14, và ngày cuối " +
            "trước khi tính phí lưu kho (khi chủ tiệm đã công bố phí). Mỗi đơn chỉ hiện lần nhắc mới " +
            "nhất; bấm “Đã nhắc” là xong lần đó. Từ ngày tính phí, đơn nằm ở “Đồ chờ lấy”.",
        ),
        h(
          "p",
          null,
          "Tin nhắn do máy chủ soạn sẵn theo mẫu cố định, không có tên hay số điện thoại của khách. " +
            "Phần mềm không tự gửi: bạn chép, dán vào Zalo hoặc SMS của tiệm, rồi bấm “Đã nhắc”. Mỗi " +
            "lần nhắc được ghi như một lần liên hệ, và được tính cho quy tắc thanh lý.",
        ),
        h(
          "p",
          { class: "hint" },
          "Chép tin chỉ được khi chủ tiệm đã công bố chính sách tin dịch vụ (DEC-033) — chủ tiệm bật " +
            "bằng lệnh công bố của mình — và khách chưa nhắn dừng nhận tin. Gọi điện luôn dùng được.",
        ),
      ),
      action: button({ label: "Tải lại", icon: "refresh", variant: "quiet", onClick: () => void load() }),
    }),
    shelfSwitch("/reminders"),
    noticeHost,
    listHost,
    sheetsHost,
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/reminders",
  title: "Nhắc khách lấy đồ",
  capability: "PICKUP_READ",
  needsStore: true,
  render: render_,
};
