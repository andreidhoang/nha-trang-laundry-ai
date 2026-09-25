/**
 * Hệ thống: the two work queues behind the console, observed and not touched.
 *
 * `GET /internal/v1/queue-recovery` returns eight integers counted straight out of PostgreSQL —
 * `outbox_events` by status and `agent_runs` by status. Four things about that response decide how
 * this screen is built, and all four are things the operator would otherwise get wrong:
 *
 *   - **It is global.** `operations.py:687` drops the principal (`del principal`) and both queries
 *     run without a store predicate. The numbers cover every store in the deployment, so the store
 *     selected in the app bar has no effect here whatsoever — said on the first line of the screen.
 *   - **`expired` is a subset of `processing`, not a fifth bucket.** The query counts
 *     `status = 'PROCESSING' AND lease_expires_at < CURRENT_TIMESTAMP`. Adding the columns up would
 *     double-count, so no total is rendered, and the card draws the expired count *inside* the
 *     processing figure ("trong đó quá hạn") rather than beside it as a peer.
 *   - **`replay_available` is always `false`.** `_queue_recovery_response` (`main.py:1094`) builds
 *     the model from the eight counts and never passes `replay_available`, so the Pydantic default
 *     wins on every call. It is rendered as an unpopulated field with that fact attached, not as a
 *     signal — treating a hardcoded `false` as "replay is unavailable right now" would invent a
 *     capability state the server never reported.
 *   - **Nothing here is actionable from this console.** There is no replay, requeue or retry route
 *     on this surface, and inventing a button that calls nothing is worse than an empty screen.
 *
 * This is a technical surface, so tokens stay beside their glosses (spec V2 §4.1): an operator
 * reporting "có 3 cái DEAD" is giving an engineer something to grep for.
 *
 * The session section reads `core/session`'s snapshot rather than re-fetching, because the shell
 * already holds the only session facts the server publishes.
 *
 * @module screens/system
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { snapshot } from "../core/session.js";
import { errorNotice, markUpdated } from "../ui/components.js";
import {
  button,
  infoButton,
  keyValues,
  list,
  listRow,
  page,
  section,
  show,
  skeletonRows,
  statusPill,
  techDetails,
} from "../ui/kit.js";

/**
 * The pill a non-zero counter earns.
 *
 * Tokens are the database's own status words, kept verbatim. `LEASE EXPIRED` is the one invented
 * token: the rows behind it are `PROCESSING` with a lapsed lease and have no status of their own.
 */
const STUCK = {
  dead: {
    token: "DEAD",
    pill: "đã bỏ",
    gloss: "đã bỏ sau khi hết lượt thử; cần người xử lý",
    state: "danger",
  },
  failed: {
    token: "FAILED",
    pill: "thất bại",
    gloss: "lượt chạy agent đã thất bại; cần người xử lý",
    state: "danger",
  },
  expired: {
    token: "LEASE EXPIRED",
    pill: "quá hạn khóa",
    gloss: "quá hạn khóa tạm; vẫn nằm trong PROCESSING",
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
 * Whether a counter says something is stuck. A queue depth, never money: there is no amount
 * anywhere on this screen.
 *
 * @param {unknown} value
 * @returns {boolean}
 */
function positive(value) {
  return Number.isInteger(value) && /** @type {number} */ (value) > 0;
}

/**
 * One big number with its gloss and its token.
 *
 * @param {object} spec
 * @param {unknown} spec.value
 * @param {string} spec.label
 * @param {string} spec.token
 * @param {unknown} [spec.sub] a line under the number (the expired subset of PROCESSING)
 * @param {"danger"|"warn"|null} [spec.state]
 * @returns {HTMLElement}
 */
function stat(spec) {
  return h(
    "div",
    { class: "stat", dataState: spec.state || null, dataToken: spec.token },
    h("span", { class: "stat__value" }, counterText(spec.value)),
    h("span", { class: "stat__label" }, spec.label),
    h("span", { class: "stat__token" }, spec.token),
    spec.sub || null,
  );
}

/**
 * One queue as a status card: its name, one pill for "is anything stuck", and four numbers.
 *
 * @param {object} spec
 * @param {string} spec.title
 * @param {string} spec.token the table name, for the engineer on the phone
 * @param {HTMLElement} spec.info
 * @param {Array<{value: unknown, label: string, token: string, stuck?: typeof STUCK.dead}>} spec.counts
 *   pending, processing, expired (inside processing), terminal
 * @returns {HTMLElement}
 */
function queueCard(spec) {
  const [pending, processing, expired, terminal] = spec.counts;
  const alarms = [terminal, expired].filter((entry) => entry.stuck && positive(entry.value));
  return h(
    "article",
    { class: "queue-card surface", dataQueue: spec.token },
    h(
      "div",
      { class: "queue-card__head" },
      h("h3", { class: "queue-card__title" }, spec.title, spec.info),
      alarms.length
        ? h(
            "span",
            { class: "row" },
            alarms.map((entry) =>
              statusPill({
                state: entry.stuck.state,
                text: `${counterText(entry.value)} ${entry.stuck.pill}`,
                token: entry.stuck.token,
              }),
            ),
          )
        : statusPill({ state: "ok", text: "Không có gì kẹt" }),
    ),
    h(
      "div",
      { class: "stat-grid" },
      stat(pending),
      stat({
        ...processing,
        sub: h(
          "span",
          { class: "stat__sub", dataState: positive(expired.value) ? "warn" : null },
          `trong đó quá hạn: ${counterText(expired.value)}`,
        ),
      }),
      stat({
        ...terminal,
        state: positive(terminal.value) ? "danger" : null,
        sub: positive(terminal.value)
          ? h("span", { class: "stat__sub", dataState: "danger" }, terminal.stuck?.gloss || "")
          : null,
      }),
    ),
    h("p", { class: "queue-card__token" }, spec.token),
  );
}

/**
 * @param {any} summary
 * @returns {HTMLElement}
 */
function internalQueue(summary) {
  return queueCard({
    title: "Hàng đợi nội bộ",
    token: "OUTBOX_EVENTS",
    info: infoButton(
      "Hàng đợi nội bộ là gì?",
      h(
        "p",
        { class: "hint" },
        "Sự kiện đã ghi cùng giao dịch nghiệp vụ và đang chờ tiến trình gửi phát đi. Đây là nơi một lệnh đã " +
          "được lưu nhưng chưa rời khỏi hệ thống.",
      ),
    ),
    counts: [
      { value: summary.pending_internal, label: "Chờ nhận", token: "PENDING" },
      { value: summary.processing_internal, label: "Đang xử lý", token: "PROCESSING" },
      { value: summary.expired_internal, label: "Quá hạn khóa tạm", token: "LEASE EXPIRED", stuck: STUCK.expired },
      { value: summary.dead_internal, label: "Đã bỏ", token: "DEAD", stuck: STUCK.dead },
    ],
  });
}

/**
 * @param {any} summary
 * @returns {HTMLElement}
 */
function agentQueue(summary) {
  return queueCard({
    title: "Hàng đợi agent",
    token: "AGENT_RUNS",
    info: infoButton(
      "Hàng đợi agent là gì?",
      h(
        "p",
        { class: "hint" },
        "Lượt chạy của agent. Một lượt chạy chỉ soạn bản nháp; nó không gửi cho khách và không " +
          "quyết định tiền, nên tồn đọng ở đây làm chậm việc, không làm sai tiền.",
      ),
    ),
    counts: [
      { value: summary.pending_agent, label: "Chờ nhận", token: "PENDING" },
      { value: summary.processing_agent, label: "Đang chạy", token: "PROCESSING" },
      { value: summary.expired_agent, label: "Quá hạn khóa tạm", token: "LEASE EXPIRED", stuck: STUCK.expired },
      { value: summary.failed_agent, label: "Đã thất bại", token: "FAILED", stuck: STUCK.failed },
    ],
  });
}

/**
 * How to read the numbers, and `replay_available` rendered as the unpopulated field it is.
 *
 * @param {any} summary
 * @returns {HTMLElement}
 */
function readingInfo(summary) {
  return infoButton(
    "Đọc các con số này thế nào?",
    h(
      "p",
      { class: "hint" },
      "Số quá hạn khóa tạm nằm bên trong số PROCESSING, không phải một nhóm riêng. Đừng cộng " +
        "bốn dòng lại với nhau — màn hình này cố ý không hiển thị tổng.",
    ),
    h(
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
        "Bảng vận hành cũng không có nút phát lại, xếp lại hay thử lại cho hai hàng đợi này — hệ thống " +
          "chưa có đường nào cho việc đó.",
      ),
    ),
  );
}

/**
 * The current session, from what the server actually publishes about it.
 *
 * @returns {HTMLElement}
 */
function sessionSection() {
  const state = snapshot();
  const person = state.principal;
  const storeName = state.storeId
    ? state.storeNames?.[state.storeId] || `Cửa hàng ${shortId(state.storeId)}`
    : "chưa chọn cửa hàng";

  return section({
    title: "Phiên hiện tại",
    info: infoButton(
      "Quản lý các phiên đăng nhập khác ở đâu?",
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
          "Máy chủ chỉ trả về mã nhân viên, vai trò và trạng thái xác thực hai bước cho phiên đang dùng; nó " +
            "không trả về mã phiên. Thu hồi một phiên cần đúng mã phiên đó, nên bảng vận hành " +
            "không thể liệt kê các phiên đang mở, cũng không thể đóng phiên trên máy khác. Nút " +
            "“Thoát” trên thanh phía trên chỉ kết thúc phiên này.",
        ),
        h("p", null, h("a", { href: "#/gaps" }, "Xem danh sách năng lực chưa hỗ trợ")),
      ),
    ),
    children: h(
      "div",
      { class: "stack" },
      keyValues([
        [
          "Vai trò",
          person && person.roles.length ? person.roles.map(enumLabel).join(" · ") : UNKNOWN,
        ],
        [
          "Xác thực hai bước",
          person ? (person.mfaVerified ? "đã xác thực" : "chưa xác thực") : UNKNOWN,
        ],
        ["Cửa hàng đang chọn", storeName],
      ]),
      h("p", { class: "hint" }, "Nút “Thoát” chỉ kết thúc phiên này; phiên trên máy khác không đóng được từ đây."),
      techDetails([
        person ? ["Mã nhân viên", person.staffUserId, { copy: person.staffUserId }] : null,
        state.storeId ? ["Mã cửa hàng", state.storeId, { copy: state.storeId }] : null,
      ]),
    ),
  });
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const host = h("div", { class: "stack" }, skeletonRows(2));
  const stamp = h("span", { class: "updated", role: "status" });
  const infoHost = h("span");

  async function load() {
    try {
      const summary = await request("/internal/v1/queue-recovery");
      markUpdated(stamp);
      render(infoHost, readingInfo(summary));
      render(host, h("div", { class: "queue-grid" }, internalQueue(summary), agentQueue(summary)));
    } catch (error) {
      show(host, errorNotice(error, { onRetry: () => void load() }));
    }
  }

  void load();

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Hệ thống",
      subtitle: "Chỉ quan sát · toàn hệ thống",
      info: infoButton(
        "Màn hình này cho biết gì?",
        h(
          "p",
          { class: "hint" },
          "Độ sâu của hai hàng đợi chạy phía sau bảng vận hành, đọc trực tiếp từ cơ sở dữ liệu tại " +
            "thời điểm bạn tải màn hình.",
        ),
      ),
    }),
    section({
      title: "Hàng đợi",
      card: false,
      info: infoHost,
      action: button({ label: "Tải lại", variant: "quiet", icon: "refresh", onClick: () => void load() }),
      children: h(
        "div",
        { class: "stack" },
        h(
          "p",
          { class: "hint" },
          "Chỉ để nhìn: không phát lại, không thử lại, không xếp lại. Số đếm trên mọi cửa hàng, " +
            "không theo cửa hàng đang chọn.",
        ),
        host,
        stamp,
      ),
    }),
    sessionSection(),
    // SHOP-CAPTURE-001: the owner's machine list lives under Hệ thống (spec §6); one row to it.
    section({
      title: "Thiết bị của tiệm",
      card: false,
      children: list(
        [
          listRow({
            href: "#/machines",
            leading: "washer",
            title: "Máy giặt, sấy",
            meta: "Danh sách máy quầy chọn khi bắt đầu giặt",
            data: { systemMachines: "true" },
          }),
        ],
        { label: "Thiết bị của tiệm" },
      ),
    }),
  );
}

export const screen = {
  path: "/system",
  title: "Hệ thống",
  capability: "QUEUE_READ",
  render: render_,
};
