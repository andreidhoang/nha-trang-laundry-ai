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
 * Round-9b verification 2 (K2): every history entry this page renders is stamped in its own
 * `history.state` (`consoleEntry`), increasing in the order entries are made -- and since a new
 * entry always discards the ones ahead of it, increasing along the history too. A held Back or
 * Forward is undone by stepping back to the stamp of the screen on show (`shownEntry`) rather than
 * by overwriting the entry it reached: that overwrite deleted the previous screen from the history,
 * and the next Back after signing in left the console. `returning` is true while those steps run.
 */
let lastStamp = 0;
let shownEntry = 0;
let returning = false;
/** The held screen's address when a held Back or Forward began its way back. */
let keptAddress = "";
/** Whether a route's screen is being built (awaited) -- the outlet is about to change. */
let building = false;
/**
 * Round-9b verification 3 (K2): where the screen on show sits in the history, when the browser
 * says (`navigation.currentEntry.index`; -1 when it does not), and how long the history was at the
 * last address change this page saw. Together they tell an entry an address written while the
 * screen was held has just made -- ahead of the held screen's -- from one Back or Forward reached.
 */
let shownIndex = -1;
let lengthSeen = 0;
/**
 * The move the person asked for while the screen was held (round-9b verification 3, K2): a press
 * or an address (`to`), or a Back or Forward (`go`). Made once the session is found back
 * (`resumeHeld`); forgotten when any screen is drawn -- and when the person carries on with the
 * held screen (round-9b verification 4, `noteWork`): the banner told them the screen is kept, and
 * a session read answering seconds later must not take it, and what they typed since, away.
 *
 * @type {{kind: "to", target: string}|{kind: "go", delta: number}|null}
 */
let heldMove = null;
/**
 * A held Back or Forward on its way back (`returning`) is sized as it goes (round-9b verification
 * 4): one more entry for every step it takes to reach the held screen's own, so a jump of several
 * entries -- the browser's history list, a long press on Back -- resumes as that jump, not as one
 * step. `resumeOnReturn`: the session was found back before the way back ended; the move is made
 * when it has.
 *
 * @type {{kind: "go", delta: number}|null}
 */
let returnMove = null;
let resumeOnReturn = false;
/**
 * The router's own `replace` is under way: the next address change is the current entry given
 * another address, not an entry of its own -- if it is held, the held address is put back on it.
 */
let replacing = false;

/** @returns {number|null} the current entry's place in the history, when the browser says */
function entryIndex() {
  const index = /** @type {any} */ (globalThis).navigation?.currentEntry?.index;
  return typeof index === "number" && index >= 0 ? index : null;
}

/** @returns {number|null} the current entry's stamp, if this page has stamped it */
function entryStamp() {
  const value = history.state?.consoleEntry;
  return typeof value === "number" ? value : null;
}

/**
 * The current entry's state with `stamp` in it, keeping whatever else is there.
 *
 * @param {number} stamp
 */
function stamped(stamp) {
  const state = history.state && typeof history.state === "object" ? history.state : {};
  return { ...state, consoleEntry: stamp };
}

/** @returns {number} the current entry's stamp, stamping it first if it has none */
function stampEntry() {
  const here = entryStamp();
  if (here !== null) return here;
  lastStamp = Math.max(Date.now(), lastStamp + 1);
  history.replaceState(stamped(lastStamp), "");
  return lastStamp;
}

/** A step of a held Back or Forward's way back (`returning`) has landed on an entry. */
function onPopstate() {
  if (!returning) return;
  const here = entryStamp();
  if (here === shownEntry) {
    returned();
    return;
  }
  if (here === null) {
    // An entry this page never stamped, on the way: it becomes the held screen's, address and all.
    history.replaceState(stamped(shownEntry), "", keptAddress);
    returned();
    return;
  }
  // One entry further from where the Back or Forward went: the move it asked for is one longer.
  if (returnMove) returnMove.delta += returnMove.delta < 0 ? -1 : 1;
  history.go(here < shownEntry ? 1 : -1);
}

/** The way back has reached the held screen's entry; a move found resumable meanwhile is made. */
function returned() {
  returning = false;
  returnMove = null;
  if (!resumeOnReturn) return;
  resumeOnReturn = false;
  resumeHeld();
}

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
    // Typed or chosen on the held screen: the person carried on with it (round-9b verification
    // 4). Not `change` -- a text field's comes when it loses focus, which a held press can cause.
    if (event.type === "input") forgetHeld();
    return;
  }
  const target = /** @type {Element|null} */ (event.target);
  if (!target?.closest?.("button, input, select, textarea, label, [role=button]")) return;
  worked = true;
  // A press on an in-page link is leaving, not carrying on: it is the held move (`holdPress`).
  if (!target.closest("a[href^='#/']")) forgetHeld();
}

/**
 * The person carried on with the held screen after a move was held (round-9b verification 4):
 * the move is not made when the session is found back. The banner told them the screen is kept;
 * signed in again, they press again -- the guide's "Đăng nhập lại xong thì bấm lại".
 */
function forgetHeld() {
  heldMove = null;
  resumeOnReturn = false;
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
  // K2: a held screen is not left for a button's destination either -- and no entry is made.
  else if (!heldFor(target)) location.hash = target;
}

/**
 * Go to `path` *instead of* the current address: no new history entry, so Back does not return to
 * an address that only forwards (CONSOLE-REDESIGN-004: `#/remedies?incident=…` → the incident page).
 * The second writer of the hash beside `navigate`, and the only other one.
 *
 * @param {string} path
 */
export function replace(path) {
  const target = `#${path}`;
  if (location.hash === target) return;
  replacing = true;
  location.replace(target);
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
  if (event.defaultPrevented || event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = /** @type {Element|null} */ (event.target)?.closest?.("a[href^='#/']");
  const target = link?.getAttribute("href") || "";
  if (!target || target === location.hash) return;
  if (heldFor(target)) event.preventDefault();
}

/**
 * Whether the screen on show is held rather than give way to `target` (K2); the shell names the
 * destination when it is.
 *
 * @param {string} target a hash
 * @returns {boolean}
 */
function heldFor(target) {
  const held = Boolean(routeShown && worked && !building && hold && hold(contextOf(target), true));
  if (held) heldMove = { kind: "to", target };
  return held;
}

/**
 * Make the move the person asked for while the screen was held (round-9b verification 3, K2).
 * The shell calls this once a session read made for that press finds the same person signed in
 * again: the guide's "Đăng nhập lại xong thì bấm lại" -- the press after signing in elsewhere goes
 * where it was pressed, rather than staying held until "Kiểm tra lại phiên" is found.
 *
 * @returns {boolean} whether there was a move to make
 */
export function resumeHeld() {
  if (!heldMove) return false;
  // Still stepping back to the held screen's entry (`onPopstate`): the move is made from there,
  // and a Back or Forward is not sized until then (round-9b verification 4).
  if (returning) {
    resumeOnReturn = true;
    return true;
  }
  const move = heldMove;
  heldMove = null;
  if (move.kind === "go") history.go(move.delta);
  else if (location.hash !== move.target) location.hash = move.target;
  else void render();
  return true;
}

/**
 * The address moved while the screen on show is held: put the history back as it was (K2).
 *
 * Back or Forward reached another entry of this page: step back to the held screen's own entry,
 * one at a time (`onPopstate`), leaving every entry where it was -- the next Back, once signed in
 * again, goes where it went before.
 *
 * The Back or Forward is remembered at its true size, counted on the way back (`onPopstate`):
 * a jump of several entries resumes as that jump (round-9b verification 4).
 *
 * A new address (typed, or written by code that does not ask `navigate`) made an entry of its own,
 * ahead of the held screen's (round-9b verification 3). It used to be given the held screen's
 * address and stamp: two entries of one screen, and one Back later did nothing at all. Now the
 * history steps back over it, to the held screen's own entry, and the next move overwrites it.
 *
 * Which entry is new: its place in the history, where the browser says (`navigation`). Where it
 * does not (Safari before 26.2, Firefox before 147), an entry this page never stamped that has an
 * address (round-9b verification 4): every entry this page drew is stamped, so the only unstamped
 * one behind the held screen is the bare address the console was opened at, before "#/" -- and
 * the history's length tells nothing when the new entry replaced exactly one ahead of a screen
 * reached by Back. That bare entry, and the current one given another address by `replace`, are
 * still rewritten to the held screen, which never leaves the console.
 *
 * @param {string} left the address before the move
 * @param {boolean} grew the history's length changed since the last address change
 * @param {boolean} replaced the move was the router's own `replace`, not an entry of its own
 */
function keepEntry(left, grew, replaced) {
  const here = entryStamp();
  if (here !== null && here !== shownEntry) {
    returnMove = { kind: "go", delta: here < shownEntry ? -1 : 1 };
    heldMove = returnMove;
    returning = true;
    keptAddress = left;
    history.go(here < shownEntry ? 1 : -1);
    return;
  }
  const index = entryIndex();
  const known = !replaced && index !== null && shownIndex >= 0 && index !== shownIndex;
  const madeSince =
    !replaced && index === null && here === null && (grew || location.hash !== "");
  if (known || madeSince) {
    const ahead = known ? index > shownIndex : true;
    heldMove = ahead
      ? { kind: "to", target: location.hash }
      : { kind: "go", delta: /** @type {number} */ (index) - shownIndex };
    returning = true;
    keptAddress = left;
    history.go(known ? shownIndex - /** @type {number} */ (index) : -1);
    return;
  }
  heldMove = { kind: "to", target: location.hash };
  history.replaceState(stamped(shownEntry), "", left);
}

/**
 * Render the screen for the current hash.
 *
 * An in-flight render is aborted when the operator navigates away, so a slow list arriving after
 * the screen changed cannot paint over the new one.
 *
 * CONSOLE-RESIDUAL-009B (K2, round-9b verification): while the shell says a screen must be held
 * (`hold` -- the session ended or the server is not answering), a change of address does not
 * replace a screen the person has worked on. The address goes back to it, the history as it was
 * (`keepEntry`), and the shell says why. A destination needs the server then -- and what the person
 * typed, which the banner promises is still on screen, stays true. Not once a request sent since
 * has succeeded (round-9b verification 2): the server is answering then, and the move a write
 * makes to what it created is not held -- the shell's `hold` answers false.
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
  const grew = history.length !== lengthSeen;
  lengthSeen = history.length;
  const replaced = replacing;
  replacing = false;
  if (moved && routeShown && !building) {
    // A step of a held Back's way back (`onPopstate` takes it), or its arrival: the entry of the
    // screen on show -- which never left -- is on show again. Nothing to render.
    if (returning || entryStamp() === shownEntry) return;
  }
  if (routeShown && worked && !building && hold?.(context, moved)) {
    if (moved) keepEntry(left, grew, replaced);
    return;
  }
  returning = false;
  heldMove = null;
  returnMove = null;
  resumeOnReturn = false;

  pending?.abort();
  const controller = new AbortController();
  pending = controller;
  // Stamped before the screen builds: a screen that forwards (`replace`) while building moves to
  // an entry of its own, and that entry's change of address must render, not pass for this one.
  const entry = stampEntry();
  const index = entryIndex();

  const blocked = guard ? guard(context, route) : null;
  if (blocked) {
    if (controller.signal.aborted) return;
    building = false;
    routeShown = false;
    outlet.replaceChildren(blocked);
    enter(outlet);
    onChange?.();
    return;
  }

  if (!route) {
    if (controller.signal.aborted) return;
    building = false;
    routeShown = false;
    outlet.replaceChildren(notFound(path));
    enter(outlet);
    onChange?.();
    return;
  }

  let view;
  building = true;
  try {
    view = await route.render(context);
  } catch (error) {
    if (controller.signal.aborted) return;
    view = renderCrash(error);
  }
  if (controller.signal.aborted) return;
  building = false;
  routeShown = true;
  worked = false;
  shownEntry = entry;
  shownIndex = index ?? -1;
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
  lengthSeen = history.length;
  window.addEventListener("hashchange", (event) => void render(event));
  window.addEventListener("popstate", onPopstate);
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
