/**
 * The console's destinations, in one table (spec V2 §3.2).
 *
 * Read by the shell (tab bar and sidebar) and by `#/more`, so the phone's "Thêm" screen and the
 * desktop sidebar can never disagree about what exists or who may open it.
 *
 * @module core/nav
 */

import { NAV, enumVi } from "./i18n.js";
import { CAPABILITIES, can } from "./rbac.js";

/**
 * Navigation, grouped by the kind of work (spec V2 §3.2). `tab` places an entry on the phone's
 * five-slot tab bar (1–5, left to right); everything else is reached through "Thêm" (`#/more`),
 * which lists every destination with its denial reason. The desktop sidebar shows all of them,
 * grouped, with "Nhận đồ" as its primary button. `phoneOnly` entries exist only on the tab bar.
 *
 * Exported for `screens/more.js`, so the "Thêm" screen and the sidebar can never disagree about
 * what exists or who may open it.
 */
export const NAV_ITEMS = [
  {
    path: "/new",
    label: NAV.newOrder,
    capability: "QUOTES_WRITE",
    icon: "plus",
    group: "Vận hành",
    tab: 3,
    primary: true,
    hint: "Khách gửi đồ: phiếu, giá, tạo đơn",
  },
  { path: "/", label: NAV.today, icon: "home", group: "Vận hành", tab: 1, hint: "Tiền, việc chờ, đơn hôm nay" },
  {
    path: "/orders",
    label: NAV.orders,
    capability: "ORDERS_READ",
    icon: "order",
    group: "Vận hành",
    tab: 2,
    hint: "Tìm phiếu, đơn đang làm, khách lấy đồ",
  },
  {
    path: "/order-requests",
    label: NAV.orderRequests,
    capability: "QUOTES_READ",
    icon: "intake",
    group: "Vận hành",
    hint: "Khách đã tiếp nhận",
  },
  {
    path: "/quotes",
    label: NAV.quotes,
    capability: "QUOTES_READ",
    icon: "quote",
    group: "Vận hành",
    hint: "Báo giá đã lập, sửa giá",
  },
  {
    path: "/sla-board",
    label: NAV.slaBoard,
    capability: "SLA_BOARD_READ",
    icon: "clock",
    group: "Vận hành",
    hint: "Đơn gần hoặc quá mốc 8 giờ",
  },
  {
    path: "/incidents",
    label: NAV.incidents,
    capability: "INCIDENTS_READ",
    icon: "incident",
    group: "Vận hành",
    hint: "Khách phàn nàn về một đơn",
  },
  {
    path: "/remedies",
    label: NAV.remedies,
    capability: "INCIDENTS_READ",
    icon: "tag",
    group: "Vận hành",
    hint: "Giặt lại, đền, giảm trừ",
  },
  {
    path: "/approvals",
    label: NAV.approvals,
    capability: "APPROVALS_READ",
    icon: "approval",
    group: "Duyệt & tin nhắn",
    tab: 4,
    hint: "Việc chờ bạn quyết",
  },
  {
    path: "/shadow",
    label: NAV.shadow,
    capability: "SHADOW_READ",
    icon: "draft",
    group: "Duyệt & tin nhắn",
    hint: "Tin AI soạn chờ người duyệt",
  },
  {
    path: "/assistant",
    label: NAV.assistant,
    capability: "ASSISTANT",
    icon: "sparkles",
    group: "Duyệt & tin nhắn",
    hint: "Hỏi nhanh về cửa hàng",
  },
  {
    path: "/exceptions",
    label: NAV.exceptions,
    capability: "SHADOW_READ",
    icon: "message",
    group: "Duyệt & tin nhắn",
    hint: "Gửi chưa rõ kết quả, gửi tay",
  },
  {
    path: "/reports",
    label: NAV.reports,
    capability: "REPORTS_READ",
    icon: "chart",
    group: "Quản trị",
    hint: "Đơn, đúng hẹn, giặt lại, tiền theo ngày",
  },
  // SHOP-CAPTURE-001 (DEC-038).
  {
    path: "/expenses",
    label: NAV.expenses,
    capability: "EXPENSES_READ",
    icon: "cash",
    group: "Quản trị",
    hint: "Chi phí của tiệm theo tháng",
  },
  {
    path: "/machines",
    label: NAV.machines,
    capability: "MACHINES_READ",
    icon: "washer",
    group: "Quản trị",
    hint: "Danh sách máy; thêm, đổi tên, ngưng dùng",
  },
  {
    path: "/staff",
    label: NAV.staff,
    capability: "STAFF_ADMIN",
    icon: "staff",
    group: "Quản trị",
    hint: "Người làm, vai trò, cửa hàng",
  },
  {
    path: "/exports",
    label: NAV.exports,
    capability: "EXPORT_DATA",
    icon: "download",
    group: "Quản trị",
    hint: "Hồ sơ đơn theo ngày hoặc khoảng ngày",
  },
  {
    path: "/system",
    label: NAV.system,
    capability: "QUEUE_READ",
    icon: "system",
    group: "Quản trị",
    hint: "Hàng đợi, phiên của bạn",
  },
  {
    path: "/gaps",
    label: NAV.unsupported,
    icon: "gaps",
    group: "Quản trị",
    hint: "Việc bảng này chưa làm được",
  },
  { path: "/more", label: NAV.more, icon: "more", group: "", tab: 5, phoneOnly: true },
];

/**
 * @typedef {{allowed: boolean, reason: string, short?: string}} NavVerdict
 * `reason` is `can()`'s full sentence -- the bound SERVER_GATE disclosure plus the roles the
 * server admits -- for a `title` and for the guard screen a tap opens. `short` is what fits under
 * a nav entry: whom to ask, in words ("Chỉ Chủ / quản trị, Người duyệt vận hành"), never a
 * paragraph in the navigation.
 */

/**
 * @param {ReturnType<typeof session.principal>} principal
 * @param {(typeof NAV_ITEMS)[number]} item
 * @returns {NavVerdict}
 */
export function navVerdict(principal, item) {
  const verdict = item.capability
    ? can(principal, item.capability)
    : { allowed: Boolean(principal), reason: "Chưa có phiên đăng nhập." };
  if (verdict.allowed) return verdict;
  return { ...verdict, short: shortReason(principal, item.capability) };
}

/**
 * The short form of a denial, derived from the same table `can()` reads, so it can never name a
 * role the full reason does not. Display only: the server decides.
 *
 * @param {ReturnType<typeof session.principal>} principal
 * @param {string|undefined} capability
 * @returns {string}
 */
function shortReason(principal, capability) {
  const rule = capability ? CAPABILITIES[capability] : undefined;
  if (!principal || !rule) return "Chưa đăng nhập";
  const held = principal.roles.filter((role) => rule.roles.includes(role));
  if (held.length === 0) return `Chỉ ${rule.roles.map((role) => enumVi(role)).join(", ")}`;
  return "Cần xác thực hai bước";
}

