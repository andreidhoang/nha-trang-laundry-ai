/**
 * Staff operations console — application shell.
 *
 * Entry point for the whole console. It owns four things and delegates everything else: the
 * session, the store scope, the navigation, and the standing banners that change what the console
 * is allowed to do at all.
 *
 * Offline is one of those standing conditions. `navigator.onLine` gates every mutating control,
 * and nothing is ever queued while offline — no IndexedDB, no background sync, no retry buffer.
 * A laundry counter loses signal often, and a console that silently banked up commands would
 * deliver them minutes later against rows that had since changed, under approvals that had since
 * expired. Read-only is the honest degradation.
 *
 * @module app
 */

import { shortId } from "./src/core/format.js";
import { request } from "./src/core/api.js";
import { focusContainer, h, render } from "./src/core/dom.js";
import { enumVi } from "./src/core/i18n.js";
import { FOLD_GROUP, navOwns, navPlan } from "./src/core/nav.js";
import { can } from "./src/core/rbac.js";
import * as router from "./src/core/router.js";
import * as session from "./src/core/session.js";
import { ROUTES } from "./src/screens/index.js";
import { errorNotice, icon } from "./src/ui/components.js";
import { avatar, section, sheet } from "./src/ui/kit.js";
import { deviceList, devicesInfo } from "./src/ui/sessions.js";

const screenTitle = document.querySelector("#screen-title");
const storeLabel = document.querySelector("#appbar-store");
const appbarActions = document.querySelector("#appbar-actions");
const banners = document.querySelector("#banners");
const navList = document.querySelector("#nav-list");
const outlet = document.querySelector("#main");
const skipLink = document.querySelector("#skip-link");
/**
 * What `index.html` put in `<body>`: the skip control, the shell, `noscript`, the module script.
 * Anything else was added since -- a screen's sheet, the account sheet, the toast host -- and goes
 * when the person signs out (C1).
 */
const SHELL_NODES = new Set(document.body.children);

function currentPath() {
  return router.current().path;
}

/**
 * Whether anything waits on the Duyệt nav entry.
 *
 * Approvals are the one queue where minutes matter — the envelopes carry ten-to-thirty-minute
 * TTLs — so the shell asks once a minute while a session is active, online, allowed, and the tab
 * is being looked at. It is a plain GET: nothing is written, retried, or queued, and a failure just
 * clears the badge.
 *
 * CONSOLE-SHELL-009 (C11). It used to fetch up to a hundred whole envelopes every minute, in every
 * tab, visible or not, to draw one number. It now asks for one (`limit=1`), which is all a badge
 * needs to know: whether the queue is empty. The badge is therefore a dot that claims "something
 * waits", never a count it did not read -- the count and the list are on `#/approvals` itself. A
 * hidden tab asks nothing; coming back to it asks at once.
 */
const APPROVALS_POLL_MS = 60_000;
const APPROVALS_BADGE_LIMIT = 1;
let approvalsWaiting = false;

async function pollApprovals() {
  // Not while nobody is looking. `visibilitychange` asks again the moment the tab is shown.
  if (document.hidden) return;
  const state = session.snapshot();
  const allowed =
    state.status === "active" && state.online && can(state.principal, "APPROVALS_READ").allowed;
  let next = false;
  if (allowed) {
    try {
      const items = await request(`/internal/v1/approvals?limit=${APPROVALS_BADGE_LIMIT}`);
      next = Array.isArray(items) && items.length > 0;
    } catch {
      next = false;
    }
  }
  if (next !== approvalsWaiting) {
    approvalsWaiting = next;
    renderNav();
  }
}

/**
 * Whether a path is "under" a nav entry for this person: an order page belongs to Đơn hàng,
 * "Nhắc khách lấy đồ" to Đồ chờ lấy, and every destination that is not one of this person's tabs
 * lights the Thêm tab on a phone.
 *
 * @param {import("./src/core/nav.js").NavEntry} entry
 * @param {import("./src/core/nav.js").NavEntry[]} shown
 * @param {string} path
 */
function isActive(entry, shown, path) {
  const { item } = entry;
  if (item.phoneOnly) {
    if (path === item.path) return true;
    const owner = shown.find((other) => !other.item.phoneOnly && navOwns(other.item, path));
    return Boolean(owner && !owner.tab);
  }
  // "Tất cả màn hình" is `#/more` on a desk: lit only there.
  if (item.deskOnly) return path === item.path;
  return navOwns(item, path);
}

/**
 * One nav destination. Only destinations this person can open are rendered (C6); a shut one is
 * listed with whom to ask on `#/more`, and its route still opens the guard screen with the reason.
 *
 * @param {import("./src/core/nav.js").NavEntry} entry
 * @param {import("./src/core/nav.js").NavEntry[]} shown
 * @returns {HTMLElement}
 */
function navLink(entry, shown) {
  const { item } = entry;
  const active = isActive(entry, shown, currentPath());
  const badge = item.path === "/approvals" && approvalsWaiting;
  return h(
    "a",
    {
      class: ["nav__link", item.primary && "nav__link--primary"],
      href: `#${item.path}`,
      "aria-current": active ? "page" : null,
      title: item.hint ? `${item.label} — ${item.hint}` : item.label,
      dataNavLink: item.path,
    },
    item.primary ? h("span", { class: "nav__plus" }, icon(item.icon)) : icon(item.icon),
    h("span", null, item.label),
    badge
      ? h(
          "span",
          { class: "nav__badge nav__badge--dot", dataApprovalsWaiting: "true" },
          h("span", { class: "sr-only" }, "Có việc chờ duyệt"),
        )
      : null,
  );
}

/**
 * @param {import("./src/core/nav.js").NavEntry} entry
 * @param {import("./src/core/nav.js").NavEntry[]} shown
 * @returns {HTMLElement}
 */
function navItem(entry, shown) {
  return h(
    "li",
    {
      class: [
        "nav__item",
        entry.tab && "nav__item--tab",
        entry.tab && `nav__item--tab-${entry.tab}`,
        entry.item.phoneOnly && "nav__item--phone-only",
      ],
    },
    navLink(entry, shown),
  );
}

/**
 * Whether the sidebar's closed "Khác" group is open. In memory only (UX spec §2 invariant 3): a
 * preference for this page's lifetime, never written to the device. Opened by a press, and kept
 * open while the screen on show is one of its entries so the active entry is never hidden.
 */
let foldOpen = false;

function renderNav() {
  const { shown } = navPlan(session.principal());
  const path = currentPath();
  const children = [];
  const folded = [];
  let lastGroup = null;

  for (const entry of shown) {
    if (entry.item.fold) {
      folded.push(entry);
      continue;
    }
    const { item } = entry;
    if (item.group && item.group !== lastGroup && !item.primary) {
      lastGroup = item.group;
      children.push(h("li", { class: "nav__group", "aria-hidden": "true" }, item.group));
    }
    children.push(navItem(entry, shown));
  }

  if (folded.length) {
    const initiallyOpen = foldOpen || folded.some((entry) => isActive(entry, shown, path));
    const details = h(
      "details",
      {
        class: "nav__fold",
        open: initiallyOpen,
        // Only a person's press is remembered; the open this render set is not a preference.
        onToggle: (event) => {
          if (event.currentTarget.open !== initiallyOpen) foldOpen = event.currentTarget.open;
        },
      },
      h(
        "summary",
        { class: "nav__link nav__fold-summary", dataNavFold: "true" },
        icon("more"),
        h("span", null, FOLD_GROUP),
      ),
      h(
        "ul",
        { class: "nav__fold-list", "aria-label": FOLD_GROUP },
        folded.map((entry) => navItem(entry, shown)),
      ),
    );
    // A sidebar-only group: on a phone these are the "Khác" section of Thêm.
    children.push(h("li", { class: "nav__item nav__sidebar-only" }, details));
  }

  render(navList, children);
  // Nobody signed in, or a session that has ended: nothing to open, so no empty sidebar or bar.
  // Kept (empty) while the session is still being read, so the page does not jump when it fills.
  const bar = navList.closest(".nav");
  if (bar) bar.hidden = children.length === 0 && session.snapshot().status !== "unknown";
  publishNavHeight();
}

/**
 * Publish the bottom bar's real height, so a control that sticks to the viewport can sit above it.
 *
 * Below 64rem the navigation is a bar pinned to the bottom of the viewport and the document is
 * what scrolls, so anything `position: sticky; bottom` pins to the same edge the bar occupies and
 * the two land on top of each other. Measured at 390×844 with a real transcript on Trợ lý AI: the
 * composer ran 719–832 and the bar 751–844, which put **the Gửi button underneath the bar** —
 * `elementFromPoint` at its centre returned `nav__list`. Enter still sent, and a thumb could not.
 *
 * The bar's height is not a constant that CSS could hold: it carries a role-dependent set of
 * entries, wraps at some label lengths, and adds `env(safe-area-inset-bottom)` on the phones that
 * have one. So it is measured here, once per nav render and on resize, and read by
 * `components.css`. Presentation only — it publishes a number, and nothing else observes it.
 */
function publishNavHeight() {
  const bar = navList.closest(".nav");
  if (!bar) return;
  document.documentElement.style.setProperty("--nav-height", `${bar.offsetHeight}px`);
}

/**
 * "Thoát", pressed (CONSOLE-SHELL-009, C1). One press, one request, no retry; the answer decides.
 *
 * Signed out: `session.signOut()` has already run `wipeWorkspace()` and the signed-out screen is
 * on show -- nothing of the last customer is left in the page. Not signed out (offline, a 5xx, a
 * timeout): the session is kept, here and on the server, and the banner says so in words; the
 * screen is untouched. Only a person presses "Thoát" again.
 */
let signingOut = false;
/** @type {unknown} why the last "Thoát" did not end the session, until the next press or a sign-out */
let signOutFailure = null;

async function pressSignOut() {
  if (signingOut) return;
  signingOut = true;
  signOutFailure = null;
  syncChrome();
  const outcome = await session.signOut();
  signingOut = false;
  if (!outcome.signedOut) signOutFailure = outcome.error || true;
  syncChrome();
}

/** The account sheet: who is signed in, with which roles, and the one way out. */
const accountSheet = sheet({
  title: "Tài khoản",
  body: h("div", { class: "stack", id: "account-sheet-body" }),
  actions: h(
    "button",
    {
      type: "button",
      dataVariant: "danger",
      class: "btn btn--block",
      dataSignOut: "sheet",
      onClick: () => {
        accountSheet.close();
        void pressSignOut();
      },
    },
    icon("logout"),
    h("span", null, "Thoát"),
  ),
});

/**
 * "Thiết bị đang đăng nhập" in the account sheet (`SESSION-LIST-001`): the caller's own devices,
 * this one marked, the others signable-out by the person themselves (owner decision 2026-09-27). Built once per signed-in session and kept,
 * because `renderAppbar` runs on every chrome sync and a list rebuilt there would re-request on
 * each one; it is read when the sheet opens, which is when somebody is looking at it.
 *
 * @type {{key: string, list: ReturnType<typeof deviceList>}|null}
 */
let accountDevices = null;
/** Built once, like the sheet it sits in: an ⓘ made per chrome sync would leave a dialog per open. */
const accountDevicesInfo = devicesInfo();

/** @param {import("./src/core/session.js").Principal} principal */
function devicesFor(principal) {
  // Roles are in the key because the sign-out verdict is: a role granted or revoked mid-session
  // must not leave the list offering (or refusing) a press on yesterday's rule.
  const key = `${principal.staffUserId}|${principal.sessionId || ""}|${principal.roles.join(",")}`;
  if (!accountDevices || accountDevices.key !== key) {
    accountDevices = {
      key,
      list: deviceList({
        path: "/internal/v1/sessions",
        // Anyone may sign out their own other devices; the server decides under the row lock.
        revokeOthers: { allowed: true, reason: "" },
        ownSessionId: principal.sessionId,
        id: "account-devices",
      }),
    };
  }
  return accountDevices.list;
}

function renderAppbar() {
  const state = session.snapshot();
  const storeName =
    state.storeId && state.storeNames?.[state.storeId]
      ? state.storeNames[state.storeId]
      : state.storeId
        ? `Cửa hàng ${shortId(state.storeId)}`
        : "Giặt Là Sạch Cộng";
  storeLabel.textContent = storeName;
  if (state.status !== "active" || !state.principal) {
    render(appbarActions, h("span", { class: "appbar__session" }, "Chưa đăng nhập"));
    return;
  }

  const principal = state.principal;
  const roles = principal.roles.map(enumVi).join(" · ") || "—";
  const mfa = principal.mfaVerified ? "đã xác thực" : "chưa xác thực";
  const name = roles;
  render(
    accountSheet.body,
    h(
      "div",
      { class: "row" },
      avatar(name),
      h(
        "div",
        { class: "stack stack--tight" },
        h("p", { class: "row-item__title" }, name),
        h("p", { class: "row-item__meta" }, `${roles} · ${mfa} hai bước`),
      ),
    ),
    state.memberStoreIds.length > 1
      ? h("p", { class: "hint" }, "Đổi cửa hàng ở dải chọn cửa hàng phía trên màn hình.")
      : null,
    section({
      title: "Thiết bị đang đăng nhập",
      info: accountDevicesInfo,
      card: false,
      children: devicesFor(principal).node,
    }),
  );
  render(
    appbarActions,
    h(
      "button",
      {
        type: "button",
        class: "appbar__account",
        "aria-label": `Tài khoản: ${roles}, ${mfa} hai bước`,
        title: `${principal.roles.join(" · ")} · ${mfa} hai bước`,
        onClick: () => {
          accountSheet.open();
          void devicesFor(principal).reload();
        },
      },
      avatar(name),
      h("span", { class: "appbar__account-role" }, roles),
      h("span", { class: "sr-only" }, ` ${mfa} hai bước`),
    ),
    // Kept visible, not only inside the sheet: signing out on a shared counter tablet must never
    // take a hunt. The icon-only form fits a 360px bar; the label is its accessible name.
    h(
      "button",
      {
        type: "button",
        dataVariant: "quiet",
        class: "appbar__signout",
        "aria-label": "Thoát",
        title: "Thoát",
        dataSignOut: "appbar",
        disabled: signingOut,
        "aria-busy": signingOut ? "true" : null,
        onClick: () => void pressSignOut(),
      },
      icon("logout"),
      h("span", { class: "appbar__signout-label" }, "Thoát"),
    ),
  );
}

/**
 * The store scope selector.
 *
 * It showed a shortened identifier for years because there was no `stores` table and any name would
 * have been invented. `STORE-REGISTRY-001` created that table, so the name the people who work
 * there use is a fact the server holds — and an owner choosing between `11111111…5555` and
 * `5442b740…aaa7` at the top of the screen is one mis-read hex digit away from filing a real order
 * against the wrong shop.
 *
 * The name is shown when the server sent one, with the identifier kept in `title` for anyone who
 * needs to quote it. A store minted before the registry has no name, and those still show the
 * shortened identifier — the fallback is the old behaviour, not an invented label.
 *
 * @returns {HTMLElement|null}
 */
function storeBanner() {
  const state = session.snapshot();
  if (state.status !== "active") return null;

  if (state.memberStoreIds.length === 0) {
    // Two different facts, and until 2026-09-09 both rendered as the first one. A single failed
    // request to `/internal/v1/stores` was recorded as "assigned to nothing", so a wifi blip told
    // a member of staff their account had no shop and sent them to the owner about an assignment
    // that already existed.
    if (!state.storeScopeKnown) {
      return h(
        "div",
        { class: "banner", dataState: "danger", role: "status" },
        "Chưa đọc được danh sách cửa hàng của bạn, nên chưa biết bạn được gán những cửa hàng " +
          "nào. Đây không phải “chưa được gán” — hãy kiểm tra mạng rồi tải lại trang.",
      );
    }
    return h(
      "div",
      { class: "banner", dataState: "warn", role: "status" },
      "Tài khoản này chưa được gán cửa hàng nào, nên mọi màn hình theo cửa hàng sẽ bị từ chối. " +
        "Chủ cửa hàng gán tại màn hình Nhân sự (khối Gán cửa hàng).",
    );
  }

  if (state.memberStoreIds.length === 1) return null;

  const select = h(
    "select",
    {
      "aria-label": "Chọn cửa hàng",
      onChange: (event) => session.selectStore(event.target.value || null),
    },
    h("option", { value: "" }, "— chọn cửa hàng —"),
    state.memberStoreIds.map((id) =>
      h(
        "option",
        { value: id, selected: id === state.storeId, title: id },
        state.storeNames?.[id] ? `${state.storeNames[id]} · ${shortId(id)}` : shortId(id),
      ),
    ),
  );

  return h("div", { class: "banner", dataState: "warn" }, select);
}

function renderBanners() {
  const state = session.snapshot();
  const children = [];

  if (!state.online) {
    children.push(
      h(
        "div",
        { class: "banner", dataState: "warn", role: "status" },
        "Đang ngoại tuyến. Chỉ đọc được những gì đã ở trên màn hình; không có thao tác nào được " +
          "xếp hàng hay gửi đi.",
      ),
    );
  }

  // C1: "Thoát" did not reach the server, so the session is still open -- here and there.
  if (signOutFailure && state.status === "active") {
    children.push(
      h(
        "div",
        { class: "banner", dataState: "danger", role: "alert", dataSignOutFailed: "true" },
        "Chưa đăng xuất được — kiểm tra mạng rồi bấm Thoát lại. Phiên của bạn vẫn đang mở.",
      ),
    );
  }

  // C7: a new build is installed and waiting. Never applied by itself: reloading throws away
  // whatever is being typed, so the person chooses the moment.
  if (updateWaiting) {
    children.push(
      h(
        "div",
        { class: "banner", dataState: "info", role: "status", dataUpdateReady: "true" },
        "Có bản mới — xong việc đang nhập thì bấm Tải lại.",
        h(
          "button",
          { type: "button", dataUpdateApply: "true", onClick: applyUpdate },
          "Tải lại",
        ),
      ),
    );
  }

  if (state.status === "unreachable") {
    children.push(
      h(
        "div",
        { class: "banner", dataState: "danger", role: "alert" },
        "Mất liên lạc với máy chủ. Chưa biết phiên còn hay hết — đây không phải đã đăng xuất. " +
          "Những gì bạn đang nhập vẫn còn trên màn hình; kiểm tra mạng rồi bấm gửi lại.",
        h(
          "button",
          { type: "button", dataVariant: "quiet", onClick: () => void session.refresh() },
          "Kiểm tra lại phiên",
        ),
      ),
    );
  }

  // Not after "Thoát": the person chose to leave, the screen was cleared, and "what you typed is
  // still on screen" would be false. The signed-out screen says what is true.
  if (state.status === "ended" && !state.signedOut) {
    // An expiry with no error used to render nothing at all: the operator's next action failed and
    // the console said why only inside that one form. The banner is the standing condition, and it
    // states the thing that most needs saying — that nothing they typed was thrown away.
    children.push(
      h(
        "div",
        { class: "banner", dataState: "danger", role: "alert" },
        state.lastError ||
          "Phiên đăng nhập đã kết thúc. Những gì bạn đang nhập vẫn còn trên màn hình — " +
            "đăng nhập lại rồi bấm gửi một lần nữa.",
        h(
          "button",
          {
            type: "button",
            dataVariant: "quiet",
            onClick: () => void session.refresh(),
          },
          "Kiểm tra lại phiên",
        ),
      ),
    );
  }

  const store = storeBanner();
  if (store) children.push(store);

  render(banners, children);
}

/**
 * The screen for a console that could not reach the server, which is not the same as a sign-out.
 *
 * `session.refresh()` used to record a transport failure as `status: "ended"`, so a wifi blip on a
 * shop tablet rendered "Chưa đăng nhập" -- and the operator signed in again, learned nothing, and
 * concluded the software had lost their shift. The paragraph above `refresh()` has always forbidden
 * exactly that. The session may well still be valid; what is unknown is whether it is.
 *
 * @returns {HTMLElement}
 */
function unreachableScreen() {
  const state = session.snapshot();
  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "PHIÊN LÀM VIỆC"),
      h("h1", null, "Chưa liên lạc được với máy chủ"),
    ),
    h(
      "div",
      { class: "card stack" },
      h(
        "div",
        { class: "notice", dataState: "danger" },
        "Không đọc được phiên làm việc. Đây KHÔNG phải đã đăng xuất — phiên của bạn có thể vẫn " +
          "còn. Kiểm tra mạng của máy này rồi thử lại; đừng đăng nhập lại trước khi thử.",
      ),
      state.lastError ? h("p", { class: "hint mono" }, state.lastError) : null,
      h(
        "div",
        { class: "form__actions" },
        h(
          "button",
          { type: "button", dataVariant: "primary", onClick: () => void session.refresh() },
          "Thử lại",
        ),
      ),
    ),
  );
}

/**
 * The signed-out screen.
 *
 * Not a login form. This console cannot mint or exchange an identity token: `connect-src 'self'`
 * forbids reaching an identity provider from the browser, and the session is established by an
 * identity surface that sets HttpOnly cookies. So the screen explains the flow, offers the
 * deployment's configured entry point if there is one, and offers to re-check.
 *
 * @returns {HTMLElement}
 */
function signedOutScreen() {
  const state = session.snapshot();
  const configured = document
    .querySelector('meta[name="console-signin-path"]')
    ?.getAttribute("content")
    ?.trim();
  // Same-origin only. A sign-in path pointing at another origin would be an open redirect sitting
  // in the one place staff are trained to trust.
  const signInPath =
    configured && configured.startsWith("/") && !configured.startsWith("//") ? configured : "";

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "PHIÊN LÀM VIỆC"),
      h("h1", null, "Chưa đăng nhập"),
    ),
    h(
      "div",
      { class: "card stack" },
      state.lastError
        ? h("div", { class: "notice", dataState: "danger" }, state.lastError)
        : h(
            "p",
            null,
            "Bảng vận hành không tự đăng nhập. Nhà cung cấp danh tính cấp token, đổi lấy cookie " +
              "phiên, rồi chuyển bạn về đây.",
          ),
      signInPath
        ? h(
            "a",
            { class: "button", dataVariant: "primary", href: signInPath },
            "Tới trang đăng nhập",
          )
        : h(
            "p",
            { class: "hint" },
            "Triển khai này chưa khai báo đường dẫn đăng nhập. Đặt thẻ meta console-signin-path " +
              "trong index.html để hiện nút.",
          ),
      h(
        "div",
        { class: "form__actions" },
        h("button", { type: "button", onClick: () => void session.refresh() }, "Kiểm tra lại phiên"),
      ),
    ),
  );
}

/**
 * Hold route rendering until both the session and its store scope have been resolved.
 *
 * Deep-linking to a store-scoped screen used to build that screen while `refresh()` was still
 * asking who the operator was. Eager list views then requested `/stores/null/...`, logged a 422,
 * and rebuilt themselves a moment later when the store arrived. It looked harmless, but it made
 * every cold start begin with a failed backend call and taught monitoring to ignore real 422s.
 *
 * @returns {HTMLElement}
 */
function sessionLoadingScreen() {
  return h(
    "section",
    { class: "screen", "aria-busy": "true" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "PHIÊN LÀM VIỆC"),
      h("h1", null, "Đang kiểm tra phiên"),
    ),
    h("div", { class: "card skeleton", "aria-hidden": "true" }),
  );
}

/**
 * Refuse a screen before it renders, with the reason.
 *
 * A courtesy, not a control. The server re-checks every call, and a screen that slips through this
 * guard still shows whatever refusal the server returns.
 *
 * @param {{path: string}} _context
 * @param {{capability?: string, needsStore?: boolean}|null} route
 * @returns {Node|null}
 */
function guard(_context, route) {
  const state = session.snapshot();
  if (state.status === "unknown") return sessionLoadingScreen();
  // Before the signed-out branch on purpose: `unreachable` also has a null principal, and telling
  // an operator they are signed out when the truth is "we could not ask" is the defect this exists
  // to prevent.
  if (state.status === "unreachable") return unreachableScreen();
  if (!state.principal) return signedOutScreen();
  if (!route) return null;

  if (route.capability) {
    const verdict = can(state.principal, route.capability);
    if (!verdict.allowed) {
      return h(
        "section",
        { class: "screen" },
        h(
          "div",
          { class: "screen__header" },
          h("p", { class: "eyebrow" }, "KHÔNG ĐỦ QUYỀN"),
          h("h1", null, "Máy chủ sẽ từ chối màn hình này"),
        ),
        h("div", { class: "card" }, h("p", null, verdict.reason)),
      );
    }
  }

  if (route.needsStore && !state.storeId) {
    return h(
      "section",
      { class: "screen" },
      h(
        "div",
        { class: "screen__header" },
        h("p", { class: "eyebrow" }, "CHƯA CHỌN CỬA HÀNG"),
        h("h1", null, "Màn hình này cần một cửa hàng"),
      ),
      h(
        "div",
        { class: "card" },
        h(
          "p",
          null,
          state.memberStoreIds.length
            ? "Chọn cửa hàng ở thanh phía trên."
            : "Tài khoản này chưa được gán cửa hàng nào.",
        ),
      ),
    );
  }

  return null;
}

/**
 * Disable every control that cannot work without the network, and re-enable only those.
 *
 * Applied live rather than baked in at render time, and re-applied after each render. A control
 * that read `navigator.onLine` once when its screen was built would keep whatever value it saw for
 * as long as the operator stayed on that screen.
 *
 * **Three things disable a control here and this function owns exactly one of them.** Until
 * 2026-09-09 it owned the attribute outright, and re-applying it while online cleared whatever
 * anyone else had set:
 *
 *   - a permission refusal from `gated()` -- an AUDITOR, read-only, was shown a live "Tạo đơn"
 *     sitting directly above the sentence explaining why they may not create an order;
 *   - an in-flight write. Six places set `.disabled = true` for the duration of a request
 *     (`exceptions.js:116`, `assistant.js:453`, `orderRequests.js:317`, `quotes.js:339`), and a
 *     tablet that drops and rejoins wifi mid-write fires `online` -- which re-armed the button and
 *     invited a second submit of a command already in flight;
 *   - being offline, which is this function's own concern.
 *
 * Rather than teach it every other reason, it now only undoes what it did: it disables what it
 * finds enabled, marks that with `data-offline-disabled`, and on the way back clears exactly those.
 * Anything already disabled when the network drops stays disabled, whoever disabled it and why.
 */
function syncNetworkAffordance() {
  const offline = !navigator.onLine;
  for (const control of document.querySelectorAll("[data-requires-network]")) {
    if (offline) {
      // Disable only what is not already disabled, and remember that we were the one who did it.
      if (!control.hasAttribute("disabled")) {
        control.toggleAttribute("disabled", true);
        control.setAttribute("data-offline-disabled", "true");
      }
      control.setAttribute("aria-disabled", "true");
      continue;
    }
    // Back online: undo our own doing and nothing else.
    if (control.getAttribute("data-offline-disabled") === "true") {
      control.removeAttribute("data-offline-disabled");
      // `data-denied` is belt and braces: a permission refusal must survive even if some future
      // code clears `disabled` between the two branches.
      if (control.getAttribute("data-denied") !== "true") {
        control.toggleAttribute("disabled", false);
      }
    }
    control.setAttribute("aria-disabled", control.hasAttribute("disabled") ? "true" : "false");
  }
}

function syncChrome() {
  renderAppbar();
  renderBanners();
  renderNav();
  syncNetworkAffordance();
  // Match parameterised routes too (`/orders/:orderId`): comparing the literal path meant every
  // detail page left the app bar saying "Bảng vận hành" instead of where the person is.
  const path = currentPath();
  const segments = path.split("/");
  const active = router.registered().find((route) => {
    const pattern = route.path.split("/");
    return (
      pattern.length === segments.length &&
      pattern.every((part, index) => part.startsWith(":") || part === segments[index])
    );
  });
  screenTitle.textContent = active?.title || "Bảng vận hành";
  document.title = active?.title ? `${active.title} · Bảng vận hành` : "Bảng vận hành";
}

/**
 * What the current screen's content depends on.
 *
 * A screen is rebuilt only when one of these changes. Connectivity is deliberately not among them:
 * losing signal used to notify the session, which re-rendered the screen, which discarded every
 * unsaved field an operator had typed. A shop counter loses signal several times a day, and a
 * half-entered incident is not something the console may throw away because the wifi blinked.
 */
function contentKey() {
  const state = session.snapshot();
  return `${state.status}|${state.principal?.staffUserId || ""}|${state.storeId || ""}`;
}

/**
 * Clear everything the person who just signed out could have left in this page (C1).
 *
 * Run by `session.signOut()` once the server has ended the session, before subscribers hear of it
 * -- so the signed-out screen they render lands on an empty page, in the same task, with nothing
 * painted in between. Not run for an idle expiry: that path keeps the screen on purpose.
 *
 *   - every open dialog is closed, and every node a screen appended to `<body>` -- sheets, the
 *     account sheet with its device list, toasts -- is removed with whatever it said;
 *   - the outlet is emptied (the signed-out screen follows), and the address goes back to `#/` so
 *     the next person does not start on the last customer's order;
 *   - what the shell held for this person is dropped: the device list, the badge, a failed-sign-out
 *     notice. Screens drop their own hand-offs through `session.onSignOut`.
 */
function wipeWorkspace() {
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  for (const node of [...document.body.children]) {
    if (!SHELL_NODES.has(node)) node.remove();
  }
  render(accountSheet.body);
  accountDevices = null;
  approvalsWaiting = false;
  signOutFailure = null;
  foldOpen = false;
  outlet.replaceChildren();
  if (currentPath() !== "/") router.replace("/");
}

// --- C7: a new build, offered and never forced ------------------------------------------------

/**
 * How often an open console asks whether a new build exists. A browser checks for a new service
 * worker when it navigates, and a hash-routed console never navigates, so without this a tablet
 * left open all day would learn of a deploy only when somebody reloaded it.
 */
const UPDATE_CHECK_MS = 30 * 60_000;
/** @type {ServiceWorker|null} a new build's worker, installed and waiting for the person's press */
let updateWaiting = null;
/** Set only by "Tải lại": the one case in which a change of worker reloads the page. */
let reloadRequested = false;

/**
 * Watch this registration for a new build (CONSOLE-SHELL-009, C7).
 *
 * The worker no longer takes over by itself (`sw.js` waits), because a worker that switched under
 * a running page served the next lazily fetched file from the new build to old code. The waiting
 * worker is offered instead, as a quiet banner; nothing reloads until the person presses it, since
 * a reload discards whatever is being typed. On the first install there is no controller yet, so
 * nothing is offered: there is no old code to replace.
 *
 * @param {ServiceWorkerRegistration} registration
 */
function watchForUpdate(registration) {
  const offer = (/** @type {ServiceWorker|null} */ worker) => {
    if (!worker || !navigator.serviceWorker.controller) return;
    updateWaiting = worker;
    renderBanners();
  };
  offer(registration.waiting);
  registration.addEventListener("updatefound", () => {
    const incoming = registration.installing;
    incoming?.addEventListener("statechange", () => {
      if (incoming.state === "installed") offer(incoming);
    });
  });
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloadRequested) location.reload();
  });
  const check = () => {
    if (!document.hidden) registration.update().catch(() => {});
  };
  document.addEventListener("visibilitychange", check);
  setInterval(check, UPDATE_CHECK_MS);
}

/** "Tải lại": let the waiting worker take over, then reload into the new build -- once. */
function applyUpdate() {
  reloadRequested = true;
  updateWaiting?.postMessage("SKIP_WAITING");
  // A worker that went redundant in the meantime never fires `controllerchange`; the press still
  // means "reload now", and the network-first shell then serves the new build from the server.
  setTimeout(() => location.reload(), 4000);
}

async function boot() {
  session.onSignOut(wipeWorkspace);
  // C5: the skip control moves focus to the screen, and nothing else -- no route, no rebuild.
  skipLink?.addEventListener("click", () => focusContainer(outlet));
  session.watchConnectivity();

  /** @type {string|null} null until an authenticated screen has been rendered at least once. */
  let renderedKey = null;
  session.subscribe(() => {
    syncChrome();
    void pollApprovals();
    const state = session.snapshot();

    // A session ending must not rebuild the screen. That is the single worst moment to discard a
    // half-typed incident, and `session.js` promises it does not happen — the banner says what
    // occurred, the form stays put, and signing in again in another tab restores the same key so
    // the operator can simply submit. Sessions idle out at eight hours, so this fires on a shift.
    //
    // The `renderedKey !== null` guard keeps the boot case correct: arriving with no session at all
    // must still render the signed-out screen rather than leave an empty outlet.
    // `unreachable` is held to the same rule, and more strongly: a wifi blip is the commonest
    // failure a shop tablet has, and rebuilding the screen for one would throw away a half-typed
    // incident for the most trivial cause. On boot (`renderedKey === null`) it still renders, which
    // is where `unreachableScreen()` belongs; mid-shift the banner below carries it and the form
    // stays exactly where the operator left it.
    //
    // A sign-out is the opposite case (C1): the person chose to leave, `wipeWorkspace()` has
    // already emptied the page, and the signed-out screen is rendered in its place at once.
    if (
      (state.status === "ended" || state.status === "unreachable") &&
      renderedKey !== null &&
      !state.signedOut
    ) {
      return;
    }

    const key = contentKey();
    if (key === renderedKey) return;
    renderedKey = key;
    void router.render();
  });

  router.start({ outlet, routes: ROUTES, guard, onChange: syncChrome });
  // A rotation or a soft keyboard changes the bar's height without re-rendering it.
  window.addEventListener("resize", publishNavHeight, { passive: true });

  try {
    await session.refresh();
  } catch (error) {
    render(outlet, errorNotice(error));
  }

  // The one standing poll in the application: a read-only look behind the approvals badge. A hidden
  // tab skips it (`pollApprovals`), and showing the tab again asks at once.
  setInterval(() => void pollApprovals(), APPROVALS_POLL_MS);
  void pollApprovals();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void pollApprovals();
  });
  // `#/approvals` announces a recorded decision so the badge follows it now, not a minute later.
  // The same read-only GET as the poll; nothing is written or retried.
  window.addEventListener("console:approvals-changed", () => void pollApprovals());

  // The service worker caches the application shell and nothing else. Registered last so that a
  // failure to register never blocks sign-in.
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker
      .register("/staff/sw.js")
      .then(watchForUpdate)
      .catch(() => {
        /* An unregistered worker costs offline shell loading and nothing else. */
      });
  }
}

/** Read, never cached: connectivity is checked at the moment a control is used. */
export function isOnline() {
  return navigator.onLine;
}

void boot();
