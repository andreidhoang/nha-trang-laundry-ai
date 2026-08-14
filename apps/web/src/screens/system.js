/**
 * Hệ thống: the two work queues behind the console, observed and not touched.
 *
 * `GET /internal/v1/queue-recovery` returns eight integers counted straight out of PostgreSQL —
 * `outbox_events` by status and `agent_runs` by status. Four things about that response decide how
 * this screen is built, and all four are things the operator would otherwise get wrong:
 *
 *   - **It is global.** `operations.py:687` drops the principal (`del principal`) and both queries
 *     run without a store predicate. The numbers cover every store in the deployment, so the store
 *     selected in the app bar has no effect here whatsoever. A screen that let someone read these
 *     as "my store's backlog" would be a lie of omission.
 *   - **`expired` is a subset of `processing`, not a fifth bucket.** The query counts
 *     `status = 'PROCESSING' AND lease_expires_at < CURRENT_TIMESTAMP`. Adding the columns up would
 *     double-count, so no total is rendered here and the containment is stated in words.
 *   - **`replay_available` is always `false`.** `_queue_recovery_response` (`main.py:1094`) builds
 *     the model from the eight counts and never passes `replay_available`, so the Pydantic default
 *     wins on every call. It is therefore rendered as an unpopulated field with that fact attached,
 *     not as a signal — treating a hardcoded `false` as "replay is unavailable right now" would
 *     invent a capability state the server never reported.
 *   - **Nothing here is actionable from this console.** There is no replay route, no requeue route
 *     and no retry route on this surface, and inventing a button that calls nothing is worse than
 *     an empty screen. Observe-only is stated as the panel's standing rule.
 *
 * The session panel underneath reads `core/session`'s snapshot rather than re-fetching, because the
 * shell already holds the only session facts the server publishes.
 *
 * @module screens/system
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { snapshot } from "../core/session.js";
import { badge, errorNotice, facts, panel, skeleton } from "../ui/components.js";

/**
 * The badge a non-zero counter earns.
 *
 * Tokens are the database's own status words, kept verbatim so that an operator reporting "có 3 cái
 * DEAD" is giving an engineer something to grep for. `LEASE EXPIRED` is the one invented token: the
 * rows behind it are `PROCESSING` with a lapsed lease and have no status of their own.
 */
const STUCK = {
  dead: { token: "DEAD", gloss: "đã bỏ sau khi hết lượt thử; cần người xử lý", state: "danger" },
  failed: { token: "FAILED", gloss: "lượt chạy agent đã thất bại; cần người xử lý", state: "danger" },
  expired: {
    token: "LEASE EXPIRED",
    gloss: "quá hạn giữ chỗ; vẫn nằm trong PROCESSING",
    state: "warn",
  },
};

/**
 * Format one counter.
 *
 * `0` here is a value the server decided and is shown as `0`. That is not a breach of the
 * never-render-zero rule: that rule is about `null`, which this response cannot contain. A missing
 * or non-integer field, on the other hand, means the contract moved and is shown as such.
 *
 * @param {unknown} value
 * @returns {string}
 */
function counterText(value) {
  if (value === null || value === undefined) return UNKNOWN;
  if (!Number.isInteger(value)) return `${String(value)} ⚠ không phải số nguyên`;
  return String(value);
}

/**
 * A counter and, when it is non-zero and means something is stuck, the badge that says so.
 *
 * The comparison is on a queue depth, not on money — comparing two amounts is the thing this
 * console may not do, and there is no amount anywhere on this screen.
 *
 * @param {unknown} value
 * @param {{token: string, gloss: string, state: string}} [warning]
 * @returns {HTMLElement}
 */
function counter(value, warning) {
  const stuck = Number.isInteger(value) && /** @type {number} */ (value) > 0;
  return h(
    "span",
    { class: "row" },
    h("span", { class: "count" }, counterText(value)),
    stuck && warning ? badge(warning) : null,
  );
}

/**
 * `outbox_events` by status.
 *
 * @param {any} summary
 * @returns {HTMLElement}
 */
function internalQueue(summary) {
  return h(
    "div",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("div", null, h("p", { class: "eyebrow" }, "OUTBOX_EVENTS"), h("h3", null, "Hàng đợi nội bộ")),
    ),
    h(
      "p",
      { class: "hint" },
      "Sự kiện đã ghi cùng giao dịch nghiệp vụ và đang chờ worker phát đi. Đây là nơi một lệnh đã " +
        "được lưu nhưng chưa rời khỏi hệ thống.",
    ),
    facts([
      ["PENDING · chờ nhận", counter(summary.pending_internal)],
      ["PROCESSING · đang xử lý", counter(summary.processing_internal)],
      ["PROCESSING quá hạn giữ chỗ", counter(summary.expired_internal, STUCK.expired)],
      ["DEAD · đã bỏ", counter(summary.dead_internal, STUCK.dead)],
    ]),
  );
}

/**
 * `agent_runs` by status.
 *
 * @param {any} summary
 * @returns {HTMLElement}
 */
function agentQueue(summary) {
  return h(
    "div",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("div", null, h("p", { class: "eyebrow" }, "AGENT_RUNS"), h("h3", null, "Hàng đợi agent")),
    ),
    h(
      "p",
      { class: "hint" },
      "Lượt chạy của agent. Một lượt chạy chỉ soạn bản nháp; nó không gửi cho khách và không " +
        "quyết định tiền, nên tồn đọng ở đây làm chậm việc, không làm sai tiền.",
    ),
    facts([
      ["PENDING · chờ nhận", counter(summary.pending_agent)],
      ["PROCESSING · đang chạy", counter(summary.processing_agent)],
      ["PROCESSING quá hạn giữ chỗ", counter(summary.expired_agent, STUCK.expired)],
      ["FAILED · đã thất bại", counter(summary.failed_agent, STUCK.failed)],
    ]),
  );
}

/**
 * `replay_available`, rendered as the unpopulated field it is.
 *
 * @param {any} summary
 * @returns {HTMLElement}
 */
function replayNotice(summary) {
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "replay_available — không dùng để quyết định gì"),
    h(
      "p",
      null,
      "Máy chủ trả về ",
      h("span", { class: "mono" }, `replay_available = ${String(summary.replay_available)}`),
      ". Trường này không được điền từ dữ liệu: bộ dựng phản hồi bỏ qua nó nên giá trị mặc định " +
        "của mô hình luôn thắng. Đừng đọc nó như “hiện chưa phát lại được”.",
    ),
    h(
      "p",
      null,
      "Bảng vận hành cũng không có nút phát lại, xếp lại hay thử lại cho hai hàng đợi này — API " +
        "chưa có route nào cho việc đó.",
    ),
  );
}

/**
 * The current session, from what the server actually publishes about it.
 *
 * @returns {HTMLElement}
 */
function sessionPanel() {
  const state = snapshot();
  const person = state.principal;

  return panel({
    eyebrow: "PHIÊN LÀM VIỆC",
    title: "Phiên hiện tại",
    children: h(
      "div",
      { class: "stack" },
      facts([
        [
          "Mã nhân viên",
          person
            ? h("span", { title: person.staffUserId }, shortId(person.staffUserId))
            : UNKNOWN,
          { mono: true, span: true },
        ],
        [
          "Vai trò",
          person && person.roles.length ? person.roles.map(enumLabel).join(" · ") : UNKNOWN,
          { span: true },
        ],
        [
          "MFA",
          person ? (person.mfaVerified ? "đã xác thực" : "chưa xác thực") : UNKNOWN,
        ],
        [
          "Cửa hàng đang chọn",
          state.storeId
            ? h("span", { title: state.storeId }, shortId(state.storeId))
            : "chưa chọn cửa hàng",
          { mono: Boolean(state.storeId) },
        ],
      ]),
      h(
        "p",
        { class: "hint" },
        "Cửa hàng đang chọn không ảnh hưởng tới các con số phía trên; hàng đợi được đếm trên toàn " +
          "hệ thống.",
      ),
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Không liệt kê và không thu hồi được phiên khác từ đây"),
        h(
          "p",
          null,
          "Máy chủ chỉ trả về mã nhân viên, vai trò và trạng thái MFA cho phiên đang dùng; nó " +
            "không trả về mã phiên. Thu hồi một phiên cần đúng mã phiên đó, nên bảng vận hành " +
            "không thể liệt kê các phiên đang mở, cũng không thể đóng phiên trên máy khác. Nút " +
            "“Thoát” trên thanh phía trên chỉ kết thúc phiên này.",
        ),
        h(
          "p",
          null,
          h("a", { href: "#/gaps" }, "Xem danh sách năng lực chưa hỗ trợ"),
        ),
      ),
    ),
  });
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const host = h("div", { class: "stack" }, skeleton(2));

  async function load() {
    render(host, skeleton(2));
    try {
      const summary = await request("/internal/v1/queue-recovery");
      render(
        host,
        h(
          "p",
          { class: "hint" },
          "Số quá hạn giữ chỗ nằm bên trong số PROCESSING, không phải một nhóm riêng. Đừng cộng " +
            "bốn dòng lại với nhau — màn hình này cố ý không hiển thị tổng.",
        ),
        h("div", { class: "panels" }, internalQueue(summary), agentQueue(summary)),
        replayNotice(summary),
      );
    } catch (error) {
      render(host, errorNotice(error, { onRetry: () => void load() }));
    }
  }

  void load();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "CHỈ QUAN SÁT · TOÀN HỆ THỐNG"),
      h("h1", null, "Hệ thống"),
      h(
        "p",
        { class: "screen__lede" },
        "Độ sâu của hai hàng đợi chạy phía sau bảng vận hành, đọc trực tiếp từ cơ sở dữ liệu tại " +
          "thời điểm bạn tải màn hình.",
      ),
    ),
    panel({
      eyebrow: "HÀNG ĐỢI",
      title: "Tình trạng phục hồi hàng đợi",
      guardrail:
        "Màn hình này chỉ để nhìn. Không phát lại, không thử lại, không xếp lại — và các con số " +
        "đếm trên mọi cửa hàng của hệ thống, không theo cửa hàng đang chọn.",
      children: host,
    }),
    sessionPanel(),
  );
}

export const screen = {
  path: "/system",
  title: "Hệ thống",
  capability: "QUEUE_READ",
  render: render_,
};
