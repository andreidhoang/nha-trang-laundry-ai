/**
 * "Tóm tắt cuối ngày": the owner's evening summary on Hôm nay (`DAILY-SUMMARY-001`, `DEC-039`).
 *
 * The server writes every sentence. `GET …/reports/daily-summary` returns the day's lines from a
 * versioned Python template over the report's own figures, the lines it had to leave out and why,
 * and `text` -- the lines joined one per row. This card prints the lines as text nodes, copies
 * `text` byte for byte, and hands the same `text` to the phone's share sheet (Zalo). It composes,
 * counts and formats nothing: no money, no number and no sentence is made here, and no model is
 * involved anywhere between the database and the owner's Zalo.
 *
 * When it loads: after 18:00 shop-local the card reads the summary as Hôm nay opens; before 18:00
 * it waits for "Xem tóm tắt", so a morning Hôm nay does not show a "so far" summary nobody asked
 * for. Either way the figures are the server's, "tính đến" the time the header names.
 *
 * Nothing is stored: the text lives in the page and, when the owner presses, on the clipboard.
 *
 * @module ui/dailySummary
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { shopHour } from "../core/format.js";
import { errorNotice } from "./components.js";
import { button, infoButton, skeletonRows, techDetails, toast } from "./kit.js";

/** The shop-local hour from which the card reads the summary by itself. */
export const EVENING_HOUR = 18;


/**
 * The ⓘ beside the title (tier 2): what the summary is, and what it is not.
 *
 * @returns {HTMLElement}
 */
export function dailySummaryInfo() {
  return infoButton(
    "Tóm tắt này do ai viết?",
    h(
      "p",
      null,
      "Một mẫu câu cố định, không phải AI: máy chủ điền số liệu trong ngày vào các câu viết sẵn. " +
        "Số liệu là số của Báo cáo — đơn nhận, đơn hoàn tất, tiền thu, đơn trễ hẹn, khiếu nại, " +
        "khoản chi — không có mô hình ngôn ngữ nào đọc hay viết lại chúng.",
    ),
    h(
      "p",
      null,
      "Dòng nào chưa có nguồn số liệu thì được bỏ ra và ghi lý do bên dưới; hệ thống không đoán " +
        "và không ghi số 0 thay cho số chưa biết. Trước 18:00, bấm “Xem tóm tắt” để xem số tính " +
        "đến lúc đó.",
    ),
    h(
      "p",
      null,
      "“Sao chép” rồi dán vào Zalo; “Chia sẻ” mở bảng chia sẻ của điện thoại để chọn Zalo. " +
        "Hệ thống không tự gửi tóm tắt đi đâu. Bản do AI viết lại vẫn chờ chủ tiệm quyết định " +
        "về nhà cung cấp AI (DEC-006).",
    ),
  );
}

/**
 * The card's body and its loader. `load(store)` decides by the hour whether to read now or to
 * offer the button; `load(store, {now: true})` reads regardless (the button, "Tải lại").
 *
 * @returns {{node: HTMLElement, load: (store: string, options?: {now?: boolean}) => void}}
 */
export function dailySummaryCard() {
  const host = h(
    "div",
    { class: "stack stack--tight daily-summary", dataDailySummary: "true" },
    skeletonRows(1),
  );
  let generation = 0;
  // Once the owner asked for the summary, a refresh of Hôm nay reads it again rather than folding
  // it back behind the button.
  let asked = false;

  /**
   * @param {string} store
   */
  function offer(store) {
    render(
      host,
      h(
        "p",
        { class: "hint", dataSummaryState: "waiting" },
        `Tự hiện sau ${EVENING_HOUR}:00. Xem ngay số tính đến bây giờ:`,
      ),
      h(
        "div",
        { class: "daily-summary__actions" },
        button({
          label: "Xem tóm tắt",
          network: true,
          data: { summaryLoad: "true" },
          onClick: () => void read(store),
        }),
      ),
    );
  }

  /**
   * @param {string} store
   */
  async function read(store) {
    asked = true;
    const mine = ++generation;
    render(host, skeletonRows(3));
    try {
      const summary = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/reports/daily-summary`,
      );
      if (mine !== generation) return;
      show(store, summary);
    } catch (error) {
      if (mine !== generation) return;
      render(host, errorNotice(error, { onRetry: () => void read(store) }));
    }
  }

  /**
   * @param {string} store
   * @param {any} summary
   */
  function show(store, summary) {
    const lines = Array.isArray(summary?.lines) ? summary.lines : [];
    const omitted = Array.isArray(summary?.omitted) ? summary.omitted : [];
    const text = typeof summary?.text === "string" ? summary.text : "";
    const status = h("p", { class: "hint", role: "status", dataSummaryStatus: "true" });
    const manual = h("div", { class: "daily-summary__manual" });
    const shareSupported = typeof navigator.share === "function";

    async function copy() {
      const clipboard = navigator.clipboard;
      try {
        if (!clipboard || typeof clipboard.writeText !== "function") throw new Error("no clipboard");
        await clipboard.writeText(text);
        render(manual);
        render(status, "Đã chép. Mở Zalo và dán.");
        toast("Đã chép tóm tắt");
      } catch {
        // No clipboard (an insecure page, an old WebView) or a refused one: the whole text,
        // selectable, so the owner can still copy it by hand.
        render(status, "Chép không được — bôi đen đoạn dưới và tự chép.");
        render(
          manual,
          h("textarea", {
            class: "daily-summary__text",
            readonly: true,
            rows: String(Math.min(lines.length + 1, 12)),
            value: text,
            "aria-label": "Tóm tắt cuối ngày",
          }),
        );
      }
    }

    async function share() {
      try {
        await navigator.share({ title: "Tóm tắt cuối ngày", text });
        render(status);
      } catch (error) {
        // Closing the share sheet is a choice, not a failure.
        if (/** @type {any} */ (error)?.name === "AbortError") return;
        render(status, "Máy này chưa chia sẻ được. Bấm “Sao chép” rồi dán vào Zalo.");
      }
    }

    render(
      host,
      h(
        "ol",
        { class: "daily-summary__lines", "aria-label": "Tóm tắt cuối ngày" },
        lines.map((line) =>
          h(
            "li",
            { class: "daily-summary__line", dataSummaryLine: String(line?.key ?? "") },
            String(line?.text ?? ""),
          ),
        ),
      ),
      omitted.length
        ? h(
            "div",
            { class: "daily-summary__omitted", dataSummaryOmitted: String(omitted.length) },
            h("p", { class: "daily-summary__omitted-title" }, "Chưa có trong tóm tắt:"),
            h(
              "ul",
              null,
              omitted.map((item) =>
                h("li", { dataOmitted: String(item?.key ?? "") }, String(item?.note ?? "")),
              ),
            ),
          )
        : null,
      h(
        "div",
        { class: "daily-summary__actions" },
        button({
          label: "Sao chép",
          variant: "primary",
          data: { summaryCopy: "true" },
          onClick: () => void copy(),
        }),
        shareSupported
          ? button({
              label: "Chia sẻ",
              data: { summaryShare: "true" },
              onClick: () => void share(),
            })
          : null,
        button({
          label: "Tải lại",
          icon: "refresh",
          variant: "quiet",
          network: true,
          data: { summaryReload: "true" },
          onClick: () => void read(store),
        }),
      ),
      status,
      manual,
      techDetails([
        ["Mẫu câu", summary?.template_version],
        ["Ngày", summary?.date],
        ["Đọc lúc", summary?.evaluated_at],
        ...(Array.isArray(summary?.sources)
          ? summary.sources.map((source) => [`Nguồn ${source?.key}`, source?.query_version])
          : []),
      ]),
    );
  }

  return {
    node: host,
    load(store, options = {}) {
      generation += 1;
      if (!store) {
        render(host, h("p", { class: "hint" }, "Chọn một cửa hàng để xem tóm tắt."));
      } else if (asked || options.now || shopHour() >= EVENING_HOUR) {
        void read(store);
      } else {
        offer(store);
      }
    },
  };
}
