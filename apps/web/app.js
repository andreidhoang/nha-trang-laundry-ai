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
import { h, render } from "./src/core/dom.js";
import { NAV, enumLabel } from "./src/core/i18n.js";
import { can } from "./src/core/rbac.js";
import * as router from "./src/core/router.js";
import * as session from "./src/core/session.js";
import { ROUTES } from "./src/screens/index.js";
import { errorNotice } from "./src/ui/components.js";

const screenTitle = document.querySelector("#screen-title");
const appbarActions = document.querySelector("#appbar-actions");
const banners = document.querySelector("#banners");
const navList = document.querySelector("#nav-list");
const outlet = document.querySelector("#main");

/** Navigation, in the order an operator's day runs. */
const NAV_ITEMS = [
  { path: "/", label: NAV.today },
  { path: "/quotes", label: NAV.quotes, capability: "QUOTES_READ" },
  { path: "/orders", label: NAV.orders, capability: "ORDERS_READ" },
  { path: "/approvals", label: NAV.approvals, capability: "APPROVALS_READ" },
  { path: "/shadow", label: NAV.shadow, capability: "SHADOW_READ" },
  { path: "/exceptions", label: NAV.exceptions, capability: "SHADOW_READ" },
  { path: "/incidents", label: NAV.incidents, capability: "INCIDENTS_READ" },
  { path: "/system", label: NAV.system, capability: "QUEUE_READ" },
  { path: "/staff", label: NAV.staff, capability: "STAFF_ADMIN" },
  { path: "/gaps", label: NAV.unsupported },
];

function currentPath() {
  return router.current().path;
}

function renderNav() {
  const principal = session.principal();
  render(
    navList,
    NAV_ITEMS.map((item) => {
      const verdict = item.capability
        ? can(principal, item.capability)
        : { allowed: Boolean(principal), reason: "Chưa có phiên đăng nhập." };
      const active = currentPath() === item.path;
      return h(
        "li",
        null,
        h(
          "a",
          {
            class: "nav__link",
            href: `#${item.path}`,
            "aria-current": active ? "page" : null,
            // A capability this role lacks is shown and marked, never removed. Hiding it teaches
            // staff the feature does not exist; marking it teaches them whom to ask.
            "aria-disabled": verdict.allowed ? null : "true",
            title: verdict.allowed ? item.label : `${item.label} — ${verdict.reason}`,
          },
          item.label,
        ),
      );
    }),
  );
}

function renderAppbar() {
  const state = session.snapshot();
  if (state.status !== "active" || !state.principal) {
    render(appbarActions, h("span", { class: "appbar__session" }, "Chưa đăng nhập"));
    return;
  }

  const roles = state.principal.roles.map(enumLabel).join(" · ") || "không có vai trò";
  const mfa = state.principal.mfaVerified ? "đã xác thực" : "chưa xác thực";
  render(
    appbarActions,
    h(
      "span",
      { class: "appbar__session", title: `${roles} · MFA ${mfa}` },
      state.principal.roles.join(" · ") || "—",
      h("span", { class: "sr-only" }, ` MFA ${mfa}`),
    ),
    h(
      "button",
      { type: "button", dataVariant: "quiet", onClick: () => void session.signOut() },
      "Thoát",
    ),
  );
}

/**
 * The store scope selector.
 *
 * There is no `stores` table and therefore no store name anywhere in the system. A shortened
 * identifier with the full value in the title is the honest maximum; inventing "Cửa hàng 1" would
 * be this console making up a fact about the business.
 *
 * @returns {HTMLElement|null}
 */
function storeBanner() {
  const state = session.snapshot();
  if (state.status !== "active") return null;

  if (state.memberStoreIds.length === 0) {
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
      h("option", { value: id, selected: id === state.storeId, title: id }, shortId(id)),
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

  if (state.status === "ended") {
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
  if (state.status === "unknown") return null;
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
 * Enable or disable every control that cannot work without the network.
 *
 * Applied live rather than baked in at render time, and re-applied after each render. A control
 * that read `navigator.onLine` once when its screen was built would keep whatever value it saw for
 * as long as the operator stayed on that screen.
 */
function syncNetworkAffordance() {
  const offline = !navigator.onLine;
  for (const control of document.querySelectorAll("[data-requires-network]")) {
    control.toggleAttribute("disabled", offline);
    control.setAttribute("aria-disabled", offline ? "true" : "false");
  }
}

function syncChrome() {
  renderAppbar();
  renderBanners();
  renderNav();
  syncNetworkAffordance();
  const active = router.registered().find((route) => route.path === currentPath());
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

async function boot() {
  session.watchConnectivity();

  /** @type {string|null} null until an authenticated screen has been rendered at least once. */
  let renderedKey = null;
  session.subscribe(() => {
    syncChrome();
    const state = session.snapshot();

    // A session ending must not rebuild the screen. That is the single worst moment to discard a
    // half-typed incident, and `session.js` promises it does not happen — the banner says what
    // occurred, the form stays put, and signing in again in another tab restores the same key so
    // the operator can simply submit. Sessions idle out at eight hours, so this fires on a shift.
    //
    // The `renderedKey !== null` guard keeps the boot case correct: arriving with no session at all
    // must still render the signed-out screen rather than leave an empty outlet.
    if (state.status === "ended" && renderedKey !== null) return;

    const key = contentKey();
    if (key === renderedKey) return;
    renderedKey = key;
    void router.render();
  });

  router.start({ outlet, routes: ROUTES, guard, onChange: syncChrome });

  try {
    await session.refresh();
  } catch (error) {
    render(outlet, errorNotice(error));
  }

  // The service worker caches the application shell and nothing else. Registered last so that a
  // failure to register never blocks sign-in.
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/staff/sw.js").catch(() => {
      /* An unregistered worker costs offline shell loading and nothing else. */
    });
  }
}

/** Read, never cached: connectivity is checked at the moment a control is used. */
export function isOnline() {
  return navigator.onLine;
}

void boot();
