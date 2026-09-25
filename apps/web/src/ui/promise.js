/**
 * Hẹn trả — the promised-ready time on an order (`PROMISE-001`, `DEC-037`).
 *
 * Every time and every state here is the server's: the promise is computed by the domain at Nhận
 * đồ in the shop's opening hours, the state (đúng tiến độ / sắp tới hẹn / trễ hẹn / xong đúng hẹn /
 * xong trễ hẹn) is decided by the server when the order is read, and what Nhận đồ would promise
 * with each choice is the server's preview (`GET /orders/{id}/promise`). This module formats them
 * and collects a person's choice; it computes no time.
 *
 * @module ui/promise
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, promiseTime, shopInstantFromInput, shopLocalInput } from "../core/format.js";
import { REASON_NOTE } from "../core/i18n.js";
import { choiceChips, inlineAlert, statusPill } from "./kit.js";

/** `PromiseState`, as the counter says it. */
export const PROMISE_STATE_VI = {
  ON_TRACK: "Đúng tiến độ",
  DUE_SOON: "Sắp tới hẹn",
  LATE: "Trễ hẹn",
  MET: "Xong đúng hẹn",
  MISSED: "Xong trễ hẹn",
};

const PROMISE_STATE_TONE = {
  ON_TRACK: "neutral",
  DUE_SOON: "warn",
  LATE: "danger",
  MET: "ok",
  MISSED: "danger",
};

/** `PromiseChoice`, as the chips read. */
export const PROMISE_CHOICE_VI = {
  H24: "24 giờ",
  H48: "48 giờ",
  EXPRESS_2H: "Gấp 2 giờ",
  CUSTOM: "Tự chọn giờ",
};

/** `PromiseChangeReason`, as the Hẹn lại chips read. */
export const PROMISE_REASON_VI = {
  CUSTOMER_REQUEST: "Khách xin đổi",
  EXTRA_TREATMENT: "Xử lý thêm",
  MACHINE_ISSUE: "Máy trục trặc",
  WORKLOAD: "Đông đơn",
  WEATHER_DRYING: "Trời ẩm",
  OTHER: "Khác",
};

/**
 * The state pill. On a list only the two states that change what someone does now are shown
 * (`urgentOnly`): late and due soon.
 *
 * @param {string|null|undefined} state
 * @param {{urgentOnly?: boolean}} [options]
 * @returns {HTMLElement|null}
 */
export function promisePill(state, options = {}) {
  if (!state || !Object.hasOwn(PROMISE_STATE_VI, state)) return null;
  if (options.urgentOnly && state !== "LATE" && state !== "DUE_SOON") return null;
  return statusPill({
    state: /** @type {any} */ (PROMISE_STATE_TONE[state]),
    text: PROMISE_STATE_VI[state],
    token: state,
  });
}

/**
 * "Hẹn 13:00 thứ Sáu 26/9" for a list row, or null for an order without a promise.
 *
 * @param {any} order an `OrderViewResponse`
 * @returns {HTMLElement|null}
 */
export function promiseMeta(order) {
  if (!order?.current_promise_at) return null;
  return h(
    "span",
    { class: "promise-meta", dataPromise: String(order.promise_state || "") },
    `Hẹn ${promiseTime(order.current_promise_at, { short: true })}`,
    " ",
    promisePill(order.promise_state, { urgentOnly: true }),
  );
}

/**
 * The Nhận đồ sheet's "Hẹn trả" block: the time pressing now would promise, the choices the server
 * offers (24/48 giờ for shoes, curtains, blankets; gấp 2 giờ for ordinary laundry; tự chọn giờ
 * always), and a date-time picker when a person must set it. Nothing is sent from here: the sheet
 * asks `extra()` for the fields to add to the `RECEIVE` step.
 *
 * @param {object} spec
 * @param {string} spec.orderId
 * @param {() => void} spec.onChange called whenever `ready()` may have changed
 * @returns {{node: HTMLElement, load: () => Promise<void>, ready: () => boolean, extra: () => Record<string, string>}}
 */
export function receivePromise(spec) {
  const id = encodeURIComponent(spec.orderId);
  const node = h("div", { class: "promise-pick", id: "receive-promise" }, h("p", { class: "hint" }, "Đang tính giờ hẹn trả…"));
  /** @type {any|null} */
  let options = null;
  let choice = "";
  const picker = /** @type {HTMLInputElement} */ (
    h("input", {
      type: "datetime-local",
      id: "receive-promise-at",
      step: "900",
      onInput: () => {
        drawLine();
        spec.onChange();
      },
    })
  );
  const line = h("p", { class: "promise-pick__line", id: "receive-promise-line" });

  function chosenTime() {
    if (!options) return "";
    if (choice === "CUSTOM") return shopInstantFromInput(picker.value);
    if (!choice) return String(options.default_promised_at || "");
    const found = (options.choices || []).find((item) => item.choice === choice);
    return found?.promised_at ? String(found.promised_at) : "";
  }

  function drawLine() {
    const at = chosenTime();
    render(
      line,
      h("span", { class: "promise-pick__label" }, "Hẹn trả: "),
      h("strong", null, at ? promiseTime(at) : choice === "CUSTOM" ? "chưa chọn" : UNKNOWN),
    );
  }

  async function load() {
    try {
      const found = await request(`/internal/v1/orders/${id}/promise`);
      if (!found?.policy_published || !found.options) {
        options = null;
        render(
          node,
          h(
            "p",
            { class: "hint", id: "receive-promise-none" },
            found?.policy_published
              ? "Đơn này đã có giờ hẹn."
              : "Chưa có giờ hẹn trả: chủ tiệm chưa công bố quy tắc hẹn trả. Phiếu sẽ ghi “Tiệm sẽ " +
                  "báo khi đồ sẵn sàng”.",
          ),
        );
        spec.onChange();
        return;
      }
      options = found.options;
      const offered = (options.choices || []).map((item) => String(item.choice));
      const required = options.requirement === "CUSTOM";
      choice = required ? "CUSTOM" : options.default_choice ? String(options.default_choice) : "";
      const notes = (options.reason_codes || [])
        .map((code) => REASON_NOTE[code])
        .filter(Boolean);
      const chips =
        required || offered.length === 0
          ? null
          : choiceChips({
              label: "Chọn giờ hẹn trả",
              name: "receive-promise-choice",
              options: [
                ...(options.requirement === "NONE"
                  ? [{ value: "", label: "Thường", title: "RULE" }]
                  : []),
                ...offered.map((value) => ({
                  value,
                  label: PROMISE_CHOICE_VI[value] || value,
                  title: value,
                })),
              ],
              onChange: (value) => {
                choice = value;
                show();
              },
            });
      if (chips) {
        const current = chips.querySelector(`input[value="${choice}"]`);
        if (current instanceof HTMLInputElement) current.checked = true;
      }
      const pickerRow = h(
        "label",
        { class: "promise-field promise-pick__custom", for: "receive-promise-at" },
        h("span", { class: "promise-field__label" }, "Ngày giờ trả (08:00–20:00)"),
        picker,
      );
      function show() {
        pickerRow.hidden = choice !== "CUSTOM";
        drawLine();
        spec.onChange();
      }
      render(
        node,
        line,
        required
          ? inlineAlert({
              state: "warn",
              title: "Bạn chọn ngày giờ trả cho đơn này.",
              body: notes.length ? notes.join(" ") : null,
            })
          : null,
        chips,
        pickerRow,
      );
      show();
    } catch {
      options = null;
      render(
        node,
        h(
          "p",
          { class: "hint", id: "receive-promise-error" },
          "Chưa đọc được giờ hẹn trả. Nhận đồ vẫn ghi được: máy chủ tự tính giờ hẹn, hoặc báo " +
            "nếu đơn cần bạn chọn giờ.",
        ),
      );
      spec.onChange();
    }
  }

  return {
    node,
    load,
    ready: () => !options || choice !== "CUSTOM" || Boolean(shopInstantFromInput(picker.value)),
    extra: () => {
      if (!options || !choice) return {};
      if (choice === "CUSTOM") {
        const at = shopInstantFromInput(picker.value);
        return at ? { promise_choice: "CUSTOM", custom_at: at } : {};
      }
      return { promise_choice: choice };
    },
  };
}

/**
 * The picker value a Hẹn lại sheet opens on: the current promise, in shop time.
 *
 * @param {any} order
 * @returns {string}
 */
export function currentPromiseInput(order) {
  return shopLocalInput(order?.current_promise_at);
}
