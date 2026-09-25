/**
 * Báo cáo: the owner's numbers for a window of days (`REPORT-DASHBOARD-001`, `FR-RPT-001`/`-005`).
 *
 * One screen, three layers, in the V2 shape: pick a window (Hôm nay · 7 ngày · 30 ngày · Tháng
 * này · Tự chọn), read the tiles, then the days one by one. Every figure is the server's.
 *
 * What this screen will not do, and why each refusal outlives whoever wrote it:
 *
 *   - **No rate is computed here.** Each KPI arrives as `{numerator, denominator}` and the tile
 *     prints both — "2 / 3" — beside the one percentage `format.percent` formats from them. The
 *     screen never divides, never totals the days, and never compares two figures.
 *   - **No money is added here.** "Tiền đã thu" is the server's net of the two ledgers, with the
 *     drawer's direction as a word; the amounts in and out are shown as the server summed them.
 *   - **The on-time tile says what it is.** Per-order SLA rules are an undecided business
 *     question, so the figure is measured against the one stated internal mark the SLA board uses,
 *     its data quality is `RULE_ASSUMED`, and its ⓘ carries the board's own sentence verbatim.
 *   - **Margin is shown as not computed**, with the reason, rather than left out: cost is not
 *     captured anywhere (`SHOP-INSTRUMENT-001`), and a margin without cost would be the rest of the
 *     price called profit, which `FR-RPT-002` forbids.
 *
 * Windows are shop-local calendar days (`Asia/Ho_Chi_Minh`) whatever the phone's own time zone,
 * at most 92, and never past today. The screen checks that before asking, only to save a round
 * trip: the server refuses the same windows with a 422 and its refusal is what counts.
 *
 * @module screens/reports
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import {
  TIMEZONE,
  UNKNOWN,
  barWidth,
  businessDate,
  calendarDay,
  dateTime,
  integer,
  money,
  percent,
} from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { PRESETS, presetWindow, windowProblem } from "../core/reportWindow.js";
import { storeId } from "../core/session.js";
import { errorNotice, labelled, markUpdated } from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  list,
  listRow,
  moneyHero,
  page,
  section,
  segmented,
  skeletonRows,
  statusPill,
  techDetails,
} from "../ui/kit.js";
import { KIND_LABEL } from "./remedies.js";

/**
 * Each KPI in words: its tile label and its definition (tier 2, behind the tile's ⓘ). The
 * definition says what the numerator counts and what the denominator counts, because a fraction
 * whose two halves are not named is a number a reader will misread.
 */
const KPI = {
  ORDERS_CREATED: {
    label: "Đơn mới",
    definition:
      "Số đơn tạo trong khoảng ngày đã chọn, tính theo lúc tạo đơn (giờ Việt Nam). Đây là số " +
      "đếm, không có mẫu số.",
  },
  ORDERS_COMPLETED: {
    label: "Đơn hoàn tất",
    definition:
      "Tử số: số đơn chuyển sang hoàn tất trong khoảng ngày. Mẫu số: số đơn tạo trong cùng " +
      "khoảng. Một đơn tạo từ trước mà hoàn tất trong khoảng này vẫn được đếm ở tử số.",
  },
  ORDERS_CANCELLED: {
    label: "Đơn huỷ",
    definition:
      "Tử số: số đơn bị huỷ trong khoảng ngày, kể cả đơn không nhận đồ. Mẫu số: số đơn tạo " +
      "trong cùng khoảng.",
  },
  ON_TIME_INTERNAL: {
    label: "Đúng hẹn (nội bộ)",
    definition:
      "Tử số: đơn giặt xong trong khoảng ngày và xong trước mốc nội bộ. Mẫu số: mọi đơn giặt " +
      "xong trong khoảng ngày. Giặt xong là lúc sản xuất báo đồ sẵn sàng tại cửa hàng lần cuối; " +
      "đơn bị giặt lại được tính theo lần xong sau cùng.",
  },
  REWASH: {
    label: "Giặt lại",
    definition:
      "Tử số: đơn bị đưa từ sự cố quay lại một bước sớm hơn bước đang làm — tức là giặt lại — " +
      "trong khoảng ngày, dù ghi bằng bước Giặt lại hay bằng từng bước sản xuất. Mẫu số: đơn vào " +
      "bước kiểm tra đồ trong khoảng ngày. Sự cố rồi làm tiếp đúng bước cũ không tính là giặt lại.",
  },
  COMPLAINTS: {
    label: "Khiếu nại",
    definition:
      "Tử số: khiếu nại mở trong khoảng ngày, mọi loại. Mẫu số: đơn hoàn tất trong khoảng ngày.",
  },
  MONEY: {
    label: "Tiền đã thu",
    definition:
      "Tiền khách trả tại quầy trong khoảng ngày, trừ tiền đã hoàn lại cho khách trong khoảng " +
      "ngày. Mỗi khoản tính vào ngày tiền thật sự vào hoặc ra két, theo giờ Việt Nam — đúng quy " +
      "tắc của ô tiền trên màn Hôm nay. Máy chủ cộng từ sổ tất toán và sổ hoàn tiền; màn hình " +
      "này không tự cộng. Đây không phải doanh thu, cũng không phải lợi nhuận.",
  },
  REMEDIES_EXECUTED: {
    label: "Bồi hoàn đã chi",
    definition:
      "Số bồi hoàn đã thực hiện trong khoảng ngày, theo loại, và tổng giá trị khoản giảm trừ đã " +
      "cấp cho khách. Giặt lại miễn phí không có tiền nên không có giá trị.",
  },
};

/** Margin (`FR-RPT-002`): why it is not a number here. Tier 1 on its tile, the rest behind ⓘ. */
const MARGIN = {
  short: "Chưa tính được: chưa ghi chi phí.",
  why:
    "Biên lợi nhuận cần chi phí thật của từng đơn: phút máy, hoá chất, công người làm và chi phí " +
    "giao hàng. Hệ thống chưa ghi những thứ đó (SHOP-INSTRUMENT-001). Lấy tiền đã thu trừ đi một " +
    "con số đoán rồi gọi phần còn lại là lợi nhuận là điều FR-RPT-002 cấm, nên ô này để trống có " +
    "lý do.",
};

/**
 * The tier-2 body of a tile: the definition, and the data-quality status verbatim (gloss + token).
 *
 * @param {string} definition
 * @param {string} quality
 * @param {...unknown} extra
 * @returns {HTMLElement[]}
 */
function definitionBody(definition, quality, ...extra) {
  return [
    h("p", null, definition),
    ...extra,
    h(
      "p",
      { class: "hint" },
      "Chất lượng dữ liệu: ",
      h("strong", { dataQuality: String(quality || UNKNOWN) }, enumLabel(quality)),
    ),
  ];
}

/**
 * One KPI tile: the label, the big figure, the fraction beside it, an ⓘ with the definition.
 *
 * @param {object} spec
 * @param {string} spec.key
 * @param {string} spec.label
 * @param {string} spec.value the big figure, already formatted
 * @param {unknown} [spec.fraction] "2 / 3 đơn giặt xong"
 * @param {unknown} [spec.note] one tier-1 line (a status pill, a refusal reason)
 * @param {HTMLElement} [spec.info]
 * @param {string} [spec.state]
 * @returns {HTMLElement}
 */
function tile(spec) {
  return h(
    "article",
    { class: "kpi", dataKpi: spec.key, dataState: spec.state || null },
    h("div", { class: "kpi__head" }, h("span", { class: "kpi__label" }, spec.label), spec.info),
    h("p", { class: "kpi__value" }, spec.value),
    spec.fraction ? h("p", { class: "kpi__fraction" }, spec.fraction) : null,
    spec.note ? h("div", { class: "kpi__note" }, spec.note) : null,
  );
}

/**
 * A ratio tile: the percentage `format.percent` makes of the server's two integers, and the two
 * integers themselves right under it.
 *
 * @param {any} kpi
 * @param {string} denominatorWords what the denominator counts, e.g. "đơn tạo"
 * @param {unknown} [note]
 * @param {unknown[]} [extraInfo]
 * @returns {HTMLElement}
 */
function ratioTile(kpi, denominatorWords, note, extraInfo = []) {
  const words = KPI[kpi.key];
  const fraction = Number.isInteger(kpi.denominator)
    ? `${integer(kpi.numerator)} / ${integer(kpi.denominator)} ${denominatorWords}`
    : integer(kpi.numerator);
  return tile({
    key: kpi.key,
    label: words.label,
    value: percent(kpi.numerator, kpi.denominator),
    fraction: h("span", { dataFraction: `${kpi.numerator}/${kpi.denominator}` }, fraction),
    note,
    info: infoButton(
      `${words.label} tính thế nào?`,
      ...definitionBody(words.definition, kpi.data_quality, ...extraInfo),
    ),
  });
}

/**
 * "Tiền đã thu": the drawer's net over the window, and what went in and out beside it.
 *
 * @param {Record<string, any>} kpis
 * @returns {HTMLElement}
 */
function moneyTile(kpis) {
  const net = kpis.MONEY_NET;
  const collected = kpis.MONEY_COLLECTED;
  const refunded = kpis.MONEY_REFUNDED;
  const out = net?.direction === "OUT";
  const inAndOut = [
    `Thu ${money(collected?.numerator)} (${integer(collected?.entries)} đơn)`,
    refunded?.numerator ? `Hoàn ${money(refunded.numerator)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return h(
    "div",
    { class: "kpi kpi--hero", dataKpi: "MONEY_NET", dataDirection: String(net?.direction || "") },
    moneyHero({
      // A net that went OUT is said as a word, never as a minus sign (invariant 2).
      label: out ? "Két giảm (hoàn nhiều hơn thu)" : KPI.MONEY.label,
      amount: money(net?.numerator),
      state: out ? "warn" : "ok",
      caption: inAndOut,
      info: infoButton(
        "Tiền đã thu gồm những gì?",
        ...definitionBody(KPI.MONEY.definition, net?.data_quality),
      ),
    }),
  );
}

/**
 * "Bồi hoàn đã chi": the count, the credit value, and the kinds that happened.
 *
 * @param {any} kpi
 * @returns {HTMLElement}
 */
function remediesTile(kpi) {
  const kinds = Array.isArray(kpi.by_kind) ? kpi.by_kind.filter((entry) => entry.count > 0) : [];
  return tile({
    key: kpi.key,
    label: KPI.REMEDIES_EXECUTED.label,
    value: integer(kpi.numerator),
    fraction: kpi.numerator ? `Giá trị ${money(kpi.amount_vnd)}` : "Chưa có bồi hoàn nào",
    note: kinds.length
      ? h(
          "ul",
          { class: "kpi__kinds" },
          kinds.map((entry) =>
            h(
              "li",
              { title: entry.kind },
              `${KIND_LABEL[entry.kind] || entry.kind}: ${integer(entry.count)}`,
            ),
          ),
        )
      : null,
    info: infoButton(
      "Bồi hoàn đã chi tính thế nào?",
      ...definitionBody(KPI.REMEDIES_EXECUTED.definition, kpi.data_quality),
    ),
  });
}

/**
 * Margin, as a tile that says why it has no number.
 *
 * @param {any} margin the server's `{shown, reason_code, blocked_by}`
 * @returns {HTMLElement}
 */
function marginTile(margin) {
  return tile({
    key: "MARGIN",
    label: "Biên lợi nhuận",
    value: UNKNOWN,
    state: "muted",
    note: h(
      "p",
      { class: "hint", title: `${margin?.reason_code || ""} · ${margin?.blocked_by || ""}` },
      MARGIN.short,
    ),
    info: infoButton("Vì sao chưa có biên lợi nhuận?", h("p", null, MARGIN.why)),
  });
}

/**
 * The tiles for one summary, in the order an owner reads a day: money, volume, then quality.
 *
 * @param {any} summary
 * @returns {HTMLElement}
 */
function tiles(summary) {
  const kpis = Object.fromEntries(summary.kpis.map((kpi) => [kpi.key, kpi]));
  const onTime = kpis.ON_TIME_INTERNAL;
  return h(
    "div",
    { class: "stack" },
    moneyTile(kpis),
    h(
      "div",
      { class: "kpi-grid" },
      tile({
        key: "ORDERS_CREATED",
        label: KPI.ORDERS_CREATED.label,
        value: integer(kpis.ORDERS_CREATED.numerator),
        fraction: "đơn",
        info: infoButton(
          "Đơn mới tính thế nào?",
          ...definitionBody(KPI.ORDERS_CREATED.definition, kpis.ORDERS_CREATED.data_quality),
        ),
      }),
      ratioTile(kpis.ORDERS_COMPLETED, "đơn tạo"),
      ratioTile(kpis.ORDERS_CANCELLED, "đơn tạo"),
      ratioTile(
        onTime,
        "đơn giặt xong",
        // Tier 1: the one fact that changes how this number is read.
        statusPill({ text: "Theo mốc nội bộ", state: "warn", token: String(onTime.data_quality) }),
        [h("p", { class: "notice", dataState: "info" }, summary.sla_rule.notice_vi)],
      ),
      ratioTile(kpis.REWASH, "đơn vào kiểm tra"),
      ratioTile(kpis.COMPLAINTS, "đơn hoàn tất"),
      remediesTile(kpis.REMEDIES_EXECUTED),
      marginTile(summary.margin),
    ),
  );
}

/**
 * One day of the window as a list row: the day, its order count as a bar, and the drawer's net.
 *
 * @param {any} day
 * @param {number} widest the largest day's order count in this window, for the bar's length
 * @returns {HTMLElement}
 */
function dayRow(day, widest) {
  const kpis = Object.fromEntries(day.kpis.map((kpi) => [kpi.key, kpi]));
  const created = kpis.ORDERS_CREATED?.numerator;
  const net = kpis.MONEY_NET;
  const facts = [
    `${integer(created)} đơn mới`,
    kpis.ORDERS_COMPLETED?.numerator ? `${integer(kpis.ORDERS_COMPLETED.numerator)} hoàn tất` : null,
    kpis.REWASH?.numerator ? `${integer(kpis.REWASH.numerator)} giặt lại` : null,
    kpis.COMPLAINTS?.numerator ? `${integer(kpis.COMPLAINTS.numerator)} khiếu nại` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return listRow({
    title: calendarDay(day.date),
    meta: [
      h("span", null, facts),
      h(
        "span",
        { class: "report-bar", "aria-hidden": "true" },
        h("span", { class: "report-bar__fill", style: { width: barWidth(created, widest) } }),
      ),
    ],
    trailing: h(
      "span",
      { class: "report-day__money", dataDirection: String(net?.direction || "") },
      net?.direction === "OUT" ? `giảm ${money(net?.numerator)}` : money(net?.numerator),
    ),
    data: { reportDay: String(day.date) },
  });
}

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  const today = businessDate();
  const asked = {
    from: context?.query?.get("from") || "",
    to: context?.query?.get("to") || "",
  };
  let preset = asked.from && asked.to ? "custom" : "7d";

  const subtitle = h("span", { class: "report__window" });
  const tier1 = h("div");
  const tilesHost = h("div", { class: "stack" }, skeletonRows(4));
  const daysHost = h("div", { class: "stack stack--tight" });
  // "Từng ngày" exists only for a window of more than one day: one day is already the tiles.
  const daysSection = section({ title: "Từng ngày", card: false, children: daysHost });
  daysSection.hidden = true;
  const techHost = h("div");
  const stamp = h("span", { class: "updated", role: "status" });

  const fromInput = /** @type {HTMLInputElement} */ (
    h("input", { type: "date", max: today, value: asked.from })
  );
  const toInput = /** @type {HTMLInputElement} */ (
    h("input", { type: "date", max: today, value: asked.to || today })
  );
  const customProblem = h("div");
  const customHost = h(
    "div",
    { class: "report__custom", hidden: preset !== "custom" },
    h(
      "div",
      { class: "report__dates" },
      labelled({ id: "report-from", label: "Từ ngày", control: fromInput }),
      labelled({ id: "report-to", label: "Đến ngày", control: toInput }),
    ),
    customProblem,
    button({
      label: "Xem",
      variant: "primary",
      block: true,
      data: { reportApply: "true" },
      onClick: () => void applyCustom(),
    }),
  );

  /** Which read is the latest; an older one landing late must not paint over a newer one. */
  let generation = 0;

  /**
   * Read the summary and the days for one window, both at once.
   *
   * @param {{from: string, to: string}} window
   * @returns {Promise<void>}
   */
  async function load(window) {
    const mine = ++generation;
    subtitle.textContent =
      window.from === window.to
        ? calendarDay(window.from)
        : `${calendarDay(window.from, { weekday: false })} – ${calendarDay(window.to, { weekday: false })}`;
    render(tier1);
    render(tilesHost, skeletonRows(4));
    render(daysHost);
    daysSection.hidden = true;
    render(techHost);
    const query = new URLSearchParams({ from: window.from, to: window.to }).toString();
    try {
      const [summary, daily] = await Promise.all([
        request(`/internal/v1/stores/${encodeURIComponent(store)}/reports/summary?${query}`),
        request(`/internal/v1/stores/${encodeURIComponent(store)}/reports/daily?${query}`),
      ]);
      if (mine !== generation) return;
      if (!Array.isArray(summary?.kpis) || !Array.isArray(daily?.days)) {
        render(
          tilesHost,
          inlineAlert({
            state: "danger",
            title: "Máy chủ trả về hình dạng lạ cho báo cáo",
            body: "Không đọc được số liệu, và hiện số 0 ở đây sẽ là sai sự thật.",
          }),
        );
        return;
      }
      markUpdated(stamp);
      render(
        tier1,
        summary.window?.ends_today
          ? h(
              "p",
              { class: "hint report__partial" },
              "Hôm nay chưa hết ngày: số của hôm nay tính đến lúc đọc.",
            )
          : null,
      );
      render(tilesHost, tiles(summary));
      const days = [...daily.days].reverse();
      const widest = Math.max(
        0,
        ...daily.days.map(
          (day) => day.kpis.find((kpi) => kpi.key === "ORDERS_CREATED")?.numerator || 0,
        ),
      );
      daysSection.hidden = days.length < 2;
      render(
        daysHost,
        days.length > 1
          ? list(
              days.map((day) => dayRow(day, widest)),
              { label: "Từng ngày" },
            )
          : null,
      );
      render(
        techHost,
        techDetails([
          ["Truy vấn", summary.query_version],
          ["Khoảng ngày", `${summary.window.from_date} → ${summary.window.to_date}`],
          ["Số ngày", integer(summary.window.days)],
          ["Múi giờ", summary.window.business_timezone || TIMEZONE],
          ["Quy tắc đúng hẹn", `${summary.sla_rule.policy_id} (${summary.sla_rule.policy_type})`],
          ["Máy chủ đọc lúc", dateTime(summary.evaluated_at), { mono: false }],
        ]),
      );
    } catch (error) {
      if (mine !== generation) return;
      render(tilesHost, errorNotice(error, { onRetry: () => void load(window) }));
    }
  }

  /** The person's own window: checked here for the obvious refusals, then asked. */
  function applyCustom() {
    const problem = windowProblem(fromInput.value, toInput.value, today);
    render(
      customProblem,
      problem ? inlineAlert({ state: "warn", title: "Chưa xem được", body: problem }) : null,
    );
    if (!problem) void load({ from: fromInput.value, to: toInput.value });
  }

  const picker = segmented({
    label: "Khoảng ngày",
    options: PRESETS,
    value: preset,
    onChange: (value) => {
      preset = value;
      customHost.hidden = value !== "custom";
      render(customProblem);
      const window = presetWindow(value, today);
      if (window) void load(window);
    },
  });

  if (!store) {
    render(
      tilesHost,
      emptyState({ icon: "store", title: "Chưa chọn cửa hàng", body: "Chọn cửa hàng ở thanh trên." }),
    );
  } else if (preset === "custom") {
    applyCustom();
  } else {
    const window = presetWindow(preset, today);
    if (window) void load(window);
  }

  return h(
    "section",
    { class: "screen report" },
    page({
      title: "Báo cáo",
      subtitle,
      info: infoButton(
        "Các con số này từ đâu ra?",
        h(
          "p",
          null,
          "Mọi con số do máy chủ tính từ những gì đã ghi trong hệ thống, theo ngày giờ Việt Nam. " +
            "Màn hình này không tự tính tỉ lệ hay cộng tiền: mỗi ô ghi cả tử số và mẫu số, và " +
            "phần trăm chỉ là cách viết khác của hai số đó.",
        ),
      ),
      action: h(
        "div",
        { class: "report__refresh" },
        stamp,
        button({
          label: "Tải lại",
          icon: "refresh",
          variant: "quiet",
          onClick: () => {
            const window =
              preset === "custom"
                ? { from: fromInput.value, to: toInput.value }
                : presetWindow(preset, today);
            if (preset === "custom") applyCustom();
            else if (window) void load(window);
          },
        }),
      ),
    }),
    h("div", { class: "stack stack--tight" }, picker, customHost, tier1),
    tilesHost,
    daysSection,
    techHost,
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/reports",
  title: "Báo cáo",
  capability: "REPORTS_READ",
  needsStore: true,
  render: render_,
};
