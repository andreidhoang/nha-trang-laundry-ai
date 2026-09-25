/**
 * What each role can actually do, measured from the server rather than read from the spec.
 *
 * `SECURITY_RELIABILITY_SPEC_V1.md:295` publishes a role × capability matrix. This is not that
 * matrix. The matrix describes the system the specification intends; the table below describes the
 * one the API enforces today, and the two differ in ways that would produce a lying interface:
 *
 *   - `AUDITOR` can read the order board and every Shadow surface, but is refused the quote list,
 *     the incident list, the approval queue and queue recovery. The matrix says "Read" across the
 *     board.
 *   - `OPERATOR` passes the route gate on the two Shadow write routes and is then refused by the
 *     repository, because the route admits `OPERATOR` and `SHADOW_DECIDE_ROLES` does not. Showing
 *     an operator a button that always fails is worse than not showing it.
 *   - Most operational routes additionally require `mfa_verified`. Four roles are guaranteed to
 *     have it by the session layer; `OPERATOR` and `DRIVER` are not, so an unverified operator
 *     authenticates successfully and is then refused everywhere.
 *
 * The rule this module exists to serve is `SECURITY_RELIABILITY_SPEC_V1.md:313` — "UI visibility is
 * not authorization". Nothing here grants anything. The server re-checks every call, and these
 * predicates only decide whether a control is presented as available or presented as unavailable
 * with a reason. Controls are disabled and explained, never hidden: a hidden control teaches staff
 * the capability does not exist, and a disabled one teaches them who to ask.
 *
 * @module core/rbac
 */

import { enumLabel } from "./i18n.js";

const OWNER = "OWNER_ADMIN";
const APPROVER = "OPS_APPROVER";
const OPERATOR = "OPERATOR";
const AUDITOR = "AUDITOR";

/**
 * @typedef {object} Capability
 * @property {string[]} roles roles the server admits
 * @property {boolean} mfa whether the server additionally demands `mfa_verified`
 * @property {string} why one line naming where enforcement happens, for the disabled-state message
 */

/** @type {Record<string, Capability>} */
export const CAPABILITIES = {
  STORES_READ: {
    roles: [OWNER, APPROVER, OPERATOR, AUDITOR, "DRIVER", "ACCOUNTANT"],
    mfa: false,
    why: "Bất kỳ phiên hợp lệ nào cũng đọc được danh sách cửa hàng của chính mình.",
  },
  ORDERS_READ: {
    roles: [OWNER, APPROVER, OPERATOR, AUDITOR],
    mfa: false,
    why: "Kho dữ liệu đơn hàng cho phép bốn vai trò này đọc.",
  },
  ORDERS_WRITE: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Tạo và chuyển trạng thái đơn cần vai trò vận hành và đã xác thực hai bước.",
  },
  QUOTES_READ: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Danh sách báo giá dùng cổng vận hành; AUDITOR bị từ chối ở đây dù đọc được đơn hàng.",
  },
  QUOTES_WRITE: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Tính giá cần vai trò vận hành và đã xác thực hai bước.",
  },
  SETTLEMENTS_READ: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Tiền đã thu hôm nay dùng cùng cổng vận hành với báo giá.",
  },
  INCIDENTS_READ: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Sự cố dùng cổng vận hành.",
  },
  INCIDENTS_WRITE: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Mở sự cố cần vai trò vận hành và đã xác thực hai bước.",
  },
  APPROVALS_READ: {
    roles: [OWNER, APPROVER],
    mfa: true,
    why: "Hàng chờ duyệt chỉ dành cho người có quyền duyệt.",
  },
  APPROVALS_DECIDE: {
    roles: [OWNER, APPROVER],
    mfa: true,
    // Same gate as reading the queue -- `decide_approval` depends on `require_approval_staff` --
    // plus two checks no client-side predicate can make: the server refuses a decision from the
    // staff member who requested it (maker-checker), and refuses one whose resource version or
    // digests have moved. Both surface as a refusal on the button, not as a hidden control.
    why: "Quyết định phê duyệt cần vai trò duyệt và đã xác thực hai bước.",
  },
  QUEUE_READ: {
    roles: [OWNER, APPROVER],
    mfa: true,
    why: "Tình trạng hàng đợi dùng cùng cổng với hàng chờ duyệt.",
  },
  SHADOW_READ: {
    roles: [OWNER, APPROVER, OPERATOR, AUDITOR],
    mfa: false,
    why: "Đọc bản nháp và dòng thời gian kiểm toán mở cho bốn vai trò; hàng chờ gửi chưa rõ kết quả cần thêm xác thực hai bước.",
  },
  UNKNOWN_SENDS_READ: {
    roles: [OWNER, APPROVER, OPERATOR, AUDITOR],
    mfa: true,
    why: "Hàng chờ gửi chưa rõ kết quả chỉ gồm biên nhận của cửa hàng bạn được gán, và cần đã xác thực hai bước.",
  },
  SHADOW_DECIDE: {
    roles: [OWNER, APPROVER],
    mfa: true,
    why: "Quyết định bản nháp và đối soát chỉ dành cho chủ hoặc người duyệt — OPERATOR qua được cổng truy cập nhưng bị từ chối ở tầng dữ liệu.",
  },
  MANUAL_SEND: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Khoá và chứng thực gửi thủ công cần vai trò vận hành và đã xác thực hai bước.",
  },
  // `DEC-033`. Lifting a customer's STOP for service messages on one channel. The route gate is
  // `require_approval_staff`; the repository re-checks role and MFA, then store membership, then
  // that the cited message is the customer's own, on that channel, after the STOP.
  SERVICE_MESSAGING_RELEASE: {
    roles: [OWNER, APPROVER],
    mfa: true,
    why:
      "Gỡ chặn tin dịch vụ chỉ dành cho chủ tiệm hoặc người duyệt đã xác thực hai bước, và phải " +
      "dựa trên một tin nhắn mới của chính khách.",
  },
  STAFF_ADMIN: {
    roles: [OWNER],
    mfa: false,
    why: "Quản lý nhân sự chỉ dành cho chủ.",
  },
  SLA_BOARD_READ: {
    roles: [OWNER, APPROVER, OPERATOR, AUDITOR],
    mfa: false,
    why: "Bảng SLA dùng đúng cổng đọc của các màn hình giám sát: bốn vai trò, và phải được gán cửa hàng.",
  },
  DAY_SUMMARY_READ: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Số đơn trong ngày dùng cổng vận hành, giống báo giá và sự cố.",
  },
  EXPORT_DATA: {
    roles: [OWNER, APPROVER],
    mfa: true,
    why: "Xuất hồ sơ ra ngoài hệ thống chỉ dành cho chủ hoặc người duyệt, và vẫn phải có chủ tiệm duyệt từng lần.",
  },
  ASSISTANT: {
    roles: [OWNER, APPROVER, OPERATOR],
    mfa: true,
    why: "Trợ lý AI dùng cổng vận hành: vai trò vận hành, đã xác thực hai bước, và được gán cửa hàng.",
  },
};

/**
 * @typedef {object} Principal
 * @property {string[]} roles
 * @property {boolean} mfaVerified
 */

/**
 * @typedef {object} Verdict
 * @property {boolean} allowed
 * @property {string} reason empty when allowed; otherwise operator-facing Vietnamese
 */

/**
 * Would the server accept this call from this principal?
 *
 * A `false` here is a prediction, not a decision. The server decides, and when the two disagree the
 * server is right — which is why every screen still renders whatever the server actually returns,
 * including a 403 for something this predicted would work.
 *
 * @param {Principal|null} principal
 * @param {keyof CAPABILITIES} capability
 * @returns {Verdict}
 */
export function can(principal, capability) {
  const rule = CAPABILITIES[capability];
  if (!rule) throw new Error(`unknown capability ${String(capability)}`);
  if (!principal) return { allowed: false, reason: "Chưa có phiên đăng nhập." };

  const held = principal.roles.filter((role) => rule.roles.includes(role));
  if (held.length === 0) {
    // The dual-language rule applies here too: the operator reads the Vietnamese gloss, the token
    // stays verbatim so the conversation with engineering has something exact to quote.
    const names = rule.roles.map((role) => enumLabel(role)).join(", ");
    return { allowed: false, reason: `${rule.why} Vai trò được phép: ${names}.` };
  }
  if (rule.mfa && !principal.mfaVerified) {
    return {
      allowed: false,
      reason: "Phiên này chưa xác thực hai bước, nên máy chủ sẽ từ chối thao tác.",
    };
  }
  return { allowed: true, reason: "" };
}

/**
 * @param {Principal|null} principal
 * @param {keyof CAPABILITIES} capability
 * @returns {boolean}
 */
export function allows(principal, capability) {
  return can(principal, capability).allowed;
}
