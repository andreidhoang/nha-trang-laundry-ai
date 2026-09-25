/**
 * Bồi hoàn: what the shop owes a customer when something went wrong, and what it may not decide.
 *
 * `REMEDY-001` gave `#/incidents` the outcome it never had. Until this screen, an incident could be
 * recorded and nothing else: the two flags read "chưa quyết định" forever, the list only grew, and
 * the 5× cap and the 100.000 ₫ escalation the owner ratified in `DEC-004` were enforced by nothing
 * but memory. Five choices here are deliberate rather than incidental:
 *
 *   - **The figures are read before anything is typed.** `GET …/remedy-options` is a pure read that
 *     reserves nothing, and its whole purpose is that the ceiling, the window, whether the window
 *     is still open and whether the owner will be needed are on screen *first*. A staff member who
 *     learns after filling in a form that the owner has to approve it has already told a customer
 *     something the shop cannot do. So the summary block sits above the money box in the DOM, not
 *     beside it and not after it.
 *   - **`LOST_ITEM` is a form that always ends at the owner.** Until `DEC-031` it rendered through
 *     `components.unsupported`, because `DEC-004` had left loss undecided. `DEC-031` (2026-09-25)
 *     gave it the damage ceiling and one rule: every loss claim needs the owner, whatever the
 *     amount. So a loss names the item and a figure like damage, and the form says "chờ chủ tiệm"
 *     from the first render — staff record and propose, only the owner releases the money.
 *   - **No ceiling is ever typed, and none is ever truncated to.** The 5× cap comes from the item
 *     fee the order's own snapshot records — one piece's price on a per-piece line, the bag's fee
 *     on a weight-priced one — and the 10% credit from its settled total; both arrive computed. An
 *     item total above the cap is refused here with the cap named, exactly as the server refuses
 *     it with `REMEDY_CEILING_EXCEEDED` — never quietly reduced to the cap, which would pay a
 *     customer less than the person at the counter believed they had agreed.
 *   - **A credit is a bearer instrument and this screen is the only place its identifier appears.**
 *     There is no route that lists an order's or a ticket's unredeemed credits, so a credit that is
 *     not written down at the moment it is issued cannot be looked up again — only applied by id.
 *     The execution result therefore renders the identifier as a copyable value under a warning
 *     that says so, rather than as one more field on a card. Moving the kind picker hides that
 *     card but never discards it, for the same reason: see `redrawRecords`.
 *   - **Nothing here is a queue.** No route lists an incident's proposals, and none lists the loss
 *     cases waiting on the owner, so this screen deliberately shows no list of either. A list
 *     assembled in the browser from what this session happened to do would read as "these are the
 *     open ones", which is a claim the console is in no position to make.
 *
 * @module screens/remedies
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, dateTime, money, quantity, shortId } from "../core/format.js";
import { REASON_NOTE } from "../core/i18n.js";
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
import { navigate } from "../core/router.js";
import { principal, storeId } from "../core/session.js";
import {
  copyable,
  errorNotice,
  facts,
  gated,
  gatedFields,
  labelled,
  panel,
  resultLine,
  revealError,
  setResult,
  skeleton,
} from "../ui/components.js";

/** `RemedyKind` in the counter's words. The token is always shown beside it, never instead. */
const KIND_LABEL = {
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
  ABOVE_STAFF_LIMIT: "tổng đền cho món này vượt mức nhân viên được duyệt",
};

/**
 * @param {string[]|null|undefined} reasons
 * @returns {string}
 */
function ownerReasonText(reasons) {
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
  AMOUNT_MISSING: "Chưa đủ dữ kiện để gửi. Điền nốt ô còn trống.",
  NOT_AN_AMOUNT:
    "Số tiền phải là số nguyên đồng. “150.000” đọc là 150000; không nhận dấu phẩy và không nhận " +
    "số lẻ. Chưa có gì được gửi đi.",
  ABOVE_CEILING:
    "Số này cộng với số đã ghi cho món này vượt trần máy chủ tính. Máy chủ từ chối và không tự hạ " +
    "xuống bằng trần — hạ xuống là trả cho khách ít hơn con số bạn vừa thoả thuận với họ.",
  BELOW_LATENESS_THRESHOLD:
    "Số phút khai chưa vượt ngưỡng chủ tiệm công bố, nên đây chưa phải chuyến giao trễ theo " +
    "chính sách. Không có gì được ghi.",
  FAULT_NOT_ATTESTED:
    "Mọi khoản bồi hoàn trong DEC-004 đều dựa trên việc một nhân viên xác định lỗi thuộc về tiệm. " +
    "Chưa ai xác định, nên chưa gửi được. Đây là lời khai của bạn, không phải ô bấm cho xong.",
};

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
 * The block that must be on screen before a single digit is typed.
 *
 * Four rows and no more, because they are the four things the packet says staff must not discover
 * afterwards: the kind, the computed ceiling, the window with its open/closed state, and whether
 * the owner will be needed.
 *
 * @param {ReturnType<typeof remedyPlan>} plan
 * @param {any} options
 * @returns {HTMLElement}
 */
function ceilingSummary(plan, options) {
  const window = remedyWindow(plan.kind, options);
  const threshold = plan.ownerThresholdVnd;

  const ceilingCell = plan.hasCeiling
    ? h("span", { class: "money" }, money(plan.ceilingVnd))
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
          dateTime(window.closesAt),
          " · ",
          h("strong", null, window.open ? "còn hạn" : "đã đóng"),
        );

  // The server's own reasons, predicted from the per-item terms it sent. A loss, a refunded order
  // or an item with no recorded fee needs the owner at any amount; otherwise it is the item's total
  // against the staff limit, never the typed amount alone.
  const ownerCell = plan.requiresOwner
    ? h(
        "span",
        null,
        h("strong", null, "Có — phải có chủ tiệm duyệt trước khi thực hiện"),
        plan.ownerReasons.length ? ` (${ownerReasonText(plan.ownerReasons)}).` : null,
      )
    : plan.ownerPossible
      ? h(
          "span",
          null,
          `Tuỳ số tiền. Nhân viên duyệt được tới ${money(threshold)} cho mỗi món, tính cả số đã ` +
            "ghi trước; trên mức đó phải có chủ tiệm.",
        )
      : threshold === null
        ? h("span", null, UNKNOWN)
        : h("span", null, `Không — nằm trong mức nhân viên duyệt được (tới ${money(threshold)}).`);

  const feeCell =
    plan.itemFeeVnd === null
      ? null
      : h(
          "span",
          null,
          h("span", { class: "money" }, money(plan.itemFeeVnd)),
          ` · ${FEE_BASIS_LABEL[plan.itemFeeBasis] || plan.itemFeeBasis || UNKNOWN}`,
        );

  return h(
    "div",
    { class: "card stack" },
    h("h3", null, "Máy chủ cho phép tới đâu — đọc trước khi gõ số"),
    facts([
      [
        "Loại bồi hoàn",
        h("span", { title: plan.kind }, `${KIND_LABEL[plan.kind] || plan.kind}`),
        { span: true },
      ],
      ...(feeCell ? [["Phí giặt tính trần", feeCell, { span: true }]] : []),
      ["Trần máy chủ tính", ceilingCell, { span: true }],
      ...(plan.committedVnd === null
        ? []
        : [["Món này đã ghi đền", money(plan.committedVnd), { span: true }]]),
      ["Cửa sổ thời gian", windowCell, { span: true }],
      ["Cần chủ tiệm duyệt?", ownerCell, { span: true }],
      ["Khách nhận đồ lúc", dateTime(options?.goods_returned_at), { span: true }],
    ]),
    h(
      "p",
      { class: "hint" },
      "Mọi con số ở đây do máy chủ tính từ dữ liệu đã lưu của chính đơn này: 5 lần phí giặt của " +
        "món (giá một cái, hoặc tiền cả túi nếu tính theo ký), 10% lấy từ tổng đã tất toán, cửa sổ " +
        "đo từ mốc khách nhận đồ. Màn hình này không tính tiền và không được gõ trần.",
    ),
  );
}

/**
 * The one sentence a loss needs before anything else: it waits for the owner, whatever the figure.
 *
 * @returns {HTMLElement}
 */
function lossNotice() {
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, "Mất đồ — luôn chờ chủ tiệm duyệt"),
    h(
      "p",
      null,
      "Nhân viên ghi lại và đề nghị số tiền; chỉ chủ tiệm duyệt mới được trả, kể cả số nhỏ. Nói " +
        "trước với khách là phải chờ, đừng hứa con số nào như đã chốt.",
    ),
  );
}

/**
 * A refunded order: compensation is still possible, and every amount goes to the owner.
 *
 * @returns {HTMLElement}
 */
function refundedNotice() {
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, "Đơn đã hoàn tiền — mọi khoản đền đều chờ chủ tiệm duyệt"),
    h(
      "p",
      null,
      "Tiền dịch vụ đã trả lại khách, nhưng món bị hỏng hay mất vẫn được đền, trần tính theo giá " +
        "đã báo. Hoàn tiền và đền trên cùng một đơn thì chủ tiệm duyệt, để không trả hai lần.",
    ),
  );
}

/**
 * A recorded proposal, with what has to happen to it next.
 *
 * @param {any} result a `RemedyProposalResponse`
 * @returns {HTMLElement}
 */
function proposalCard(result) {
  const owner = awaitsOwner(result.status);
  return h(
    "div",
    { class: "card stack" },
    h("h3", null, "Đã ghi đề nghị bồi hoàn"),
    result.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
            "đề nghị mới nào được tạo.",
        )
      : null,
    facts([
      [
        "Mã đề nghị",
        copyable({ value: result.proposal_id || "", display: shortId(result.proposal_id) }),
        { span: true },
      ],
      ["Loại", `${KIND_LABEL[result.kind] || result.kind} (${result.kind})`, { mono: true, span: true }],
      ["Trạng thái", result.status, { mono: true }],
      ["Kết luận của máy chủ", result.outcome, { mono: true }],
      ["Số tiền", money(result.amount_vnd), { span: true }],
      ["Trần máy chủ tính", money(result.ceiling_vnd), { span: true }],
      ["Cửa sổ đóng lúc", dateTime(result.window_closes_at), { span: true }],
      [
        "Phiếu duyệt",
        result.approval_id
          ? copyable({ value: result.approval_id, display: shortId(result.approval_id) })
          : h("span", null, `${UNKNOWN} `, h("span", { class: "hint" }, "Không cần phiếu duyệt.")),
        { span: true },
      ],
    ]),
    result.reason_code
      ? h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Máy chủ ghi nhận nhưng không định giá"),
          h(
            "p",
            null,
            h("span", { class: "mono" }, result.reason_code),
            REASON_NOTE[result.reason_code] ? ` — ${REASON_NOTE[result.reason_code]}` : null,
          ),
        )
      : null,
    owner
      ? h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chờ chủ tiệm duyệt — chưa được thực hiện"),
          h(
            "p",
            null,
            "Máy chủ đã lập phiếu duyệt và dừng lại. Mở màn hình Duyệt để chủ tiệm quyết; bấm " +
              "thực hiện trước khi có quyết định thì máy chủ từ chối.",
          ),
          // Why, in the server's own reasons: a 20.000 ₫ loss waits for the owner too, and "above
          // the staff limit" -- the only reason this notice used to give -- would be false for it.
          result.owner_reasons?.length
            ? h("p", null, `Lý do: ${ownerReasonText(result.owner_reasons)}.`)
            : null,
          h(
            "p",
            { class: "hint" },
            h("a", { href: "#/approvals" }, "Mở màn hình Duyệt"),
            " — phiếu duyệt có hạn, đừng để quá giờ rồi mới mở.",
          ),
        )
      : null,
  );
}

/**
 * What a carried-out remedy leaves behind, and the one identifier that cannot be looked up again.
 *
 * @param {any} result a `RemedyExecutionResponse`
 * @returns {HTMLElement}
 */
function executionCard(result) {
  return h(
    "div",
    { class: "card stack" },
    h("h3", null, "Đã thực hiện"),
    result.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Kết quả được phát lại: cùng khoá thao tác đã gửi trước đó. Không có gì được làm hai lần.",
        )
      : null,
    facts([
      ["Loại", `${KIND_LABEL[result.kind] || result.kind} (${result.kind})`, { mono: true, span: true }],
      ["Trạng thái", result.status, { mono: true }],
      ["Sự kiện đã ghi", result.event_type, { mono: true }],
      ["Số tiền", money(result.amount_vnd), { span: true }],
    ]),
    result.credit_id
      ? h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chép mã giảm trừ này lại ngay — không tra lại được"),
          h(
            "p",
            null,
            "Khoản giảm trừ là phiếu cầm tay: ai cầm phiếu thì dùng được, và dùng đúng một lần. " +
              "Trong API này chưa có đường nào liệt kê các khoản giảm trừ chưa dùng của một đơn " +
              "hay một số phiếu, nên chỉ áp dụng được bằng đúng mã dưới đây. Chép vào phiếu giấy " +
              "của khách trước khi rời màn hình.",
          ),
          copyable({ value: result.credit_id }),
        )
      : h(
          "p",
          { class: "hint" },
          "Giặt lại không sinh khoản giảm trừ nào: không có đồng nào chuyển đi. Máy chủ ghi lệnh " +
            "giặt lại và đóng sự cố; việc giặt lại là việc mới ở xưởng, đơn cũ không quay lại dây " +
            "chuyền.",
        ),
    h(
      "p",
      { class: "hint" },
      "Sự cố gắn với đề nghị này đã chuyển sang CLOSED và cột bồi hoàn đã là “đã quyết định”.",
    ),
  );
}

/**
 * The cross-screen hand-off staged by an incident card's "Đề xuất bồi hoàn".
 *
 * Module state, in memory only, exactly like `screens/incidents.js`'s order carry-over: an
 * incident id is not a secret worth persisting, and the render below reads it once and clears it so
 * a later visit starts empty rather than resurrecting a stale incident.
 *
 * @type {string}
 */
let incidentPrefill = "";

/**
 * Stage an incident UUID for this screen's next render.
 *
 * @param {string} incidentId
 */
export function setRemedyIncidentPrefill(incidentId) {
  incidentPrefill = String(incidentId || "");
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const writeVerdict = can(principal(), "INCIDENTS_WRITE");
  const proposeSubmission = new Submission("remedy-propose");
  const executeSubmission = new Submission("remedy-execute");
  const redeemSubmission = new Submission("remedy-redeem");

  /** @type {{incidentId: string, kind: string, lineId: string, amount: string, lateness: string, fault: boolean}} */
  const draft = {
    incidentId: "",
    kind: REMEDY_KIND.FREE_REWASH,
    lineId: "",
    amount: "",
    lateness: "",
    fault: false,
  };

  // The hand-off staged by an incident card's "Đề xuất bồi hoàn", consumed exactly once here, and
  // the deep link `#/remedies?incident=…` that the same button writes. Both are read before the
  // inputs are built, so the field opens holding the incident rather than filling in afterwards.
  const staged = incidentPrefill || new URLSearchParams(location.hash.split("?")[1] || "").get("incident") || "";
  incidentPrefill = "";
  if (staged && UUID.test(staged)) draft.incidentId = staged;

  /** @type {any} the `RemedyOptionsResponse` for the incident currently loaded, or null */
  let options = null;
  /** @type {any} the proposal recorded in this session, so it can be carried out */
  let proposal = null;
  /** @type {any} what carrying it out returned, held for the credit id that cannot be looked up */
  let execution = null;

  /** The live reading under the money box, rebuilt in place so the box itself is never rebuilt. */
  /** @type {HTMLElement|null} */
  let amountFeedback = null;

  const optionsHost = h("div", { class: "stack" });
  const formHost = h("div", { class: "stack" });
  const proposalHost = h("div", { class: "stack" });
  const executionHost = h("div", { class: "stack" });
  const optionsResult = resultLine();
  const proposeResult = resultLine();
  const executeResult = resultLine();

  const incidentInput = h("input", {
    type: "text",
    value: draft.incidentId,
    autocomplete: "off",
    spellcheck: "false",
    dataFormat: "id",
    placeholder: "00000000-0000-0000-0000-000000000000",
    "aria-invalid": draft.incidentId && !UUID.test(draft.incidentId) ? "true" : null,
    onInput: (event) => {
      const raw = event.target.value.trim();
      if (raw !== event.target.value) event.target.value = raw;
      draft.incidentId = raw;
      proposeSubmission.reset();
      event.target.setAttribute("aria-invalid", raw && !UUID.test(raw) ? "true" : "false");
    },
  });

  const kindSelect = h(
    "select",
    {
      name: "remedy-kind",
      onChange: (event) => {
        draft.kind = event.target.value;
        draft.lineId = "";
        draft.amount = "";
        draft.lateness = "";
        proposeSubmission.reset();
        redrawForm();
      },
    },
    REMEDY_KIND_ORDER.map((kind) =>
      h("option", { value: kind, selected: kind === draft.kind, title: kind }, KIND_LABEL[kind]),
    ),
  );

  /**
   * Rebuild the summary and the fields under it for the kind currently chosen.
   *
   * The summary is rebuilt from the plan on every change, and the money box is *not* — rebuilding
   * an input the operator is typing into is the defect `verify_console_interaction.py` exists to
   * catch, where a code typed one character at a time came out as its first letter.
   */
  function redrawForm() {
    redrawRecords();
    if (options === null) {
      render(formHost);
      return;
    }
    const plan = currentPlan();

    render(
      formHost,
      draft.kind === REMEDY_KIND.LOST_ITEM ? lossNotice() : null,
      options.order_refunded === true && plan.needsLine ? refundedNotice() : null,
      ceilingSummary(plan, options),
      planNotice(plan),
      plan.needsLine ? lineField() : null,
      plan.needsAmount ? amountField() : null,
      plan.needsLateness ? latenessField() : null,
      faultField(),
      h("div", { class: "action-bar" }, gated(proposeButton, writeVerdict)),
      proposeResult,
    );
    if (!writeVerdict.allowed) gatedFields(formHost, writeVerdict);
  }

  /**
   * Show a recorded proposal or execution only while its own kind is the one selected.
   *
   * A proposal belongs to the kind it was raised for. Left on screen after the picker moved, a
   * damage proposal kept its server-computed 600.000 ₫ ceiling visible underneath the loss form,
   * and kept a live "Thực hiện bồi hoàn" button that would carry out that damage remedy while the
   * form above it described a different kind -- and a loss that only the owner may release. The
   * first misleads; the second is worse, because it is a press.
   *
   * Hidden rather than discarded, and this is the whole reason the two results are held in
   * variables. The credit id is a bearer instrument that no route can look up again, and the
   * proposal id is the only handle the execution route takes, so throwing either away because
   * somebody read the picker would lose work the server has already done. Selecting that kind
   * again brings its record straight back.
   */
  function redrawRecords() {
    if (proposal && proposal.kind === draft.kind) {
      render(proposalHost, proposalCard(proposal), executeControls(), executeResult);
    } else {
      render(proposalHost);
    }
    if (execution && execution.kind === draft.kind) {
      render(executionHost, executionCard(execution));
    } else {
      render(executionHost);
    }
  }

  /** @returns {ReturnType<typeof remedyPlan>} */
  function currentPlan() {
    return remedyPlan({
      options,
      kind: draft.kind,
      storeFaultAttested: draft.fault,
      lineId: draft.lineId,
      typedAmount: draft.amount,
      typedLateness: draft.lateness,
    });
  }

  /**
   * @param {ReturnType<typeof remedyPlan>} plan
   * @returns {HTMLElement|null}
   */
  function planNotice(plan) {
    const note = PLAN_NOTE[plan.state];
    if (!note) return null;
    const blocking =
      plan.state === PLAN.POLICY_UNPUBLISHED ||
      plan.state === PLAN.WINDOW_EVIDENCE_MISSING ||
      plan.state === PLAN.WINDOW_CLOSED ||
      plan.state === PLAN.CREDIT_UNAVAILABLE ||
      plan.state === PLAN.ABOVE_CEILING ||
      plan.state === PLAN.NOT_AN_AMOUNT;
    return h(
      "div",
      { class: "notice", dataState: blocking ? "danger" : "info" },
      h("p", { class: "notice__title" }, planTitle(plan)),
      h("p", null, note),
    );
  }

  /**
   * @param {ReturnType<typeof remedyPlan>} plan
   * @returns {string}
   */
  function planTitle(plan) {
    if (plan.state === PLAN.ABOVE_CEILING) {
      return `Vượt trần: trần của dòng này là ${money(plan.ceilingVnd)}`;
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
    if (plan.state === PLAN.NOT_AN_AMOUNT) return "Chưa đọc được số tiền";
    if (plan.state === PLAN.BELOW_LATENESS_THRESHOLD) return "Chưa tới ngưỡng giao trễ";
    if (plan.state === PLAN.FAULT_NOT_ATTESTED) return "Chưa xác định lỗi thuộc về tiệm";
    return "Còn thiếu dữ kiện";
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
      label:
        draft.kind === REMEDY_KIND.LOST_ITEM
          ? "Món bị mất (dòng đã có giá trên đơn)"
          : "Món bị hỏng (dòng đã có giá trên đơn)",
      hint:
        "Trần là 5 lần phí giặt của một món: đồ tính theo cái lấy giá một cái, đồ tính theo ký " +
        "lấy tiền cả túi. “Đã ghi” là số các đề nghị trước đã giữ cho món đó. Không có dòng nào " +
        "ở đây nghĩa là máy chủ chưa gửi điều kiện từng món.",
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
    return `${line.label}${count} · trần ${money(line.ceiling)}${held}${owner}`;
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
        refreshAmountFeedback(event.target);
      },
    });
    amountFeedback = h("div", { class: "stack stack--tight" });
    const field = labelled({
      id: "remedy-amount",
      label: "Số tiền đề nghị đền cho khách (đồng)",
      hint:
        "Số nguyên đồng. Không gõ trần vào đây — trần đã hiện ở trên và do máy chủ tính. Số này " +
        "cộng với số đã ghi cho món mà vượt trần thì máy chủ từ chối chứ không tự hạ xuống.",
      control: input,
    });
    refreshAmountFeedback(input);
    return h("div", { class: "stack stack--tight" }, field, amountFeedback);
  }

  /**
   * The live reading of the money box, updated without rebuilding the box itself.
   *
   * @param {HTMLElement} input
   */
  function refreshAmountFeedback(input) {
    const plan = currentPlan();
    const bad = plan.state === PLAN.ABOVE_CEILING || plan.state === PLAN.NOT_AN_AMOUNT;
    input.setAttribute("aria-invalid", bad ? "true" : "false");
    if (amountFeedback) {
      render(
        amountFeedback,
        draft.amount.trim() ? planNotice(plan) : null,
        draft.amount.trim() && plan.requiresOwner
          ? h(
              "p",
              { class: "hint" },
              `Gửi đi sẽ lập phiếu chờ chủ tiệm duyệt: ${ownerReasonText(plan.ownerReasons)}. ` +
                "Nói trước với khách là phải chờ.",
            )
          : null,
      );
    }
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
      },
    });
    return labelled({
      id: "remedy-lateness",
      label: "Chuyến giao trễ bao nhiêu phút (lời khai của nhân viên)",
      hint:
        "Tiệm không lưu giờ hẹn giao, nên máy chủ không suy ra được mức trễ — đây là lời khai của " +
        `người đã giao. Ngưỡng đã công bố: ${minutesLabel(options?.late_delivery_threshold_minutes)}. ` +
        "Số tiền giảm trừ do máy chủ tính, không gõ ở đây.",
      control: input,
    });
  }

  /** @returns {HTMLElement} */
  function faultField() {
    const box = h("input", {
      type: "checkbox",
      checked: draft.fault,
      onChange: (event) => {
        draft.fault = Boolean(event.target.checked);
        proposeSubmission.reset();
        redrawForm();
      },
    });
    return labelled({
      id: "remedy-fault",
      label: "Tôi xác định lỗi thuộc về tiệm",
      hint:
        "Bắt buộc, và không có giá trị mặc định: DEC-004 đặt mọi khoản bồi hoàn lên việc một " +
        "người xác định lỗi thuộc về tiệm. Tên bạn được ghi kèm quyết định này.",
      control: box,
    });
  }

  const proposeButton = h(
    "button",
    { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
    "Gửi đề nghị bồi hoàn",
  );

  /**
   * Read the ceilings and windows for one incident. A pure read: nothing is written or reserved.
   */
  async function loadOptions() {
    if (!UUID.test(draft.incidentId)) {
      setResult(optionsResult, "danger", "Mã sự cố phải là UUID đủ 36 ký tự.");
      return;
    }
    options = null;
    proposal = null;
    render(proposalHost);
    render(executionHost);
    setResult(proposeResult, null, null);
    setResult(executeResult, null, null);
    render(formHost);
    render(optionsHost, skeleton(2));
    setResult(optionsResult, "warn", "Đang đọc mức trần và thời hạn…");
    try {
      const loaded = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/incidents/${encodeURIComponent(draft.incidentId)}/remedy-options`,
      );
      options = loaded;
      setResult(
        optionsResult,
        loaded.policy_published ? "ok" : "danger",
        loaded.policy_published
          ? `Đã đọc mức trần và thời hạn cho sự cố ${shortId(loaded.incident_id)}.`
          : "Chưa có chính sách bồi hoàn được công bố. Mọi loại bồi hoàn đều bị từ chối.",
      );
      render(optionsHost, optionsCard(loaded));
      redrawForm();
    } catch (error) {
      options = null;
      setResult(optionsResult, error.kind === "DENIED" ? "warn" : "danger", "Không đọc được.");
      const notice = errorNotice(error, { onRetry: () => void loadOptions() });
      render(optionsHost, notice);
      render(formHost);
      revealError(notice);
    }
  }

  /**
   * Everything the server published for this incident, before any kind is chosen.
   *
   * @param {any} loaded
   * @returns {HTMLElement}
   */
  function optionsCard(loaded) {
    const lines = damageLines(loaded);
    return h(
      "div",
      { class: "card stack" },
      facts([
        ["Sự cố", h("span", { title: loaded.incident_id }, shortId(loaded.incident_id)), { mono: true }],
        [
          "Đơn liên quan",
          h(
            "a",
            { href: `#/orders/${encodeURIComponent(loaded.order_id || "")}`, title: loaded.order_id },
            shortId(loaded.order_id),
          ),
          { mono: true },
        ],
        ["Khách nhận đồ lúc", dateTime(loaded.goods_returned_at), { span: true }],
        [
          "Nhân viên duyệt được tới",
          money(loaded.staff_approval_ceiling_vnd),
          { span: true },
        ],
        [
          "Giặt lại — hạn chót",
          h(
            "span",
            null,
            dateTime(loaded.rewash_window_closes_at),
            " · ",
            loaded.rewash_window_open ? "còn hạn" : "đã đóng",
          ),
          { span: true },
        ],
        [
          "Báo hỏng — hạn chót",
          h(
            "span",
            null,
            dateTime(loaded.defect_window_closes_at),
            " · ",
            loaded.defect_window_open ? "còn hạn" : "đã đóng",
          ),
          { span: true },
        ],
        [
          "Giảm trừ giao trễ máy chủ tính",
          h(
            "span",
            null,
            money(loaded.late_delivery_credit_vnd),
            Number.isInteger(loaded.late_delivery_credit_vnd)
              ? null
              : h(
                  "span",
                  { class: "hint" },
                  " Không có chuyến giao nào đã giao thành công, hoặc chưa có tổng nào được tất " +
                    "toán. Ô này để trống chứ không phải bằng 0.",
                ),
          ),
          { span: true },
        ],
        [
          "Số dòng có thể đền",
          lines.length ? String(lines.length) : `${UNKNOWN} (máy chủ chưa gửi dòng nào có giá)`,
          { span: true },
        ],
        [
          "Đơn đã hoàn tiền?",
          loaded.order_refunded === true
            ? h("strong", null, "Có — mọi khoản đền đều chờ chủ tiệm duyệt")
            : loaded.order_refunded === false
              ? "Không"
              : UNKNOWN,
          { span: true },
        ],
      ]),
      h(
        "p",
        { class: "hint" },
        "Đọc mức trần không ghi gì và không giữ chỗ gì. Bấm lại bất cứ lúc nào để xem số mới nhất.",
      ),
    );
  }

  /**
   * @param {SubmitEvent} event
   */
  async function propose(event) {
    event.preventDefault();
    const plan = currentPlan();
    const body = remedyProposalBody(plan, draft);
    if (body === null) {
      setResult(proposeResult, "danger", PLAN_NOTE[plan.state] || "Chưa gửi được.");
      return;
    }
    setResult(proposeResult, "warn", "Đang gửi đề nghị…");
    try {
      const created = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/incidents/${encodeURIComponent(draft.incidentId)}/remedy-proposals`,
        { method: "POST", body, idempotencyKey: proposeSubmission.key() },
      );
      proposeSubmission.reset();
      proposal = created;
      // A fresh proposal supersedes whatever the last one was carried out as, so the old execution
      // record goes rather than sitting above a proposal it did not come from.
      execution = null;
      setResult(
        proposeResult,
        awaitsOwner(created.status) ? "warn" : "ok",
        awaitsOwner(created.status)
          ? `Đã ghi đề nghị ${shortId(created.proposal_id)}. Phải có chủ tiệm duyệt trước khi thực hiện.`
          : `Đã ghi đề nghị ${shortId(created.proposal_id)}. Bấm thực hiện để tiến hành.`,
      );
      redrawRecords();
    } catch (error) {
      const refused = error.kind === "DENIED" || error.kind === "REQUIRE_HUMAN" || error.status === 422;
      setResult(
        proposeResult,
        refused ? "warn" : "danger",
        refused
          ? "Máy chủ từ chối đề nghị này. Không có gì được ghi — đọc mã lý do bên dưới."
          : "Không gửi được đề nghị.",
      );
      const notice = errorNotice(error);
      render(proposalHost, notice);
      revealError(notice);
    }
  }

  /** @returns {HTMLElement} */
  function executeControls() {
    const button = h(
      "button",
      { type: "button", dataVariant: "primary", dataRequiresNetwork: "true", onClick: execute },
      "Thực hiện bồi hoàn",
    );
    return h(
      "div",
      { class: "stack stack--tight" },
      h(
        "p",
        { class: "hint" },
        "Thực hiện không gửi lại số tiền: mọi thứ đã được quyết khi ghi đề nghị và nằm bất biến " +
          "trên bản ghi đó. Cho gõ lại số ở bước này là để con số đổi giữa lúc chủ tiệm duyệt và " +
          "lúc tiệm trả tiền.",
      ),
      h("div", { class: "action-bar" }, gated(button, writeVerdict)),
    );
  }

  async function execute() {
    if (!proposal) return;
    setResult(executeResult, "warn", "Đang thực hiện…");
    try {
      const done = await request(
        `/internal/v1/remedy-proposals/${encodeURIComponent(proposal.proposal_id)}/execution`,
        { method: "POST", idempotencyKey: executeSubmission.key() },
      );
      executeSubmission.reset();
      execution = done;
      setResult(executeResult, "ok", "Đã thực hiện. Sự cố đã đóng.");
      render(executionHost, executionCard(done));

    } catch (error) {
      const refused = error.kind === "DENIED" || error.status === 422;
      setResult(
        executeResult,
        refused ? "warn" : "danger",
        refused
          ? "Máy chủ chưa cho thực hiện. Thường là vì phiếu duyệt của chủ tiệm chưa có hoặc đã hết hạn."
          : "Không thực hiện được.",
      );
      const notice = errorNotice(error);
      render(executionHost, notice);
      revealError(notice);
    }
  }

  const form = h(
    "form",
    { class: "form", onSubmit: propose },
    labelled({
      id: "remedy-kind",
      label: "Loại bồi hoàn",
      hint: KIND_NOTE[draft.kind],
      control: kindSelect,
    }),
    formHost,
  );

  // The kind's own note changes with the kind, and it lives on the label rather than in the body,
  // so it is refreshed in place rather than by rebuilding the picker the operator just used.
  kindSelect.addEventListener("change", () => {
    const hint = form.querySelector("#remedy-kind-hint");
    if (hint) hint.textContent = KIND_NOTE[draft.kind] || "";
  });

  const redemption = redemptionPanel(store, redeemSubmission, writeVerdict);

  if (draft.incidentId) void loadOptions();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Bồi hoàn sự cố · DEC-004"),
      h("h1", null, "Bồi hoàn"),
      h(
        "p",
        { class: "screen__lede" },
        "Một sự cố đi tới kết cục ở đây: giặt lại, đền món hỏng, đền món mất, hoặc giảm trừ do " +
          "giao trễ. Mức trần, thời hạn và việc có cần chủ tiệm duyệt hay không đều do máy chủ " +
          "tính và hiện ra trước khi bạn gõ bất cứ con số nào.",
      ),
    ),
    panel({
      eyebrow: "Đọc trước",
      title: "Sự cố này được phép bồi hoàn tới đâu",
      guardrail:
        "Màn hình này không tính tiền và không đặt trần. 5 lần phí giặt của một món lấy từ bản giá " +
        "của chính đơn, 10% lấy từ tổng đã tất toán, cửa sổ đo từ mốc khách nhận đồ — máy chủ tính " +
        "hết. Chưa công bố chính sách thì mọi loại đều bị từ chối, không có mức tạm.",
      children: h(
        "div",
        { class: "stack" },
        labelled({
          id: "remedy-incident",
          label: "Mã sự cố (incident_id)",
          hint:
            "Lấy từ màn hình Sự cố — bấm “Đề xuất bồi hoàn” trên đúng sự cố thì mã tự điền sang " +
            "đây. Sự cố phải thuộc cửa hàng đang chọn.",
          control: incidentInput,
        }),
        h(
          "div",
          { class: "action-bar" },
          h(
            "button",
            { type: "button", dataRequiresNetwork: "true", onClick: () => void loadOptions() },
            "Đọc mức trần và thời hạn",
          ),
          h("button", { type: "button", dataVariant: "quiet", onClick: () => navigate("/incidents") }, "Mở màn hình Sự cố"),
        ),
        optionsResult,
        optionsHost,
      ),
    }),
    panel({
      eyebrow: "Lệnh",
      title: "Đề nghị một khoản bồi hoàn",
      guardrail:
        "Nhân viên không bao giờ gõ trần. Vượt trần thì bị từ chối kèm con số trần, không bị tự " +
        "hạ xuống. Mất đồ, và mọi khoản đền trên đơn đã hoàn tiền, luôn chờ chủ tiệm duyệt.",
      children: h("div", { class: "stack" }, gatedFields(form, writeVerdict), proposalHost, executionHost),
    }),
    redemption,
  );
}

/**
 * Spending one credit on the next bill.
 *
 * This panel asks for four values and that is the honest shape of the thing today: there is no
 * route that lists an order's or a ticket's unredeemed credits, so a credit can only be applied by
 * its identifier, and the revision and digest are the caller's evidence that it read the quote it
 * is spending on before it changed the total.
 *
 * @param {string} store
 * @param {import("../core/api.js").Submission} submission
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {HTMLElement}
 */
function redemptionPanel(store, submission, verdict) {
  const draft = { quoteId: "", creditId: "", revision: "", snapshotHash: "" };
  const result = resultLine();
  const host = h("div", { class: "stack" });

  /** @param {string} key @param {string} placeholder @param {RegExp} pattern */
  const field = (key, placeholder, pattern) =>
    h("input", {
      type: "text",
      autocomplete: "off",
      spellcheck: "false",
      dataFormat: key === "snapshotHash" ? "hash" : "id",
      placeholder,
      onInput: (event) => {
        const raw = event.target.value.trim();
        if (raw !== event.target.value) event.target.value = raw;
        draft[key] = raw;
        submission.reset();
        event.target.setAttribute("aria-invalid", raw && !pattern.test(raw) ? "true" : "false");
      },
    });

  const HASH = /^JCS-SHA256-V1:[0-9a-f]{64}$/;
  const REVISION = /^[1-9]\d{0,8}$/;

  const button = h(
    "button",
    { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
    "Áp dụng khoản giảm trừ",
  );

  /** @param {SubmitEvent} event */
  async function submit(event) {
    event.preventDefault();
    if (!UUID.test(draft.quoteId) || !UUID.test(draft.creditId)) {
      setResult(result, "danger", "Mã báo giá và mã giảm trừ đều phải là UUID đủ 36 ký tự.");
      return;
    }
    if (!REVISION.test(draft.revision)) {
      setResult(result, "danger", "Số bản sửa đổi phải là số nguyên dương, lấy đúng trên bản báo giá.");
      return;
    }
    if (!HASH.test(draft.snapshotHash)) {
      setResult(result, "danger", "Dấu vân bản báo giá phải đúng dạng JCS-SHA256-V1:… — chép nguyên văn.");
      return;
    }
    setResult(result, "warn", "Đang áp dụng…");
    try {
      const applied = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(draft.quoteId)}/remedy-credits`,
        {
          method: "POST",
          body: {
            credit_id: draft.creditId,
            expected_current_revision: Number.parseInt(draft.revision, 10),
            expected_snapshot_hash: draft.snapshotHash,
          },
          idempotencyKey: submission.key(),
        },
      );
      submission.reset();
      setResult(result, "ok", `Đã áp dụng vào bản sửa đổi ${applied.revision}.`);
      render(
        host,
        h(
          "div",
          { class: "card stack" },
          h("h3", null, "Đã trừ vào hoá đơn lần này"),
          facts([
            ["Bản sửa đổi mới", String(applied.revision), { mono: true }],
            ["Khoản đã trừ", money(applied.credit_vnd), { span: true }],
            ["Tiền dịch vụ sau giảm trừ", money(applied.net_service_subtotal_vnd), { span: true }],
            ["Tổng hiển thị", money(applied.display_total_vnd), { span: true }],
            [
              "Dấu vân bản mới",
              copyable({
                value: applied.snapshot_hash || "",
                display: String(applied.snapshot_hash || UNKNOWN).slice(0, 26),
              }),
              { span: true },
            ],
          ]),
          h(
            "p",
            { class: "hint" },
            "Phiếu này đã dùng xong và không dùng lại được: bấm lần nữa máy chủ trả " +
              "REMEDY_CREDIT_ALREADY_REDEEMED. Đọc lại tổng mới cho khách nghe trước khi thu tiền.",
          ),
        ),
      );
    } catch (error) {
      const refused = error.kind === "DENIED" || error.status === 422 || error.kind === "STALE";
      setResult(
        result,
        refused ? "warn" : "danger",
        refused
          ? "Máy chủ từ chối áp dụng khoản này. Không có gì được ghi — đọc mã lý do bên dưới."
          : "Không áp dụng được.",
      );
      const notice = errorNotice(error);
      render(host, notice);
      revealError(notice);
    }
  }

  const form = gatedFields(
    h(
      "form",
      { class: "form", onSubmit: submit },
      labelled({
        id: "credit-id",
        label: "Mã khoản giảm trừ (credit_id)",
        hint:
          "Chép từ lúc phát hành, hoặc từ phiếu giấy của khách. Trong API này chưa có đường nào " +
          "liệt kê các khoản chưa dùng, nên không có mã thì không tra ra được.",
        control: field("creditId", "00000000-0000-0000-0000-000000000000", UUID),
      }),
      labelled({
        id: "credit-quote",
        label: "Mã báo giá sẽ trừ vào (quote_id)",
        hint: "Báo giá của lần này, đang còn mở. Bản đã chốt giá với khách thì không trừ vào được nữa.",
        control: field("quoteId", "00000000-0000-0000-0000-000000000000", UUID),
      }),
      labelled({
        id: "credit-revision",
        label: "Bản sửa đổi hiện tại",
        hint: "Số bản sửa đổi bạn vừa đọc trên màn hình Báo giá. Sai số thì máy chủ từ chối chứ không đoán.",
        control: field("revision", "1", REVISION),
      }),
      labelled({
        id: "credit-hash",
        label: "Dấu vân của bản sửa đổi đó",
        hint: "Chép nguyên văn, đủ cả tiền tố JCS-SHA256-V1:. Chép thiếu là một dấu vân khác.",
        control: field("snapshotHash", "JCS-SHA256-V1:…", HASH),
      }),
      h("div", { class: "action-bar" }, gated(button, verdict)),
      result,
    ),
    verdict,
  );

  return panel({
    eyebrow: "Lệnh",
    title: "Dùng một khoản giảm trừ cho hoá đơn lần sau",
    guardrail:
      "Khoản giảm trừ là phiếu cầm tay, dùng đúng một lần, và trừ vào tổng trước khi báo cho " +
      "khách — không phải trừ vào số khách đã đồng ý trả. Đường tất toán vẫn chỉ nhận đúng tổng " +
      "đã báo, đủ một lần.",
    children: h("div", { class: "stack" }, form, host),
  });
}

export const screen = {
  path: "/remedies",
  title: "Bồi hoàn",
  capability: "INCIDENTS_READ",
  needsStore: true,
  render: render_,
};
