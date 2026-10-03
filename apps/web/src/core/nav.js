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

/** The closed group at the bottom of the sidebar, and its section on `#/more` (C6). */
export const FOLD_GROUP = "Khác";

/**
 * Navigation, grouped by the kind of work (spec V2 §3.2). `tab` places an entry on the phone's
 * five-slot tab bar (1, 2, 3 and 5, left to right; slot 4 is the role's own, `TAB_FOUR`);
 * everything else is reached through "Thêm" (`#/more`). The desktop sidebar shows the same
 * destinations, grouped, with "Nhận đồ" as its primary button. `phoneOnly` entries exist only on
 * the tab bar.
 *
 * CONSOLE-SHELL-009 (C6). The navigation lists only what this person can open (`navPlan`; one
 * exception since CONSOLE-RESIDUAL-009B K2: "Nhận đồ" shut for a missing second step): a new
 * member of staff met 24 entries, half of them shut, and could not tell the ones that were their
 * job from the ones that were not. A shut destination is not silently absent: `#/more` lists each
 * one, disabled, with whom to ask, under a closed "Cần quyền khác" -- and a deep link to it still
 * opens the guard screen with the full reason. `fold` entries (system, admin, the two V2 lists and
 * "Việc chưa hỗ trợ") sit in one closed group at the bottom of the sidebar, and in "Khác" on
 * `#/more`. `also` names routes that are the same job as the entry: "Nhắc khách lấy đồ" is the
 * reminder half of "Đồ chờ lấy" -- the same shelf of finished laundry, the same customers, the same
 * Gọi -- so it is one destination, with a switch at the top of both screens.
 *
 * "Bảng trễ hạn" and "Giao trễ" stay two destinations in two groups, deliberately: the first is
 * production triage (orders still being washed, which one to do first so it is not late), the
 * second is what happens after a delivery arrived late (whose fault, which credit). Different
 * orders, different moment, different people deciding.
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
    path: "/customers",
    label: NAV.customers,
    capability: "CUSTOMERS_READ",
    icon: "user",
    group: "Vận hành",
    hint: "Khách quen: SĐT, 4 số cuối hoặc tên",
  },
  // UNCLAIMED-001 (DEC-036) and PICKUP-REMIND-001 (DEC-043): one job, one destination (C6).
  {
    path: "/pickup",
    label: NAV.pickup,
    capability: "PICKUP_READ",
    icon: "store",
    group: "Vận hành",
    also: ["/reminders"],
    hint: "Đồ xong chưa lấy: gọi, nhắc khách, phí lưu kho",
  },
  {
    path: "/sla-board",
    label: NAV.slaBoard,
    capability: "SLA_BOARD_READ",
    icon: "clock",
    group: "Vận hành",
    hint: "Đơn đang làm sắp trễ hoặc đã trễ",
  },
  {
    path: "/incidents",
    label: NAV.incidents,
    capability: "INCIDENTS_READ",
    icon: "incident",
    group: "Khiếu nại & bồi hoàn",
    hint: "Khách phàn nàn về một đơn",
  },
  {
    path: "/remedies",
    label: NAV.remedies,
    capability: "INCIDENTS_READ",
    icon: "tag",
    group: "Khiếu nại & bồi hoàn",
    hint: "Giặt lại, đền, giảm trừ",
  },
  // LATE-CREDIT-002 (DEC-042).
  {
    path: "/late-deliveries",
    label: NAV.lateDeliveries,
    capability: "INCIDENTS_READ",
    icon: "truck",
    group: "Khiếu nại & bồi hoàn",
    hint: "Đã giao trễ hẹn: lỗi tiệm hay không",
  },
  {
    path: "/approvals",
    label: NAV.approvals,
    capability: "APPROVALS_READ",
    icon: "approval",
    group: "Duyệt & tin nhắn",
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
  // EINVOICE-REQUEST-001 (DEC-040).
  {
    path: "/invoices",
    label: NAV.invoices,
    capability: "INVOICES_READ",
    icon: "quote",
    group: "Quản trị",
    hint: "Khách cần hóa đơn: tải cho kế toán, ghi số",
  },
  // The fold (C6): closed at the bottom of the sidebar, "Khác" on `#/more`. The two V2 lists
  // (spec V2 §5.2: "remain as lists under Thêm"), the machine register, and the system screens.
  {
    path: "/order-requests",
    label: NAV.orderRequests,
    capability: "QUOTES_READ",
    icon: "intake",
    group: FOLD_GROUP,
    fold: true,
    hint: "Khách đã tiếp nhận",
  },
  {
    path: "/quotes",
    label: NAV.quotes,
    capability: "QUOTES_READ",
    icon: "quote",
    group: FOLD_GROUP,
    fold: true,
    hint: "Báo giá đã lập, sửa giá",
  },
  // CASH-COUNT-009 (DEC-049). The counter's way in is Hôm nay: "Đếm két" sits beside the drawer
  // figure it is counted against, twice a day. The entry here is the index one (Thêm, the closed
  // group) so the screen is listed for those who may open it -- and an operator's desk list stays
  // at the fourteen C6 measured (a fifteenth top-level row for a twice-a-day job breaks it).
  {
    path: "/cash-count",
    label: NAV.cashCount,
    capability: "CASH_COUNT",
    icon: "cash",
    group: FOLD_GROUP,
    fold: true,
    hint: "Tiền đầu ngày, đếm cuối ngày, thừa thiếu — mở từ Hôm nay",
  },
  {
    path: "/machines",
    label: NAV.machines,
    capability: "MACHINES_READ",
    icon: "washer",
    group: FOLD_GROUP,
    fold: true,
    hint: "Danh sách máy; thêm, đổi tên, ngưng dùng",
  },
  {
    path: "/staff",
    label: NAV.staff,
    capability: "STAFF_ADMIN",
    icon: "staff",
    group: FOLD_GROUP,
    fold: true,
    hint: "Người làm, vai trò, cửa hàng",
  },
  {
    path: "/exports",
    label: NAV.exports,
    capability: "EXPORT_DATA",
    icon: "download",
    group: FOLD_GROUP,
    fold: true,
    hint: "Hồ sơ đơn theo ngày hoặc khoảng ngày",
  },
  {
    path: "/system",
    label: NAV.system,
    capability: "QUEUE_READ",
    icon: "system",
    group: FOLD_GROUP,
    fold: true,
    hint: "Hàng đợi, phiên của bạn",
  },
  {
    path: "/gaps",
    label: NAV.unsupported,
    icon: "gaps",
    group: FOLD_GROUP,
    fold: true,
    hint: "Việc bảng này chưa làm được",
  },
  // On a desk, the way to `#/more` -- and so to every shut destination and whom to ask for it.
  {
    path: "/more",
    label: "Tất cả màn hình",
    icon: "more",
    group: FOLD_GROUP,
    fold: true,
    deskOnly: true,
    hint: "Mọi màn hình, kể cả màn cần quyền khác",
  },
  { path: "/more", label: NAV.more, icon: "more", group: "", tab: 5, phoneOnly: true },
];

/**
 * Phone tab 4 (C6): the most-used screen of this person's role that they can open -- the first
 * pair whose role they hold and whose screen they may open wins. The queue for those who decide;
 * the shelf of finished laundry for the counter; the numbers for those who read them. A person
 * with none of these gets no fourth tab rather than a shut one.
 *
 * @type {ReadonlyArray<readonly [string, string]>}
 */
export const TAB_FOUR = [
  ["OWNER_ADMIN", "/approvals"],
  ["OPS_APPROVER", "/approvals"],
  ["OPERATOR", "/pickup"],
  ["AUDITOR", "/reports"],
  ["ACCOUNTANT", "/reports"],
  ["ACCOUNTANT", "/expenses"],
];

/**
 * @typedef {object} NavEntry
 * @property {(typeof NAV_ITEMS)[number]} item
 * @property {number|null} tab the phone tab slot this entry occupies for this person, if any
 * @property {NavVerdict} [denied] shown, but shut, with this verdict (K2: see `navPlan`)
 */

/** `shortReason` for a role that holds the capability in a session without two-step verification. */
export const MFA_SHORT = "Cần xác thực hai bước";

/**
 * What the navigation shows this person, and what it does not (C6).
 *
 * `shown` is every destination they can open, in table order, each with the phone tab it takes
 * for them; `denied` is every destination they cannot, with its verdict -- listed by `#/more`
 * under "Cần quyền khác", never in the sidebar or the tab bar. Display only: the server decides,
 * and a deep link to a denied route still renders the guard screen with the full reason.
 *
 * @param {ReturnType<typeof import("./session.js").principal>} principal
 * @returns {{shown: NavEntry[], denied: {item: (typeof NAV_ITEMS)[number], verdict: NavVerdict}[]}}
 */
export function navPlan(principal) {
  /** @type {NavEntry[]} */
  const shown = [];
  /** @type {{item: (typeof NAV_ITEMS)[number], verdict: NavVerdict}[]} */
  const denied = [];
  if (!principal) return { shown, denied };
  const opens = (/** @type {string} */ path) => {
    const item = NAV_ITEMS.find((candidate) => candidate.path === path && !candidate.fold);
    return Boolean(item && navVerdict(principal, item).allowed);
  };
  const four =
    TAB_FOUR.find(([role, path]) => principal.roles.includes(role) && opens(path))?.[1] || null;
  for (const item of NAV_ITEMS) {
    const verdict = navVerdict(principal, item);
    if (!verdict.allowed) {
      if (!item.phoneOnly && !item.deskOnly) denied.push({ item, verdict });
      // CONSOLE-RESIDUAL-009B (K2): "Nhận đồ" is the counter's job. A counter role whose session
      // has not passed two-step verification is shown it shut, with that reason -- it is theirs
      // once they sign in with it, and a missing button teaches nobody what is missing.
      if (item.primary && verdict.short === MFA_SHORT) {
        shown.push({ item, tab: item.tab || null, denied: verdict });
      }
      continue;
    }
    shown.push({ item, tab: item.tab || (!item.fold && item.path === four ? 4 : null) });
  }
  return { shown, denied };
}

/**
 * Whether `path` is under this destination: an order page belongs to Đơn hàng, and "Nhắc khách lấy
 * đồ" to Đồ chờ lấy (`also`).
 *
 * @param {(typeof NAV_ITEMS)[number]} item
 * @param {string} path
 * @returns {boolean}
 */
export function navOwns(item, path) {
  if (item.path === "/") return path === "/";
  const under = (/** @type {string} */ root) => path === root || path.startsWith(`${root}/`);
  return under(item.path) || (item.also || []).some(under);
}

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
  return MFA_SHORT;
}

