/**
 * Session and store scope — the two pieces of state every screen reads.
 *
 * There is no token here and there never will be one. The session cookie is `HttpOnly`, so this
 * module cannot see it even if it wanted to, and `SECURITY_RELIABILITY_SPEC_V1.md:259` forbids a
 * token in browser storage regardless. What is cached is the principal the server described and
 * the store the operator is looking at — an identifier, not customer data.
 *
 * Sessions end without warning: eight hours idle, twenty-four absolute, and immediately when an
 * owner disables the account (`FR-IAM-005`). Every screen therefore has to survive a 401 arriving
 * mid-action, which is why `end()` is a state transition subscribers react to rather than a
 * redirect that throws away whatever the operator had typed.
 *
 * @module core/session
 */

import { request, whenSessionEnds } from "./api.js";
import { visibleMessage } from "./errors.js";

const STORE_KEY = "staff_store_id";

/**
 * `sessionId` is this browser's own session (`SESSION-LIST-001`): the device list marks it and never
 * offers to sign it out from there -- "Thoát" is that control, and it also clears the cookies.
 *
 * @typedef {{staffUserId: string, roles: string[], mfaVerified: boolean, sessionId: string|null}} Principal
 */

/** @type {Set<() => void>} */
const listeners = new Set();

const state = {
  /** @type {Principal|null} */
  principal: null,
  /** @type {"unknown"|"active"|"ended"} */
  status: "unknown",
  // Whether the member-store list is an answer. False means the question was not answered.
  storeScopeKnown: false,
  /** @type {Record<string, string>} store id → the name the people who work there use */
  storeNames: {},
  /** @type {string|null} */
  storeId: null,
  /** @type {string[]} */
  memberStoreIds: [],
  /** @type {boolean} */
  online: navigator.onLine,
  /** @type {string} */
  lastError: "",
  // CONSOLE-SHELL-009 (C1): true once "Thoát" has been answered by the server. Distinguishes the
  // person who chose to leave -- whose screen must be cleared -- from an idle expiry, whose screen
  // is deliberately kept so the typed input survives (`end()`). Cleared by the next session read.
  signedOut: false,
  // CONSOLE-RESIDUAL-009B (K2): the "Thoát" was pressed in another tab of this browser. The
  // signed-out screen says so, so nobody wonders why this tab left too.
  signedOutElsewhere: false,
};

/** @type {Set<() => void>} what must be forgotten when this person signs out (C1) */
const signOutListeners = new Set();

function notify() {
  for (const listener of listeners) listener();
}

/**
 * @param {() => void} listener
 * @returns {() => void} unsubscribe
 */
export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** @returns {typeof state} a frozen read of current state */
export function snapshot() {
  return { ...state, roles: state.principal ? [...state.principal.roles] : [] };
}

/** @returns {Principal|null} */
export function principal() {
  return state.principal;
}

/** @returns {string|null} */
export function storeId() {
  return state.storeId;
}

/**
 * Ask the server who we are.
 *
 * A 401 is an answer, not a failure: it means no session, which is the normal first state of the
 * application. Anything else is left as an error for the shell to display, because a console that
 * cannot reach its own session endpoint must not pretend to be signed out — the operator would try
 * to sign in again and learn nothing.
 *
 * @returns {Promise<void>}
 */
export async function refresh() {
  try {
    const body = await request("/internal/v1/session");
    state.principal = {
      staffUserId: String(body.staff_user_id),
      roles: Array.isArray(body.roles) ? body.roles.map(String) : [],
      mfaVerified: Boolean(body.mfa_verified),
      sessionId: typeof body.session_id === "string" ? body.session_id : null,
    };
    state.status = "active";
    state.lastError = "";
    state.signedOut = false;
    state.signedOutElsewhere = false;
    await loadMemberStores();
  } catch (error) {
    state.principal = null;
    state.memberStoreIds = [];
    state.storeNames = {};
    state.storeScopeKnown = false;
    if (error && error.kind === "SESSION_ENDED") {
      // A 401 is an answer: there is no session, which is the application's normal first state.
      state.status = "ended";
      state.lastError = "";
    } else {
      // Anything else is not an answer, and the paragraph above this function has always said so:
      // a console that cannot reach its own session endpoint must not pretend to be signed out,
      // because the operator then tries to sign in again and learns nothing. Both branches used to
      // set "ended" and differ only in `lastError`, so a wifi blip on a shop tablet -- the most
      // ordinary failure this deployment has -- presented as a sign-out.
      state.status = "unreachable";
      state.lastError = visibleMessage(error);
    }
  }
  notify();
}

/**
 * Load the stores this principal is assigned to and settle the current scope.
 *
 * An empty list is the default state of a newly created staff user: store assignment is a separate
 * owner-only command (STORE-ASSIGNMENT-001, exercised from the Nhân sự screen), so a new account
 * starts scoped to nothing. That is a real operational state rather than an error, so it is
 * recorded as an empty list and surfaced by the shell.
 *
 * @returns {Promise<void>}
 */
export async function loadMemberStores() {
  try {
    const body = await request("/internal/v1/stores");
    state.memberStoreIds = Array.isArray(body?.store_ids) ? body.store_ids.map(String) : [];
    // The names, when the server knows them. A store minted before the registry has none, and the
    // shell falls back to the shortened identifier for exactly those.
    state.storeNames = Array.isArray(body?.stores)
      ? Object.fromEntries(
          body.stores
            .filter((entry) => entry && entry.store_id && entry.name)
            .map((entry) => [String(entry.store_id), String(entry.name)]),
        )
      : {};
    state.storeScopeKnown = true;
  } catch (error) {
    // An empty list and an unanswered question are different facts, and the shell says something
    // different about each. A bare `catch` recorded both as "assigned to nothing", so one failed
    // request made the console tell a member of staff that their account has no shop and to go and
    // ask the owner -- about an assignment that exists. `storeScopeKnown` is what separates them.
    state.memberStoreIds = [];
    state.storeNames = {};
    state.storeScopeKnown = error?.kind === "SESSION_ENDED";
  }

  const remembered = localStorage.getItem(STORE_KEY);
  if (remembered && state.memberStoreIds.includes(remembered)) {
    state.storeId = remembered;
  } else if (state.memberStoreIds.length === 1) {
    // One store is the whole pilot. Making the operator choose it every morning is friction with
    // no safety value, because the server would refuse any other store anyway.
    state.storeId = state.memberStoreIds[0];
    localStorage.setItem(STORE_KEY, state.storeId);
  } else if (remembered && state.memberStoreIds.length === 0) {
    // Keep a hand-entered identifier so an operator whose assignment is still being provisioned
    // can see the refusal for that exact store rather than a blank screen.
    state.storeId = remembered;
  } else {
    state.storeId = null;
  }
}

/**
 * @param {string|null} value
 */
export function selectStore(value) {
  state.storeId = value;
  if (value) localStorage.setItem(STORE_KEY, value);
  else localStorage.removeItem(STORE_KEY);
  notify();
}

/**
 * Record that the server has stopped accepting this session.
 *
 * Called from anywhere a 401 surfaces. It does not navigate and does not clear the screen: the
 * shell shows a session banner over whatever the operator was doing, so a half-typed incident is
 * still on screen after signing in again in another tab.
 */
export function end() {
  if (state.status === "ended" && !state.principal) return;
  state.principal = null;
  state.status = "ended";
  notify();
}

/**
 * Register what must be forgotten the moment this person signs out: an in-memory hand-off, a
 * stash, anything the shell or a screen holds for the person who is leaving (CONSOLE-SHELL-009,
 * C1). Called only for a sign-out the server answered -- never for an idle expiry, whose whole
 * promise is that nothing is thrown away.
 *
 * @param {() => void} listener
 * @returns {() => void} unregister
 */
export function onSignOut(listener) {
  signOutListeners.add(listener);
  return () => signOutListeners.delete(listener);
}

/**
 * The browser holds no session any more: back to the state of a cold start with nobody signed
 * in. The store scope goes with the person (the device's `staff_store_id` stays, and the next
 * session read restores it); every registered listener drops what it held; then the subscribers
 * are told, and the shell renders the signed-out screen in the place of whatever was open.
 */
function forget(elsewhere = false) {
  state.principal = null;
  state.status = "ended";
  state.lastError = "";
  state.signedOut = true;
  state.signedOutElsewhere = elsewhere;
  state.memberStoreIds = [];
  state.storeNames = {};
  state.storeScopeKnown = false;
  state.storeId = null;
  for (const listener of signOutListeners) listener();
  notify();
}

/**
 * "Thoát": end this browser's session on the server, and only then here.
 *
 * Until CONSOLE-SHELL-009 a failed sign-out was swallowed and the local session ended anyway. On a
 * shared counter PC with the wifi down that left the server session alive behind a console that
 * said "Chưa đăng nhập" -- and "Kiểm tra lại phiên" then signed the next person in as the one who
 * had just left. So the outcome is now reported, not assumed:
 *
 *   - the server answered the sign-out (200), or answered that there was no session to end (401 --
 *     a second sign-out, an expiry that got there first): the person is out. The screen is
 *     cleared by `forget()`'s listeners and subscribers, never left showing the last customer;
 *   - anything else -- offline, a timeout, a 5xx, a refusal: the session may well be alive, so it
 *     is kept here too, the operator stays signed in, and the caller says so. Nothing is retried:
 *     only a person presses "Thoát" again.
 *
 * @returns {Promise<{signedOut: boolean, error: unknown}>}
 */
export async function signOut() {
  /** @type {string | null} */
  let endSessionUrl = null;
  try {
    const result = await request("/internal/v1/auth/logout", {
      method: "POST",
      idempotencyKey: `logout:${crypto.randomUUID()}`,
    });
    endSessionUrl = result?.end_session_url ?? null;
  } catch (error) {
    // `SESSION_ENDED` is the server saying this browser has no session -- the goal state -- or
    // the CSRF cookie that is set and expires with the session cookie being gone with it.
    if (/** @type {any} */ (error)?.kind !== "SESSION_ENDED") return { signedOut: false, error };
  }
  forget();
  // K2: every other console tab of this origin shares the cookie that just ended -- they are
  // signed out too, and must not keep the last customer on show.
  announceSignOut();

  // Ending our session and leaving the issuer's alive is how a shop tablet hands the next person a
  // silent sign-in as whoever used it last: their Keycloak cookies survive, so the next
  // authorization request comes back with a code and nobody types anything. Measured on a real
  // Keycloak -- AUTH_SESSION_ID, KEYCLOAK_IDENTITY and KEYCLOAK_SESSION were all still held after
  // this function returned. The navigation is last so a failure to reach the issuer cannot leave
  // the operator looking at a console that still thinks they are signed in.
  // Null whenever the issuer is not same-origin with this console -- the demo stack, and any
  // deployment with no issuer at all. Navigating there anyway sent the operator to a host the
  // browser cannot resolve.
  if (endSessionUrl) location.assign(endSessionUrl);
  return { signedOut: true, error: null };
}

// --- CONSOLE-RESIDUAL-009B (K2): one "Thoát" clears every tab ---------------------------------

/**
 * The tabs of this console in one browser share one session cookie, so a "Thoát" the server
 * answered in one tab has ended the session in all of them -- and each of the others was still
 * showing whatever its last screen held: a customer's name, a phone number, an order. They are
 * told on a `BroadcastChannel` (same origin only, nothing written to the device, nothing kept
 * after the message), and each clears itself exactly as the tab that pressed did (`forget`).
 * A browser without `BroadcastChannel` keeps the old behaviour: the other tab finds out at its
 * next request (401), with its screen kept as for an idle expiry.
 */
const SIGN_OUT_CHANNEL = "staff-console-session";

/** @type {BroadcastChannel|null} */
const signOutChannel = (() => {
  try {
    return typeof BroadcastChannel === "function" ? new BroadcastChannel(SIGN_OUT_CHANNEL) : null;
  } catch {
    return null;
  }
})();

function announceSignOut() {
  try {
    signOutChannel?.postMessage({ type: "signed-out" });
  } catch {
    /* A tab that cannot be told learns at its next request, as before. */
  }
}

signOutChannel?.addEventListener("message", (event) => {
  if (event?.data?.type !== "signed-out") return;
  // Already out by its own press: nothing more to clear.
  if (state.signedOut && state.status === "ended") return;
  forget(true);
});

/**
 * Keep the online flag current. Offline is a first-class state: reads may still be served from
 * whatever is on screen, writes are refused before they are attempted, and nothing is queued.
 */
export function watchConnectivity() {
  const update = () => {
    state.online = navigator.onLine;
    notify();
  };
  window.addEventListener("online", update);
  window.addEventListener("offline", update);
}

// Registered at module load rather than from a caller, so that importing the session module is
// enough for a 401 anywhere to reach `end()`. A registration a caller can forget is one that will
// eventually be forgotten, and the symptom — a console that still looks signed in — is silent.
whenSessionEnds(end);
