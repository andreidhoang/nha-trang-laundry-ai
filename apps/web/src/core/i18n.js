/**
 * Vietnamese labels, and the spec-mandated tokens they gloss.
 *
 * The specification pulls in two directions. `IMPLEMENTATION_ROADMAP_V1.md:883` requires Vietnamese
 * labels; `:886` and `:896-904` mandate ten specific strings that are English uppercase tokens
 * (`HUMAN REQUIRED`, `TAX UNVERIFIED`, …) alongside three Vietnamese ones (`ƯỚC TÍNH`, `KHOẢNG GIÁ`,
 * `ĐÃ DUYỆT`). Nothing says which wins, and dropping either is a defect: the tokens are what an
 * eval or an auditor greps for, the Vietnamese is what the person at the counter reads.
 *
 * So both are rendered — but Vietnamese leads, because Vietnamese is the reading language of the
 * counter. The mandated token stays verbatim as the secondary caption (`Chưa xác minh thuế · TAX
 * UNVERIFIED`), never translated away and never abbreviated: it is what an eval or an auditor
 * greps for, and what an operator quotes when reporting a problem.
 *
 * Enum values follow the same rule. A server enum is shown Vietnamese-first with the verbatim
 * value in parentheses (`Nháp (DRAFT)`): the person at the counter reads their own language, and
 * the token survives for the conversation with engineering.
 *
 * @module core/i18n
 */

/** The three price-state labels of `IMPLEMENTATION_ROADMAP_V1.md:886`. These tokens are already
 * Vietnamese — they are the mandated display strings — so they lead and the gloss follows. */
export const PRICE_STATE = {
  ESTIMATE: { token: "ƯỚC TÍNH", gloss: "chưa phải giá cuối", state: "info", tokenFirst: true },
  RANGE: { token: "KHOẢNG GIÁ", gloss: "chờ nhân viên chọn giá chính xác", state: "warn", tokenFirst: true },
  APPROVED_EXACT: { token: "ĐÃ DUYỆT", gloss: "giá chính xác đã được duyệt", state: "ok", tokenFirst: true },
};

/** The seven critical warnings of `IMPLEMENTATION_ROADMAP_V1.md:896-904`. */
export const WARNING = {
  HUMAN_REQUIRED: { token: "HUMAN REQUIRED", gloss: "Cần người quyết định", state: "warn" },
  PROMOTION_PROVISIONAL: {
    token: "PROMOTION PROVISIONAL",
    gloss: "Khuyến mãi chưa chốt",
    state: "warn",
  },
  TAX_UNVERIFIED: { token: "TAX UNVERIFIED", gloss: "Chưa xác minh thuế", state: "warn" },
  CAPACITY_NOT_CONFIRMED: {
    token: "CAPACITY NOT CONFIRMED",
    gloss: "Chưa xác nhận năng lực/lịch",
    state: "warn",
  },
  DELIVERY_FEE_MISSING: {
    token: "DELIVERY FEE MISSING",
    gloss: "Chưa có phí giao hàng",
    state: "warn",
  },
  CUSTOMER_RECONFIRMATION: {
    token: "CUSTOMER RECONFIRMATION",
    gloss: "Cần khách xác nhận lại",
    state: "warn",
  },
  INCIDENT: { token: "INCIDENT", gloss: "Đơn có sự cố", state: "danger" },
};

/**
 * The answer for a reason code that states a settled fact rather than a risk to the price.
 *
 * Needed because the fallback below is `HUMAN REQUIRED` and an *absent* mapping cannot be told
 * apart from a deliberate silence. `PROMO-WIRING-001` made that distinction matter: a promotion is
 * now evaluated on every quote, and most of its answers are settled ones. With no programme
 * published — which is the shop's state today — every single quote carries
 * `PROMOTION_NOT_PUBLISHED`, and mapping that to `HUMAN REQUIRED` would put a permanent "cần người
 * quyết định" badge on every price in the shop. A warning that is always on is a warning nobody
 * reads, and the cost lands on the five promotion answers that do mean somebody must act.
 *
 * Silence here is never silence on the screen: every code below still appears verbatim in
 * `reasonCodeList` with its `REASON_NOTE` sentence. What it does not get is a badge claiming
 * somebody has to act.
 */
const STATES_A_FACT = Symbol("states a fact, raises no warning");

/**
 * Which mandated warning a domain reason code raises.
 *
 * The pairing is derived, not quoted: the spec fixes the strings in one place and the error codes
 * in another and never joins them. Each entry below is the join, and an unmapped code falls back to
 * `HUMAN REQUIRED`, which is the safe direction — over-warning costs a glance, under-warning costs
 * a wrong price.
 */
const REASON_TO_WARNING = {
  TAX_TREATMENT_UNVERIFIED: WARNING.TAX_UNVERIFIED,
  DELIVERY_FEE_UNRESOLVED: WARNING.DELIVERY_FEE_MISSING,
  DELIVERY_FEE_REQUIRES_HUMAN: WARNING.DELIVERY_FEE_MISSING,
  DELIVERY_DISTANCE_UNVERIFIED: WARNING.DELIVERY_FEE_MISSING,
  // The promotion vocabulary, as `packages/domain/.../promotion.py` and `quote_composition.py`
  // emit it. One retired code used to be the only entry here and it is gone: PROMO-WIRING-001
  // deleted it from the domain because a promotion is now always assessed, and a gloss for a code
  // no server can send is dead weight that hides the ones that are missing. It is named nowhere in
  // this file on purpose — `test_the_deleted_promotion_reason_code_can_no_longer_be_emitted_or_
  // rendered` greps the console tree for it, so the console is where the absence has to be total.
  //
  // The split is between an answer and an open question, and the test of an open question is
  // whether anybody at the counter can still do something about this quote. A programme that is
  // not published, not running, or does not cover these services has answered: the price is list
  // price and the note says which of those zeroes it is.
  //
  // `PROMOTION_TARGET_REQUIRES_HUMAN` is on this side of the line and was not, before PROMO-FIX-002
  // decided what a `HUMAN_CONFIRM` target actually costs: the promotion does not apply, the line is
  // charged at the published price, and the quote sells. Nothing is pending, so a `HUMAN REQUIRED`
  // badge sent staff looking for an approval screen this system does not have — and would have sat
  // on the accepted, paid, immutable revision afterwards.
  //
  // `PROMOTION_STACKING_REQUIRES_HUMAN` joined it in PROMO-FIX-003 and stays there after
  // PROMO-FIX-004, for a reason that moved. It once offered the counter two instruments to choose
  // between, and PROMO-FIX-003 had the domain choose for them, which turned out to raise the bill
  // above the total the customer had just been read. The choice is a person's again — but it is
  // taken at the redemption, which `REMEDY_CREDIT_PROMOTION_NOT_STACKABLE` refuses outright while
  // the credit is still unspent. DEC-030 (2026-09-25) then made pricing follow the same rule:
  // a reprice releases a reserved credit when a non-stacking programme takes dong off. What is
  // left wearing this code is acceptance of a bill whose credit was already on the agreed price
  // before the programme began; the agreed price stands (DEC-021), nothing is pending, it sells.
  //
  // The rest are genuinely unfinished: eligibility resolves at acceptance, a band has to be closed
  // before a promotion can be computed against it, a discount that moved between the quote and the
  // handshake needs the bag priced again, and a band approved before a programme started has to be
  // proposed again.
  PROMOTION_APPLIED: STATES_A_FACT,
  PROMOTION_NOT_PUBLISHED: STATES_A_FACT,
  PROMOTION_OUTSIDE_INTERVAL: STATES_A_FACT,
  PROMOTION_NOT_TARGETED: STATES_A_FACT,
  PROMOTION_TARGET_REQUIRES_HUMAN: STATES_A_FACT,
  PROMOTION_STACKING_REQUIRES_HUMAN: STATES_A_FACT,
  PROMOTION_ELIGIBILITY_UNRESOLVED: WARNING.PROMOTION_PROVISIONAL,
  PROMOTION_PENDING_BAND_CLOSE: WARNING.PROMOTION_PROVISIONAL,
  PROMOTION_CHANGED_SINCE_QUOTE: WARNING.HUMAN_REQUIRED,
  PROMOTION_PUBLISHED_SINCE_APPROVAL: WARNING.HUMAN_REQUIRED,
  SLOT_APPROVAL_REQUIRED: WARNING.CAPACITY_NOT_CONFIRMED,
  CUSTOMER_RECONFIRMATION_REQUIRED: WARNING.CUSTOMER_RECONFIRMATION,
  RANGE_PRICE_REQUIRES_HUMAN: WARNING.HUMAN_REQUIRED,
  // `RangePriceRefusal`, from RANGE-PRICE-001. All three are the deterministic engine refusing an
  // amount rather than pricing it, which is `HUMAN REQUIRED` in the mandated vocabulary: a person
  // has to supply a different number, or republish the pricebook. None of them is a price.
  RANGE_PRICE_OUT_OF_BAND: WARNING.HUMAN_REQUIRED,
  RANGE_PRICE_NOT_APPLICABLE: WARNING.HUMAN_REQUIRED,
  RANGE_PRICE_PRICEBOOK_MISMATCH: WARNING.HUMAN_REQUIRED,
  PRICE_RULE_UNRESOLVED: WARNING.HUMAN_REQUIRED,
  MEASUREMENT_POLICY_UNRESOLVED: WARNING.HUMAN_REQUIRED,
  HUMAN_APPROVAL_REQUIRED: WARNING.HUMAN_REQUIRED,
};

/**
 * `null` for a code that states a settled fact; otherwise the warning it raises.
 *
 * @param {string} reasonCode
 * @returns {{token: string, gloss: string, state: string}|null}
 */
export function warningFor(reasonCode) {
  const known = REASON_TO_WARNING[reasonCode];
  if (known === STATES_A_FACT) return null;
  return known || WARNING.HUMAN_REQUIRED;
}

/**
 * Plain-language notes for the reason codes this API actually returns today.
 *
 * An operator sees the tax one on every quote and one of the promotion ones on every quote, so the
 * table has to say which are normal and which are blocking.
 */
export const REASON_NOTE = {
  TAX_TREATMENT_UNVERIFIED:
    "Mọi báo giá R1 đều mang mã này. Số tiền hiển thị không được gọi là đã gồm hay chưa gồm thuế.",
  DELIVERY_FEE_UNRESOLVED:
    "Chưa có phí giao hàng nên chưa có tổng cuối. Tiền dịch vụ bên dưới không phải số khách trả.",
  // The promotion vocabulary. One sentence stood here alone and said "chưa xét khuyến mãi" —
  // nothing has been assessed — which the domain stopped being able to say. Every sentence below
  // names which zero it is, or what has to happen next, because a bare 0 ₫ with no explanation is
  // the same failure as rendering a null total as 0.
  // Deliberately points at the two rows this screen really draws. There is no promotion panel yet
  // and no separate discount row, so the mức giảm is the gap between "Giá niêm yết trước giảm" and
  // the service-money row; saying so is honest, and naming a panel that does not exist would not be.
  PROMOTION_APPLIED:
    "Khuyến mãi đã áp vào bản báo giá này: chênh lệch giữa dòng “Giá niêm yết trước giảm” và dòng " +
    "tiền dịch vụ chính là mức giảm. Chương trình tạo ra con số đó được ghi trên bản báo giá bất biến.",
  PROMOTION_NOT_PUBLISHED:
    "Chưa có chương trình khuyến mãi nào được công bố, nên báo giá tính theo giá niêm yết. Đây không " +
    "phải là “chưa xét”: máy chủ đã xét và câu trả lời là không có chương trình nào đang chạy. Chủ tiệm " +
    "là người công bố chương trình; đừng hứa mức giảm nào ở quầy.",
  PROMOTION_OUTSIDE_INTERVAL:
    "Chương trình đã công bố không bao trùm thời điểm này, nên mức giảm là 0 ₫. Máy chủ gửi kèm " +
    "ngày kết thúc của chương trình — đây là một số không có lý do, không phải một ô bỏ trống.",
  PROMOTION_NOT_TARGETED:
    "Chương trình đang chạy không bao gồm dịch vụ trên bản báo giá này, nên không có mức giảm nào. " +
    "Chương trình là một danh sách dịch vụ: không có tên trong danh sách thì không được giảm.",
  PROMOTION_TARGET_REQUIRES_HUMAN:
    "Chủ tiệm chưa quyết định dịch vụ này có nằm trong chương trình hay không, nên khuyến mãi không " +
    "áp cho dòng này: tính đúng giá niêm yết. Không có gì phải chờ duyệt, cứ nhận đồ bình thường. " +
    "Muốn dòng này được giảm thì chủ tiệm công bố lại chương trình có tên dịch vụ; đừng tự giảm ở quầy.",
  // DEC-030 (2026-09-25): pricing now applies the programme and releases the credit, so this code
  // is left only on a bill whose credit was already on the price the customer agreed.
  PROMOTION_STACKING_REQUIRES_HUMAN:
    "Quy tắc của tiệm (DEC-030): khuyến mãi không cộng dồn thì đơn hưởng khuyến mãi, còn phiếu bồi " +
    "hoàn giữ nguyên, chưa dùng, cho đơn sau. Bản này có phiếu trên giá khách đã đồng ý từ trước " +
    "khi chương trình bắt đầu, nên giữ đúng giá đó. Không phải chờ ai duyệt.",
  PROMOTION_PENDING_BAND_CLOSE:
    "Dòng khoảng giá chưa được chốt nên chưa xét khuyến mãi được: khuyến mãi áp trên số tiền nhân viên " +
    "chọn, không áp trên khoảng. Chốt giá trong khoảng xong thì mức giảm mới hiện ra.",
  PROMOTION_ELIGIBILITY_UNRESOLVED:
    "Điều kiện hưởng khuyến mãi gắn với lúc khách đồng ý giao đồ, mà việc đó chưa xảy ra. Mức giảm " +
    "đang hiển thị là tạm tính và sẽ được kiểm lại đúng vào lúc chốt đơn.",
  PROMOTION_CHANGED_SINCE_QUOTE:
    "Mức giảm tính lại lúc chốt đơn không còn bằng mức đã đọc cho khách nghe, nên không chốt được và " +
    "không có gì được ghi. Báo giá lại rồi đọc số mới cho khách trước khi nhận đồ.",
  PROMOTION_PUBLISHED_SINCE_APPROVAL:
    "Chủ tiệm đã duyệt giá trong khoảng khi chưa có chương trình khuyến mãi nào, mà bây giờ đã có " +
    "một chương trình đang chạy. Áp nó vào sẽ làm số tiền chủ tiệm đã duyệt đổi khác, nên phải đề " +
    "xuất lại giá. Đề xuất lại xong thì chương trình được tính vào như mọi báo giá khác.",
  // `_outstanding_approvals` mints this and `accept_quote_revision` returns it, so the console
  // glosses it. It is not the dead entry the retired promotion code above was: that one's producer
  // had been deleted from the domain outright, while this guard is live, enforced, and has a test
  // that watches it refuse. What is true is narrower, and is what the sentence says out loud — no
  // composer writes an outstanding approval onto a quote revision today, so meeting this means a
  // stored revision is not what this system writes, and the counter is told not to press again.
  APPROVAL_OUTSTANDING_APPLY_PROMOTION:
    "Bản báo giá gốc còn một phiếu duyệt khuyến mãi chưa dứt điểm, nên không chốt được bản này và " +
    "không có gì được ghi. Hiện không có màn hình nào trong hệ thống tạo ra tình huống này: đây " +
    "là lỗi dữ liệu chứ không phải chữ bạn gõ. Chụp màn hình và báo kỹ thuật, đừng bấm lại.",
  MISSING_REQUIRED_FACT: "Thiếu dữ kiện bắt buộc; máy chủ không đoán.",
  RANGE_PRICE_REQUIRES_HUMAN: "Dịch vụ này có khoảng giá; nhân viên phải chọn giá chính xác.",
  // `RangePriceRefusal`, exactly as packages/domain/.../range_prices.py names them. Each says what
  // the counter should do next, because a staff member standing in front of a customer needs an
  // action and not only a refusal — and because none of these three is fixable by retyping harder.
  RANGE_PRICE_OUT_OF_BAND:
    "Số tiền nằm ngoài khoảng giá đã niêm yết cho dòng này. Khoảng lấy từ đúng bản báo giá khách " +
    "đã nghe, và cả hai đầu đều nhận. Không có gì được ghi: nhập lại một số trong khoảng, hoặc " +
    "báo chủ tiệm nếu khoảng giá cần đổi.",
  RANGE_PRICE_NOT_APPLICABLE:
    "Dòng này đã có giá cố định trong bảng giá, nên không chốt giá trong khoảng cho nó được. " +
    "Kiểm lại xem bạn có gửi số cho đúng dòng hay không.",
  RANGE_PRICE_PRICEBOOK_MISMATCH:
    "Khoảng giá được đọc từ một phiên bản bảng giá khác với phiên bản đã dùng để tính bản báo giá " +
    "này. Bảng giá đã công bố là bất biến, nên bản cũ vẫn giữ khoảng cũ. Tải lại bản báo giá.",
  PRICE_RULE_UNRESOLVED: "Không có quy tắc giá áp dụng được cho dòng này.",
  MEASUREMENT_POLICY_UNRESOLVED: "Chưa có chính sách đo lường được công bố cho cơ sở khối lượng này.",
  // `core/errors.js` mints this code itself for a 409 whose detail starts `HUMAN_APPROVAL_REQUIRED`,
  // and the domain emits it for a price submitted with no approval envelope behind it. It reached
  // `reasonCodeList` as a bare English token in both cases, which is the one thing this table
  // exists to stop.
  HUMAN_APPROVAL_REQUIRED:
    "Việc này cần một người thứ hai duyệt trước, và chưa có phiếu duyệt nào hợp lệ gắn với đúng " +
    "nội dung đang gửi. Không có gì được ghi. Mở màn hình Duyệt để xem phiếu, hoặc gửi lại đề nghị.",
  AMBIGUOUS_SERVICE: "Mã dịch vụ khớp nhiều mục; cần chọn rõ.",
  INCOMPATIBLE_UNIT: "Đơn vị không dùng được với dịch vụ này.",
  VALIDATION_ERROR: "Dữ liệu vào không hợp lệ với quy tắc miền.",
  // `SettlementRefusal`, exactly as packages/domain/.../settlement.py names them. Each says what
  // the counter should do now, because a staff member holding a customer's money needs a next
  // action and not only a refusal.
  // DEC-010 is resolved: exact payment in full, once. The commonest way to meet this code is a
  // slipped keystroke -- "13.200" for 132.000 -- so the note says to check and retype first.
  AMOUNT_IS_NOT_THE_EXACT_TOTAL:
    "Số tiền phải đúng bằng tổng của đơn, thu đủ một lần. Gõ nhầm một chữ số (ví dụ 13.200 thay " +
    "vì 132.000) cũng bị từ chối như vậy: kiểm tra lại số rồi nhập lại. Trả thiếu, trả thừa, đặt " +
    "cọc hay trả góp không được nhận — chủ tiệm đã quyết định như vậy (DEC-010). Nếu tổng khách " +
    "phải trả đã đổi thì báo giá lại.",
  COLLECTION_WAS_NOT_BY_THE_CUSTOMER:
    "Ô “khách đã tự lấy đồ” phải khớp với hình thức của đơn: khách tự lấy thì tích, đơn giao tận " +
    "nơi thì để trống và chặng giao mới là thứ đóng đơn.",
  NO_PRESENTABLE_TOTAL:
    "Báo giá gắn với đơn này chưa có tổng cuối, thường vì phí giao chưa chốt. Chưa có tổng thì " +
    "chưa thu được tiền.",
  TOTAL_IS_A_RANGE:
    "Báo giá này là một khoảng giá, chưa phải một số. Nhân viên phải chốt giá chính xác trước.",
  // The rest of what `record_settlement` can answer. These are not open decisions -- they are
  // states -- and they arrive with `decision: null`. Leaving them unglossed put a bare English
  // token in front of a counter holding a customer's money.
  ORDER_NOT_FOUND:
    "Không có đơn nào mang mã này ở cửa hàng đang chọn. Kiểm tra lại mã đơn trên bảng đơn.",
  ORDER_NOT_ACTIVE:
    "Chỉ đơn đang chạy mới tất toán được. Đơn chưa nhận đồ thì bấm “Nhận đồ” trước; đơn đã đóng hoặc " +
    "đã huỷ thì không thu tiền qua màn hình này.",
  ALREADY_SETTLED:
    "Đơn này đã được tất toán rồi — không phải lỗi của bạn, và không thu thêm lần nữa. Mở lại đơn " +
    "để xem lần tất toán đã ghi.",
  STALE_VERSION:
    "Có người vừa đổi đơn này trong lúc bạn đang xem. Tải lại đơn rồi ghi nhận theo số mới.",
  // PREPAID-DROPOFF-001 (DEC-032): the handover rule and the pickup command's refusals. States,
  // not open decisions, so each says what to do first.
  // Shown by two presses: "Khách đã nhận đồ" on a prepaid walk-in, and the settlement checkbox.
  // It used to answer only the second ("bấm Khách trả trước…"), which is advice about taking money
  // given to a staff member who had already taken it and was trying to hand the laundry over.
  GOODS_NOT_READY_FOR_HANDOVER:
    "Đồ chưa giặt xong nên chưa đưa cho khách được. Làm xong đơn tới “sẵn sàng tại cửa hàng” " +
    "rồi mới ghi nhận khách nhận đồ. Nếu khách đang trả tiền lúc gửi đồ thì bấm “Khách trả " +
    "trước”.",
  COLLECTION_REQUIRES_PAYMENT:
    "Đơn này chưa trả tiền. Khách trả lúc lấy đồ thì bấm “Thu tiền”, gõ đúng số khách đưa rồi " +
    "bấm “Ghi nhận đã thu tiền”.",
  NOT_A_PREPAID_SELF_COLLECTION:
    "Đơn này không phải khách tự mang đồ tới rồi tự lấy. Đơn giao tận nơi thì ghi chuyến giao.",
  ALREADY_COLLECTED: "Đơn này đã ghi nhận khách nhận đồ rồi. Không ghi lần nữa.",
  // `RemedyRefusal`, exactly as packages/domain/.../remedies.py names them, plus the codes
  // `RemedyProposalRepository` raises that are states rather than policy answers. The rule is the
  // settlement vocabulary's: a refusal a staff member meets with a customer in front of them may
  // not arrive as a bare English token, and each note says what to do next rather than only what
  // went wrong. `_raise_remedy_error` sends these as a 422 whose detail is an object carrying
  // `reason_code`, which `classify` reads through `reasonCodesOf`.
  REMEDY_POLICY_UNPUBLISHED:
    "Chưa có bản chính sách bồi hoàn nào được công bố, nên không có con số nào để áp dụng và " +
    "không được suy ra con số nào — kể cả cho loại không chuyển tiền, vì cửa sổ 7 ngày cũng là " +
    "một con số đã công bố. Báo chủ tiệm công bố chính sách; đừng hứa mức nào ở quầy.",
  // Since DEC-031 only a loss recorded before 2026-09-25 carries this: it has no figure and is
  // never paid. A new loss is proposed with a figure and goes to the owner.
  LOSS_POLICY_UNRESOLVED:
    "Bản ghi mất đồ này được lập khi chủ tiệm chưa có chính sách cho mất đồ, nên không có số tiền " +
    "và không trả được. Muốn đền, lập đề nghị mất đồ mới cho đúng món: đề nghị đó chờ chủ tiệm " +
    "duyệt.",
  // Re-keyed when the two damage figures became cumulative per item: the ceiling is compared against
  // what earlier proposals on the same garment already committed plus this one, and the refusal
  // carries both numbers (`ceiling_vnd`, `committed_vnd`).
  REMEDY_CEILING_EXCEEDED:
    "Vượt trần máy chủ tính. Mỗi món tối đa 5 lần phí giặt của món đó (giá một cái, hoặc tiền cả " +
    "túi nếu tính theo ký), cộng dồn mọi đề nghị hỏng và mất đã ghi cho món đó — kể cả đề nghị " +
    "ghi cho cả dòng từ trước khi chọn được từng món — dù ai duyệt; cả dòng tối đa bằng trần của " +
    "mọi món trên dòng. Máy chủ từ chối kèm con số trần và số đã ghi, không tự hạ xuống. Báo chủ " +
    "tiệm nếu vụ này cần khác đi.",
  REMEDY_INCIDENT_NOT_OPEN:
    "Sự cố này đã có kết quả và đã đóng, nên không ghi thêm đề nghị bồi hoàn nào vào đó. Nếu khách " +
    "báo một vấn đề mới, mở một sự cố mới cho đơn; các mức trần vẫn tính chung theo từng món.",
  REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED:
    "Đơn này đã có một khoản giảm trừ giao trễ được đề nghị hoặc đã trả. Một lần giao trễ chỉ được " +
    "một khoản 10%, dù mở bao nhiêu sự cố về nó. Không có gì được ghi.",
  REMEDY_CREDIT_ALREADY_ON_QUOTE:
    "Phiếu giảm trừ này đã nằm trên bản báo giá này rồi. Một phiếu chỉ trừ vào một hoá đơn một lần; " +
    "không có gì được ghi và tổng không đổi.",
  // A quote reason code, not a refusal: a re-priced revision that could not carry a credit its
  // previous revision reserved. The credit is not spent and not shrunk, so the customer still has it.
  // DEC-030 added the fourth reason: a non-stacking programme applies to this bill, so the
  // promotion is taken and the credit waits for a later order.
  REMEDY_CREDIT_RELEASED:
    "Bản báo giá trước có phiếu giảm trừ, nhưng bản vừa tính lại không trừ phiếu đó: hoá đơn mới " +
    "nhỏ hơn giá trị phiếu, là khoảng giá, phiếu đã dùng cho đơn khác, hoặc đơn này đang hưởng " +
    "khuyến mãi không cộng dồn (DEC-030: đơn hưởng khuyến mãi, phiếu để dành). Phiếu KHÔNG bị trừ " +
    "và không bị cắt bớt: nếu chưa dùng ở đơn khác thì khách vẫn còn nguyên cho lần sau. Nói rõ " +
    "với khách trước khi chốt.",
  REMEDY_WINDOW_CLOSED:
    "Đã quá cửa sổ chủ tiệm công bố cho loại này, đo từ lúc khách nhận đồ. Nói với khách đúng mốc " +
    "đã qua chứ không chỉ nói là hết hạn. Muốn làm ngoài cửa sổ thì phải hỏi chủ tiệm.",
  REMEDY_WINDOW_EVIDENCE_MISSING:
    "Tiệm không có bản ghi nào cho biết khách đã nhận đồ lúc nào, nên không đo được cửa sổ nào " +
    "cả. Đây không phải lỗi máy và cũng không phải mất dữ liệu: đơn này được trả trước khi hệ " +
    "thống bắt đầu ghi mốc giao đồ. Lấy hôm nay hay ngày tạo đơn thay vào là bịa ra một phép đo.",
  REMEDY_STORE_FAULT_NOT_ATTESTED:
    "Chưa có nhân viên nào xác định lỗi thuộc về tiệm, mà DEC-004 đặt mọi khoản bồi hoàn lên đúng " +
    "việc đó. Xác định rồi hãy gửi; tên người xác định được ghi kèm.",
  REMEDY_AMOUNT_NOT_APPLICABLE:
    "Có ô không thuộc về loại bồi hoàn đang chọn, hoặc thiếu ô bắt buộc của loại đó. Chỉ đền món " +
    "hỏng và đền món mất mới có người gõ món và số tiền: giặt lại không chuyển tiền, giảm trừ giao " +
    "trễ do máy chủ tính.",
  REMEDY_LINE_NOT_PRICED:
    "Dòng được chọn không có trong bản báo giá hiện tại của đơn, nên không có phí giặt nào để lấy " +
    "5 lần. Mở lại đơn, đọc đúng mã dòng trên bản giá.",
  REMEDY_ORDER_NOT_SETTLED:
    "Đơn chưa tất toán nên không có tổng nào để lấy 10%. Thu tiền xong rồi mới đề nghị giảm trừ " +
    "cho lần sau được.",
  // REMEDY-GARMENT-001 (the DEC-031 addendum): a claim on a line priced per piece names which
  // garment it is, because each garment has its own staff limit and its own 5x ceiling.
  REMEDY_GARMENT_REQUIRED:
    "Dòng này có nhiều món tính giá theo cái, mỗi món có mức nhân viên duyệt và trần riêng, nên " +
    "phải chọn “Món thứ mấy”. Máy chủ không tự đoán là món thứ nhất. Không có gì được ghi.",
  REMEDY_GARMENT_NOT_APPLICABLE:
    "Dòng hoặc loại bồi hoàn này không tách theo từng món: đồ tính theo ký (cả túi), dòng không ghi " +
    "giá từng món, hoặc giặt lại / giảm trừ giao trễ. Bỏ ô “Món thứ mấy” rồi gửi lại.",
  REMEDY_GARMENT_OUT_OF_RANGE:
    "Dòng này không có món thứ đó: số thứ tự món phải từ 1 tới số lượng trên dòng. Chọn lại đúng " +
    "món; không có gì được ghi.",
  REMEDY_DELIVERY_NOT_RECORDED:
    "Đơn này không có chuyến giao nào đã giao thành công, nên không thể có chuyến giao trễ. Khách " +
    "tự lấy ở quầy thì dùng loại khác.",
  REMEDY_LATENESS_BELOW_THRESHOLD:
    "Số phút khai chưa vượt ngưỡng chủ tiệm công bố, nên theo chính sách đây chưa phải giao trễ. " +
    "Không có gì được ghi.",
  REMEDY_CREDIT_UNALLOCATABLE:
    "Khoản giảm trừ lớn hơn phần còn có thể giảm trên bản báo giá định trừ vào. Máy chủ từ chối " +
    "và giữ nguyên phiếu chứ không cắt bớt — cắt bớt là xoá một phần nợ tiệm đang nợ khách. Trừ " +
    "vào một hoá đơn lớn hơn.",
  REMEDY_CREDIT_REVISION_NOT_OPEN:
    "Bản báo giá định trừ vào đang là khoảng giá hoặc khách đã chốt rồi. Giảm trừ chỉ đổi tổng " +
    "trước khi báo cho khách, không đổi tổng đã thoả thuận. Lập bản mới rồi trừ vào bản đó.",
  // DEC-030 (2026-09-25, option A): the promotion applies and the credit waits. This refusal was
  // already that outcome; the gloss now says it is the rule rather than a choice left open.
  REMEDY_CREDIT_PROMOTION_NOT_STACKABLE:
    "Bản báo giá này đã có khuyến mãi không cho cộng dồn. Theo quy tắc của tiệm (DEC-030), đơn " +
    "này hưởng khuyến mãi, còn phiếu bồi hoàn chưa bị trừ và giữ nguyên để dùng cho đơn sau. " +
    "Không có gì được ghi, tổng vừa đọc cho khách không đổi. Nói với khách: lần này được khuyến " +
    "mãi, phiếu để dành lần sau.",
  REMEDY_CREDIT_ALREADY_REDEEMED:
    "Phiếu giảm trừ này đã dùng rồi. Phiếu là vật cầm tay, dùng đúng một lần — không phải lỗi của " +
    "bạn, và không trừ thêm lần nữa. Mở lại báo giá cũ để xem lần đã trừ.",
  REMEDY_APPROVAL_REQUIRED:
    "Khoản này phải có chủ tiệm duyệt trước khi thực hiện: vượt mức nhân viên duyệt được, là mất " +
    "đồ, hoặc đơn đã hoàn tiền. Mở màn hình Duyệt; chưa duyệt mà bấm thực hiện thì máy chủ từ chối.",
  // The DEC-031 addendum: a remedy envelope stays open until the end of the next business day,
  // not ten minutes. It still ends, and it still never extends itself.
  REMEDY_APPROVAL_EXPIRED:
    "Phiếu duyệt của chủ tiệm đã hết hạn: phiếu bồi hoàn mở tới hết ngày làm việc kế tiếp (giờ " +
    "Việt Nam) và không tự gia hạn. Gửi lại đề nghị để lập phiếu mới, đừng chờ thêm.",
  REMEDY_APPROVAL_NOT_BOUND:
    "Phiếu duyệt không gắn đúng với đề nghị đang thực hiện — khác cửa hàng, khác đề nghị, hoặc nội " +
    "dung đã đổi từ lúc chủ tiệm ký. Không có gì được ghi. Gửi lại đề nghị để lập phiếu mới.",
  REMEDY_ALREADY_EXECUTED:
    "Đề nghị này đã được thực hiện rồi — không phải lỗi của bạn, và không làm lại lần nữa. Mở lại " +
    "sự cố để xem kết quả đã ghi.",
  REMEDY_PROPOSAL_NOT_FOUND:
    "Không có đề nghị bồi hoàn nào mang mã này. Kiểm tra lại mã đã chép.",
  REMEDY_CREDIT_NOT_FOUND:
    "Không có khoản giảm trừ nào mang mã này ở cửa hàng đang chọn. Phiếu phát ở tiệm nào thì dùng " +
    "ở tiệm đó.",
  REMEDY_INCIDENT_NOT_FOUND:
    "Không có sự cố nào mang mã này ở cửa hàng đang chọn, hoặc sự cố không gắn với đơn nào. Kiểm " +
    "tra lại mã sự cố trên màn hình Sự cố.",
  REMEDY_ORDER_REVISION_UNREADABLE:
    "Máy chủ không đọc được bản báo giá của đơn này nên không tính được trần nào. Đây là lỗi dữ " +
    "liệu, không phải chữ bạn gõ: chụp màn hình và báo kỹ thuật, đừng gõ lại kiểu khác.",
  REMEDY_ORDER_TIMESTAMP_INVALID:
    "Mốc thời gian lưu trên đơn không đọc được nên không đo được cửa sổ nào. Đây là lỗi dữ liệu, " +
    "không phải chữ bạn gõ: chụp màn hình và báo kỹ thuật.",
  CONTACT_BINDING_UNKNOWN:
    "Không có liên hệ nào mang mã này. Liên hệ chỉ được tạo từ một hội thoại kênh đã xác minh; " +
    "màn hình tiếp nhận không tạo liên hệ mới.",
  // `SlaReason`, from `packages/domain/src/nha_trang_laundry_domain/sla.py`. OPS-BOARD-001 put the
  // SLA board on a screen, and these codes are the evidence behind every row on it. Every one of
  // them appears on an ordinary order in an ordinary shift, so the notes say which are simply the
  // rule describing itself and which mean somebody has to move.
  PRODUCTION_SLA_EXCLUDES_DELIVERY:
    "Mốc này chỉ đo phần giặt trong tiệm. Thời gian giao tận nơi không nằm trong đó, nên đơn " +
    "“đúng hạn” ở đây vẫn có thể tới tay khách muộn.",
  ELAPSED_EIGHT_HOUR_INTERNAL_RISK:
    "Đây là mốc rủi ro nội bộ 8 giờ, tính từ lúc nhận vào sản xuất. Nó là mốc của tiệm để tự soi " +
    "công việc, không phải lời hứa đã nói với khách.",
  EXACT_CLOSURE_CUTOFF_UNPUBLISHED:
    "Giờ đóng cửa chính xác chưa được công bố, nên mốc trên tính tròn theo giờ đồng hồ chứ không " +
    "trừ giờ tiệm nghỉ. Đơn nhận cuối ngày vì vậy trông gấp hơn thực tế.",
  SLA_PENDING: "Đơn vẫn đang trong mốc: chưa quá hạn và cũng chưa xong.",
  SLA_MET: "Đồ đã giặt xong trước mốc. Đồng hồ đã dừng ở lúc báo xong, không chạy tiếp.",
  SLA_BREACHED:
    "Đơn đã quá mốc rủi ro nội bộ. Đây là việc cần một người xử lý trước, không phải một con số " +
    "để theo dõi.",
  BREACH_REMEDY_REQUIRES_HUMAN:
    "Quá mốc không tự sinh ra khoản bù cho khách. Ai được bù gì là quyết định của người, ở màn " +
    "hình Bồi hoàn.",
  HUMAN_PROMISE_REQUIRED:
    "Chưa có ai hứa giờ trả đồ cho khách trên hệ thống. Mốc bên cạnh là mốc nội bộ của tiệm.",
  HUMAN_PROMISE_RECORDED: "Đã có người xác nhận giờ trả đồ cho khách, và giờ đó được ghi lại.",
  GUIDANCE_RANGE_24_TO_48_HOURS:
    "Món này chỉ có khoảng thời gian tham khảo 24–48 giờ, không có mốc cứng.",
  GUIDANCE_DOES_NOT_CREATE_BREACH:
    "Khoảng tham khảo không phải lời hứa, nên không có “trễ hạn” cho nhóm này. Hiển thị nó như " +
    "một lời hứa bị vỡ là nói sai với chính nhân viên tiệm.",
  HUMAN_ETA_REQUIRED:
    "Món này phải có người xem rồi mới hẹn được giờ. Hệ thống không tự đoán một mốc nào.",
  PRODUCTION_NOT_ACCEPTED: "Đơn chưa được nhận vào sản xuất nên đồng hồ chưa bắt đầu chạy.",
  // Export refusals, from `packages/db/src/nha_trang_laundry_db/exports.py`.
  EXPORT_APPROVAL_REQUIRED:
    "Bản xuất này cần chủ tiệm duyệt trước. Phong bì duyệt đã được tạo; mở màn hình Duyệt để chủ " +
    "tiệm quyết định, rồi quay lại bấm xuất.",
  EXPORT_APPROVAL_NOT_BOUND:
    "Phong bì duyệt đang cầm không khớp với yêu cầu xuất này — khác cửa hàng, khác ngày, hoặc " +
    "khác danh sách cột. Tạo lại yêu cầu xuất và xin duyệt lại; không có dữ liệu nào ra khỏi hệ thống.",
  EXPORT_APPROVAL_EXPIRED:
    "Phê duyệt của chủ tiệm đã hết hạn (cửa sổ 10 phút). Tạo yêu cầu mới và xin duyệt lại.",
  EXPORT_APPROVAL_SELF_DECIDED:
    "Phiếu duyệt này do chính người đã tạo yêu cầu xuất bấm duyệt, nên máy chủ không cho xuất. " +
    "Người chọn dữ liệu nào rời khỏi hệ thống không được tự duyệt việc đó. Nhờ một chủ tiệm khác " +
    "duyệt; chưa có dữ liệu nào ra khỏi hệ thống.",
  // API-INTEGRITY-003: the two 503 reasons. The title (`BUSY`) says what to do; these say what
  // happened, which is the part a staff member repeats when they ask whether anything was lost.
  DATABASE_BUSY:
    "Một câu lệnh phải chờ quá lâu nên máy chủ huỷ nó, và mọi thứ nó định ghi đã được hoàn tác " +
    "cùng lúc — không có nửa lệnh nào nằm lại.",
  DATABASE_UNAVAILABLE:
    "Máy chủ không mở được kết nối tới cơ sở dữ liệu, nên lệnh chưa chạy tới bước nào.",
  // EXPORT-RANGE-001: the two ways a chosen window is refused before anything is written.
  EXPORT_WINDOW_TOO_LONG:
    "Một lần xuất tối đa 92 ngày. Chọn khoảng ngắn hơn, hoặc chia thành nhiều lần xuất.",
  EXPORT_WINDOW_REVERSED:
    "Ngày bắt đầu đang sau ngày kết thúc. Đổi lại hai ngày rồi tạo yêu cầu lại.",
  EXPORT_ALREADY_PRODUCED:
    "Yêu cầu này đã xuất một lần rồi. Một lần duyệt cho đúng một bản; cần bản nữa thì tạo yêu cầu mới.",
  EXPORT_REQUEST_NOT_FOUND:
    "Không có yêu cầu xuất nào mang mã này. Kiểm tra lại mã đã chép.",
  EXPORT_REQUEST_STORE_MISMATCH:
    "Yêu cầu xuất này thuộc cửa hàng khác với cửa hàng đang chọn. Chưa có tệp nào được tạo và " +
    "phiếu duyệt vẫn còn dùng được: chọn đúng cửa hàng rồi bấm lại.",
  EXPORT_CELL_NOT_SAFE:
    "Có một ô trong dữ liệu bắt đầu bằng =, +, - hoặc @ — bảng tính sẽ chạy nó như công thức khi " +
    "mở tệp. Máy chủ dừng lại và không tạo tệp nào. Đây là lỗi dữ liệu, không phải chữ bạn gõ: " +
    "chụp màn hình và báo kỹ thuật.",
  EXPORT_REQUEST_CORRUPT:
    "Bản ghi của yêu cầu xuất này không đọc lại được, nên máy chủ không dựng lại được đúng văn " +
    "bản mà chủ tiệm đã ký. Máy chủ không tự viết lại nội dung ấy: viết lại là đưa chủ tiệm duyệt " +
    "một văn bản khác với văn bản đã niêm phong. Tạo một yêu cầu xuất mới; chưa có dữ liệu nào ra " +
    "khỏi hệ thống.",
  // ORDER-STEPS-001: why "Nhận đồ" was refused. `RECEIVE` checks six readiness facts in one go and
  // refuses the whole step if any is missing -- nothing is half-applied -- and names each missing
  // one. Five are read by the server off the order and its quote; only the slot is the operator's.
  SLOT_APPROVAL_REQUIRED:
    "Chưa tích “tiệm làm kịp đơn này”. Máy không tự biết tiệm còn chỗ hay không — đó là lời của " +
    "người nhận đồ, được ghi kèm tên bạn.",
  QUANTITY_NOT_MEASURED:
    "Báo giá còn dòng dùng số lượng khách tự ước, chưa do nhân viên cân hoặc đếm. Cân lại, sửa " +
    "báo giá rồi mới nhận đồ.",
  SERVICE_NOT_CLASSIFIED:
    "Báo giá còn dòng chưa có dịch vụ trong bảng giá. Sửa báo giá cho đúng dịch vụ rồi mới nhận đồ.",
  EXACT_PRICE_NOT_APPROVED:
    "Giá trên báo giá chưa phải giá chính xác đã duyệt (còn ước tính hoặc còn khoảng giá). Chốt giá " +
    "trước rồi mới nhận đồ.",
  CUSTOMER_AGREEMENT_MISSING:
    "Khách chưa đồng ý bản giá mà đơn này gắn vào. Đọc lại giá cho khách và chốt, rồi mới nhận đồ.",
  CUSTODY_NOT_RECORDED:
    "Chưa ghi nhận tiệm đã cầm đồ của khách. Chỉ nhận đồ khi túi đồ đã ở trên quầy.",
  // PROMISE-001 (DEC-037): the promised-ready time ("hẹn trả"). Why Nhận đồ or Hẹn lại was refused.
  PROMISE_REQUIRED:
    "Đơn này cần bạn chọn giờ hẹn trả trước khi nhận đồ. Chọn ngày giờ ở ô “Hẹn trả” rồi bấm lại.",
  TET_DATES_UNPUBLISHED:
    "Giờ hẹn sẽ rơi vào dịp Tết mà chủ tiệm chưa nhập ngày nghỉ Tết năm đó, nên máy không tự hẹn. " +
    "Bạn tự chọn ngày giờ trả.",
  SERVICE_NOT_IN_POLICY:
    "Có món chưa nằm trong quy tắc hẹn trả chủ tiệm đã công bố. Bạn tự chọn ngày giờ trả.",
  NO_LINES: "Báo giá không có món nào để hẹn giờ trả.",
  TURNAROUND_POLICY_UNPUBLISHED:
    "Chủ tiệm chưa công bố quy tắc hẹn trả, nên chưa ghi được giờ hẹn nào. Không có gì được ghi.",
  PROMISE_CHOICE_NOT_APPLICABLE:
    "Lựa chọn này không áp cho các món trong đơn (24/48 giờ chỉ cho giày, rèm, chăn; 2 giờ chỉ cho " +
    "đồ giặt sấy thường). Tải lại đơn và chọn lại.",
  PROMISE_CUSTOM_AT_REQUIRED: "Chưa chọn ngày giờ hẹn trả.",
  PROMISE_CUSTOM_AT_NOT_TAKEN: "Ngày giờ tự chọn chỉ đi kèm lựa chọn “Tự chọn giờ”.",
  PROMISE_NOT_AFTER_ACCEPTANCE: "Giờ hẹn phải sau lúc này. Chọn một giờ muộn hơn.",
  PROMISE_OUTSIDE_OPENING_HOURS: "Giờ hẹn phải trong giờ mở cửa, 08:00–20:00.",
  PROMISE_ON_CLOSED_DAY: "Ngày đó tiệm nghỉ. Chọn một ngày tiệm mở cửa.",
  PROMISE_UNCHANGED: "Giờ mới trùng giờ đang hẹn — không có gì để đổi.",
  PROMISE_NOTE_REQUIRED: "Lý do “Khác” cần vài chữ ghi rõ vì sao hẹn lại.",
  PROMISE_NOTE_TOO_LONG: "Ghi chú tối đa 120 ký tự.",
  PROMISE_NOT_SET:
    "Đơn này không có giờ hẹn (nhận trước khi chủ tiệm công bố quy tắc), nên không có gì để hẹn lại.",
  PROMISE_ORDER_DONE: "Đồ đã giặt xong hoặc đơn đã đóng — không cần hẹn lại nữa.",
  // SHOP-CAPTURE-001 (DEC-038): machines, trip costs and Sổ thu chi.
  MACHINE_UNAVAILABLE:
    "Máy này không bắt đầu mẻ giặt được (đã ngưng dùng, là máy sấy, hoặc của tiệm khác). Chọn máy " +
    "khác hoặc bấm Bỏ qua.",
  MACHINE_CODE_TAKEN: "Tiệm đã có máy mang mã này. Đặt mã khác, ví dụ WASH-03.",
  MACHINE_CODE_INVALID: "Mã máy chỉ gồm chữ in, số và dấu gạch, ví dụ WASH-03.",
  MACHINE_NAME_INVALID: "Tên máy từ 1 đến 60 ký tự.",
  MACHINE_RETIRED: "Máy này đã ngưng dùng; không đổi được nữa.",
  NOTE_LOOKS_LIKE_PHONE:
    "Ghi chú có số giống số điện thoại. Sổ của tiệm không ghi số điện thoại khách; bỏ số đó đi.",
  NOTE_INVALID: "Ghi chú tối đa 120 ký tự.",
  TRIP_KM_INVALID: "Số km là một số, tối đa một chữ số sau dấu phẩy (ví dụ 4,5).",
  TRIP_COST_TOO_LARGE: "Một chuyến tối đa 10.000.000 ₫. Kiểm tra lại số tiền.",
  EXPENSE_AMOUNT_REQUIRED: "Số tiền phải lớn hơn 0.",
  EXPENSE_AMOUNT_TOO_LARGE: "Một dòng tối đa 1.000.000.000 ₫. Kiểm tra lại số tiền.",
  EXPENSE_DATE_IN_FUTURE: "Ngày chi là hôm nay hoặc trước đó.",
  EXPENSE_DATE_TOO_OLD: "Ngày chi trong vòng 366 ngày gần đây.",
  EXPENSE_ALREADY_VOIDED: "Dòng này đã bị huỷ trước đó.",
  MONTH_INVALID: "Tháng không hợp lệ.",
};

/**
 * SHOP-CAPTURE-001: `Vehicle` of a delivery trip. Scoped, as `ACQUISITION_SOURCE_VI` is.
 */
export const VEHICLE_VI = {
  XE_MAY: "Xe máy",
  O_TO: "Ô tô",
  THUE_NGOAI: "Thuê ngoài",
};

/**
 * SHOP-CAPTURE-001: Sổ thu chi's categories (`ExpenseCategory`) in the owner's words. Scoped:
 * `KHAC` and `LUONG` are short words another enum could reuse with another meaning.
 */
export const EXPENSE_CATEGORY_VI = {
  DIEN: "Điện",
  NUOC: "Nước",
  HOA_CHAT: "Hoá chất",
  TUI_NHAN: "Túi, nhãn",
  LUONG: "Lương",
  MAT_BANG: "Mặt bằng",
  SUA_CHUA: "Sửa chữa",
  XANG_XE: "Xăng xe",
  KHAC: "Khác",
};

/**
 * SHOP-CAPTURE-001: `MachineCategory` (the machine master's `category` column) in the shop's words.
 */
export const MACHINE_CATEGORY_VI = {
  washer: "Máy giặt",
  dryer: "Máy sấy",
  dry_cleaner: "Máy giặt khô",
  shoe_washer_dryer: "Máy giặt giày",
  vacuum_ironing_table: "Cầu là",
  boiler_iron_set: "Bàn là hơi",
  other: "Máy khác",
};

/**
 * `OrderStep` (ORDER-STEPS-001) as the button a counter worker presses: a business verb, not a
 * state name. Scoped, not in `ENUM_GLOSS`, because `QUALITY_CHECK` is also a `ProductionStatus`
 * member there ("đang kiểm tra" — a state), and one flat map cannot hold both the verb and the
 * state for one token (the `ACQUISITION_SOURCE_VI` collision, again).
 */
export const ORDER_STEP_VI = {
  RECEIVE: "Nhận đồ",
  START_WASH: "Bắt đầu giặt",
  QUALITY_CHECK: "Giặt xong, kiểm tra đồ",
  MARK_READY: "Báo đồ đã sẵn sàng",
  HOLD: "Tạm dừng",
  RESUME: "Làm tiếp",
  REWASH: "Giặt lại",
  RELEASE: "Đưa đồ đi giao",
  HAND_OVER: "Giao đồ & đóng đơn",
  COMPLETE: "Đóng đơn",
  CANCEL: "Huỷ đơn",
  REJECT_INTAKE: "Không nhận đồ",
  REOPEN: "Không huỷ nữa, làm tiếp",
  SETTLE: "Thu tiền",
  PREPAY: "Khách trả trước",
  COLLECT: "Khách đã nhận đồ",
  DELIVERY_PICKUP: "Đã lấy đồ tại nhà khách",
  DELIVERY_RETURN: "Đã giao đồ cho khách",
};

/** The toast after a step landed: what is now true, in the past tense. */
export const ORDER_STEP_DONE_VI = {
  RECEIVE: "Đã nhận đồ",
  START_WASH: "Đã bắt đầu giặt",
  QUALITY_CHECK: "Đang kiểm tra đồ",
  MARK_READY: "Đồ đã sẵn sàng",
  HOLD: "Đã tạm dừng",
  RESUME: "Đã làm tiếp",
  REWASH: "Đã chuyển đi giặt lại",
  RELEASE: "Đồ đã đưa đi giao",
  HAND_OVER: "Đã giao đồ và đóng đơn",
  COMPLETE: "Đã đóng đơn",
  CANCEL: "Đã huỷ đơn",
  REJECT_INTAKE: "Đã trả đồ, huỷ đơn",
  REOPEN: "Đơn tiếp tục làm",
  SETTLE: "Đã thu tiền",
  PREPAY: "Đã thu tiền trả trước",
  COLLECT: "Đã ghi khách nhận đồ",
  DELIVERY_PICKUP: "Đã ghi chuyến lấy đồ",
  DELIVERY_RETURN: "Đã ghi chuyến giao đồ",
};

/**
 * A step's label, or its raw token when this console does not know it yet -- a new server step
 * must look unfamiliar, never be silently dropped or renamed.
 *
 * @param {string} step
 * @returns {string}
 */
export function stepVi(step) {
  return ORDER_STEP_VI[step] || String(step);
}

/** Server enum values, glossed. The value itself is always displayed too. */
export const ENUM_GLOSS = {
  // CommercialOrderStatus
  DRAFT: "nháp",
  REQUESTED: "đã yêu cầu",
  STORE_CONFIRMATION_PENDING: "chờ cửa hàng xác nhận",
  CONFIRMED: "đã xác nhận",
  ACTIVE: "đang chạy",
  CANCELLATION_REVIEW: "đang xét huỷ",
  CANCELLED: "đã huỷ",
  COMPLETED: "đã hoàn tất",
  // IntakeStatus
  AWAITING_HANDOFF: "chờ nhận đồ",
  RECEIVED_PENDING_INSPECTION: "đã nhận, chờ kiểm",
  WAITING_PRICE_APPROVAL: "chờ duyệt giá",
  WAITING_CUSTOMER_RECONFIRMATION: "chờ khách xác nhận lại",
  WAITING_SLOT_APPROVAL: "chờ duyệt lịch",
  ACCEPTED: "đã nhận",
  REJECTED: "đã từ chối",
  // ProductionStatus
  NOT_STARTED: "chưa bắt đầu",
  QUEUED: "đã xếp hàng",
  IN_PROCESS: "đang giặt",
  QUALITY_CHECK: "đang kiểm tra",
  READY_AT_STORE: "sẵn sàng tại cửa hàng",
  RELEASED: "đã giao ra",
  ON_HOLD: "tạm dừng",
  EXCEPTION: "sự cố",
  // DEC-024. `CustodyResolution`: what a named staff member says happened to the laundry and the
  // money when an order is cancelled after work began.
  NOT_RECEIVED: "chưa nhận đồ",
  RETURNED_UNWASHED_REFUNDED: "đã trả đồ chưa giặt và hoàn tiền",
  SHOP_FAULT_NO_CHARGE: "lỗi tiệm, không thu tiền",
  // ORDER-STEPS-002. `RewashReason` (why laundry is washed again before it leaves) and
  // `IntakeRejectionReason` (why goods on the counter were refused); `OTHER` is in both.
  NOT_CLEAN: "chưa sạch",
  MACHINE_FAULT: "máy lỗi",
  OTHER: "lý do khác",
  NOT_SERVICEABLE: "tiệm không giặt loại này",
  DAMAGED_ON_ARRIVAL: "hỏng sẵn khi mang tới",
  // OrderBalanceStatus
  UNPAID: "chưa thanh toán",
  PARTIALLY_PAID: "thanh toán một phần",
  PAID: "đã thanh toán",
  OVERPAID: "thu thừa",
  ON_ACCOUNT: "ghi nợ",
  REFUNDED: "đã hoàn tiền",
  // OrderRequestStatus — `order_requests.status CHECK (status IN ('DRAFT','SUBMITTED','CANCELLED'))`;
  // DRAFT and CANCELLED share the glosses declared above with the same meaning here
  SUBMITTED: "đã gửi",
  // QuoteRevisionStatus
  PROVISIONAL: "tạm thời",
  REVIEW_REQUIRED: "cần xem xét",
  APPROVED: "đã duyệt",
  PRESENTED: "đã gửi khách",
  ACKNOWLEDGED_ESTIMATE: "khách đã nhận ước tính",
  ACCEPTED_FINAL: "khách đã chốt",
  SUPERSEDED: "đã bị thay thế",
  EXPIRED: "đã hết hạn",
  // FulfillmentMode
  SELF_DROP_SELF_COLLECT: "khách tự mang tới và tự lấy",
  PICKUP_AND_RETURN: "lấy và trả tận nơi",
  PICKUP_ONLY: "chỉ lấy",
  RETURN_ONLY: "chỉ trả",
  // Unit
  KG: "kilôgam",
  ITEM: "cái",
  PAIR: "đôi",
  SET: "bộ",
  ANIMAL_PLUSH_ITEM: "thú bông",
  CASE: "vỏ",
  M2: "mét vuông",
  // QuantityBasis
  CUSTOMER_ESTIMATE: "khách tự ước",
  STAFF_MEASUREMENT: "nhân viên đã cân",
  APPROVED_MANUAL: "nhập tay có duyệt",
  // Roles
  OWNER_ADMIN: "chủ / quản trị",
  OPS_APPROVER: "người duyệt vận hành",
  OPERATOR: "nhân viên vận hành",
  DRIVER: "tài xế",
  ACCOUNTANT: "kế toán",
  AUDITOR: "kiểm toán",
  // Reconciliation and sends
  UNKNOWN: "chưa rõ kết quả",
  UNKNOWN_REQUIRES_HUMAN: "chưa rõ, cần người xử lý",
  CONFIRMED_SENT: "xác nhận đã gửi",
  CONFIRMED_NOT_SENT: "xác nhận chưa gửi",
  APPROVED_FOR_MANUAL_SEND: "đã khoá để gửi thủ công",
  MANUAL_SEND_RECORDED: "đã ghi nhận người gửi tay",
  // Consent and suppression (`DEC-033`). The service-messaging read's `NONE` ("no row: nobody
  // wrote STOP on this channel") is glossed where it is read, not here: `NONE` is also a remedy
  // next step, and one flat map cannot say both.
  SUPPRESSED: "khách đã yêu cầu dừng",
  PENDING_REVIEW_BLOCKED: "đang chặn chờ người đọc lại",
  UNKNOWN_BLOCKED: "chặn vì không rõ",
  CLEAR: "không chặn",
  CUSTOMER_INITIATED: "khách nhắn cho tiệm gần đây",
  OPEN_ORDER: "khách có đơn đang mở hoặc vừa đóng",
  // Actor types on the audit timeline
  STAFF: "nhân viên",
  AGENT_RUNNER: "tiến trình chạy agent",
  OUTBOX_WORKER: "tiến trình gửi",
  BOOTSTRAP: "khởi tạo hệ thống",
  // Assistant intents
  GREETING: "lời chào",
  TODAY_OVERVIEW: "tình hình hôm nay",
  SLA_RISK: "nguy cơ trễ SLA",
  PENDING_APPROVALS: "chờ phê duyệt",
  ORDER_LOOKUP: "tra cứu đơn",
  REVENUE_UNAVAILABLE: "doanh thu chưa kết nối",
  UNSUPPORTED: "chưa trả lời được",
  // REPORT-DASHBOARD-001: a KPI's `data_quality` (`FR-RPT-005`)
  COMPLETE: "đủ dữ liệu",
  RULE_ASSUMED: "theo một quy tắc tạm",
  // Incident status — `customer_incidents.status CHECK (status IN ('OPEN','UNDER_REVIEW','CLOSED'))`
  OPEN: "đang mở",
  UNDER_REVIEW: "đang xem xét",
  CLOSED: "đã đóng",
  // Incident categories — `IncidentRepository` admits exactly these two
  SERVICE_QUALITY: "chất lượng dịch vụ",
  AUTOMATED_MESSAGE_ERROR: "lỗi tin nhắn tự động",
  // Shadow draft review decisions — `agent_draft_reviews.decision`. Deliberately kept apart from
  // the approval-envelope vocabulary above (`APPROVED`/`REJECTED`), which decides a different
  // aggregate entirely; `screens/shadow.js` explains why the two must never be mixed. These are
  // the imperative a reviewer presses, not the state a request ends in.
  APPROVE: "dùng được",
  EDIT: "sửa lại",
  REJECT: "không dùng được",
  // Agent run terminal outcome — `agent_drafts.terminal_outcome IN ('DRAFT','REQUIRE_HUMAN')`;
  // DRAFT shares the order-status gloss "nháp" above, which is what it means here too
  REQUIRE_HUMAN: "cần người quyết định",
  // Agent run terminal codes — the fixed vocabulary `responses_runtime` and the worker pipeline
  // emit. Model-supplied reason codes pass through untouched and render raw, which is deliberate:
  // a code this map does not know must look unfamiliar.
  DRAFT_REQUIRES_HUMAN: "bản nháp chờ người duyệt",
  VALIDATED_DRAFT: "bản nháp hợp lệ",
  MODEL_REQUESTED_HANDOFF: "mô hình xin chuyển cho người",
  PROVIDER_CONNECTION_FAILURE: "không kết nối được nhà cung cấp",
  PROVIDER_OUTCOME_AMBIGUOUS: "kết quả từ nhà cung cấp không rõ",
  PROVIDER_REQUEST_CANCELLED: "yêu cầu tới nhà cung cấp bị huỷ",
  PROVIDER_TIMEOUT: "nhà cung cấp không trả lời kịp",
  CONTEXT_REJECTED: "gói ngữ cảnh bị từ chối",
  BUDGET_EXHAUSTED: "hết hạn mức cho lượt chạy",
  TOOL_BRIDGE_REJECTED: "cầu nối công cụ từ chối",
  INVALID_PROVIDER_OUTPUT: "kết quả nhà cung cấp không hợp lệ",
  UNEXPECTED_RUNTIME_FAILURE: "tiến trình chạy agent gặp lỗi không mong đợi",
  // Audit actions visible on the order timeline — the `audit_action` of every material change
  // whose aggregate id is the order's
  ORDER_CREATE_FROM_FINAL_QUOTE: "tạo đơn từ báo giá đã chốt",
  ORDER_STATE_TRANSITION: "chuyển trạng thái đơn",
  ORDER_SETTLEMENT_RECORD: "ghi nhận tất toán",
  ORDER_COLLECTION_RECORD: "ghi nhận khách nhận đồ",
  // PROMISE-001: the first promise at Nhận đồ, and a Hẹn lại.
  ORDER_PROMISE_SET: "hẹn giờ trả đồ",
  ORDER_PROMISE_CHANGE: "hẹn lại giờ trả đồ",
  // `ApprovalAction` — what an approval envelope asks a person to authorise. `#/approvals` titles
  // each card with this gloss (spec V2 §5.5) and keeps the token in the card's technical record.
  PRESENT_QUOTE: "cho gửi báo giá tới khách",
  FINALIZE_QUOTE: "xác nhận giá khách đã đồng ý",
  CONFIRM_SLOT: "xác nhận khung giờ",
  SET_RANGE_PRICE: "duyệt giá trong khoảng",
  SET_DELIVERY_FEE: "duyệt phí giao",
  APPLY_PROMOTION: "áp khuyến mãi",
  SEND_MESSAGE: "cho gửi tin nhắn",
  ACCEPT_ORDER: "nhận đơn",
  CANCEL_ACTIVE_ORDER: "huỷ đơn đang làm",
  APPROVE_REMEDY: "duyệt bồi hoàn",
  APPROVE_B2B_TERMS: "duyệt điều khoản khách doanh nghiệp",
  PUBLISH_POLICY: "công bố chính sách",
  EXPORT_SANITIZED_DATA: "xuất dữ liệu",
  // `ChannelProvider` — the channel a contact binding was recorded on (`CONTACT-PICK-001`: the
  // title of a row in "Khách nhắn tin gần đây"). A product name, never a handle.
  ZALO_OA: "Zalo",
  // CUSTOMER-001 (`DEC-034`): a customer's kind, why a record was erased, what was linked.
  RETAIL: "khách lẻ",
  BUSINESS: "doanh nghiệp",
  CUSTOMER_REQUEST: "khách yêu cầu xoá",
  RETENTION: "24 tháng không có đơn",
  COUNTER_TICKET: "phiếu quầy",
  CHANNEL_BINDING: "khách nhắn tin",
  TELEGRAM_SANDBOX: "Telegram (thử nghiệm)",
  FACEBOOK_MESSENGER: "Messenger",
};

/**
 * `AcquisitionSource`, glossed separately from `ENUM_GLOSS` because it collides with it.
 *
 * `ENUM_GLOSS` is one flat map across every server enum, which works only while no two enums share
 * a member name. `AcquisitionSource.UNKNOWN` is the first that does: the map already binds
 * `UNKNOWN` to "chưa rõ kết quả" for send reconciliation, where it means the shop does not know
 * whether a message arrived. Here it means nobody asked the customer where they came from. Those
 * are different facts and no single gloss serves both — folding them together would put "chưa rõ
 * kết quả" into a form field about how someone found the shop.
 *
 * A later duplicate key in a JavaScript object literal silently wins over the earlier one, so this
 * collision would never have failed anywhere. It would have quietly relabelled the send column.
 * Hence a scoped map passed explicitly, and not a second `UNKNOWN`.
 */
export const ACQUISITION_SOURCE_VI = {
  WALK_IN: "đi ngang qua, vào luôn",
  GOOGLE_MAPS: "tìm trên Google",
  ZALO: "qua Zalo",
  FACEBOOK: "qua Facebook",
  PARTNER_FRONT_DESK: "lễ tân đối tác giới thiệu",
  REFERRAL_CUSTOMER: "khách cũ giới thiệu",
  LEAFLET_QR: "tờ rơi hoặc mã QR",
  // Reported speech, and the wording says so. No customer record exists (`DEC-015`), so nothing
  // here verified it; a bare "khách cũ" would read as a fact the database had checked.
  RETURNING: "khách nói đã từng dùng",
  // A state of knowledge, not a missing entry.
  UNKNOWN: "chưa biết",
};

/**
 * `QuoteAdjustmentKind` — the money rows a stored revision applies on top of its lines — in the
 * words a customer reads on their receipt (`RECEIPT-PRINT-001`). Scoped, for the reason
 * `ACQUISITION_SOURCE_VI` gives: `PROMOTION` and `DELIVERY` are plain words that another enum can
 * reuse with another meaning, and `ENUM_GLOSS` is one flat map. A kind not listed here is printed
 * as its raw token, never dropped.
 */
export const QUOTE_ADJUSTMENT_VI = {
  PROMOTION: "Khuyến mãi",
  MANUAL_DISCOUNT: "Giảm giá",
  REMEDY_CREDIT: "Khoản giảm trừ",
  DELIVERY: "Phí giao",
  SURCHARGE: "Phụ phí",
};

/**
 * The Vietnamese name of a server enum, capitalized for standalone display.
 *
 * Vietnamese is the reading language of this console; the raw token remains available wherever
 * precision matters (selects keep it in parentheses, badges keep it as a secondary caption, and
 * the app bar keeps it in the tooltip), because it is what an engineer greps for.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function enumVi(value) {
  if (!value) return "—";
  const gloss = ENUM_GLOSS[value];
  if (!gloss) return String(value);
  return gloss.charAt(0).toUpperCase() + gloss.slice(1);
}

/**
 * Vietnamese headings for the published pricebook's service categories.
 *
 * These are not server enums, which is why they live apart from `ENUM_GLOSS`. They are values of a
 * free-text column in the owner's own pricebook CSV (`templates/services-pricebook.csv`), and they
 * arrive lowercase and English — `dry_cleaning`, `standard_weight`. The service *names* in that
 * file are already Vietnamese; only the grouping key is not, so only the grouping key is mapped.
 *
 * An unmapped category falls through to its raw value on purpose. The picker must not invent a
 * heading for a category the owner added after this map was written — an unfamiliar-looking group
 * label is a prompt to update this file, whereas a plausible guess would hide the omission.
 */
const SERVICE_CATEGORY = {
  standard_weight: "Giặt sấy theo ký",
  drying: "Sấy riêng",
  bedding: "Chăn ga gối",
  ironing: "Ủi",
  leather: "Đồ da",
  shoes: "Giày dép",
  dry_cleaning: "Giặt khô",
  other: "Dịch vụ khác",
};

/**
 * The Vietnamese heading for one pricebook service category.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function serviceCategoryVi(value) {
  if (!value) return "Khác";
  return SERVICE_CATEGORY[value] || String(value);
}

/**
 * `Gloss (VALUE)` — Vietnamese first, the verbatim token in parentheses. Never gloss-only: an
 * operator who learns that `WAITING_SLOT_APPROVAL` is the thing the API says can talk to an
 * engineer about it, and a translated-only UI makes that conversation impossible.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function enumLabel(value) {
  if (!value) return "—";
  const gloss = ENUM_GLOSS[value];
  return gloss ? `${enumVi(value)} (${value})` : String(value);
}

/** Screen and navigation titles. */
export const NAV = {
  today: "Hôm nay",
  newOrder: "Nhận đồ",
  more: "Thêm",
  orderRequests: "Tiếp nhận",
  quotes: "Báo giá",
  orders: "Đơn hàng",
  slaBoard: "Bảng trễ hạn",
  approvals: "Duyệt",
  shadow: "Bản nháp AI",
  assistant: "Trợ lý AI",
  exceptions: "Ngoại lệ",
  incidents: "Khiếu nại",
  remedies: "Bồi hoàn",
  exports: "Xuất dữ liệu",
  system: "Hệ thống",
  staff: "Nhân sự",
  unsupported: "Việc chưa hỗ trợ",
  reports: "Báo cáo",
  expenses: "Sổ thu chi",
  machines: "Máy giặt, sấy",
  customers: "Khách hàng",
};
