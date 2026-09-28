/**
 * The report's late-delivery tile (`LATE-CREDIT-002`, `DEC-042`, `report-v4`).
 *
 * Every figure is the server's: how many deliveries in the window were measured late, and of those
 * how many were the shop's fault (and credited, with the value PostgreSQL summed), not the shop's
 * fault, or not yet decided. Before the owner publishes the remedy policy the block is
 * `UNAVAILABLE` and the tile says so instead of printing zeros.
 *
 * @module ui/lateDelivery
 */

import { h } from "../core/dom.js";
import { integer, money } from "../core/format.js";
import { infoButton, statusPill } from "./kit.js";

/**
 * @param {any} block the summary's `late_deliveries`
 * @returns {HTMLElement|null}
 */
export function lateReportTile(block) {
  if (!block) return null;
  const available = block.status === "COMPLETE";
  const info = infoButton(
    "Giao trễ tính thế nào?",
    h(
      "p",
      null,
      "Các chuyến giao thành công trong khoảng ngày, của đơn có giờ hẹn. Máy chủ đo trễ từ giờ hẹn " +
        "đầu tiên (chỉ dời khi khách xin hẹn lại) tới lúc giao thành công; trễ quá mức chủ tiệm " +
        "công bố thì tính là giao trễ.",
    ),
    h(
      "p",
      null,
      "Lỗi của tiệm là số chuyến đã ghi lỗi của tiệm; “đã giảm” là khoản giảm trừ đã cấp và tổng " +
        "tiền của chúng. Chưa xử lý là chuyến trễ chưa ai chọn lỗi của tiệm hay không.",
    ),
  );
  if (!available) {
    return h(
      "article",
      { class: "kpi", dataKpi: "LATE_DELIVERIES", dataState: "unavailable" },
      h("div", { class: "kpi__head" }, h("span", { class: "kpi__label" }, "Giao trễ"), info),
      h("p", { class: "kpi__value" }, "—"),
      h(
        "p",
        { class: "kpi__fraction" },
        "Chưa đo: chủ tiệm chưa công bố mức bồi hoàn.",
      ),
    );
  }
  const credited = block.credited
    ? ` · đã giảm ${integer(block.credited)} (${money(block.credited_vnd)})`
    : "";
  return h(
    "article",
    { class: "kpi", dataKpi: "LATE_DELIVERIES" },
    h("div", { class: "kpi__head" }, h("span", { class: "kpi__label" }, "Giao trễ"), info),
    h("p", { class: "kpi__value", dataLateCount: String(block.late) }, integer(block.late)),
    h("p", { class: "kpi__fraction" }, `trên ${integer(block.measured)} chuyến giao có hẹn`),
    block.late
      ? h(
          "div",
          { class: "kpi__note" },
          h(
            "ul",
            { class: "kpi__kinds" },
            h("li", { dataLate: "store-fault" }, `Lỗi của tiệm: ${integer(block.store_fault)}${credited}`),
            h("li", { dataLate: "not-store-fault" }, `Không phải lỗi tiệm: ${integer(block.not_store_fault)}`),
            h(
              "li",
              { dataLate: "undecided" },
              block.undecided
                ? statusPill({
                    state: "warn",
                    text: `Chưa xử lý: ${integer(block.undecided)}`,
                    token: "UNDECIDED",
                  })
                : "Chưa xử lý: 0",
            ),
          ),
        )
      : null,
  );
}
