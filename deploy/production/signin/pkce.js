/**
 * PKCE, because this client cannot hold a secret.
 *
 * The verifier lives in `sessionStorage` rather than `localStorage`: it is single-use, scoped to
 * one tab, and gone when the tab closes. It is deleted as soon as the code is exchanged.
 */

const VERIFIER_KEY = "signin.pkce.verifier";
const STATE_KEY = "signin.pkce.state";

function randomText(bytes) {
  const buffer = new Uint8Array(bytes);
  crypto.getRandomValues(buffer);
  return base64Url(buffer);
}

function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function beginExchange() {
  const verifier = randomText(64);
  const state = randomText(16);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  return { state, challenge: base64Url(new Uint8Array(digest)) };
}

export function completeExchange(returnedState) {
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  const state = sessionStorage.getItem(STATE_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  if (!verifier || !state) {
    throw new Error("Phiên đăng nhập đã hết hạn. Hãy bắt đầu lại từ trang đăng nhập.");
  }
  if (state !== returnedState) {
    throw new Error("Phản hồi đăng nhập không khớp yêu cầu. Hãy bắt đầu lại từ trang đăng nhập.");
  }
  return verifier;
}
