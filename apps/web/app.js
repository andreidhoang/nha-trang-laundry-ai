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

import { count, shortId } from "./src/core/format.js";
import { request } from "./src/core/api.js";
import { h, render } from "./src/core/dom.js";
import { enumVi } from "./src/core/i18n.js";
import { NAV_ITEMS, navVerdict } from "./src/core/nav.js";
import { can } from "./src/core/rbac.js";
import * as router from "./src/core/router.js";
import * as session from "./src/core/session.js";
import { ROUTES } from "./src/screens/index.js";
import { errorNotice, icon } from "./src/ui/components.js";
import { avatar, sheet } from "./src/ui/kit.js";

const screenTitle = document.querySelector("#screen-title");
const storeLabel = document.querySelector("#appbar-store");
const appbarActions = document.querySelector("#appbar-actions");
const banners = document.querySelector("#banners");
const navList = document.querySelector("#nav-list");
const outlet = document.querySelector("#main");

function currentPath() {
  return router.current().path;
}

/**
 * The pending-approvals count shown on the Duyệt nav entry.
 *
 * Approvals are the one queue where minutes matter — the envelopes carry ten-to-thirty-minute
 * TTLs — so the shell polls this single read endpoint once a minute while a session is active,
 * online, and allowed. It is a plain GET: nothing is written, retried, or queued, a failure just
 * clears the badge, and the count renders through `count()` so a full page reads `100+` rather
 * than posing as an exact total.
 */
const APPROVALS_POLL_MS = 60_000;
const APPROVALS_LIMIT = 100;
let approvalsBadge = "";

async function pollApprovals() {
  const state = session.snapshot();
  const allowed =
    state.status === "active" && state.online && can(state.principal, "APPROVALS_READ").allowed;
  let next = "";
  if (allowed) {
    try {
      const items = await request(`/internal/v1/approvals?limit=${APPROVALS_LIMIT}`);
      next = Array.isArray(items) && items.length ? count(items, APPROVALS_LIMIT) : "";
    } catch {
      next = "";
    }
  }
  if (next !== approvalsBadge) {
    approvalsBadge = next;
    renderNav();
  }
}

/**
 * Whether a path is "under" a nav entry: an order page belongs to Đơn hàng, and every destination
 * reached from "Thêm" lights the Thêm tab on a phone.
 *
 * @param {(typeof NAV_ITEMS)[number]} item
 * @param {string} path
 */
function isActive(item, path) {
  if (item.path === "/") return path === "/";
  if (path === item.path || path.startsWith(`${item.path}/`)) return true;
  if (item.phoneOnly) {
    const owner = NAV_ITEMS.find(
      (other) => !other.phoneOnly && other.path !== "/" && (path === other.path || path.startsWith(`${other.path}/`)),
    );
    return Boolean(owner && !owner.tab);
  }
  return false;
}

/**
 * One nav destination. A capability this role lacks is shown and marked, never removed — hiding it
 * teaches staff the feature does not exist; marking it teaches them whom to ask. The short form
 * ("Chỉ Chủ / quản trị, Người duyệt vận hành") rides in a `nav__reason` span that CSS reveals where
 * there is room for it (the desktop sidebar), as `#/more` shows it; the full reason is the `title`,
 * and the guard screen this link opens prints it -- a `title` alone is unreachable on touch.
 *
 * @param {(typeof NAV_ITEMS)[number]} item
 * @param {import("./src/core/nav.js").NavVerdict} verdict
 * @returns {HTMLElement}
 */
function navLink(item, verdict) {
  const active = isActive(item, currentPath());
  return h(
    "a",
    {
      class: ["nav__link", item.primary && "nav__link--primary"],
      href: `#${item.path}`,
      "aria-current": active ? "page" : null,
      "aria-disabled": verdict.allowed ? null : "true",
      title: verdict.allowed ? item.label : `${item.label} — ${verdict.reason}`,
    },
    item.primary ? h("span", { class: "nav__plus" }, icon(item.icon)) : icon(item.icon),
    h("span", null, item.label),
    item.path === "/approvals" && approvalsBadge
      ? h("span", { class: "nav__badge" }, approvalsBadge)
      : null,
    verdict.allowed ? null : h("span", { class: "nav__reason" }, verdict.short || verdict.reason),
  );
}

function renderNav() {
  const principal = session.principal();
  const children = [];
  let lastGroup = null;

  for (const item of NAV_ITEMS) {
    if (item.group && item.group !== lastGroup && !item.primary) {
      lastGroup = item.group;
      children.push(h("li", { class: "nav__group", "aria-hidden": "true" }, item.group));
    }
    const verdict = navVerdict(principal, item);
    children.push(
      h(
        "li",
        {
          class: [
            "nav__item",
            item.tab && "nav__item--tab",
            item.tab && `nav__item--tab-${item.tab}`,
            item.phoneOnly && "nav__item--phone-only",
          ],
        },
        navLink(item, verdict),
      ),
    );
  }
  render(navList, children);
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
      onClick: () => {
        accountSheet.close();
        void session.signOut();
      },
    },
    icon("logout"),
    h("span", null, "Thoát"),
  ),
});

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
        onClick: () => accountSheet.open(),
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
        onClick: () => void session.signOut(),
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

async function boot() {
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
    if ((state.status === "ended" || state.status === "unreachable") && renderedKey !== null) {
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

  // The one standing poll in the application: a read-only count behind the approvals badge.
  setInterval(() => void pollApprovals(), APPROVALS_POLL_MS);
  void pollApprovals();
  // `#/approvals` announces a recorded decision so the badge follows it now, not a minute later.
  // The same read-only GET as the poll; nothing is written or retried.
  window.addEventListener("console:approvals-changed", () => void pollApprovals());

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
