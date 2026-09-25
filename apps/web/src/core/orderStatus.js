/**
 * How an order *reads* to the person at the counter: one status word, where it is in its life, and
 * which list tab it belongs to. Presentation only (`CONSOLE-REDESIGN-002`, spec V2 §5.3/§5.4).
 *
 * An order has four independent axes on the server — commercial, intake, production, balance —
 * plus its delivery legs. A counter worker does not want four badges; they want "Đang giặt" or
 * "Sẵn sàng". This module is the one place that folds the axes into those words, and it is kept
 * deliberately small and deliberately *descriptive*:
 *
 *   - **It decides nothing.** Which step is legal next is the server's `next_steps`
 *     (ORDER-STEPS-001, computed by dry-running the domain state machines). Nothing here says what
 *     may happen; only how what did happen is worded. There is no transition table in this file,
 *     and one must never be added (`ENGINEERING_SPEC_V1.md:530`).
 *   - **An unrecognised state is shown, not absorbed.** A value this map does not know falls
 *     through to its raw gloss (`enumVi`), so a new server state looks unfamiliar instead of being
 *     silently filed under a plausible word.
 *   - **The raw axes stay reachable.** Every surface that shows the summary keeps the four tokens
 *     one tap away (the order page's "Chi tiết kỹ thuật"); the summary is never the only record.
 *
 * No money is touched here: amounts are formatted by `format.money()` at the call site.
 *
 * @module core/orderStatus
 */

import { enumVi } from "./i18n.js";
import { parseInstant } from "./format.js";

/**
 * The fulfilment modes whose laundry goes back to the customer by courier — the console's copy of
 * the server's `MODES_EXPECTING_RETURN`, used only to *word* a status ("Chờ giao" rather than
 * "Sẵn sàng"). It gates no control: which delivery steps are offered comes from `next_steps`.
 */
const RETURN_MODES = new Set(["PICKUP_AND_RETURN", "RETURN_ONLY"]);

/**
 * The list tab an order belongs to.
 *
 * @typedef {"active"|"ready"|"delivery"|"done"} OrderGroup
 */

/**
 * One human status for an order.
 *
 * @param {any} order an `OrderViewResponse`
 * @returns {{text: string, state: "ok"|"warn"|"danger"|"info"|"neutral", group: OrderGroup, token: string}}
 */
export function orderStatus(order) {
  const commercial = String(order?.commercial || "");
  const intake = String(order?.intake || "");
  const production = String(order?.production || "");
  const token = [commercial, intake, production, order?.balance].filter(Boolean).join(" · ");
  const delivery = RETURN_MODES.has(String(order?.fulfillment_mode || ""));
  /** @param {string} text @param {any} state @param {OrderGroup} group */
  const out = (text, state, group) => ({ text, state, group, token });

  if (commercial === "CANCELLED") return out("Đã huỷ", "neutral", "done");
  if (commercial === "COMPLETED") return out("Đã xong", "ok", "done");
  if (commercial === "CANCELLATION_REVIEW") return out("Đang xét huỷ", "warn", "active");
  if (commercial !== "ACTIVE" || intake !== "ACCEPTED") {
    return out(intake === "REJECTED" ? "Từ chối nhận đồ" : "Chờ nhận đồ", "info", "active");
  }
  switch (production) {
    case "NOT_STARTED":
    case "QUEUED":
      return out("Chờ giặt", "info", "active");
    case "IN_PROCESS":
      return out("Đang giặt", "info", "active");
    case "QUALITY_CHECK":
      return out("Đang kiểm tra", "info", "active");
    case "ON_HOLD":
      return out("Tạm dừng", "warn", "active");
    case "EXCEPTION":
      return out("Có sự cố", "danger", "active");
    case "READY_AT_STORE":
      return delivery ? out("Chờ giao", "info", "delivery") : out("Sẵn sàng", "ok", "ready");
    case "RELEASED":
      if (delivery && !order?.required_delivery_legs_succeeded) {
        return out("Đang giao", "info", "delivery");
      }
      if (order?.balance === "UNPAID") return out("Chờ thu tiền", "warn", "ready");
      if (!delivery && order?.self_collection_recorded === false) {
        return out("Chờ khách lấy", "ok", "ready");
      }
      return out("Chờ đóng đơn", "warn", "ready");
    default:
      return out(enumVi(production), "neutral", "active");
  }
}

/**
 * The order's life as four stops — Nhận đồ → Đang giặt → Sẵn sàng → Đã trả — for `kit.progress`.
 *
 * Returns null for a cancelled order: a tracker frozen half-way would read as "stuck", and the
 * status pill already says what happened.
 *
 * @param {any} order an `OrderViewResponse`
 * @returns {Array<{label: string, state: "done"|"current"|"todo"|"blocked"}>|null}
 */
export function orderProgress(order) {
  const commercial = String(order?.commercial || "");
  if (commercial === "CANCELLED") return null;
  const production = String(order?.production || "");
  const received =
    order?.intake === "ACCEPTED" &&
    ["ACTIVE", "CANCELLATION_REVIEW", "COMPLETED"].includes(commercial);
  const washed = ["READY_AT_STORE", "RELEASED"].includes(production);
  const released = production === "RELEASED";
  const closed = commercial === "COMPLETED";
  const interrupted = production === "ON_HOLD" || production === "EXCEPTION";
  const delivery = RETURN_MODES.has(String(order?.fulfillment_mode || ""));

  /** @type {Array<"done"|"current"|"todo"|"blocked">} */
  let states;
  if (closed) states = ["done", "done", "done", "done"];
  else if (!received) states = ["current", "todo", "todo", "todo"];
  else if (released) states = ["done", "done", "done", "current"];
  else if (washed) states = ["done", "done", "current", "todo"];
  else states = ["done", interrupted ? "blocked" : "current", "todo", "todo"];
  const labels = ["Nhận đồ", "Đang giặt", "Sẵn sàng", delivery ? "Đã giao" : "Đã trả"];
  return labels.map((label, index) => ({ label, state: states[index] }));
}

/**
 * Whether a list row belongs under a tab. `all` matches everything.
 *
 * @param {any} order
 * @param {string} group
 * @returns {boolean}
 */
export function inGroup(order, group) {
  if (!group || group === "all") return true;
  return orderStatus(order).group === group;
}

/**
 * The fulfilment mode as an icon name and a short word for a list row.
 *
 * @param {string|null|undefined} mode
 * @returns {{icon: string, text: string}}
 */
export function modeBadge(mode) {
  switch (mode) {
    case "PICKUP_AND_RETURN":
      return { icon: "truck", text: "Lấy & trả tận nơi" };
    case "PICKUP_ONLY":
      return { icon: "truck", text: "Lấy tận nơi" };
    case "RETURN_ONLY":
      return { icon: "truck", text: "Trả tận nơi" };
    case "SELF_DROP_SELF_COLLECT":
      return { icon: "store", text: "Tại quầy" };
    default:
      return { icon: "order", text: enumVi(mode) };
  }
}

/**
 * "vừa xong", "12 phút trước", "3 giờ trước", "2 ngày trước" — how long ago an instant was, in
 * whole units. Only for "how fresh is this row"; the exact time is always one tap away (the
 * caller puts it in `title`). `short` drops "trước" for a dense list row ("12 phút").
 *
 * @param {string|null|undefined} value
 * @param {{short?: boolean, now?: Date}} [options]
 * @returns {string}
 */
export function timeAgo(value, options = {}) {
  const at = parseInstant(value);
  if (!at) return "—";
  const now = options.now || new Date();
  const ago = options.short ? "" : " trước";
  const minutes = Math.floor((now.getTime() - at.getTime()) / 60000);
  if (minutes < 1) return "vừa xong";
  if (minutes < 60) return `${minutes} phút${ago}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ${ago}`;
  return `${Math.floor(hours / 24)} ngày${ago}`;
}
