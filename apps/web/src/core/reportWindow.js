/**
 * The report's windows of shop-local calendar days (`REPORT-DASHBOARD-001`). Pure: no DOM, no
 * clock — "today" is passed in — so each rule can be checked for a named day.
 *
 * The server is the authority on what a report answers (`validate_window`: at most 92 days, never
 * reversed, never past the shop's today) and refuses anything else with a 422. The console checks
 * the same three things first only so a person who picks a quarter and a day is told before a
 * round trip, in words.
 *
 * @module core/reportWindow
 */

import { addDays } from "./format.js";

/** The longest window the server answers (`REPORT_MAX_DAYS`). */
export const MAX_DAYS = 92;

/** The window choices, in the order the segmented control shows them. */
export const PRESETS = [
  { value: "today", label: "Hôm nay" },
  { value: "7d", label: "7 ngày" },
  { value: "30d", label: "30 ngày" },
  { value: "month", label: "Tháng này" },
  { value: "custom", label: "Tự chọn" },
];

/**
 * The calendar days a preset means, ending on the shop's today: "7 ngày" on 25/09 is 19/09–25/09
 * (seven days counting today), and "Tháng này" is the 1st to today.
 *
 * @param {string} preset
 * @param {string} today `YYYY-MM-DD`, the shop's calendar day
 * @returns {{from: string, to: string}|null} null for `custom`, which the person chooses
 */
export function presetWindow(preset, today) {
  if (preset === "today") return { from: today, to: today };
  if (preset === "7d") return { from: addDays(today, -6), to: today };
  if (preset === "30d") return { from: addDays(today, -29), to: today };
  if (preset === "month") return { from: `${today.slice(0, 8)}01`, to: today };
  return null;
}

/**
 * Why the server would refuse this window, in the counter's words — or "" when it would not.
 *
 * @param {string} from `YYYY-MM-DD`
 * @param {string} to `YYYY-MM-DD`
 * @param {string} today `YYYY-MM-DD`
 * @returns {string}
 */
export function windowProblem(from, to, today) {
  const shape = /^\d{4}-\d{2}-\d{2}$/;
  if (!shape.test(String(from)) || !shape.test(String(to))) {
    return "Chọn cả ngày bắt đầu và ngày kết thúc.";
  }
  if (from > to) return "Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.";
  if (to > today) return "Chưa tới ngày đó: báo cáo chỉ tính tới hôm nay.";
  if (addDays(from, MAX_DAYS - 1) < to) return `Tối đa ${MAX_DAYS} ngày một lần xem.`;
  return "";
}
