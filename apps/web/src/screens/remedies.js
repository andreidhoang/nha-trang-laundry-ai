/**
 * Bồi hoàn: what the shop owes a customer when something went wrong, and what it may not decide.
 *
 * `CONSOLE-REDESIGN-004` (spec V2 §5.6) moved the whole flow onto the complaint it answers: the
 * incident page (`#/incidents/:incidentId`, `screens/incidents.js`) renders `remedyFlow()` from this
 * module inline, under the customer's own words. Nobody types or pastes an incident id any more,
 * and the figures are read the moment the page opens — there is no "Đọc mức trần" press. The
 * `#/remedies` route stays, because approval cards and old links point at `?incident=…`: it
 * forwards such a link to the incident page, and without one lists the open complaints that carry
 * proposals.
 *
 * The flow lives here rather than in `incidents.js` on purpose: the three claim tables below
 * (`PLAN_NOTE`, `KIND_NOTE`, `OWNER_REASON_NOTE`) are read lexically by the disclosure registry
 * from this file (`scripts/console_disclosures.py` `CLAIM_TABLES`), and the approvals card imports
 * `KIND_LABEL` and `ownerReasonText` so the owner reads the same words the counter does.
 *
 * Five choices are deliberate rather than incidental, and each survived the rebuild unchanged:
 *
 *   - **The figures are read before anything is typed.** `GET …/remedy-options` is a pure read that
 *     reserves nothing. The one-line summary ("Tối đa X ₫ · còn Y · cần chủ tiệm duyệt nếu trên Z")
 *     and the facts under it sit above every number box in the DOM, not beside it and not after it:
 *     a staff member who learns after filling in a form that the owner has to approve it has
 *     already told a customer something the shop cannot do.
 *   - **`LOST_ITEM` always ends at the owner** (`DEC-031`), and the form says so from the first
 *     render, before a line or an amount exists.
 *   - **No ceiling is ever typed, and none is ever truncated to.** An item total above the cap is
 *     refused here with the cap named, exactly as the server refuses it with
 *     `REMEDY_CEILING_EXCEEDED` — never quietly reduced to it.
 *   - **A credit is a bearer instrument.** The execution result prints the code in full with a copy
 *     control and the "give it to the customer" fact beside it; a lost code is found again on the
 *     order that issued it.
 *   - **The incident's proposals are the server's list, not this session's**, and what may be done
 *     with each row is the server's `next_step`. A proposal is never carried out from the answer to
 *     the proposal itself: after proposing, the list is re-read and the row offers "Thực hiện bồi
 *     hoàn" only when the server says `EXECUTE` — so a claim the owner approved yesterday on another
 *     device is paid from the same row, by whoever is at the counter.
 *
 * The credit-redemption panel ("Dùng một khoản giảm trừ cho hoá đơn lần sau") left this screen
 * with this slice: spending a credit belongs to the bill it is spent on, so `CONSOLE-REDESIGN-001`
 * puts it on the quote receipt in `#/new`, where the revision and digest are already in hand.
 *
 * @module screens/remedies
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, ago, countdown, dateTime, money, quantity, shortId } from "../core/format.js";
import { REASON_NOTE, enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import {
  PLAN,
  REMEDY_KIND,
  REMEDY_KIND_ORDER,
  awaitsOwner,
  damageLines,
  remedyPlan,
  remedyProposalBody,
  remedyWindow,
} from "../core/remedies.js";
import { replace } from "../core/router.js";
import { principal, storeId } from "../core/session.js";
import {
  copyable,
  errorNotice,
  gated,
  gatedFields,
  labelled,
  listView,
  resultLine,
  setResult,
} from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  keyValues,
  linkButton,
  list,
  listRow,
  page,
  section,
  segmented,
  show,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";

/**
 * `RemedyKind` in the counter's words. The token is always shown beside it, never instead.
 * Exported for the approvals card (`REMEDY-OWNER-DECIDE-001`), so the owner reads the same words.
 */
export const KIND_LABEL = {
  FREE_REWASH: "Giặt lại miễn phí",
  DAMAGE_COMPENSATION: "Bồi thường món bị hỏng",
  LATE_DELIVERY_CREDIT: "Giảm trừ do giao trễ",
  LOST_ITEM: "Mất đồ",
};

/** What each kind is, in one line, so the picker is not four English tokens. */
const KIND_NOTE = {
  FREE_REWASH:
    "Giặt lại không thu thêm tiền. Không có trần vì không có đồng nào chuyển đi, và không cần " +
    "chủ tiệm duyệt. Cửa sổ 7 ngày tính từ lúc khách nhận đồ.",
  DAMAGE_COMPENSATION:
    "Đền tiền cho một món tiệm làm hỏng. Trần là 5 lần phí giặt của món đó: đồ tính theo cái thì " +
    "lấy giá một cái, đồ tính theo ký thì lấy tiền cả túi. Cửa sổ 24 giờ tính từ lúc khách nhận đồ.",
  LATE_DELIVERY_CREDIT:
    "Giảm trừ 10% vào hoá đơn lần sau khi chuyến giao trễ do lỗi tiệm. Máy chủ tự tính 10% từ " +
    "tổng đã tất toán; nhân viên không gõ số tiền, chỉ khai trễ bao nhiêu phút.",
  LOST_ITEM:
    "Đền tiền cho một món tiệm làm mất. Trần như hàng hỏng (5 lần phí giặt món đó), hạn 24 giờ từ " +
    "lúc khách nhận đồ. Nhân viên ghi và đề nghị số tiền; luôn phải chờ chủ tiệm duyệt.",
};

/** `Unit`, as the counter says it next to a quantity. */
const UNIT_LABEL = {
  KG: "kg",
  ITEM: "cái",
  PAIR: "đôi",
  SET: "bộ",
  ANIMAL_PLUSH_ITEM: "con",
  CASE: "lần",
  M2: "m²",
};

/**
 * Why the owner is needed, in the counter's words. `OwnerReason`, one sentence each, so the person
 * at the counter can tell the customer why they have to wait rather than only that they do.
 */
const OWNER_REASON_NOTE = {
  LOSS_CLAIM: "mất đồ luôn do chủ tiệm duyệt, dù số tiền nhỏ",
  ORDER_REFUNDED: "đơn này đã hoàn tiền, nên mọi khoản đền đều do chủ tiệm duyệt",
  ITEM_FEE_NOT_RECORDED:
    "bản giá không ghi giá của từng món trên dòng này, nên chủ tiệm quyết mọi số tiền",
  // Per item: the garment on a line priced per piece (REMEDY-GARMENT-001), the bag otherwise.
  ABOVE_STAFF_LIMIT: "tổng đền cho món này (hoặc cả túi, nếu tính theo ký) vượt mức nhân viên được duyệt",
};

/**
 * Exported for the approvals card, which tells the owner why a claim reached them in these words.
 *
 * @param {string[]|null|undefined} reasons
 * @returns {string}
 */
export function ownerReasonText(reasons) {
  const said = (reasons || []).map((reason) => OWNER_REASON_NOTE[reason] || reason);
  return said.join("; ");
}

/** `ItemFeeBasis` in the counter's words: what the 5× was taken of. */
const FEE_BASIS_LABEL = {
  UNIT: "giá một món",
  BAG: "tiền cả túi (tính theo ký)",
  NOT_RECORDED: "không ghi giá từng món — chủ tiệm quyết",
};

/**
 * The one sentence that explains a plan state to the person at the counter.
 *
 * Keyed by `PLAN`, not by the server's reason code: these are the console's own reading of figures
 * the server already sent, before anything is submitted. The server's own refusal, when one comes
 * back, is rendered verbatim by `errorNotice` with its code and its `REASON_NOTE` gloss.
 */
const PLAN_NOTE = {
  POLICY_UNPUBLISHED:
    "Chưa có bản chính sách bồi hoàn nào được công bố, nên không có con số nào để áp dụng và " +
    "không được suy ra con số nào. Mọi loại bồi hoàn đều bị từ chối, kể cả loại không chuyển tiền: " +
    "cửa sổ 7 ngày cũng là một con số đã công bố. Báo chủ tiệm công bố chính sách.",
  WINDOW_EVIDENCE_MISSING:
    "Tiệm không có bản ghi nào cho biết khách đã nhận đồ lúc nào, nên không đo được cửa sổ thời " +
    "gian nào cả. Đây không phải lỗi máy: đơn này được trả trước khi hệ thống bắt đầu ghi mốc " +
    "giao đồ. Lấy ngày hôm nay hay ngày tạo đơn thay vào là bịa ra một phép đo không ai làm.",
  WINDOW_CLOSED:
    "Cửa sổ chủ tiệm công bố cho loại này đã đóng. Nói với khách đúng mốc đã qua chứ không chỉ nói " +
    "là hết hạn. Muốn làm ngoài cửa sổ thì phải hỏi chủ tiệm, không phải gõ lại ở đây.",
  CREDIT_UNAVAILABLE:
    "Máy chủ không tính được khoản giảm trừ cho đơn này: hoặc đơn không có chuyến giao nào đã " +
    "giao thành công, hoặc chưa có tổng nào được tất toán để lấy 10%. Không có tổng thì không có " +
    "10% — và ô này để trống chứ không hiện 0.",
  LINE_NOT_CHOSEN:
    "Trần 5 lần thuộc về một món trên đơn, không thuộc về cả đơn. Chọn đúng món thì mới thấy trần " +
    "của nó và số đã ghi cho nó.",
  GARMENT_NOT_CHOSEN:
    "Dòng này có nhiều món tính giá theo cái. Mỗi món có mức nhân viên duyệt và trần 5 lần riêng, " +
    "nên phải chọn “Món thứ mấy” thì mới biết món đó đã ghi bao nhiêu và còn được bao nhiêu.",
  AMOUNT_MISSING: "Chưa đủ dữ kiện để gửi. Điền nốt ô còn trống.",
  NOT_AN_AMOUNT:
    "Số tiền phải là số nguyên đồng. “150.000” đọc là 150000; không nhận dấu phẩy và không nhận " +
    "số lẻ. Chưa có gì được gửi đi.",
  ABOVE_CEILING:
    "Số này vượt trần một món, hoặc cộng với số đã ghi cho món này hay cho cả dòng thì vượt trần. Máy chủ " +
    "từ chối và không tự hạ xuống bằng trần — hạ xuống là trả cho khách ít hơn con số bạn vừa thoả " +
    "thuận với họ.",
  BELOW_LATENESS_THRESHOLD:
    "Số phút khai chưa vượt ngưỡng chủ tiệm công bố, nên đây chưa phải chuyến giao trễ theo " +
    "chính sách. Không có gì được ghi.",
  FAULT_NOT_ATTESTED:
    "Mọi khoản bồi hoàn trong DEC-004 đều dựa trên việc một nhân viên xác định lỗi thuộc về tiệm. " +
    "Chưa ai xác định, nên chưa gửi được. Đây là lời khai của bạn, không phải ô bấm cho xong.",
};

/** Plan states that stop the form: shown as a visible refusal, never behind an ⓘ. */
const BLOCKING = new Set([
  PLAN.POLICY_UNPUBLISHED,
  PLAN.WINDOW_EVIDENCE_MISSING,
  PLAN.WINDOW_CLOSED,
  PLAN.CREDIT_UNAVAILABLE,
  PLAN.ABOVE_CEILING,
  PLAN.NOT_AN_AMOUNT,
]);

/**
 * Plan states the form itself already asks for — the empty money box, the unticked attestation —
 * so no separate line repeats them above the button. Pressing anyway still answers with the note.
 */
const SELF_EVIDENT = new Set([PLAN.AMOUNT_MISSING, PLAN.FAULT_NOT_ATTESTED]);

/** Plan states about the typed amount: said under the money box, where the eyes are. */
const AMOUNT_STATES = new Set([PLAN.ABOVE_CEILING, PLAN.NOT_AN_AMOUNT]);

/**
 * The number of minutes the shop treats as "more than two hours", as published.
 *
 * Never hardcoded: it arrives on `RemedyOptionsResponse.late_delivery_threshold_minutes`, and a
 * null is rendered as unknown rather than as 120 — the whole reason `DEC-004` is configuration and
 * not a constant is that the owner can move it without a deploy.
 *
 * @param {number|null|undefined} minutes
 * @returns {string}
 */
function minutesLabel(minutes) {
  return Number.isInteger(minutes) ? `${minutes} phút` : UNKNOWN;
}

/**
 * @param {ReturnType<typeof remedyPlan>} plan
 * @returns {string}
 */
function planTitle(plan) {
  if (plan.state === PLAN.ABOVE_CEILING && plan.ceilingBreached === "LINE") {
    return (
      `Vượt trần cả dòng: tối đa ${money(plan.lineCeilingVnd)}, ` +
      `đã ghi ${money(plan.lineCommittedVnd)}`
    );
  }
  if (plan.state === PLAN.ABOVE_CEILING && plan.ceilingBreached === "GARMENT") {
    return (
      `Vượt trần món thứ ${plan.garmentIndex}: tối đa ${money(plan.ceilingVnd)}, ` +
      `đã ghi ${money(plan.committedVnd)}`
    );
  }
  if (plan.state === PLAN.ABOVE_CEILING) {
    return `Vượt trần một món: mỗi đề nghị tối đa ${money(plan.ceilingVnd)}`;
  }
  if (plan.state === PLAN.WINDOW_CLOSED) {
    return `Đã quá hạn: cửa sổ đóng lúc ${dateTime(plan.windowClosesAt)}`;
  }
  if (plan.state === PLAN.POLICY_UNPUBLISHED) return "Chưa công bố chính sách bồi hoàn";
  if (plan.state === PLAN.WINDOW_EVIDENCE_MISSING) {
    return "Không biết khách nhận đồ lúc nào";
  }
  if (plan.state === PLAN.CREDIT_UNAVAILABLE) return "Không có khoản giảm trừ để đề nghị";
  if (plan.state === PLAN.LINE_NOT_CHOSEN) {
    return plan.kind === REMEDY_KIND.LOST_ITEM ? "Chưa chọn món bị mất" : "Chưa chọn món bị hỏng";
  }
  if (plan.state === PLAN.GARMENT_NOT_CHOSEN) return "Chưa chọn món thứ mấy trên dòng này";
  if (plan.state === PLAN.NOT_AN_AMOUNT) return "Chưa đọc được số tiền";
  if (plan.state === PLAN.BELOW_LATENESS_THRESHOLD) return "Chưa tới ngưỡng giao trễ";
  if (plan.state === PLAN.FAULT_NOT_ATTESTED) return "Chưa xác định lỗi thuộc về tiệm";
  return "Còn thiếu dữ kiện";
}

/**
 * What the plan says, at the weight it deserves: a blocking state is a visible refusal with its
 * reason (tier 1); a "what is still missing" state is one line with its explanation behind ⓘ.
 *
 * @param {ReturnType<typeof remedyPlan>} plan
 * @returns {HTMLElement|null}
 */
function planNotice(plan) {
  const note = PLAN_NOTE[plan.state];
  if (!note) return null;
  const title = planTitle(plan);
  if (BLOCKING.has(plan.state)) {
    return h(
      "div",
      { dataPlan: plan.state },
      inlineAlert({ state: "danger", title, body: h("p", null, note) }),
    );
  }
  return h(
    "p",
    { class: "fact-line remedy-plan", dataPlan: plan.state },
    h("span", { class: "remedy-plan__text" }, title),
    infoButton(title, h("p", null, note)),
  );
}

/**
 * The line every kind starts with: the ceiling, the time left and whether the owner is needed —
 * "Tối đa X ₫ · còn Y · cần chủ tiệm duyệt nếu trên Z". Composed from the server's figures only.
 *
 * @param {ReturnType<typeof remedyPlan>} plan
 * @param {any} options
 * @returns {string}
 */
function summaryLine(plan, options) {
  const window = remedyWindow(plan.kind, options);
  const parts = [];
  if (plan.kind === REMEDY_KIND.FREE_REWASH) parts.push("Không có trần — không chuyển tiền");
  else if (plan.kind === REMEDY_KIND.LATE_DELIVERY_CREDIT) {
    parts.push(
      Number.isInteger(options?.late_delivery_credit_vnd)
        ? `Giảm trừ ${money(options.late_delivery_credit_vnd)} do máy chủ tính`
        : "Chưa có khoản giảm trừ nào tính được",
    );
    parts.push(`trễ trên ${minutesLabel(options?.late_delivery_threshold_minutes)}`);
  } else if (plan.hasCeiling) {
    parts.push(`Tối đa ${money(plan.ceilingVnd)}`);
  } else {
    parts.push("Chọn món để thấy trần");
  }
  if (window !== null) {
    parts.push(
      !options?.goods_returned_at
        ? "không rõ khách nhận đồ lúc nào"
        : window.open
          ? countdown(window.closesAt).text
          : "đã quá hạn",
    );
  }
  const threshold = plan.ownerThresholdVnd;
  if (plan.requiresOwner === true) parts.push("luôn cần chủ tiệm duyệt");
  else if (plan.ownerPossible && threshold !== null) {
    parts.push(`cần chủ tiệm duyệt nếu trên ${money(threshold)}`);
  } else if (plan.kind === REMEDY_KIND.FREE_REWASH || plan.requiresOwner === false) {
    parts.push("không cần chủ tiệm duyệt");
  }
  return parts.join(" · ");
}

/**
 * The block that must be on screen before a single digit is typed: the summary line, then the
 * server's figures for the kind chosen (ceiling, what the item already carries, the window, the
 * owner, the handover it is measured from).
 *
 * @param {ReturnType<typeof remedyPlan>} plan
 * @param {any} options
 * @returns {HTMLElement}
 */
function ceilingSummary(plan, options) {
  const window = remedyWindow(plan.kind, options);
  const threshold = plan.ownerThresholdVnd;

  // Several pieces on one line: one item's ceiling per claim, and the line's for all of them.
  const ceilingCell = plan.hasCeiling
    ? Number.isInteger(plan.pieces) && plan.pieces > 1
      ? h(
          "span",
          null,
          h("span", { class: "money" }, money(plan.ceilingVnd)),
          " mỗi món",
          h("span", { class: "kv__note" }, `cả dòng ${plan.pieces} món tối đa ${money(plan.lineCeilingVnd)}`),
        )
      : h("span", { class: "money" }, money(plan.ceilingVnd))
    : plan.kind === REMEDY_KIND.FREE_REWASH
      ? h("span", null, "Không có trần — không có đồng nào chuyển đi")
      : h(
          "span",
          null,
          `${UNKNOWN} `,
          h("span", { class: "hint" }, "Máy chủ chưa tính được trần cho lựa chọn hiện tại."),
        );

  const windowCell =
    window === null
      ? h(
          "span",
          null,
          `${UNKNOWN} `,
          h(
            "span",
            { class: "hint" },
            "Loại này không đo theo cửa sổ thời gian. DEC-004 xét theo mức trễ và lỗi thuộc về " +
              `ai, ngưỡng đã công bố là ${minutesLabel(options?.late_delivery_threshold_minutes)}.`,
          ),
        )
      : h(
          "span",
          null,
          h("strong", null, window.open ? "còn hạn" : "đã đóng"),
          h("span", { class: "kv__note" }, `tới ${dateTime(window.closesAt)}`),
        );

  // The server's own reasons, predicted from the per-item terms it sent. A loss, a refunded order
  // or an item with no recorded fee needs the owner at any amount; otherwise it is the item's total
  // against the staff limit, never the typed amount alone.
  const ownerCell = plan.requiresOwner
    ? h(
        "span",
        null,
        h("strong", null, "Có — phải có chủ tiệm duyệt trước khi thực hiện"),
        plan.ownerReasons.length
          ? h("span", { class: "kv__note" }, `${ownerReasonText(plan.ownerReasons)}.`)
          : null,
      )
    : plan.ownerPossible
      ? h(
          "span",
          null,
          "Tuỳ số tiền",
          h(
            "span",
            { class: "kv__note" },
            Number.isInteger(plan.garments) && plan.garments > 1
              ? `trên ${money(threshold)} cho món này, tính cả số đã ghi`
              : `trên ${money(threshold)} cho cả dòng, tính cả số đã ghi`,
          ),
        )
      : threshold === null
        ? h("span", null, UNKNOWN)
        : h("span", null, `Không (nhân viên duyệt tới ${money(threshold)})`);

  const feeCell =
    plan.itemFeeVnd === null
      ? null
      : h(
          "span",
          null,
          h("span", { class: "money" }, money(plan.itemFeeVnd)),
          ` · ${FEE_BASIS_LABEL[plan.itemFeeBasis] || plan.itemFeeBasis || UNKNOWN}`,
        );

  const blocked = BLOCKING.has(plan.state) && !AMOUNT_STATES.has(plan.state);
  return h(
    "div",
    { class: "remedy-summary", id: "remedy-summary", dataState: blocked ? "danger" : null },
    h(
      "p",
      { class: "remedy-summary__line" },
      h("span", null, summaryLine(plan, options)),
      infoButton(
        "Các con số này ở đâu ra?",
        h(
          "p",
          { class: "hint" },
          "Mọi con số ở đây do máy chủ tính từ dữ liệu đã lưu của chính đơn này: 5 lần phí giặt của " +
            "món (giá một cái, hoặc tiền cả túi nếu tính theo ký), 10% lấy từ tổng đã tất toán, cửa sổ " +
            "đo từ mốc khách nhận đồ. Màn hình này không tính tiền và không được gõ trần.",
        ),
        h(
          "p",
          { class: "hint" },
          "Đọc mức trần không ghi gì và không giữ chỗ gì. Mở lại trang bất cứ lúc nào để xem số mới nhất.",
        ),
      ),
    ),
    keyValues([
      feeCell ? ["Phí giặt tính trần", feeCell] : null,
      plan.kind === REMEDY_KIND.FREE_REWASH ? null : ["Trần máy chủ tính", ceilingCell],
      // REMEDY-GARMENT-001: on a line of several garments the item is the garment, so what it
      // already carries is shown for that garment, beside the line's total.
      ...(plan.committedVnd === null
        ? []
        : Number.isInteger(plan.garments) && plan.garments > 1
          ? [
              [`Món thứ ${plan.garmentIndex} đã ghi đền`, money(plan.committedVnd)],
              ["Cả dòng đã ghi đền", money(plan.lineCommittedVnd)],
            ]
          : [["Dòng này đã ghi đền", money(plan.committedVnd)]]),
      ["Cửa sổ thời gian", windowCell],
      ["Cần chủ tiệm duyệt?", ownerCell],
      ["Khách nhận đồ lúc", dateTime(options?.goods_returned_at)],
    ]),
  );
}

/**
 * The one sentence a loss needs before anything else: it waits for the owner, whatever the figure.
 *
 * @returns {HTMLElement}
 */
function lossNotice() {
  return inlineAlert({
    state: "warn",
    title: "Mất đồ — luôn chờ chủ tiệm duyệt",
    body: h(
      "p",
      { class: "fact-line" },
      h("span", null, "Nói trước với khách là phải chờ, đừng hứa số nào như đã chốt."),
      infoButton(
        "Vì sao mất đồ luôn chờ chủ tiệm?",
        h(
          "p",
          { class: "hint" },
          "Nhân viên ghi lại và đề nghị số tiền; chỉ chủ tiệm duyệt mới được trả, kể cả số nhỏ. Nói " +
            "trước với khách là phải chờ, đừng hứa con số nào như đã chốt.",
        ),
      ),
    ),
  });
}

/**
 * A refunded order: compensation is still possible, and every amount goes to the owner.
 *
 * @returns {HTMLElement}
 */
function refundedNotice() {
  return inlineAlert({
    state: "warn",
    title: "Đơn đã hoàn tiền — mọi khoản đền đều chờ chủ tiệm duyệt",
    body: h(
      "p",
      { class: "hint" },
      "Tiền dịch vụ đã trả lại khách, nhưng món bị hỏng hay mất vẫn được đền, trần tính theo giá " +
        "đã báo. Hoàn tiền và đền trên cùng một đơn thì chủ tiệm duyệt, để không trả hai lần.",
    ),
  });
}

/**
 * What the server answered to a proposal, above the list it now sits in.
 *
 * The row in the list below is where the proposal is acted on; this says what was recorded and,
 * for an owner-bound claim, why it waits and where the owner decides it.
 *
 * @param {any} result a `RemedyProposalResponse`
 * @returns {HTMLElement}
 */
function proposalAlert(result) {
  const owner = awaitsOwner(result.status);
  return inlineAlert({
    state: owner ? "warn" : "ok",
    title: owner
      ? `Đã ghi · Chờ chủ tiệm duyệt — ${KIND_LABEL[result.kind] || result.kind}`
      : `Đã ghi đề nghị — ${KIND_LABEL[result.kind] || result.kind}`,
    body: h(
      "div",
      { class: "stack stack--tight" },
      result.replayed
        ? h(
            "p",
            { class: "hint" },
            "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
              "đề nghị mới nào được tạo.",
          )
        : null,
      Number.isInteger(result.amount_vnd)
        ? h("p", null, "Số tiền: ", h("strong", { class: "money" }, money(result.amount_vnd)))
        : null,
      owner
        ? h(
            "p",
            { class: "fact-line" },
            h(
              "span",
              null,
              result.owner_reasons?.length
                ? `Lý do: ${ownerReasonText(result.owner_reasons)}.`
                : "Chưa được thực hiện cho tới khi chủ tiệm duyệt.",
            ),
            infoButton(
              "Chờ chủ tiệm duyệt — chưa được thực hiện",
              h(
                "p",
                { class: "hint" },
                "Máy chủ đã lập phiếu duyệt và dừng lại. Mở màn hình Duyệt để chủ tiệm quyết; bấm " +
                  "thực hiện trước khi có quyết định thì máy chủ từ chối.",
              ),
            ),
          )
        : h("p", null, "Bấm “Thực hiện bồi hoàn” trên dòng của nó ở danh sách bên dưới."),
      result.reason_code
        ? h(
            "p",
            null,
            h("strong", null, "Máy chủ ghi nhận nhưng không định giá: "),
            REASON_NOTE[result.reason_code] || result.reason_code,
          )
        : null,
      techDetails([
        ["Mã đề nghị", shortId(result.proposal_id), { copy: String(result.proposal_id || "") }],
        ["Loại", String(result.kind || UNKNOWN)],
        ["Trạng thái", String(result.status || UNKNOWN)],
        ["Kết luận", String(result.outcome || UNKNOWN)],
        result.reason_code ? ["Mã lý do", String(result.reason_code)] : null,
        ["Trần máy chủ tính", money(result.ceiling_vnd), { mono: false }],
        Number.isInteger(result.garment_index) ? ["Món thứ", String(result.garment_index)] : null,
        ["Cửa sổ đóng lúc", dateTime(result.window_closes_at), { mono: false }],
        result.approval_id
          ? ["Phiếu duyệt", shortId(result.approval_id), { copy: String(result.approval_id) }]
          : null,
        ["Phiên bản chính sách", String(result.policy_version ?? UNKNOWN)],
        ["Dấu vân đề nghị", String(result.proposal_hash || UNKNOWN)],
      ]),
    ),
    actions: owner ? linkButton({ href: "#/approvals", label: "Mở màn hình Duyệt", variant: "quiet" }) : null,
  });
}

/**
 * What a carried-out remedy leaves behind, and the code the customer will need next time.
 *
 * The code is the one value the customer walks away with, so it is the largest thing on the card,
 * in full, with a copy control — and the sentence that says to hand it over sits beside it (tier 1).
 * Whether this payment closed the complaint is not this card's to say: an incident with several
 * claims is paid one row at a time, and the page re-reads the incident's status after every press.
 *
 * @param {any} result a `RemedyExecutionResponse`
 * @returns {HTMLElement}
 */
function executionCard(result) {
  return h(
    "div",
    { class: "stack" },
    inlineAlert({
      state: "ok",
      title: `Đã thực hiện · ${KIND_LABEL[result.kind] || result.kind}`,
      body: Number.isInteger(result.amount_vnd)
        ? h("p", null, "Số tiền: ", h("strong", { class: "money" }, money(result.amount_vnd)))
        : null,
    }),
    result.replayed
      ? h(
          "p",
          { class: "hint" },
          "Kết quả được phát lại: cùng khoá thao tác đã gửi trước đó. Không có gì được làm hai lần.",
        )
      : null,
    result.credit_id
      ? h(
          "div",
          { class: "credit-code", dataCreditCode: String(result.credit_id) },
          h("p", { class: "credit-code__label" }, "Mã giảm trừ — đưa cho khách"),
          copyable({ value: String(result.credit_id) }),
          h(
            "p",
            { class: "fact-line" },
            h(
              "span",
              { class: "hint credit-code__fact" },
              "Chép mã giảm trừ này lại ngay, vào phiếu của khách: ai cầm mã thì dùng được, đúng một lần.",
            ),
            infoButton(
              "Khách làm mất mã thì sao?",
              h(
                "p",
                { class: "hint" },
                "Khoản giảm trừ là phiếu cầm tay: ai cầm mã thì dùng được, và dùng đúng một lần. Chép " +
                  "vào phiếu giấy của khách trước khi rời màn hình. Khách làm mất mã thì tìm lại đơn " +
                  "này theo số phiếu ở màn hình Đơn hàng: mã nằm ở mục “Khoản giảm trừ của đơn này”.",
              ),
            ),
          ),
        )
      : h(
          "p",
          { class: "hint" },
          "Giặt lại không sinh khoản giảm trừ nào: không có đồng nào chuyển đi. Máy chủ ghi lệnh " +
            "giặt lại; việc giặt lại là việc mới ở xưởng, đơn cũ không quay lại dây " +
            "chuyền.",
        ),
    techDetails([
      ["Mã đề nghị", shortId(result.proposal_id), { copy: String(result.proposal_id || "") }],
      ["Loại", String(result.kind || UNKNOWN)],
      ["Trạng thái", String(result.status || UNKNOWN)],
      ["Sự kiện đã ghi", String(result.event_type || UNKNOWN)],
    ]),
  );
}

/**
 * The remedy flow for one incident, rendered inline on the incident page.
 *
 * Reads the options and the recorded proposals as soon as it is built; nothing waits for a press.
 * Every write re-reads what it changed: a proposal re-reads the options (what each item already
 * carries has moved) and the list; an execution re-reads the list, the options and — through
 * `onChanged` — the incident itself, whose status only the server moves.
 *
 * @param {object} spec
 * @param {string} spec.store
 * @param {string} spec.incidentId
 * @param {() => void} [spec.onChanged] the incident may have changed status; re-read it
 * @returns {{node: HTMLElement, setStatus: (status: string) => void}}
 */
export function remedyFlow(spec) {
  const { store, incidentId } = spec;
  const writeVerdict = can(principal(), "INCIDENTS_WRITE");
  const proposeSubmission = new Submission("remedy-propose");

  /** @type {{kind: string, lineId: string, garment: string, amount: string, lateness: string, fault: boolean}} */
  const draft = {
    kind: REMEDY_KIND.FREE_REWASH,
    lineId: "",
    // "Món thứ mấy" on a line of several garments (REMEDY-GARMENT-001), as the select holds it.
    garment: "",
    amount: "",
    lateness: "",
    fault: false,
  };

  /** @type {any} the `RemedyOptionsResponse` for this incident, or null until read */
  let options = null;
  /**
   * Whether the incident is closed, from the incident page's own read; null until it is known.
   * A closed incident takes no more proposals (`REMEDY_INCIDENT_NOT_OPEN`), so the form is not
   * offered on it — the server's rule is shown instead. The server still decides every proposal.
   */
  let closed = /** @type {boolean|null} */ (null);

  const optionsHost = h("div", { class: "stack" }, skeletonRows(3));
  const formHost = h("div", { class: "stack" });
  const planHost = h("div");
  const proposeResult = resultLine();
  const proposeHost = h("div", { class: "stack", id: "remedy-proposed" });
  const pendingHost = h("div");

  /** The live reading under the money box, rebuilt in place so the box itself is never rebuilt. */
  /** @type {HTMLElement|null} */
  let amountFeedback = null;

  const kindNote = h("p", { class: "hint", id: "remedy-kind-hint" }, KIND_NOTE[draft.kind]);
  const kindPicker = segmented({
    id: "remedy-kind",
    label: "Loại bồi hoàn",
    wrap: true,
    value: draft.kind,
    options: REMEDY_KIND_ORDER.map((kind) => ({ value: kind, label: KIND_LABEL[kind] })),
    onChange: (value) => {
      draft.kind = value;
      draft.lineId = "";
      draft.garment = "";
      draft.amount = "";
      draft.lateness = "";
      proposeSubmission.reset();
      kindNote.textContent = KIND_NOTE[draft.kind] || "";
      redrawForm();
    },
  });
  for (const option of kindPicker.querySelectorAll("button")) {
    option.setAttribute("title", option.dataset.value || "");
  }

  const proposeButton = button({
    label: "Gửi đề nghị bồi hoàn",
    type: "submit",
    variant: "primary",
    block: true,
    network: true,
    id: "remedy-propose",
  });

  const form = h(
    "form",
    { class: "form remedy-form", onSubmit: propose, id: "remedy-form" },
    h("div", { class: "stack stack--tight" }, h("p", { class: "remedy-label" }, "Khách được gì?"), kindPicker, kindNote),
    formHost,
  );

  /** @returns {ReturnType<typeof remedyPlan>} */
  function currentPlan() {
    return remedyPlan({
      options,
      kind: draft.kind,
      storeFaultAttested: draft.fault,
      lineId: draft.lineId,
      garmentIndex: draft.garment,
      typedAmount: draft.amount,
      typedLateness: draft.lateness,
    });
  }

  /**
   * Rebuild the summary and the fields under it for the kind currently chosen.
   *
   * The money box is rebuilt only here — on a kind, line, garment or attestation change — and never
   * while it is being typed into: `refreshPlan` updates the readings around it in place. Rebuilding
   * an input the operator is typing into is the defect `verify_console_interaction.py` exists to
   * catch, where a code typed one character at a time came out as its first letter.
   */
  function redrawForm() {
    if (closed === true) {
      form.hidden = true;
      render(
        optionsHost,
        inlineAlert({
          state: "info",
          title: "Khiếu nại này đã đóng",
          body: h("p", null, REASON_NOTE.REMEDY_INCIDENT_NOT_OPEN),
        }),
      );
      render(formHost);
      return;
    }
    form.hidden = false;
    if (options === null || closed === null) {
      render(formHost);
      return;
    }
    render(optionsHost);
    const plan = currentPlan();
    render(
      formHost,
      draft.kind === REMEDY_KIND.LOST_ITEM ? lossNotice() : null,
      options.order_refunded === true && plan.needsLine ? refundedNotice() : null,
      ceilingSummary(plan, options),
      plan.needsLine ? lineField() : null,
      plan.needsGarment ? garmentField(plan) : null,
      plan.needsAmount ? amountField() : null,
      plan.needsLateness ? latenessField() : null,
      faultField(),
      planHost,
      gated(proposeButton, writeVerdict),
      proposeResult,
      proposeHost,
    );
    refreshPlan();
    if (!writeVerdict.allowed) gatedFields(formHost, writeVerdict);
  }

  /** The "what is still missing" line above the button, and the money box's own readings. */
  function refreshPlan() {
    const plan = currentPlan();
    const typed = Boolean(draft.amount.trim());
    render(
      planHost,
      (typed && AMOUNT_STATES.has(plan.state)) || SELF_EVIDENT.has(plan.state)
        ? null
        : planNotice(plan),
    );
    if (amountFeedback) {
      render(
        amountFeedback,
        typed && Number.isInteger(plan.amountVnd)
          ? h("p", { class: "money-echo" }, "= ", h("span", { class: "money" }, money(plan.amountVnd)))
          : null,
        typed && AMOUNT_STATES.has(plan.state) ? planNotice(plan) : null,
        typed && plan.requiresOwner
          ? h(
              "p",
              { class: "hint", dataState: "warn" },
              `Gửi đi sẽ lập phiếu chờ chủ tiệm duyệt: ${ownerReasonText(plan.ownerReasons)}. ` +
                "Nói trước với khách là phải chờ.",
            )
          : null,
      );
    }
  }

  /** @returns {HTMLElement} */
  function lineField() {
    const lines = damageLines(options);
    const select = h(
      "select",
      {
        name: "remedy-line",
        onChange: (event) => {
          draft.lineId = event.target.value;
          // Another line's garment numbers are not this line's; the choice starts over.
          draft.garment = "";
          proposeSubmission.reset();
          redrawForm();
        },
      },
      h("option", { value: "", selected: !draft.lineId }, "— chọn món —"),
      lines.map((line) =>
        h(
          "option",
          {
            value: line.lineId,
            selected: line.lineId === draft.lineId,
            title: `${line.serviceCode} · ${line.lineId}`,
          },
          lineOptionText(line),
        ),
      ),
    );
    return labelled({
      id: "remedy-line",
      label: draft.kind === REMEDY_KIND.LOST_ITEM ? "Món bị mất" : "Món bị hỏng",
      hint: lines.length
        ? "Mỗi dòng ghi trần của một món và số đã ghi đền cho nó."
        : "Máy chủ chưa gửi dòng nào có giá cho đơn này.",
      control: select,
    });
  }

  /**
   * "Món thứ mấy": which garment on a line of several priced per piece. REMEDY-GARMENT-001.
   *
   * Each garment has its own 100.000 ₫ staff limit and its own 5× ceiling, so the picker shows what
   * each already carries — the server's figure, line-level claims included — before one is chosen.
   * A line of one garment never shows this: it is garment 1. No default is preselected on a line
   * of several, because picking garment 1 for somebody spends garment 1's limit.
   *
   * @param {ReturnType<typeof remedyPlan>} plan
   * @returns {HTMLElement}
   */
  function garmentField(plan) {
    const line = damageLines(options).find((candidate) => candidate.lineId === draft.lineId);
    const count = Number.isInteger(plan.garments) ? plan.garments : 0;
    const held = line && Array.isArray(line.garmentCommitted) ? line.garmentCommitted : null;
    const select = h(
      "select",
      {
        name: "remedy-garment",
        onChange: (event) => {
          draft.garment = event.target.value;
          proposeSubmission.reset();
          redrawForm();
        },
      },
      h("option", { value: "", selected: !draft.garment }, "— chọn món thứ mấy —"),
      Array.from({ length: count }, (_, offset) => {
        const position = offset + 1;
        const carried = held ? held[offset] : null;
        return h(
          "option",
          { value: String(position), selected: draft.garment === String(position) },
          carried ? `Món thứ ${position} · đã ghi ${money(carried)}` : `Món thứ ${position}`,
        );
      }),
    );
    return labelled({
      id: "remedy-garment",
      label: "Món thứ mấy",
      hint:
        `Dòng này có ${count} món; mỗi món có mức duyệt và trần riêng, đề nghị sau cộng dồn với ` +
        "đề nghị trước của cùng món.",
      control: select,
    });
  }

  /**
   * One line of the picker: the service by name, how many, the ceiling, and what it already holds.
   *
   * @param {ReturnType<typeof damageLines>[number]} line
   * @returns {string}
   */
  function lineOptionText(line) {
    const unit = line.unit ? UNIT_LABEL[line.unit] || line.unit : "";
    // `quantity`, the house formatter: shown exactly as stored, never re-rounded (`DEC-001`).
    const count = line.quantity && unit ? ` × ${quantity(line.quantity)} ${unit}` : "";
    const held = line.committed ? ` · đã ghi ${money(line.committed)}` : "";
    const owner = line.ownerAlways.length ? " · chủ tiệm duyệt" : "";
    const cap =
      line.pieces && line.pieces > 1
        ? `trần ${money(line.ceiling)}/món, cả dòng ${money(line.lineCeiling)}`
        : `trần ${money(line.ceiling)}`;
    return `${line.label}${count} · ${cap}${held}${owner}`;
  }

  /** @returns {HTMLElement} */
  function amountField() {
    const input = h("input", {
      type: "text",
      inputmode: "numeric",
      autocomplete: "off",
      maxlength: "16",
      value: draft.amount,
      onInput: (event) => {
        draft.amount = event.target.value;
        proposeSubmission.reset();
        const plan = currentPlan();
        event.target.setAttribute("aria-invalid", AMOUNT_STATES.has(plan.state) ? "true" : "false");
        refreshPlan();
      },
    });
    amountFeedback = h("div", { class: "stack stack--tight", id: "remedy-amount-feedback" });
    const field = labelled({
      id: "remedy-amount",
      label: "Số tiền đền cho khách",
      hint: "Số nguyên đồng. Trần ở trên do máy chủ tính; vượt trần thì bị từ chối, không tự hạ xuống.",
      control: h("div", { class: "money-input" }, input, h("span", { class: "money-input__unit" }, "₫")),
    });
    return h("div", { class: "stack stack--tight" }, field, amountFeedback);
  }

  /** @returns {HTMLElement} */
  function latenessField() {
    const input = h("input", {
      type: "text",
      inputmode: "numeric",
      autocomplete: "off",
      maxlength: "7",
      value: draft.lateness,
      onInput: (event) => {
        draft.lateness = event.target.value;
        proposeSubmission.reset();
        refreshPlan();
      },
    });
    return labelled({
      id: "remedy-lateness",
      label: "Chuyến giao trễ bao nhiêu phút",
      hint:
        "Lời khai của người đã giao — tiệm không lưu giờ hẹn giao. " +
        `Ngưỡng đã công bố: ${minutesLabel(options?.late_delivery_threshold_minutes)}.`,
      control: input,
    });
  }

  /** @returns {HTMLElement} */
  function faultField() {
    const box = h("input", {
      type: "checkbox",
      id: "remedy-fault",
      checked: draft.fault,
      "aria-describedby": "remedy-fault-hint",
      onChange: (event) => {
        draft.fault = Boolean(event.target.checked);
        proposeSubmission.reset();
        refreshPlan();
      },
    });
    return h(
      "div",
      { class: "check-row" },
      h("label", { for: "remedy-fault", class: "check-row__label" }, box, h("span", null, "Tôi xác định lỗi thuộc về tiệm")),
      h(
        "p",
        { class: "fact-line", id: "remedy-fault-hint" },
        h("span", { class: "hint" }, "Bắt buộc, không có mặc định. Tên bạn được ghi kèm."),
        infoButton(
          "Vì sao phải tự xác định lỗi?",
          h(
            "p",
            { class: "hint" },
            "Bắt buộc, và không có giá trị mặc định: DEC-004 đặt mọi khoản bồi hoàn lên việc một " +
              "người xác định lỗi thuộc về tiệm. Tên bạn được ghi kèm quyết định này.",
          ),
        ),
      ),
    );
  }

  /**
   * Read the ceilings and windows for this incident. A pure read: nothing is written or reserved.
   *
   * @param {{quiet?: boolean}} [how] `quiet` keeps the form on screen while re-reading after a write
   */
  async function loadOptions(how = {}) {
    if (!how.quiet) {
      options = null;
      render(formHost);
      render(optionsHost, skeletonRows(3));
    }
    try {
      const loaded = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/incidents/${encodeURIComponent(incidentId)}/remedy-options`,
      );
      options = loaded;
      if (closed !== null) render(optionsHost);
      redrawForm();
    } catch (error) {
      options = null;
      render(formHost);
      const codes = /** @type {any} */ (error)?.reasonCodes || [];
      if (codes.includes("REMEDY_INCIDENT_NOT_OPEN")) {
        // The server's answer for a complaint whose every claim has an outcome: not a failure.
        render(
          optionsHost,
          inlineAlert({
            state: "info",
            title: "Khiếu nại này đã đóng",
            body: h("p", null, REASON_NOTE.REMEDY_INCIDENT_NOT_OPEN),
          }),
        );
        return;
      }
      show(optionsHost, errorNotice(error, { onRetry: () => void loadOptions() }));
    }
  }

  /**
   * @param {SubmitEvent} event
   */
  async function propose(event) {
    event.preventDefault();
    const plan = currentPlan();
    const body = remedyProposalBody(plan, draft);
    render(proposeHost);
    if (body === null) {
      setResult(proposeResult, "danger", PLAN_NOTE[plan.state] || "Chưa gửi được.");
      return;
    }
    setResult(proposeResult, "warn", "Đang gửi đề nghị…");
    proposeButton.setAttribute("aria-busy", "true");
    try {
      const created = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/incidents/${encodeURIComponent(incidentId)}/remedy-proposals`,
        { method: "POST", body, idempotencyKey: proposeSubmission.key() },
      );
      proposeSubmission.reset();
      // A new claim is a new attestation and a new figure: nothing of this one is carried into it.
      draft.amount = "";
      draft.fault = false;
      toast(
        awaitsOwner(created.status)
          ? "Đã ghi đề nghị · chờ chủ tiệm duyệt"
          : "Đã ghi đề nghị bồi hoàn",
      );
      // What each item already carries has moved, so the figures are read again before the form
      // is offered for a second claim; the list is re-read rather than appended to.
      await Promise.all([loadOptions({ quiet: true }), recorded.load()]);
      // The alert below says what was recorded and what happens next; its id is in its tech drawer.
      setResult(proposeResult, null, null);
      render(proposeHost, proposalAlert(created));
      spec.onChanged?.();
    } catch (error) {
      const api = /** @type {any} */ (error);
      const refused = api.kind === "DENIED" || api.kind === "REQUIRE_HUMAN" || api.status === 422;
      setResult(
        proposeResult,
        refused ? "warn" : "danger",
        refused
          ? "Máy chủ từ chối đề nghị này. Không có gì được ghi — đọc mã lý do bên dưới."
          : "Không gửi được đề nghị.",
      );
      show(proposeHost, errorNotice(error));
    } finally {
      proposeButton.removeAttribute("aria-busy");
    }
  }

  const recorded = recordedProposalsPanel(store, incidentId, writeVerdict, {
    onExecuted: () => {
      void loadOptions({ quiet: true });
      spec.onChanged?.();
    },
    onLoaded: (rows) => {
      // A claim someone can pay right now is the most urgent thing on the page, and the list sits
      // under the form: one line at the top says so and takes the thumb there.
      const ready = rows.filter((row) => row.next_step === "EXECUTE").length;
      render(
        pendingHost,
        ready
          ? inlineAlert({
              state: "info",
              title: `${ready} khoản đã được duyệt, chờ thực hiện`,
              actions: button({
                label: "Xem",
                variant: "quiet",
                onClick: () => recorded.node.scrollIntoView({ block: "start", behavior: "smooth" }),
              }),
            })
          : null,
      );
    },
    onProposeAgain: (row) => {
      kindPicker.setValue(String(row.kind || REMEDY_KIND.FREE_REWASH));
      draft.kind = String(row.kind || REMEDY_KIND.FREE_REWASH);
      draft.lineId = typeof row.order_line_id === "string" ? row.order_line_id : "";
      draft.garment = "";
      draft.amount = "";
      draft.lateness = "";
      draft.fault = false;
      proposeSubmission.reset();
      kindNote.textContent = KIND_NOTE[draft.kind] || "";
      redrawForm();
      form.scrollIntoView({ block: "start", behavior: "smooth" });
    },
  });

  void loadOptions();
  void recorded.load();

  const node = h(
    "div",
    { class: "stack" },
    pendingHost,
    section({
      title: "Bồi hoàn",
      id: "remedy-flow",
      info: infoButton(
        "Bồi hoàn ở đây làm được gì, không làm được gì?",
        h(
          "p",
          { class: "hint" },
          "Một sự cố đi tới kết cục ở đây: giặt lại, đền món hỏng, đền món mất, hoặc giảm trừ do " +
            "giao trễ. Mức trần, thời hạn và việc có cần chủ tiệm duyệt hay không đều do máy chủ " +
            "tính và hiện ra trước khi bạn gõ bất cứ con số nào.",
        ),
        h(
          "p",
          { class: "hint" },
          "Nhân viên không bao giờ gõ trần. Vượt trần thì bị từ chối kèm con số trần, không bị tự " +
            "hạ xuống. Mất đồ, và mọi khoản đền trên đơn đã hoàn tiền, luôn chờ chủ tiệm duyệt.",
        ),
      ),
      children: h("div", { class: "stack" }, optionsHost, gatedFields(form, writeVerdict)),
    }),
    recorded.node,
  );
  return {
    node,
    /** @param {string} status the incident's status, as the incident page just read it */
    setStatus(status) {
      const next = status === "CLOSED";
      if (next === closed) return;
      closed = next;
      redrawForm();
    },
  };
}

// --- READ-PATHS-001: the proposals recorded on an incident ---------------------------------------
//
// One section, one read. It shares nothing with the proposal form but `KIND_LABEL`; the form's
// hooks re-read it after a proposal, and its own execute press re-reads it after a payment.

/** `RemedyStatus` in the counter's words. The token is kept on the pill's title. */
const RECORDED_STATUS_LABEL = {
  STAFF_AUTHORIZED: "Nhân viên được duyệt, chờ thực hiện",
  OWNER_APPROVAL_REQUIRED: "Chờ chủ tiệm duyệt",
  EXECUTED: "Đã thực hiện",
  POLICY_UNRESOLVED: "Ghi trước DEC-031, không có số tiền",
};

/** The owner envelope's stored state, in the counter's words. */
const RECORDED_APPROVAL_LABEL = {
  REQUESTED: "đang chờ chủ tiệm",
  APPROVED: "chủ tiệm đã duyệt",
  REJECTED: "chủ tiệm từ chối",
  EXPIRED: "phiếu duyệt đã hết hạn",
  EXECUTED: "đã thực hiện",
};

/**
 * Đề nghị đã ghi cho sự cố này — `GET …/incidents/{id}/remedy-proposals`.
 *
 * `REMEDY-OWNER-DECIDE-001` made the list a place to act as well as read. The execute route takes
 * nothing but the proposal id and an idempotency key — every figure was fixed when the proposal
 * was recorded — so a row the server marks `next_step: EXECUTE` carries "Thực hiện bồi hoàn", and
 * an owner approval given hours later, on another device, is paid from here. Nothing about what
 * may be executed is decided in this file: the server says `EXECUTE`, `AWAIT_OWNER`,
 * `PROPOSE_AGAIN` or `NONE`, and the row renders that. Since `CONSOLE-REDESIGN-004` this is the
 * only execute press there is.
 *
 * One `Submission` per proposal, held across list reloads, so a second press after a lost answer
 * carries the same key and the server replays the payment instead of refusing it as done.
 *
 * @param {string} store
 * @param {string} incidentId
 * @param {{allowed: boolean, reason: string}} verdict whether this principal may execute
 * @param {{onExecuted?: () => void, onLoaded?: (rows: any[]) => void, onProposeAgain?: (row: any) => void}} hooks
 * @returns {{node: HTMLElement, load: () => Promise<void>}}
 */
function recordedProposalsPanel(store, incidentId, verdict, hooks) {
  const host = h("div", { id: "remedy-recorded-proposals", class: "stack" }, skeletonRows(2));
  // Outside `host`, so the list re-read after a payment does not wipe the credit code it issued.
  const executionHost = h("div", { id: "remedy-recorded-execution", class: "stack" });
  const executionResult = resultLine();
  /** @type {Map<string, Submission>} */
  const submissions = new Map();
  let generation = 0;

  /** @param {any} row @param {HTMLButtonElement} button */
  async function execute(row, button) {
    const id = String(row.proposal_id || "");
    if (!UUID.test(id)) return;
    let submission = submissions.get(id);
    if (!submission) {
      submission = new Submission(`remedy-execute-${id}`);
      submissions.set(id, submission);
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setResult(executionResult, "warn", "Đang thực hiện…");
    render(executionHost);
    try {
      const done = await request(`/internal/v1/remedy-proposals/${encodeURIComponent(id)}/execution`, {
        method: "POST",
        idempotencyKey: submission.key(),
      });
      submission.reset();
      // One claim of possibly several on the incident. Whether paying it closed the incident is
      // for the incident's own re-read to show, so this line says what was done and nothing more.
      // The card below says it, in full; a second "done" line above it would only repeat it.
      setResult(executionResult, null, null);
      render(executionHost, executionCard(done));
      toast("Đã thực hiện bồi hoàn");
      await load();
      hooks.onExecuted?.();
      executionHost.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (error) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      const api = /** @type {any} */ (error);
      const refused = api.kind === "DENIED" || api.status === 422;
      setResult(
        executionResult,
        refused ? "warn" : "danger",
        refused
          ? "Máy chủ chưa cho thực hiện. Đọc lý do bên dưới rồi đọc lại danh sách."
          : "Không thực hiện được.",
      );
      show(executionHost, errorNotice(error, { onRetry: () => void load() }));
    }
  }

  async function load() {
    generation += 1;
    const mine = generation;
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/incidents/${encodeURIComponent(incidentId)}/remedy-proposals`,
      );
      if (mine !== generation) return;
      const rows = Array.isArray(body?.proposals) ? body.proposals : [];
      render(
        host,
        rows.length
          ? h(
              "ol",
              { class: "rows proposals" },
              rows.map((row) => recordedProposalCard(row, body.order_id, verdict, execute, hooks)),
            )
          : emptyState({
              icon: "tag",
              title: "Chưa có đề nghị bồi hoàn nào cho khiếu nại này.",
            }),
        body?.truncated ? h("p", { class: "hint" }, "Máy chủ cắt danh sách; có thể còn nữa.") : null,
      );
      hooks.onLoaded?.(rows);
    } catch (error) {
      if (mine !== generation) return;
      show(host, errorNotice(error, { onRetry: () => void load() }));
    }
  }

  return {
    node: section({
      title: "Đề nghị đã ghi cho sự cố này",
      id: "remedy-recorded",
      card: false,
      info: infoButton(
        "Danh sách này gồm những gì?",
        h(
          "p",
          { class: "hint" },
          "Danh sách của máy chủ, cũ nhất trước: mọi đề nghị đã ghi cho sự cố, của ai và lúc nào, " +
            "kể cả vụ mất đồ ghi trước DEC-031 không có số tiền. Đề nghị đang chờ chủ tiệm được " +
            "duyệt ở màn hình Duyệt. Đề nghị nào đã được duyệt mà chưa thực hiện thì có nút " +
            "thực hiện ngay trên dòng của nó, kể cả khi chủ tiệm duyệt từ hôm qua hay trên máy khác.",
        ),
        h(
          "p",
          { class: "hint" },
          "Thực hiện không gửi lại số tiền: mọi thứ đã được quyết khi ghi đề nghị và nằm bất biến " +
            "trên bản ghi đó. Cho gõ lại số ở bước này là để con số đổi giữa lúc chủ tiệm duyệt và " +
            "lúc tiệm trả tiền.",
        ),
        h(
          "p",
          { class: "hint" },
          "Sự cố chỉ đóng khi mọi đề nghị trên nó đã có kết cục. Còn đề nghị đang chờ chủ tiệm " +
            "hoặc chưa thực hiện thì sự cố vẫn mở, và ghi tiếp được món khác của cùng lời phàn nàn.",
        ),
      ),
      children: h("div", { class: "stack" }, host, executionResult, executionHost),
    }),
    load,
  };
}

/**
 * What the server says the counter may do next with one row, as a sentence and, for `EXECUTE`,
 * the press. `next_step` is the server's; nothing here compares a clock or reads a status to decide
 * whether a proposal may be paid.
 *
 * @param {any} row
 * @param {{allowed: boolean, reason: string}} verdict
 * @param {(row: any, button: HTMLButtonElement) => Promise<void>} onExecute
 * @param {{onProposeAgain?: (row: any) => void}} hooks
 * @returns {HTMLElement|null}
 */
function recordedNextStep(row, verdict, onExecute, hooks) {
  const step = String(row.next_step || "");
  if (step === "EXECUTE") {
    const press = button({
      label: "Thực hiện bồi hoàn",
      variant: "primary",
      block: true,
      network: true,
      data: { remedyExecute: String(row.proposal_id || "") },
    });
    press.addEventListener("click", () => void onExecute(row, press));
    return h(
      "div",
      { class: "stack stack--tight proposal__next" },
      h(
        "p",
        { class: "hint" },
        row.approval_id
          ? `Chủ tiệm đã duyệt, chưa thực hiện. Thực hiện trước khi phiếu duyệt hết hạn lúc ${dateTime(row.approval_expires_at)}.`
          : "Nhân viên được tự duyệt khoản này, chưa thực hiện.",
      ),
      gated(press, verdict),
    );
  }
  if (step === "AWAIT_OWNER") {
    return h(
      "div",
      { class: "stack stack--tight proposal__next", dataNextStep: step },
      h(
        "p",
        { class: "hint" },
        `Chưa thực hiện được: đang chờ chủ tiệm duyệt ở màn hình Duyệt, hạn tới ${dateTime(row.approval_expires_at)}.`,
      ),
      linkButton({ href: "#/approvals", label: "Chờ chủ tiệm duyệt", variant: "quiet", icon: "approval" }),
    );
  }
  if (step === "PROPOSE_AGAIN") {
    return h(
      "div",
      { class: "stack stack--tight proposal__next", dataNextStep: step },
      h(
        "p",
        { class: "hint", dataState: "warn" },
        row.approval_status === "REJECTED"
          ? "Chủ tiệm đã từ chối khoản này, nên không thực hiện được."
          : "Phiếu duyệt của khoản này đã quá hạn, nên không thực hiện được nữa.",
      ),
      h(
        "p",
        { class: "hint" },
        "Muốn bồi hoàn thì đề nghị lại ở biểu mẫu bên trên: máy chủ tính lại trần và mở phiếu duyệt mới.",
      ),
      button({ label: "Đề xuất lại", variant: "quiet", onClick: () => hooks.onProposeAgain?.(row) }),
    );
  }
  return null;
}

/**
 * @param {any} row an `IncidentRemedyProposalItemResponse`
 * @param {string|null|undefined} orderId the incident's order, for the credit link
 * @param {{allowed: boolean, reason: string}} verdict
 * @param {(row: any, button: HTMLButtonElement) => Promise<void>} onExecute
 * @param {{onProposeAgain?: (row: any) => void}} hooks
 * @returns {HTMLElement}
 */
function recordedProposalCard(row, orderId, verdict, onExecute, hooks) {
  const status = String(row.status || "");
  const approval = row.approval_id
    ? row.approval_lapsed
      ? "phiếu duyệt đã quá hạn, chủ tiệm không duyệt được nữa"
      : RECORDED_APPROVAL_LABEL[row.approval_status] || String(row.approval_status || UNKNOWN)
    : null;
  // An approved envelope leaves the proposal's own status at OWNER_APPROVAL_REQUIRED until it is
  // paid, so "Chờ chủ tiệm duyệt" on the pill would be false for it. The server's next step says.
  const statusLabel =
    status === "OWNER_APPROVAL_REQUIRED" && row.next_step === "EXECUTE"
      ? "Chủ tiệm đã duyệt, chờ thực hiện"
      : RECORDED_STATUS_LABEL[status] || status || UNKNOWN;
  const amount = Number.isInteger(row.amount_vnd)
    ? money(row.amount_vnd)
    : row.kind === "FREE_REWASH"
      ? "Không có — giặt lại không chuyển tiền"
      : "Không có số tiền";
  return h(
    "li",
    {
      class: "rows__item proposal",
      dataProposalId: String(row.proposal_id || ""),
      dataProposalStatus: status,
      dataProposalNextStep: String(row.next_step || ""),
    },
    h(
      "div",
      { class: "proposal__head" },
      h("strong", null, KIND_LABEL[row.kind] || String(row.kind || UNKNOWN)),
      statusPill({
        state: status === "EXECUTED" ? "ok" : status === "POLICY_UNRESOLVED" ? "danger" : "warn",
        text: statusLabel,
        token: status || UNKNOWN,
      }),
    ),
    h(
      "p",
      { class: "proposal__meta" },
      h("span", { class: "proposal__amount" }, amount),
      ` · ${String(row.proposed_by_name || UNKNOWN)} · ${ago(row.proposed_at)}`,
    ),
    // While it waits, the next-step line below already says so; the envelope's state is news
    // only once it has one (approved, refused, lapsed).
    approval && row.next_step !== "AWAIT_OWNER"
      ? h("p", { class: "hint" }, `Phiếu duyệt: ${approval}`)
      : null,
    row.executed_at ? h("p", { class: "hint" }, `Thực hiện lúc ${dateTime(row.executed_at)}`) : null,
    row.credit_id
      ? h(
          "p",
          { class: "hint" },
          "Đã phát khoản giảm trừ · ",
          orderId
            ? h(
                "a",
                { href: `#/orders/${encodeURIComponent(String(orderId))}` },
                "xem mã và đã dùng chưa ở đơn",
              )
            : null,
        )
      : null,
    recordedNextStep(row, verdict, onExecute, hooks),
    techDetails([
      ["Mã đề nghị", shortId(row.proposal_id), { copy: String(row.proposal_id || "") }],
      ["Trạng thái", status || UNKNOWN],
      ["Bước tiếp theo", String(row.next_step || UNKNOWN)],
      Number.isInteger(row.ceiling_vnd) ? ["Trần đã kiểm", money(row.ceiling_vnd), { mono: false }] : null,
      row.order_line_id ? ["Dòng", String(row.order_line_id)] : null,
      Number.isInteger(row.attested_late_by_minutes)
        ? ["Trễ (lời khai)", `${row.attested_late_by_minutes} phút`]
        : null,
      ["Lúc đề nghị", dateTime(row.proposed_at), { mono: false }],
      row.approval_id
        ? ["Phiếu duyệt", shortId(row.approval_id), { copy: String(row.approval_id) }]
        : null,
      row.approval_status ? ["Trạng thái phiếu duyệt", String(row.approval_status)] : null,
      row.credit_id ? ["Mã giảm trừ", shortId(row.credit_id), { copy: String(row.credit_id) }] : null,
    ]),
  );
}

// --- #/remedies: the old address, and the open complaints that carry claims ----------------------

/** How many open complaints the list reads the proposals of. Each costs one read. */
const OPEN_CHECKED = 20;
const INCIDENT_LIMIT = 100;

/**
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  const incident = context?.query?.get("incident") || "";
  if (incident && UUID.test(incident)) {
    // Approval cards and older links name the incident here. Its page is the incident's own now;
    // `replace` keeps Back from bouncing through this address again.
    replace(`/incidents/${encodeURIComponent(incident)}`);
    return h("section", { class: "screen" }, skeletonRows(3));
  }

  /** How many open complaints there were, and how many were read, for the disclosure line. */
  let openCount = 0;
  let listTruncated = false;

  const view = listView({
    limit: INCIDENT_LIMIT,
    skeleton: () => skeletonRows(3),
    fetch: async () => {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/incidents?limit=${INCIDENT_LIMIT}`,
      );
      listTruncated = isTruncated(items, INCIDENT_LIMIT);
      const open = (Array.isArray(items) ? items : []).filter((item) => item.status !== "CLOSED");
      openCount = open.length;
      const checked = open.slice(0, OPEN_CHECKED);
      const read = await Promise.all(
        checked.map((item) =>
          request(
            `/internal/v1/stores/${encodeURIComponent(store)}/incidents/${encodeURIComponent(item.incident_id)}/remedy-proposals`,
          ).then(
            (body) => ({ item, proposals: Array.isArray(body?.proposals) ? body.proposals : [] }),
            () => ({ item, proposals: null }),
          ),
        ),
      );
      return read.filter((entry) => entry.proposals === null || entry.proposals.length > 0);
    },
    truncationText: () => "",
    onLoaded: () => {
      const unread = Math.max(0, openCount - OPEN_CHECKED);
      disclosure.hidden = !(unread || listTruncated);
      render(
        disclosure,
        unread || listTruncated
          ? h(
              "p",
              { class: "hint", dataState: "warn" },
              `Chỉ xem ${Math.min(openCount, OPEN_CHECKED)} khiếu nại đang mở mới nhất` +
                (listTruncated ? " trong 100 khiếu nại mới nhất" : "") +
                ". Khiếu nại cũ hơn: mở từ màn hình Khiếu nại.",
            )
          : null,
      );
    },
    renderRows: (nodes) => list(nodes, { label: "Khiếu nại đang mở có đề nghị" }),
    renderEmpty: () =>
      emptyState({
        icon: "tag",
        title: "Không có khiếu nại đang mở nào có đề nghị bồi hoàn.",
        action: linkButton({ href: "#/incidents", label: "Mở danh sách khiếu nại", variant: "quiet" }),
      }),
    emptyText: "Không có khiếu nại đang mở nào có đề nghị bồi hoàn.",
    renderItem: ({ item, proposals }) => {
      const ready = proposals ? proposals.filter((row) => row.next_step === "EXECUTE").length : 0;
      const waiting = proposals
        ? proposals.filter((row) => row.next_step === "AWAIT_OWNER").length
        : 0;
      return listRow({
        href: `#/incidents/${encodeURIComponent(item.incident_id)}`,
        leading: "incident",
        title: Number.isInteger(item.ticket_number) ? `Phiếu ${item.ticket_number}` : "Khiếu nại",
        // Untrusted text, as a text node, one line.
        meta:
          typeof item.evidence_summary === "string"
            ? h("span", { class: "row-item__clip" }, item.evidence_summary)
            : null,
        trailing: proposals ? `${proposals.length} đề nghị` : UNKNOWN,
        trailingMeta: proposals
          ? ready
            ? `${ready} chờ thực hiện`
            : waiting
              ? `${waiting} chờ chủ tiệm`
              : enumVi(item.status)
          : "chưa đọc được",
        data: { incidentId: String(item.incident_id) },
      });
    },
  });
  const disclosure = h("div", { hidden: true });
  void view.reload();

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Bồi hoàn",
      subtitle: "Khiếu nại đang mở có đề nghị bồi hoàn.",
      info: infoButton(
        "Bồi hoàn làm ở đâu?",
        h(
          "p",
          { class: "hint" },
          "Bồi hoàn làm ngay trên trang của từng khiếu nại: mở khiếu nại, chọn loại, gửi đề nghị, " +
            "rồi thực hiện trên dòng của nó. Danh sách này chỉ gom các khiếu nại đang mở đã có đề nghị.",
        ),
      ),
    }),
    view.bar.node,
    disclosure,
    view.host,
  );
}

export const screen = {
  path: "/remedies",
  title: "Bồi hoàn",
  capability: "INCIDENTS_READ",
  needsStore: true,
  render: render_,
};
