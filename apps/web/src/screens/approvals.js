/**
 * Approvals: the envelopes waiting for a human, and an honest account of what this screen cannot do.
 *
 * This is the only screen in the console that shows a queue and then refuses to let the operator
 * work it. That is deliberate, and the reasons are load-bearing rather than incidental:
 *
 *   - **The queue is a filtered view, and the filter is invisible server-side.** `list_pending`
 *     resolves each request through `approval_requests → orders → staff_store_assignments`, so an
 *     approval whose resource is a `QUOTE_REVISION`, a `MESSAGE_DRAFT`, a `SLOT_PROPOSAL` or a
 *     `DELIVERY_FEE_PROPOSAL` never appears — its `resource_id` is not an order id and the join
 *     drops it. An empty list here does not mean nothing is pending, so the screen says so in copy
 *     rather than letting the absence speak.
 *   - **Only `REQUESTED` is listed.** The `WHERE s.status = 'REQUESTED'` clause means approved,
 *     rejected and expired envelopes are not in this list and cannot be reviewed from it.
 *   - **Deciding is not offered, and not because a button was forgotten.** A decision requires
 *     `resource_version`, `snapshot_hash` and `rendered_hash`. The queue response carries none of
 *     the three — only `envelope_hash`, which is a *different* value and not a substitute. An
 *     approver therefore cannot construct a valid decision from anything this screen can see, and a
 *     form asking someone to paste three hashes for content they have not been shown is blind
 *     approval of a message that goes to a customer. So the controls are present, disabled, and
 *     explained. There is no paste field, on purpose.
 *   - **The countdown is the point.** Approval TTLs are 10, 15 or 30 minutes by action
 *     (`SECURITY_RELIABILITY_SPEC_V1.md:354`) and an expiry never extends implicitly, so remaining
 *     time matters far more than a wall-clock timestamp. One interval drives every badge and stops
 *     itself the moment the screen leaves the document — a console is left open all day, and a
 *     leaked timer per navigation is a real bug rather than a tidiness complaint.
 *
 * Nothing here mutates. There is no `Submission` and no idempotency key in this module because
 * there is no write to carry one.
 *
 * @module screens/approvals
 */

import { isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { count, countdown, dateTime, shortHash, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { badge, empty, errorNotice, facts, panel, skeleton } from "../ui/components.js";

const LIST_LIMIT = 100;

/** One second. Fast enough that a badge flipping to expired is seen, slow enough to cost nothing. */
const TICK_MS = 1000;

/** The id the disabled decision controls point at, so the refusal is announced with the control. */
const BLOCK_ID = "approval-decision-blocked";

/**
 * The remaining-time badge for one envelope.
 *
 * Rebuilt whole on every tick rather than having its text patched, because the warn → danger flip
 * is carried by `data-state` and not by the words. A tick that updated only the text would leave an
 * expired envelope wearing the colour of a live one.
 *
 * @param {string|null|undefined} expiresAt
 * @returns {HTMLElement}
 */
function countdownBadge(expiresAt) {
  const left = countdown(expiresAt);
  return badge({
    token: left.text,
    gloss: left.expired
      ? "phong bì đã hết hạn — không có gia hạn ngầm, phải tạo yêu cầu mới"
      : "thời gian còn lại trước khi phong bì hết hạn",
    state: left.expired ? "danger" : "warn",
  });
}

/**
 * The two controls an approver would use, if the API let them.
 *
 * Not wrapped in `gated()`. `gated()` says "your role may not"; this is not a role problem — an
 * `OWNER_ADMIN` with MFA is refused by the same missing fields, so a role-conditional control would
 * enable itself for exactly the person most able to do harm with it.
 *
 * @returns {HTMLElement}
 */
function decisionControls() {
  /** @param {string} label */
  const control = (label) =>
    h(
      "button",
      {
        type: "button",
        disabled: true,
        "aria-disabled": "true",
        "aria-describedby": BLOCK_ID,
      },
      label,
    );

  return h(
    "div",
    { class: "stack stack--tight" },
    h("div", { class: "form__actions" }, control("Duyệt"), control("Từ chối")),
    h(
      "p",
      { class: "hint" },
      "Không bấm được: quyết định cần ba giá trị mà danh sách này không trả về. Xem “Vì sao không " +
        "quyết định được ở đây” bên trên.",
    ),
  );
}

/**
 * One pending envelope.
 *
 * @param {any} item
 * @param {(host: HTMLElement, expiresAt: string) => void} registerClock
 * @returns {HTMLElement}
 */
function approvalCard(item, registerClock) {
  const clockHost = h("span", { class: "row" }, countdownBadge(item.expires_at));
  registerClock(clockHost, item.expires_at);

  return h(
    "article",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h(
        "strong",
        { class: "mono", title: item.approval_request_id },
        shortId(item.approval_request_id),
      ),
      clockHost,
    ),
    facts([
      ["Trạng thái", enumLabel(item.status), { mono: true }],
      ["Vai trò được phép quyết định", enumLabel(item.required_role), { mono: true, span: true }],
      ["Hết hạn lúc", dateTime(item.expires_at)],
      [
        "Mã băm phong bì",
        h("span", { title: item.envelope_hash || "" }, shortHash(item.envelope_hash)),
        { mono: true, span: true },
      ],
    ]),
    item.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Bản ghi được phát lại từ một lệnh trùng khoá thao tác trước đó.",
        )
      : null,
    decisionControls(),
  );
}

/**
 * Everything this screen deliberately does not offer, stated before the queue rather than after it.
 *
 * @returns {HTMLElement}
 */
function limitsPanel() {
  return panel({
    eyebrow: "GIỚI HẠN CỦA MÀN HÌNH",
    title: "Danh sách này không phải toàn bộ hàng chờ",
    guardrail:
      "Đây là màn hình chỉ đọc. Không có thao tác nào ở đây ghi vào máy chủ, kể cả khi phong bì " +
      "sắp hết hạn.",
    children: h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Chỉ hiện phê duyệt gắn với đơn hàng"),
        h(
          "p",
          null,
          "Máy chủ tra cứu hàng chờ qua đơn hàng, nên phong bì nào trỏ tới thứ khác — bản báo giá " +
            "(QUOTE_REVISION), bản nháp tin nhắn (MESSAGE_DRAFT), đề xuất khung giờ " +
            "(SLOT_PROPOSAL), đề xuất phí giao (DELIVERY_FEE_PROPOSAL) — sẽ không xuất hiện ở đây. " +
            "Những phong bì đó vẫn tồn tại và vẫn đếm ngược; danh sách này chỉ không thấy chúng.",
        ),
        h(
          "p",
          { class: "hint" },
          "Hệ quả: danh sách trống không có nghĩa là không còn gì chờ duyệt.",
        ),
      ),
      h(
        "div",
        { class: "notice", dataState: "info" },
        h("p", { class: "notice__title" }, "Chỉ trạng thái REQUESTED"),
        h(
          "p",
          null,
          "Phong bì đã duyệt, đã từ chối hoặc đã hết hạn không nằm trong danh sách này và không " +
            "tra cứu lại được từ đây.",
        ),
      ),
      h(
        "div",
        { class: "notice", dataState: "warn", id: BLOCK_ID },
        h("p", { class: "notice__title" }, "Vì sao không quyết định được ở đây"),
        h(
          "p",
          null,
          "Gửi một quyết định bắt buộc phải kèm resource_version, snapshot_hash và rendered_hash. " +
            "Danh sách trên không trả về giá trị nào trong ba giá trị đó — nó chỉ có envelope_hash, " +
            "là một giá trị khác và không thay thế được. Người duyệt vì thế không thể dựng một " +
            "quyết định hợp lệ từ những gì màn hình này nhìn thấy.",
        ),
        h(
          "p",
          null,
          "Gõ tay các mã băm để duyệt một nội dung bạn chưa được xem chính là duyệt mù một tin " +
            "nhắn sẽ gửi tới khách. Nên màn hình này cố ý không có ô để dán mã băm, và hai nút " +
            "Duyệt / Từ chối được để hiện nhưng khoá.",
        ),
        h(
          "p",
          null,
          h("a", { href: "#/gaps" }, "Xem danh mục việc chưa hỗ trợ"),
        ),
      ),
      h(
        "div",
        { class: "notice", dataState: "info" },
        h("p", { class: "notice__title" }, "Tạo yêu cầu duyệt cũng không có ở đây"),
        h(
          "p",
          null,
          "Cùng một loại lý do: một yêu cầu duyệt cần resource_type khớp đúng ánh xạ hành động → " +
            "loại tài nguyên của máy chủ, cộng hai mã băm JCS và một policy_version. Không giá trị " +
            "nào trong số đó nhân viên gõ tay ra được, nên màn hình không mời bạn thử.",
        ),
      ),
      h(
        "dl",
        { class: "fields" },
        h(
          "div",
          { class: "field field--span" },
          h("dt", null, "Tách vai người làm / người duyệt"),
          h(
            "dd",
            null,
            "Máy chủ từ chối quyết định đến từ chính nhân viên đã tạo yêu cầu. Danh sách này không " +
              "trả về ai là người yêu cầu, nên bạn chỉ biết mình vướng quy tắc đó khi máy chủ từ chối.",
          ),
        ),
        h(
          "div",
          { class: "field field--span" },
          h("dt", null, "Thời hạn phong bì"),
          h(
            "dd",
            null,
            "10, 15 hoặc 30 phút tuỳ hành động, và hết hạn thì không bao giờ được gia hạn ngầm. " +
              "Hết giờ nghĩa là phải tạo lại yêu cầu mới, không phải xin thêm thời gian.",
          ),
        ),
        h(
          "div",
          { class: "field field--span" },
          h("dt", null, "Phạm vi cửa hàng"),
          h(
            "dd",
            null,
            "Hàng chờ này gồm mọi cửa hàng mà tài khoản của bạn được gán, không lọc theo cửa hàng " +
              "đang chọn ở thanh phía trên.",
          ),
        ),
      ),
    ),
  });
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  /**
   * The live countdown badges, rebuilt every time the list reloads.
   *
   * Held as a list rather than read out of the DOM so that one interval — started once, in this
   * function — drives every badge no matter how many times the list is refreshed. Starting a timer
   * inside `loadList` would stack a new one on each reload.
   *
   * @type {Array<{host: HTMLElement, expiresAt: string}>}
   */
  const clocks = [];

  const listHost = h("div", null, skeleton(3));
  const listCount = h("span", { class: "count" }, "…");
  const truncation = h("p", { class: "hint" });

  async function loadList() {
    clocks.length = 0;
    render(listHost, skeleton(3));
    try {
      const items = await request(`/internal/v1/approvals?limit=${LIST_LIMIT}`);
      clocks.length = 0;
      listCount.textContent = count(items, LIST_LIMIT);
      truncation.textContent = isTruncated(items, LIST_LIMIT)
        ? `Máy chủ trả tối đa ${LIST_LIMIT} bản ghi và đã trả đủ; có thể còn nữa. API này không có phân trang.`
        : "";

      if (!items.length) {
        render(
          listHost,
          empty(
            "Không có phong bì nào gắn với đơn hàng đang chờ bạn quyết định. Đây không phải bằng " +
              "chứng là hàng chờ trống — xem giới hạn ở trên.",
          ),
        );
        return;
      }

      // Server order is `ORDER BY expires_at, id`: the envelope dying soonest is first. Not
      // re-sorted here — the order is itself information about what to look at next.
      render(
        listHost,
        h(
          "div",
          { class: "stack" },
          items.map((item) =>
            approvalCard(item, (host, expiresAt) => clocks.push({ host, expiresAt })),
          ),
        ),
      );
    } catch (error) {
      clocks.length = 0;
      listCount.textContent = "—";
      truncation.textContent = "";
      render(listHost, errorNotice(error, { onRetry: () => void loadList() }));
    }
  }

  const root = h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "HÀNG CHỜ DUYỆT · CHỈ ĐỌC"),
      h("h1", null, "Duyệt"),
      h(
        "p",
        { class: "screen__lede" },
        "Các phong bì duyệt đang chờ và thời gian còn lại của từng cái. Quyết định duyệt hay từ " +
          "chối không thực hiện được từ đây, và phần bên dưới nói rõ vì sao — đó là giới hạn của " +
          "API, không phải nút bị quên.",
      ),
    ),
    limitsPanel(),
    panel({
      eyebrow: "ĐANG CHỜ",
      title: "Phong bì chờ quyết định",
      count: listCount,
      actions: h(
        "button",
        { type: "button", dataVariant: "quiet", onClick: () => void loadList() },
        "Tải lại",
      ),
      children: h("div", { class: "stack" }, truncation, listHost),
    }),
  );

  // One interval for the whole screen. It checks that the screen is still in the document before
  // doing any work and clears itself when it is not, so navigating away — or the router replacing
  // this screen on a session change — cannot leave a timer running against a detached tree.
  const timer = setInterval(() => {
    if (!root.isConnected) {
      clearInterval(timer);
      return;
    }
    for (const clock of clocks) render(clock.host, countdownBadge(clock.expiresAt));
  }, TICK_MS);

  void loadList();

  return root;
}

export const screen = {
  path: "/approvals",
  title: "Duyệt",
  capability: "APPROVALS_READ",
  render: render_,
};
