/**
 * Đếm két: the opening float and the closing count of today's drawer (`CASH-COUNT-009`, `DEC-049`).
 *
 * The counter does two presses a day here -- "Ghi tiền đầu ngày" in the morning and "Ghi số đếm
 * cuối ngày" at closing -- and reads one answer: thừa, thiếu or khớp. What the screen will not do:
 *
 *   - **No money is computed here.** "Két phải có" is the server's: tiền đầu ngày, plus cash
 *     taken, less cash handed back, less "chi từ két" -- every part summed by PostgreSQL and the
 *     one subtraction done by the domain. The difference arrives as a size and a word; this screen prints the word, never a
 *     minus sign.
 *   - **Unknown is not zero.** With no float recorded the screen says the figure cannot be
 *     computed, rather than counting from 0; refunds recorded before the shop asked how money went
 *     back are left out and named (`DEC-048`).
 *   - **Nothing is edited.** Each figure is recorded once a day; a wrong one is corrected by a new
 *     entry that says why, and the first stays listed. A second phone that already recorded the
 *     figure turns this screen's press into a re-read, not a second entry.
 *   - **Nothing happens automatically** (`DEC-049`): no adjustment, no money moved, nobody blamed.
 *     The gap is recorded and the owner reads it on Báo cáo and in the evening summary.
 *
 * @module screens/cashCount
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { calendarDay, clock, money, parseDong } from "../core/format.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { comparisonSentence, differenceWords, expectedNote } from "../ui/cashCount.js";
import { errorNotice, gated, markUpdated } from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  keyValues,
  list,
  listRow,
  moneyHero,
  moneyInput,
  page,
  section,
  sheet,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";

/** The server's named refusals, in the counter's words. */
const REFUSAL_VI = {
  // Verification round 5 of 9b: this told the worker to reload and count today's drawer, which led
  // into the new day's form whose hint forbids last night's count there. It now gives the hint's own
  // instruction; `send` names the sheet's day in front of it.
  CASH_COUNT_DAY_NOT_TODAY: "đừng ghi lại vào sổ ngày mới: ghi ra giấy, đưa chủ tiệm.",
  CASH_COUNT_AMOUNT_INVALID: "Gõ số tiền đếm được, ví dụ 500000.",
  CASH_COUNT_AMOUNT_TOO_LARGE: "Số quá lớn (tối đa 1.000.000.000 ₫). Kiểm tra lại số vừa gõ.",
  CASH_COUNT_REASON_REQUIRED: "Ghi lý do sửa, ví dụ “đếm sót tờ 50.000”.",
  CASH_COUNT_REASON_INVALID: "Lý do dài tối đa 120 chữ.",
  CASH_COUNT_REASON_LOOKS_LIKE_PHONE:
    "Lý do không được chứa số điện thoại. Số tiền ghi có dấu chấm hoặc chữ k, ví dụ 1.250.000 - 50.000 hay 150k.",
  CASH_COUNT_REASON_NOT_EXPECTED: "Lần ghi đầu tiên không cần lý do.",
};

/** The 409s that mean "someone else recorded first": the sheet is re-read, nothing is lost. */
const MOVED = ["CASH_COUNT_ALREADY_RECORDED", "CASH_COUNT_STALE"];

/** Tier 2: the rule, verbatim from `DEC-049`, behind the ⓘ beside "Két phải có". */
const RULE =
  "Két phải có là tiền đầu ngày, cộng tiền mặt khách trả hôm nay, trừ tiền mặt hoàn lại khách " +
  "hôm nay, trừ các khoản trong Sổ thu chi đánh dấu “Trả từ két”. Chuyển khoản không nằm trong " +
  "két. Lần hoàn tiền ghi trước khi hệ thống hỏi cách hoàn không rõ là tiền mặt hay chuyển " +
  "khoản, nên không được tính và được nói riêng. Máy chủ tính mọi con số; màn hình này không tự " +
  "cộng trừ.";

/** Tier 2: what the count is for, and what it never does. */
const PURPOSE =
  "Đếm tiền trong két lúc mở cửa (trước khoản thu đầu tiên) và lúc đóng cửa, rồi ghi đúng số đếm " +
  "được. Hệ thống ghi phần thừa hoặc thiếu để chủ tiệm xem trong Báo cáo và tóm tắt cuối ngày. " +
  "Không có gì tự động: không tự điều chỉnh tiền, không trừ lương ai. Ghi nhầm thì bấm Sửa và " +
  "ghi lý do; số cũ vẫn được giữ để đối chiếu.";

/**
 * @param {unknown} error
 * @returns {string} the reason code the server named, or ""
 */
function codeOf(error) {
  const known = /** @type {any} */ (error);
  if (Array.isArray(known?.reasonCodes) && known.reasonCodes.length) {
    return String(known.reasonCodes[0]);
  }
  const detail = String(known?.detail || "");
  return MOVED.find((code) => detail.startsWith(code)) || "";
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const me = principal();
  const writeVerdict = can(me, "CASH_COUNT");
  const stamp = h("span", { class: "updated", role: "status" });
  const subtitle = h("span", { class: "cash__day" });
  const banner = h("div");
  const openingHost = h("div", { class: "stack stack--tight" }, skeletonRows(1));
  const expectedHost = h("div", { class: "stack stack--tight" }, skeletonRows(3));
  const closingHost = h("div", { class: "stack stack--tight" }, skeletonRows(1));
  const historyHost = h("div", { class: "stack stack--tight" });
  const techHost = h("div");
  const sheetsHost = h("div");

  /** @type {any} */
  let current = null;
  let generation = 0;

  /**
   * What the worker has typed and not recorded, carried across a re-read (pre-production review
   * 9): "Tải lại", a re-read when the tablet wakes, a re-read after someone else recorded first.
   * The session banner promises typed input stays on screen; on this screen it now does.
   *
   * @typedef {object} Carry
   * @property {string} opening the opening float's entry field
   * @property {string} closing the closing count's entry field
   * @property {{kind: string, amount: string, reason: string, entryId?: string}|null} correction
   *   an open "Sửa" (`entryId`: the entry it corrects, when the sheet is still open)
   * @property {boolean} [toToday] the opening amount goes onto a new day's sheet: the worker
   *   pressed "Mở sổ hôm nay" after it was refused for yesterday's
   */

  /** @returns {Carry} */
  function typedNow() {
    /**
     * @param {HTMLElement} host
     * @param {string} selector
     */
    const value = (host, selector) => {
      const input = host.querySelector(selector);
      return input instanceof HTMLInputElement ? input.value : "";
    };
    return {
      opening: value(openingHost, "#cash-float-amount"),
      closing: value(closingHost, "#cash-close-amount"),
      correction: correcting(),
    };
  }

  /** The fields a worker types into, by id. */
  const FIELDS = ["#cash-float-amount", "#cash-close-amount", "#cash-correct-amount", "#cash-correct-reason"];

  /**
   * The focused entry field and its caret, so a re-read that redraws the forms can put them back.
   *
   * @returns {{id: string, start: number|null, end: number|null}|null}
   */
  function caretNow() {
    const active = document.activeElement;
    if (!(active instanceof HTMLInputElement) || !active.id) return null;
    if (!FIELDS.includes(`#${active.id}`) || !active.isConnected) return null;
    return { id: active.id, start: active.selectionStart, end: active.selectionEnd };
  }

  /** @param {{id: string, start: number|null, end: number|null}|null} caret */
  function restoreCaret(caret) {
    if (!caret) return;
    const input = document.getElementById(caret.id);
    if (!(input instanceof HTMLInputElement) || input === document.activeElement) return;
    input.focus({ preventScroll: true });
    if (caret.start !== null && caret.end !== null) {
      try {
        input.setSelectionRange(caret.start, caret.end);
      } catch {
        // an input type without a caret: the focus is enough
      }
    }
  }

  /**
   * What is typed into the open "Sửa" sheet, and the entry it corrects; null when none is open.
   *
   * @returns {{kind: string, amount: string, reason: string, entryId: string}|null}
   */
  function correcting() {
    if (!open || !openFor || !open.node.isConnected || !open.node.open) return null;
    const amount = open.node.querySelector("#cash-correct-amount");
    const reason = open.node.querySelector("#cash-correct-reason");
    return {
      kind: String(openFor.kind),
      amount: amount instanceof HTMLInputElement ? amount.value : "",
      reason: reason instanceof HTMLInputElement ? reason.value : "",
      entryId: String(openFor.entry_id),
    };
  }

  /**
   * @param {string} [notice] one line kept above the sheet after a re-read
   * @param {Partial<Carry>} [override] what the caller knows better than the screen (the amount
   *   a refused press sent); everything else typed is read from the screen when the answer arrives
   */
  async function load(notice = "", override = {}) {
    const mine = ++generation;
    const before = current?.business_day;
    try {
      const sheetRead = await request(`/internal/v1/stores/${encodeURIComponent(store)}/cash-count`);
      if (mine !== generation) return;
      markUpdated(stamp);
      // Digits typed while this re-read was in flight are on screen now, not in a snapshot taken
      // before the await: read what is typed, and where the caret is, only now, just before the
      // sheet is drawn and the values are put back.
      const carry = { ...typedNow(), ...override };
      const caret = caretNow();
      const moved = before !== undefined && before !== sheetRead.business_day;
      // Pre-production review 9, round 2: a "Sửa" sheet left open across midnight corrects an
      // entry of the day it was opened on. It used to stay open over the new day's sheet and send
      // that day with the old entry -- the refusal then reopened it on today's figure, prefilled
      // with yesterday's. A new day closes it (what was typed is named below); on the same day it
      // stays exactly as it is while the entry it corrects is still the current one.
      if (moved && open) open.close();
      const still =
        !moved &&
        carry.correction?.entryId !== undefined &&
        carry.correction.entryId ===
          String(
            (carry.correction.kind === "OPENING_FLOAT"
              ? sheetRead.opening_float
              : sheetRead.closing_count
            )?.entry_id ?? "",
          );
      // A new day's sheet: last night's closing count and any correction are not carried onto it
      // (they belong to the day they were counted for); the opening float only when asked for.
      const kept = moved
        ? { opening: carry.toToday ? carry.opening : "", closing: "", correction: null }
        : still
          ? { ...carry, correction: null }
          : carry;
      const dropped =
        moved &&
        Boolean(
          carry.closing.trim() ||
            carry.correction?.amount.trim() ||
            carry.correction?.reason.trim() ||
            (!carry.toToday && carry.opening.trim()),
        );
      const words = [
        moved ? `Sổ đã sang ${calendarDay(sheetRead.business_day)}.` : "",
        moved && dropped
          ? `Số đang gõ cho ${calendarDay(before)} chưa được ghi và không chuyển sang sổ mới — số ` +
            "đếm cuối ngày hôm trước thì ghi ra giấy, đưa chủ tiệm."
          : "",
        // The entry the open "Sửa" corrected was itself corrected meanwhile: the sheet reopens on
        // the newer figure with what was typed, and says why.
        !moved && carry.correction?.entryId !== undefined && !still
          ? "Người khác vừa sửa số này. Kiểm tra số đang ghi rồi sửa nếu cần."
          : "",
        notice,
      ];
      draw(sheetRead, "");
      const lost = restore(sheetRead, kept);
      restoreCaret(caret);
      if (lost) words.push(`Số bạn đang gõ (${lost}) chưa được ghi.`);
      const said = words.filter(Boolean).join(" ");
      if (said) render(banner, inlineAlert({ state: "info", title: said }), ...bannerRest(sheetRead));
    } catch (error) {
      if (mine !== generation) return;
      const failed = errorNotice(error, { onRetry: () => void load() });
      if (current) {
        // The sheet already on show stays, with whatever was typed in it: only the re-read failed.
        render(banner, failed);
        return;
      }
      render(openingHost);
      render(closingHost);
      render(expectedHost, failed);
    }
  }

  /**
   * Put typed values back into the forms the new sheet still has.
   *
   * @param {any} day
   * @param {Carry} carry
   * @returns {string} what was typed for a form the sheet no longer has (recorded meanwhile), or ""
   */
  function restore(day, carry) {
    const lost = [];
    /**
     * @param {HTMLElement} host
     * @param {string} id
     * @param {string} text
     */
    const refill = (host, id, text) => {
      if (!text.trim()) return;
      const input = host.querySelector(id);
      if (!(input instanceof HTMLInputElement)) {
        lost.push(text.trim());
        return;
      }
      input.value = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    refill(openingHost, "#cash-float-amount", carry.opening);
    refill(closingHost, "#cash-close-amount", carry.closing);
    if (carry.correction) {
      const entry = carry.correction.kind === "OPENING_FLOAT" ? day.opening_float : day.closing_count;
      if (entry) openCorrection(entry, carry.correction);
      else if (carry.correction.amount.trim()) lost.push(carry.correction.amount.trim());
    }
    return lost.join(", ");
  }

  /**
   * @param {any} day a `CashCountDayResponse`
   * @param {string} notice
   */
  function draw(day, notice) {
    current = day;
    subtitle.textContent = calendarDay(day.business_day);
    render(
      banner,
      notice ? inlineAlert({ state: "info", title: notice }) : null,
      ...bannerRest(day),
    );
    drawOpening(day);
    drawExpected(day.expected);
    drawClosing(day);
    drawHistory(day.entries || []);
    render(
      techHost,
      techDetails([
        ["Quy tắc", day.query_version],
        ["Múi giờ", day.business_timezone],
        day.closing_count?.trace_hash ? ["Vết tính", day.closing_count.trace_hash] : null,
      ].filter(Boolean)),
    );
  }

  /**
   * The banner's standing line, under any one-off notice.
   *
   * @param {any} day
   * @returns {Array<HTMLElement|null>}
   */
  function bannerRest(day) {
    return [
      day.changed_since_count
        ? inlineAlert({
            state: "warn",
            title: "Sổ đã thay đổi sau lúc đếm",
            body:
              day.expected?.expected_vnd === null || day.expected?.expected_vnd === undefined
                ? "Số đếm đã ghi vẫn giữ nguyên. Đếm lại rồi bấm “Sửa số đếm” nếu cần."
                : `Bây giờ két phải có ${money(day.expected.expected_vnd)}. Số đếm đã ghi vẫn giữ ` +
                  "nguyên; đếm lại rồi bấm “Sửa số đếm” nếu cần.",
          })
        : null,
    ];
  }

  /** @param {any} day */
  function drawOpening(day) {
    const entry = day.opening_float;
    if (entry) {
      render(
        openingHost,
        keyValues([
          ["Đã đếm", h("span", { class: "money", dataCashFloat: "true" }, money(entry.counted_vnd))],
          ["Người ghi", entry.recorded_by_name || "—"],
          ["Lúc", clock(entry.recorded_at)],
        ]),
        gated(
          button({
            label: "Sửa tiền đầu ngày",
            variant: "quiet",
            data: { cashCorrect: "OPENING_FLOAT" },
            onClick: () => openCorrection(entry),
          }),
          writeVerdict,
        ),
      );
      return;
    }
    render(openingHost, entryForm("OPENING_FLOAT"));
  }

  /** @param {any} expected a `CashExpectedResponse` */
  function drawExpected(expected) {
    const note = expectedNote(expected);
    render(
      expectedHost,
      keyValues([
        ["Tiền đầu ngày", expected.float_vnd === null ? "Chưa ghi" : money(expected.float_vnd)],
        [`Thu tiền mặt (${expected.cash_in_count})`, money(expected.cash_in_vnd)],
        [`Hoàn tiền mặt (${expected.cash_refunded_count})`, money(expected.cash_refunded_vnd)],
        [`Chi từ két (${expected.drawer_expenses_count})`, money(expected.drawer_expenses_vnd)],
        [
          "Két phải có",
          h(
            "strong",
            { class: "money", dataCashExpected: String(expected.status) },
            expected.expected_vnd === null ? "Chưa tính được" : money(expected.expected_vnd),
          ),
        ],
      ]),
      note
        ? h(
            "p",
            {
              class: expected.status === "BOOKS_BELOW_ZERO" ? "notice" : "hint",
              dataState: expected.status === "BOOKS_BELOW_ZERO" ? "danger" : null,
              dataCashNote: String(expected.status),
            },
            note,
          )
        : null,
    );
  }

  /** @param {any} day */
  function drawClosing(day) {
    const entry = day.closing_count;
    if (!entry) {
      render(closingHost, entryForm("CLOSING_COUNT", day));
      return;
    }
    const words = differenceWords(entry);
    render(
      closingHost,
      moneyHero({
        label: "Đếm được",
        amount: money(entry.counted_vnd),
        state: words.state === "ok" ? "ok" : words.state === "warn" ? "warn" : "neutral",
        caption: h(
          "span",
          { dataCashDifference: words.token },
          statusPill({ state: words.state, text: words.text, token: words.token }),
          " ",
          comparisonSentence(entry),
        ),
      }),
      h(
        "p",
        { class: "hint" },
        `${entry.recorded_by_name || "—"} ghi lúc ${clock(entry.recorded_at)}.`,
      ),
      gated(
        button({
          label: "Sửa số đếm",
          variant: "quiet",
          data: { cashCorrect: "CLOSING_COUNT" },
          onClick: () => openCorrection(entry),
        }),
        writeVerdict,
      ),
    );
  }

  /** @param {any[]} entries */
  function drawHistory(entries) {
    const corrected = entries.some((entry) => entry.supersedes_id);
    render(
      historyHost,
      corrected
        ? list(
            entries.map((entry) =>
              listRow({
                title: `${entry.kind === "OPENING_FLOAT" ? "Đầu ngày" : "Cuối ngày"} · ${money(
                  entry.counted_vnd,
                )}`,
                meta: [
                  `${entry.recorded_by_name || "—"} · ${clock(entry.recorded_at)}`,
                  entry.correction_reason ? `Lý do sửa: ${entry.correction_reason}` : null,
                ]
                  .filter(Boolean)
                  .join(" · "),
                trailing: entry.superseded
                  ? statusPill({ state: "neutral", text: "Đã thay", token: "SUPERSEDED" })
                  : statusPill({ state: "ok", text: "Đang dùng", token: "CURRENT" }),
                data: { cashEntry: String(entry.entry_id) },
              }),
            ),
            { label: "Các lần ghi hôm nay" },
          )
        : null,
    );
    historySection.hidden = !corrected;
  }

  /**
   * The first entry of a kind: an amount and one press.
   *
   * @param {"OPENING_FLOAT"|"CLOSING_COUNT"} kind
   * @param {any} [day] the sheet the form records on (closing count only)
   * @returns {HTMLElement}
   */
  function entryForm(kind, day) {
    const opening = kind === "OPENING_FLOAT";
    const id = opening ? "cash-float" : "cash-close";
    const alertHost = h("div");
    const submission = new Submission(`cash-${kind.toLowerCase()}`);
    const amount = moneyInput({
      id: `${id}-amount`,
      label: opening ? "Tiền đầu ngày đếm được" : "Tiền cuối ngày đếm được",
      echo: (text) => {
        if (!text.trim()) return "";
        const parsed = parseDong(text);
        return parsed === null ? "Chưa đọc được số tiền" : `= ${money(parsed)}`;
      },
      onInput: () => submission.reset(),
    });
    const save = button({
      label: opening ? "Ghi tiền đầu ngày" : "Ghi số đếm cuối ngày",
      variant: "primary",
      block: true,
      network: true,
      id: `${id}-save`,
      onClick: () =>
        void send({ kind, amountText: amount.input.value, submission, alertHost, save }),
    });
    return h(
      "div",
      { class: "stack stack--tight cash__form" },
      h(
        "label",
        { class: "field-label", for: `${id}-amount` },
        opening ? "Đếm tiền trong két lúc mở cửa" : "Đếm tiền trong két lúc đóng cửa",
      ),
      amount.node,
      !opening && day && !day.opening_float ? dayWithoutFloatHint(day) : null,
      alertHost,
      gated(save, writeVerdict),
    );
  }

  /**
   * CASH-COUNT-009 (verification round 4): a sheet opened after midnight is the new day's, and
   * the server records a closing count on it without complaint -- it cannot know the drawer
   * counted is last night's. With no float on the sheet, the closing form names the day it records
   * on and says not to put yesterday's count there. Words only: nothing is refused here.
   *
   * @param {any} day
   * @returns {HTMLElement}
   */
  function dayWithoutFloatHint(day) {
    return h(
      "p",
      { class: "hint", dataCashDayHint: String(day.business_day) },
      `Số này ghi cho ${calendarDay(day.business_day)} — ngày này chưa ghi tiền đầu ngày. ` +
        "Sau 0 giờ là sổ của ngày mới: đừng ghi số đếm của hôm qua vào đây; ghi ra giấy, đưa chủ tiệm.",
    );
  }

  /**
   * Send one entry. The day is the one the sheet showed (the server's), never the device's.
   *
   * @param {object} spec
   * @param {"OPENING_FLOAT"|"CLOSING_COUNT"} spec.kind
   * @param {string} spec.amountText
   * @param {Submission} spec.submission
   * @param {HTMLElement} spec.alertHost
   * @param {HTMLButtonElement} spec.save
   * @param {any} [spec.supersedes] the entry corrected
   * @param {string} [spec.reason]
   * @param {() => void} [spec.done]
   */
  async function send(spec) {
    render(spec.alertHost);
    const parsed = parseDong(spec.amountText);
    if (parsed === null || !current) {
      render(
        spec.alertHost,
        inlineAlert({ state: "warn", title: "Chưa ghi được", body: REFUSAL_VI.CASH_COUNT_AMOUNT_INVALID }),
      );
      return;
    }
    if (spec.supersedes && !String(spec.reason || "").trim()) {
      render(
        spec.alertHost,
        inlineAlert({ state: "warn", title: "Chưa ghi được", body: REFUSAL_VI.CASH_COUNT_REASON_REQUIRED }),
      );
      return;
    }
    spec.save.disabled = true;
    try {
      const answer = await request(`/internal/v1/stores/${encodeURIComponent(store)}/cash-count`, {
        method: "POST",
        idempotencyKey: spec.submission.key(),
        body: {
          // A correction is for the day of the entry it corrects, which a re-read may have moved
          // the sheet past (pre-production review 9, round 2): the server then refuses it as not
          // today's, rather than taking it against today's figure.
          business_day: spec.supersedes ? spec.supersedes.business_day : current.business_day,
          kind: spec.kind,
          counted_vnd: parsed,
          ...(spec.supersedes
            ? { supersedes_entry_id: spec.supersedes.entry_id, reason: String(spec.reason).trim() }
            : {}),
        },
      });
      // Pre-production review 9, round 2: a re-read sent while this was in flight read the sheet
      // from before it -- its answer must not paint the empty form, with the amount just recorded
      // typed back into it, over the entry. This answer is the newest sheet there is.
      generation += 1;
      spec.submission.reset();
      spec.done?.();
      toast(
        spec.kind === "OPENING_FLOAT"
          ? `Đã ghi tiền đầu ngày · ${money(parsed)}`
          : `Đã ghi số đếm · ${differenceWords(answer.entry).text}`,
      );
      markUpdated(stamp);
      draw(answer, "");
    } catch (error) {
      const code = codeOf(error);
      // The day this entry was sent for: a correction's is its entry's.
      const day = spec.supersedes ? spec.supersedes.business_day : current.business_day;
      if (MOVED.includes(code)) {
        // Someone else recorded first: re-read, keep their figure, say so in one line. What this
        // worker typed is kept: a correction reopens on the newer entry with its amount and reason.
        /** @type {Partial<Carry>} */
        const override = spec.supersedes
          ? {
              correction: {
                kind: spec.kind,
                amount: spec.amountText,
                reason: String(spec.reason || ""),
              },
            }
          : {};
        spec.done?.();
        void load(
          "Người khác vừa ghi số này. Màn hình đã tải lại — kiểm tra rồi sửa nếu cần.",
          override,
        );
        return;
      }
      if (code === "CASH_COUNT_DAY_NOT_TODAY" && spec.kind === "OPENING_FLOAT") {
        // Pre-production review 9: the tablet kept yesterday's sheet overnight. A float counted
        // this morning belongs on today's sheet -- never on paper, which is the closing count's
        // rule -- so the way forward is offered, carrying the amount.
        render(
          spec.alertHost,
          inlineAlert({
            state: "warn",
            title: "Chưa ghi được",
            body:
              `Màn hình còn mở sổ ${calendarDay(day)}, đã qua ngày. Tiền đầu ` +
              "ngày ghi vào sổ hôm nay: bấm “Mở sổ hôm nay”, kiểm tra số rồi bấm “Ghi tiền đầu ngày”.",
            actions: button({
              label: "Mở sổ hôm nay",
              variant: "primary",
              icon: "refresh",
              data: { cashOpenToday: "true" },
              onClick: () => {
                spec.done?.();
                void load("", { opening: spec.amountText, toToday: true });
              },
            }),
          }),
        );
        return;
      }
      const known = REFUSAL_VI[/** @type {keyof typeof REFUSAL_VI} */ (code)];
      const words =
        code === "CASH_COUNT_DAY_NOT_TODAY"
          ? `Đã sang ngày mới nên số của ${calendarDay(day)} chưa được ghi — ${known}`
          : known;
      render(
        spec.alertHost,
        words
          ? inlineAlert({ state: "warn", title: "Chưa ghi được", body: words })
          : errorNotice(error),
      );
    } finally {
      if (spec.save.isConnected) spec.save.disabled = false;
    }
  }

  /** @type {ReturnType<typeof sheet>|null} */
  let open = null;
  /** @type {any} the entry the open "Sửa" sheet corrects */
  let openFor = null;

  /**
   * Sửa: a new entry superseding the current one, with a reason. The old figure stays listed.
   *
   * @param {any} entry
   * @param {{amount: string, reason: string}} [prefill] what was typed into an earlier sheet
   */
  function openCorrection(entry, prefill) {
    open?.close();
    const opening = entry.kind === "OPENING_FLOAT";
    const alertHost = h("div");
    const submission = new Submission(`cash-correct-${entry.kind.toLowerCase()}`);
    const amount = moneyInput({
      id: "cash-correct-amount",
      label: "Số đúng",
      echo: (text) => {
        if (!text.trim()) return "";
        const parsed = parseDong(text);
        return parsed === null ? "Chưa đọc được số tiền" : `= ${money(parsed)}`;
      },
      onInput: () => submission.reset(),
    });
    const reason = /** @type {HTMLInputElement} */ (
      h("input", {
        id: "cash-correct-reason",
        type: "text",
        maxlength: "120",
        autocomplete: "off",
        placeholder: "Ví dụ: đếm sót tờ 50.000",
        onInput: () => submission.reset(),
      })
    );
    const save = button({
      label: "Ghi số đã sửa",
      variant: "primary",
      block: true,
      network: true,
      id: "cash-correct-save",
      onClick: () =>
        void send({
          kind: entry.kind,
          amountText: amount.input.value,
          submission,
          alertHost,
          save,
          supersedes: entry,
          reason: reason.value,
          done: () => made.close(),
        }),
    });
    const made = sheet({
      id: "cash-correct",
      title: opening ? "Sửa tiền đầu ngày" : "Sửa số đếm cuối ngày",
      body: h(
        "div",
        { class: "stack" },
        h("p", { class: "hint" }, `Số đang ghi: ${money(entry.counted_vnd)}. Số cũ vẫn được giữ.`),
        h("label", { class: "field-label", for: "cash-correct-amount" }, "Số đúng"),
        amount.node,
        h("label", { class: "field-label", for: "cash-correct-reason" }, "Vì sao sửa?"),
        reason,
        alertHost,
      ),
      actions: gated(save, writeVerdict),
      onClose: () => made.node.remove(),
    });
    render(sheetsHost, made.node);
    open = made;
    openFor = entry;
    made.open();
    if (prefill) {
      amount.input.value = prefill.amount;
      amount.input.dispatchEvent(new Event("input", { bubbles: true }));
      reason.value = prefill.reason;
    }
  }

  if (!store) {
    render(
      expectedHost,
      emptyState({ icon: "store", title: "Chưa chọn cửa hàng", body: "Chọn cửa hàng ở thanh trên." }),
    );
    render(openingHost);
    render(closingHost);
  } else {
    void load();
  }

  const historySection = section({
    title: "Các lần ghi hôm nay",
    card: false,
    children: historyHost,
  });
  historySection.hidden = true;

  const root = h(
    "section",
    { class: "screen cash" },
    page({
      title: "Đếm két",
      subtitle,
      info: infoButton("Đếm két để làm gì?", h("p", null, PURPOSE)),
      back: { href: "#/", label: "Hôm nay" },
      action: h(
        "div",
        { class: "cash__refresh" },
        stamp,
        button({ label: "Tải lại", icon: "refresh", variant: "quiet", onClick: () => void load() }),
      ),
    }),
    banner,
    section({ title: "Tiền đầu ngày", children: openingHost }),
    section({
      title: "Két phải có",
      info: infoButton("Két phải có tính thế nào?", h("p", null, RULE)),
      children: expectedHost,
    }),
    section({ title: "Đếm cuối ngày", children: closingHost }),
    historySection,
    techHost,
    sheetsHost,
  );

  // Pre-production review 9: the counter tablet is left on this screen overnight. When it wakes,
  // the sheet is read again -- the day may have changed -- keeping whatever is typed (`load`).
  const onWake = () => {
    if (!root.isConnected) {
      document.removeEventListener("visibilitychange", onWake);
      return;
    }
    if (document.visibilityState === "visible" && store) void load();
  };
  document.addEventListener("visibilitychange", onWake);
  return root;
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/cash-count",
  title: "Đếm két",
  capability: "CASH_COUNT",
  needsStore: true,
  render: render_,
};
