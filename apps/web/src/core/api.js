/**
 * The only place in this application that talks to the server.
 *
 * Four rules are enforced here rather than left to callers, because a caller that forgets one of
 * them produces a defect nobody notices until a customer is affected:
 *
 *   1. A write is never retried automatically. Not on timeout, not on network failure, not on 500.
 *      A request that timed out may have committed; software repeating it is how the same message
 *      is sent twice, which `CHANNEL_ADAPTER_SPEC_V1.md:145` prohibits rather than discourages.
 *   2. A write is never queued while offline. There is no background sync, no retry buffer and no
 *      IndexedDB. Offline means read-only, and the operator is told so.
 *   3. `204` is never parsed. FastAPI sends `content-type: application/json` with an empty body on
 *      the five 204 routes, so branching on content-type — the obvious implementation — throws.
 *   4. Every request carries a correlation id, and every failure surfaces it, so an operator can
 *      quote one identifier that appears in the server log.
 *
 * @module core/api
 */

import { ApiError, apiError, classify } from "./errors.js";

const READ_TIMEOUT_MS = 20_000;
const WRITE_TIMEOUT_MS = 30_000;

/** The server bounds every list at 200 and answers a larger value with a 409. Ask for less. */
export const MAX_LIMIT = 200;

/** @type {() => void} */
let sessionEndObserver = () => {};

/**
 * Register the one handler told whenever the server stops accepting this session.
 *
 * The dependency runs this way — `core/session` registers with `core/api`, never the reverse —
 * because `session` already imports `request` and the other direction would be a cycle.
 *
 * Without this the console lies. A session idles out after eight hours, the operator's next submit
 * comes back 401, and that one form shows an error while the app bar still lists their roles and
 * the navigation still looks live. Nothing else in the application learns anything happened.
 *
 * @param {() => void} handler
 */
export function whenSessionEnds(handler) {
  sessionEndObserver = handler;
}

/**
 * Read a cookie by name. Only `staff_csrf` is readable — the session cookie is `HttpOnly`, which
 * is why this file never sees a token and never puts one anywhere.
 *
 * @param {string} name
 * @returns {string|null}
 */
function cookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const match = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

/**
 * Parse a response body, tolerating the empty-but-JSON-typed bodies this API sends.
 *
 * @param {Response} response
 * @returns {Promise<unknown>}
 */
async function readBody(response) {
  if (response.status === 204 || response.status === 205) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.slice(0, 400) };
  }
}

/**
 * @typedef {object} RequestOptions
 * @property {"GET"|"POST"} [method]
 * @property {unknown} [body] serialized as JSON when present
 * @property {string} [idempotencyKey] required by every mutating route in this API
 * @property {number} [ifMatch] a row version; sent strong-quoted, which is the only form accepted
 * @property {AbortSignal} [signal]
 */

/**
 * Issue one request.
 *
 * @param {string} path
 * @param {RequestOptions} [options]
 * @returns {Promise<any>} the parsed body, or `null` for a 204
 * @throws {ApiError} for every non-2xx outcome and for every transport failure
 */
export async function request(path, options = {}) {
  const method = options.method || "GET";
  const mutating = method !== "GET";
  const correlationId = crypto.randomUUID();

  if (mutating && !navigator.onLine) throw apiError("OFFLINE", { correlationId });

  /** @type {Record<string, string>} */
  const headers = { Accept: "application/json", "X-Correlation-ID": correlationId };

  if (mutating) {
    const csrf = cookie("staff_csrf");
    // The double-submit token lives in a readable cookie set at sign-in. Its absence means the
    // session is gone, not that the request is malformed, so it is reported as a session end.
    if (!csrf) {
      sessionEndObserver();
      throw apiError("SESSION_ENDED", { correlationId });
    }
    headers["X-CSRF-Token"] = csrf;
    if (!options.idempotencyKey) {
      throw new Error(`${method} ${path} was issued without an idempotency key`);
    }
    headers["Idempotency-Key"] = options.idempotencyKey;
  }

  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.ifMatch !== undefined) headers["If-Match"] = `"${options.ifMatch}"`;

  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), mutating ? WRITE_TIMEOUT_MS : READ_TIMEOUT_MS);
  const signal = options.signal
    ? AbortSignal.any([options.signal, timeout.signal])
    : timeout.signal;

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      signal,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (cause) {
    if (options.signal?.aborted) throw cause;
    throw apiError(timeout.signal.aborted ? "TIMEOUT" : "NETWORK", { correlationId });
  } finally {
    clearTimeout(timer);
  }

  const body = await readBody(response);

  if (!response.ok) {
    const retryAfter = Number.parseInt(response.headers.get("Retry-After") || "", 10);
    const failure = classify(response.status, body === null ? "" : body?.detail, {
      correlationId: response.headers.get("X-Correlation-ID") || correlationId,
      retryAfterSeconds: Number.isFinite(retryAfter) ? retryAfter : null,
    });
    // One notification, from the one place every response passes through, so no screen has to
    // remember to handle its own 401.
    if (failure.kind === "SESSION_ENDED") sessionEndObserver();
    throw failure;
  }

  return body;
}

/**
 * A submission's idempotency key.
 *
 * The key must be stable while one intent is being retried and different once the intent changes,
 * because the server hashes the payload alongside the key: replaying the same key with edited
 * content is a 409, not a replay. So the key is minted on first use, cleared when the operator
 * edits the form, and cleared again after a commit succeeds — the next submission is a new intent
 * and deserves a new key.
 */
export class Submission {
  /** @param {string} scope a short human label, e.g. "quote-create" */
  constructor(scope) {
    this.scope = scope;
    /** @type {string|null} */
    this._key = null;
  }

  /** @returns {string} */
  key() {
    if (!this._key) this._key = `${this.scope}:${crypto.randomUUID()}`;
    return this._key;
  }

  /** Call on any edit to the form, and after a successful commit. */
  reset() {
    this._key = null;
  }
}

/**
 * Whether a list came back at its ceiling, meaning there is probably more behind it.
 *
 * This API has no cursor and no offset, so a full page is the only truncation signal that exists.
 * Rendering the length as a total when it equals the limit would be a hidden truncation, and
 * `IMPLEMENTATION_ROADMAP_V1.md:394` forbids a hidden unsupported default.
 *
 * @param {unknown[]} items
 * @param {number} limit
 * @returns {boolean}
 */
export function isTruncated(items, limit) {
  return Array.isArray(items) && items.length >= limit;
}

export { ApiError };
