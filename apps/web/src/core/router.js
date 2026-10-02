/**
 * Hash routing, deliberately.
 *
 * The console is served by `StaticFiles(directory=…, html=True)` mounted at `/staff`. That mount
 * has no SPA fallback: `/staff/orders/abc` is a 404, not `index.html`. History-API routing would
 * therefore require a catch-all route in the API — a change to request handling on an
 * authenticated surface, made as a side effect of a frontend delivery. A fragment is never sent to
 * the server, so it needs nothing from it, and deep links still work.
 *
 * `navigate()` is the only writer of `location.hash`, so switching to the History API later is a
 * change to two functions rather than to every screen.
 *
 * @module core/router
 */

import { focusContainer, h } from "./dom.js";
import { technicalText, visibleMessage } from "./errors.js";

/**
 * @typedef {object} Route
 * @property {string} path e.g. "/orders/:orderId"
 * @property {(context: RouteContext) => Promise<Node>|Node} render
 * @property {string} [capability] a `core/rbac` capability required to reach the screen
 * @property {boolean} [needsStore] whether the screen is meaningless without a selected store
 */

/**
 * @typedef {object} RouteContext
 * @property {Record<string, string>} params
 * @property {URLSearchParams} query
 * @property {string} path
 */

/** @type {Route[]} */
let routes = [];
/** @type {HTMLElement|null} */
let outlet = null;
/** @type {((context: RouteContext, route: Route|null) => Node|null)|null} */
let guard = null;
/** @type {(() => void)|null} */
let onChange = null;
/** @type {AbortController|null} */
let pending = null;
/** @type {((context: RouteContext, moved: boolean) => boolean)|null} */
let hold = null;
/**
 * Whether the outlet shows a screen a route rendered rather than a guard's screen, a "not found"
 * or nothing yet -- and whether the person has worked on it since it opened: typed, ticked, chose
 * or pressed something on it (`noteWork`). Only such a screen is held (`hold`): a screen only
 * looked at gives way as before, to the screen that says why and offers the way back in.
 */
let routeShown = false;
let worked = false;

/**
 * Something done on the screen on show -- a key typed, a box ticked, an option chosen, a button
 * pressed. A link is not work: pressing one is leaving.
 *
 * @param {Event} event
 */
function noteWork(event) {
  if (!routeShown) return;
  if (event.type !== "click") {
    worked = true;
    return;
  }
  const target = /** @type {Element|null} */ (event.target);
  if (target?.closest?.("button, input, select, textarea, label, [role=button]")) worked = true;
}

/**
 * Split a hash into a path and a query string.
 *
 * @param {string} hash
 * @returns {{path: string, query: URLSearchParams}}
 */
function parse(hash) {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [path, search = ""] = raw.split("?");
  return { path: path || "/", query: new URLSearchParams(search) };
}

/**
 * Match a concrete path against a pattern with `:name` segments.
 *
 * @param {string} pattern
 * @param {string} path
 * @returns {Record<string, string>|null}
 */
function match(pattern, path) {
  const expected = pattern.split("/").filter(Boolean);
  const actual = path.split("/").filter(Boolean);
  if (expected.length !== actual.length) return null;
  /** @type {Record<string, string>} */
  const params = {};
  for (let index = 0; index < expected.length; index += 1) {
    const part = expected[index];
    if (part.startsWith(":")) params[part.slice(1)] = decodeURIComponent(actual[index]);
    else if (part !== actual[index]) return null;
  }
  return params;
}

/**
 * @param {string} path
 * @param {Record<string, string>} [query]
 */
export function navigate(path, query) {
  const search = query && Object.keys(query).length ? `?${new URLSearchParams(query)}` : "";
  const target = `#${path}${search}`;
  if (location.hash === target) render();
  else location.hash = target;
}

/**
 * Go to `path` *instead of* the current address: no new history entry, so Back does not return to
 * an address that only forwards (CONSOLE-REDESIGN-004: `#/remedies?incident=…` → the incident page).
 * The second writer of the hash beside `navigate`, and the only other one.
 *
 * @param {string} path
 */
export function replace(path) {
  location.replace(`#${path}`);
}

/** @returns {RouteContext} */
export function current() {
  const { path, query } = parse(location.hash);
  return { path, query, params: {} };
}

/**
 * Enter a freshly rendered screen: top of the new content, focus on the container.
 *
 * Focus alone was not enough, and `preventScroll: true` in `focusContainer` is why — it is there
 * deliberately, so that moving focus does not yank the viewport, but it also meant the scroll
 * offset of the screen being left survived into the screen arriving. Leaving the order board
 * halfway down and opening Hôm nay dropped the operator into the middle of a screen they had
 * never seen, with the header above them off-screen.
 *
 * Two elements can hold that offset and which one does depends on the viewport: from 64rem up
 * `.app` is `height: 100dvh; overflow: hidden` and `#main` scrolls by itself, while below that the
 * document scrolls and `#main` does not. Resetting both is one real reset and one no-op either
 * way, which is cheaper and steadier than reading the breakpoint back out in JavaScript.
 *
 * @param {HTMLElement} element the routing outlet, which is also the desktop scroll container
 */
function enter(element) {
  element.scrollTop = 0;
  window.scrollTo(0, 0);
  focusContainer(element);
}

/**
 * @param {string} path
 * @returns {Route|null}
 */
function routeOf(path) {
  return routes.find((candidate) => match(candidate.path, path) !== null) || null;
}

/**
 * @param {string} hash
 * @returns {RouteContext}
 */
function contextOf(hash) {
  const { path, query } = parse(hash);
  const route = routeOf(path);
  return { params: route ? match(route.path, path) || {} : {}, query, path };
}

/**
 * A press on an in-page link while the screen on show is held (K2) is answered before the address
 * moves, so it leaves no history entry behind -- Back then still goes where it went before.
 *
 * @param {MouseEvent} event
 */
function holdPress(event) {
  if (!routeShown || !worked || !hold || event.defaultPrevented || event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = /** @type {Element|null} */ (event.target)?.closest?.("a[href^='#/']");
  const target = link?.getAttribute("href") || "";
  if (!target || target === location.hash) return;
  if (hold(contextOf(target), true)) event.preventDefault();
}

/**
 * Render the screen for the current hash.
 *
 * An in-flight render is aborted when the operator navigates away, so a slow list arriving after
 * the screen changed cannot paint over the new one.
 *
 * CONSOLE-RESIDUAL-009B (K2, round-9b verification): while the shell says a screen must be held
 * (`hold` -- the session ended or the server is not answering), a change of address does not
 * replace a screen the person has worked on. The address goes back to it (no new history entry)
 * and the shell says why. Every destination needs the server then, so nothing is lost by staying
 * -- and what the person typed, which the banner promises is still on screen, stays true.
 *
 * @param {HashChangeEvent} [event] the address change that asked for this render, if one did
 */
export async function render(event) {
  if (!outlet) return;

  const context = contextOf(location.hash);
  const { path } = context;
  const route = routeOf(path);

  const left = event?.oldURL ? new URL(event.oldURL).hash : "";
  const moved = Boolean(left) && left !== location.hash;
  if (routeShown && worked && hold?.(context, moved)) {
    if (moved) history.replaceState(history.state, "", left);
    return;
  }

  pending?.abort();
  const controller = new AbortController();
  pending = controller;

  const blocked = guard ? guard(context, route) : null;
  if (blocked) {
    if (controller.signal.aborted) return;
    routeShown = false;
    outlet.replaceChildren(blocked);
    enter(outlet);
    onChange?.();
    return;
  }

  if (!route) {
    if (controller.signal.aborted) return;
    routeShown = false;
    outlet.replaceChildren(notFound(path));
    enter(outlet);
    onChange?.();
    return;
  }

  let view;
  try {
    view = await route.render(context);
  } catch (error) {
    if (controller.signal.aborted) return;
    view = renderCrash(error);
  }
  if (controller.signal.aborted) return;
  routeShown = true;
  worked = false;
  outlet.replaceChildren(view);
  enter(outlet);
  onChange?.();
}

/**
 * A screen that failed to build at all. Distinct from a screen that rendered a server refusal —
 * this one means the console itself is broken, and says so rather than blaming the server.
 *
 * The error's own text is an engineer's (`TypeError: Cannot read properties of undefined…`), so it
 * sits in the collapsed "Chi tiết kỹ thuật" and the visible sentence is Vietnamese
 * (CONSOLE-COPY-A11Y-009, review C8). A server refusal that escaped a screen keeps its own
 * Vietnamese sentence, which `visibleMessage` returns.
 *
 * @param {unknown} error
 * @returns {HTMLElement}
 */
function renderCrash(error) {
  const engineering = technicalText(error);
  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "notice", dataState: "danger", role: "alert", dataCrash: "true" },
      h("p", { class: "notice__title" }, "Màn hình này không dựng được"),
      h("p", null, visibleMessage(error)),
      engineering
        ? h(
            "details",
            { class: "tech notice__tech", dataTech: "error" },
            h("summary", null, "Chi tiết kỹ thuật"),
            h(
              "dl",
              { class: "tech__list" },
              h(
                "div",
                { class: "tech__row" },
                h("dt", null, "Lỗi của bảng vận hành"),
                h("dd", { class: "mono" }, engineering),
              ),
            ),
          )
        : null,
    ),
  );
}

/**
 * @param {string} path
 * @returns {HTMLElement}
 */
function notFound(path) {
  const section = document.createElement("section");
  section.className = "screen";
  const heading = document.createElement("h1");
  heading.textContent = "Không có màn hình này";
  const body = document.createElement("p");
  body.textContent = `Đường dẫn ${path} không tồn tại trong bảng vận hành.`;
  section.append(heading, body);
  return section;
}

/**
 * @param {object} config
 * @param {HTMLElement} config.outlet
 * @param {Route[]} config.routes
 * @param {(context: RouteContext, route: Route|null) => Node|null} [config.guard]
 * @param {() => void} [config.onChange]
 * @param {(context: RouteContext, moved: boolean) => boolean} [config.hold] whether the screen on
 *   show must stay rather than give way to `context` (K2); the shell says why when it answers true
 */
export function start(config) {
  outlet = config.outlet;
  routes = config.routes;
  guard = config.guard || null;
  onChange = config.onChange || null;
  hold = config.hold || null;
  window.addEventListener("hashchange", (event) => void render(event));
  document.addEventListener("click", holdPress, true);
  for (const type of ["input", "change", "click"]) outlet.addEventListener(type, noteWork, true);
  if (!location.hash) location.hash = "#/";
  else void render();
}

/**
 * The routes table, exported so a contract test can assert every screen is reachable and no screen
 * requires a capability that does not exist.
 *
 * @returns {Route[]}
 */
export function registered() {
  return [...routes];
}
