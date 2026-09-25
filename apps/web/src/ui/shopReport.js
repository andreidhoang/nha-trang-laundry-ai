/**
 * The report's shop-measurement part (`SHOP-CAPTURE-001`, `DEC-038`, query `report-v3`): wash
 * cycles and machines, delivery cost per delivered order, the month's spending by category, and
 * margin -- only when the month is complete.
 *
 * Every figure is the server's. Rates are `format.percent` of the two integers sent; the per-order
 * cost and the average minutes arrive computed (domain and PostgreSQL); spending totals are
 * PostgreSQL's sums. This module adds, subtracts and divides nothing.
 *
 * **Margin is never called profit** (`FR-RPT-002`). A month missing any of electricity, water,
 * chemicals, wages or rent shows "Chưa đủ số liệu" and names what is missing, with no figure at
 * all -- the server sends none. A complete month shows the amount left after the recorded
 * spending, said with a direction word rather than a minus sign.
 *
 * @module ui/shopReport
 */

import { h } from "../core/dom.js";
import { UNKNOWN, integer, money, percent } from "../core/format.js";
import { EXPENSE_CATEGORY_VI } from "../core/i18n.js";
import { infoButton, list, listRow, section, statusPill } from "./kit.js";

/** Tier 2 definitions, each behind its tile's ⓘ. */
const WORDS = {
  cycles:
    "Tử số: số mẻ giặt bắt đầu trong khoảng ngày mà quầy đã chọn máy khi bấm Bắt đầu giặt. Mẫu " +
    "số: mọi mẻ bắt đầu trong khoảng ngày, kể cả mẻ giặt lại. Mẻ bấm Bỏ qua được đếm là chưa ghi " +
    "máy. Công lao động theo phút không ghi, theo quyết định DEC-038: lương vào sổ thu chi.",
  trip:
    "Chỉ tính đơn đã giao tới khách trong khoảng ngày và mọi chuyến của đơn đó (lấy đồ, giao hụt, " +
    "giao đồ) đều đã ghi tiền. Máy chủ cộng tiền các chuyến rồi chia cho số đơn đó, làm tròn tới " +
    "đồng. Đơn còn chuyến chưa ghi tiền được đếm là đã giao nhưng không đưa vào bình quân.",
  machines:
    "Mỗi dòng là một máy: số mẻ đã xong (từ Bắt đầu giặt tới Giặt xong, kiểm tra đồ) bắt đầu trong " +
    "khoảng ngày, và thời gian trung bình một mẻ, làm tròn tới phút. Mẻ đang chạy chưa được tính.",
  margin:
    "Tiền thu trong tháng trừ tiền đã hoàn cho khách, trừ mọi khoản chi đã ghi trong sổ thu chi của " +
    "tháng đó. Chỉ hiện khi tháng đã có đủ điện, nước, hoá chất, lương và mặt bằng (DEC-038); thiếu " +
    "mục nào thì không có con số nào cả. Đây không phải lợi nhuận: chưa tính khấu hao máy, thuế hay " +
    "công của chủ tiệm (FR-RPT-002). Chi phí chuyến ghi trên đơn không bị trừ ở đây, vì xăng mua " +
    "theo bình cũng ghi trong sổ mục Xăng xe; tiền thuê xe ngoài hãy ghi vào sổ.",
};

/**
 * @param {string} month `YYYY-MM`
 * @returns {string}
 */
function monthLabel(month) {
  const [year, number] = String(month).split("-");
  return `Tháng ${Number.parseInt(number, 10)}/${year}`;
}

/**
 * A tile in the report's `kpi` markup.
 *
 * @param {object} spec
 * @param {string} spec.key
 * @param {string} spec.label
 * @param {string} spec.value
 * @param {unknown} [spec.fraction]
 * @param {unknown} [spec.note]
 * @param {HTMLElement} [spec.info]
 * @param {string} [spec.state]
 * @param {Record<string, string>} [spec.data]
 * @returns {HTMLElement}
 */
function tile(spec) {
  const props = { class: "kpi", dataKpi: spec.key, dataState: spec.state || null };
  for (const [key, value] of Object.entries(spec.data || {})) {
    props[`data${key[0].toUpperCase()}${key.slice(1)}`] = value;
  }
  return h(
    "article",
    props,
    h("div", { class: "kpi__head" }, h("span", { class: "kpi__label" }, spec.label), spec.info),
    h("p", { class: "kpi__value" }, spec.value),
    spec.fraction ? h("p", { class: "kpi__fraction" }, spec.fraction) : null,
    spec.note ? h("div", { class: "kpi__note" }, spec.note) : null,
  );
}

/**
 * The two capture tiles for the report's grid: cycles with a machine, and delivery cost per order.
 *
 * @param {any} capture a `ReportCaptureResponse`
 * @returns {HTMLElement[]}
 */
export function captureTiles(capture) {
  if (!capture) return [];
  return [
    tile({
      key: "CYCLES_CAPTURED",
      label: "Mẻ có ghi máy",
      value: percent(capture.cycles_captured, capture.cycles),
      fraction: h(
        "span",
        { dataFraction: `${capture.cycles_captured}/${capture.cycles}` },
        `${integer(capture.cycles_captured)} / ${integer(capture.cycles)} mẻ`,
      ),
      info: infoButton("Mẻ có ghi máy tính thế nào?", h("p", null, WORDS.cycles)),
    }),
    tile({
      key: "TRIP_COST_PER_ORDER",
      label: "Chi phí giao / đơn",
      value:
        capture.cost_per_delivered_order_vnd === null
          ? UNKNOWN
          : money(capture.cost_per_delivered_order_vnd),
      fraction: h(
        "span",
        { dataFraction: `${capture.costed_orders}/${capture.delivered_orders}` },
        `${integer(capture.costed_orders)} / ${integer(capture.delivered_orders)} đơn giao có đủ tiền chuyến`,
      ),
      info: infoButton("Chi phí giao mỗi đơn tính thế nào?", h("p", null, WORDS.trip)),
    }),
  ];
}

/**
 * Per machine: closed cycles and average minutes, as a list section. Null when nothing was timed.
 *
 * @param {any} capture
 * @returns {HTMLElement|null}
 */
export function machineSection(capture) {
  const machines = Array.isArray(capture?.machines) ? capture.machines : [];
  if (!machines.length) return null;
  return section({
    title: "Thời gian mỗi mẻ",
    card: false,
    info: infoButton("Thời gian mỗi mẻ tính thế nào?", h("p", null, WORDS.machines)),
    children: list(
      machines.map((machine) =>
        listRow({
          leading: "washer",
          title: String(machine.code),
          meta: `${machine.display_name} · ${integer(machine.closed_cycles)} mẻ đã xong`,
          trailing: h(
            "span",
            { dataAverageMinutes: String(machine.average_minutes) },
            `${integer(machine.average_minutes)} phút/mẻ`,
          ),
          data: { reportMachine: String(machine.code) },
        }),
      ),
      { label: "Thời gian mỗi mẻ" },
    ),
  });
}

/**
 * The margin tile for one month: the amount when complete, else "Chưa đủ số liệu" and what is
 * missing. Keeps `data-kpi="MARGIN"`, the tile the report has always had.
 *
 * @param {any} month a `ReportMonthResponse`
 * @returns {HTMLElement}
 */
export function marginTile(month) {
  const margin = month?.margin || {};
  const info = infoButton("Biên của tháng tính thế nào?", h("p", null, WORDS.margin));
  if (margin.status !== "COMPLETE") {
    const missing = Array.isArray(margin.missing) ? margin.missing : [];
    return tile({
      key: "MARGIN",
      label: `Biên ${monthLabel(month?.month).toLowerCase()}`,
      value: "Chưa đủ số liệu",
      state: "muted",
      data: { marginStatus: "INCOMPLETE", marginMissing: missing.join(" ") },
      note: [
        h(
          "p",
          { class: "hint", title: missing.join(", ") },
          `Sổ thu chi còn thiếu: ${missing
            .map((code) => EXPENSE_CATEGORY_VI[code] || code)
            .join(", ")
            .toLowerCase()}.`,
        ),
        h(
          "a",
          { href: `#/expenses?month=${encodeURIComponent(String(month?.month || ""))}` },
          "Mở sổ thu chi",
        ),
      ],
      info,
    });
  }
  const out = margin.direction === "OUT";
  return tile({
    key: "MARGIN",
    label: out ? "Chi nhiều hơn thu" : `Còn lại ${monthLabel(month.month).toLowerCase()}`,
    value: money(margin.amount_vnd),
    state: out ? "warn" : null,
    data: { marginStatus: "COMPLETE", marginDirection: String(margin.direction) },
    fraction: `Thu ${money(month.collected_vnd)} · chi ${money(month.spending_vnd)}`,
    note: month.in_progress
      ? statusPill({ state: "warn", text: "Tháng chưa hết", token: "IN_PROGRESS" })
      : null,
    info,
  });
}

/**
 * One month's spending by category, as the server summed it; the categories with nothing recorded
 * are left out of the list and named by the margin tile when they matter.
 *
 * @param {any} month a `ReportMonthResponse`
 * @returns {HTMLElement}
 */
export function monthSection(month) {
  const spending = (Array.isArray(month?.spending) ? month.spending : []).filter(
    (total) => total.entries > 0,
  );
  return section({
    title: `Chi ${monthLabel(month.month).toLowerCase()}${month.in_progress ? " (đến hôm nay)" : ""}`,
    card: false,
    children: h(
      "div",
      { class: "stack stack--tight", dataReportMonth: String(month.month) },
      spending.length
        ? list(
            [
              ...spending.map((total) =>
                listRow({
                  title: EXPENSE_CATEGORY_VI[total.category] || String(total.category),
                  meta: `${integer(total.entries)} dòng`,
                  trailing: h(
                    "span",
                    { class: "money", dataSpending: String(total.category) },
                    money(total.amount_vnd),
                  ),
                }),
              ),
              listRow({
                title: h("strong", null, "Tổng chi"),
                trailing: h(
                  "strong",
                  { class: "money", dataSpendingTotal: String(month.spending_vnd) },
                  money(month.spending_vnd),
                ),
              }),
            ],
            { label: `Chi ${monthLabel(month.month)}` },
          )
        : h("p", { class: "surface muted" }, "Sổ thu chi tháng này chưa có dòng nào."),
    ),
  });
}
