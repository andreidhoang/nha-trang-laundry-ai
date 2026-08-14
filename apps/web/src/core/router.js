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

import { focusContainer } from "./dom.js";

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

/** @returns {RouteContext} */
export function current() {
  const { path, query } = parse(location.hash);
  return { path, query, params: {} };
}

/**
 * Render the screen for the current hash.
 *
 * An in-flight render is aborted when the operator navigates away, so a slow list arriving after
 * the screen changed cannot paint over the new one.
 */
export async function render() {
  if (!outlet) return;
  pending?.abort();
  const controller = new AbortController();
  pending = controller;

  const { path, query } = parse(location.hash);
  const route = routes.find((candidate) => match(candidate.path, path) !== null) || null;
  const params = route ? match(route.path, path) || {} : {};
  /** @type {RouteContext} */
  const context = { params, query, path };

  const blocked = guard ? guard(context, route) : null;
  if (blocked) {
    if (controller.signal.aborted) return;
    outlet.replaceChildren(blocked);
    focusContainer(outlet);
    onChange?.();
    return;
  }

  if (!route) {
    if (controller.signal.aborted) return;
    outlet.replaceChildren(notFound(path));
    focusContainer(outlet);
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
  outlet.replaceChildren(view);
  focusContainer(outlet);
  onChange?.();
}

/**
 * A screen that failed to build at all. Distinct from a screen that rendered a server refusal —
 * this one means the console itself is broken, and says so rather than blaming the server.
 *
 * @param {unknown} error
 * @returns {HTMLElement}
 */
function renderCrash(error) {
  const section = document.createElement("section");
  section.className = "screen";
  const notice = document.createElement("div");
  notice.className = "notice";
  notice.dataset.state = "danger";
  const title = document.createElement("p");
  title.className = "notice__title";
  title.textContent = "Màn hình này không dựng được";
  const body = document.createElement("p");
  body.textContent = error instanceof Error ? error.message : String(error);
  notice.append(title, body);
  section.append(notice);
  return section;
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
 */
export function start(config) {
  outlet = config.outlet;
  routes = config.routes;
  guard = config.guard || null;
  onChange = config.onChange || null;
  window.addEventListener("hashchange", () => void render());
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
