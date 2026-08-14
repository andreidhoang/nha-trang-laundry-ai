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

import { request } from "./api.js";

const STORE_KEY = "staff_store_id";

/** @typedef {{staffUserId: string, roles: string[], mfaVerified: boolean}} Principal */

/** @type {Set<() => void>} */
const listeners = new Set();

const state = {
  /** @type {Principal|null} */
  principal: null,
  /** @type {"unknown"|"active"|"ended"} */
  status: "unknown",
  /** @type {string|null} */
  storeId: null,
  /** @type {string[]} */
  memberStoreIds: [],
  /** @type {boolean} */
  online: navigator.onLine,
  /** @type {string} */
  lastError: "",
};

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
    };
    state.status = "active";
    state.lastError = "";
    await loadMemberStores();
  } catch (error) {
    state.principal = null;
    state.memberStoreIds = [];
    if (error && error.kind === "SESSION_ENDED") {
      state.status = "ended";
      state.lastError = "";
    } else {
      state.status = "ended";
      state.lastError = error?.message || String(error);
    }
  }
  notify();
}

/**
 * Load the stores this principal is assigned to and settle the current scope.
 *
 * An empty list is the default state of every staff user the API can create, because assigning a
 * store has no HTTP route at all — it is provisioned out of band. That is a real operational gap
 * rather than an error, so it is recorded as an empty list and surfaced by the shell.
 *
 * @returns {Promise<void>}
 */
export async function loadMemberStores() {
  try {
    const body = await request("/internal/v1/stores");
    state.memberStoreIds = Array.isArray(body?.store_ids) ? body.store_ids.map(String) : [];
  } catch {
    state.memberStoreIds = [];
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

/** @returns {Promise<void>} */
export async function signOut() {
  try {
    await request("/internal/v1/auth/logout", {
      method: "POST",
      idempotencyKey: `logout:${crypto.randomUUID()}`,
    });
  } catch {
    // A failed sign-out still ends the local session. The cookie may survive on the server, which
    // is why the session list and its revoke control exist as a real remedy.
  }
  end();
}

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
