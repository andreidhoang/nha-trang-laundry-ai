import { CLIENT_ID, CONSOLE_PATH, ISSUER_PATH, REDIRECT_PATH } from "/signin/config.js";
import { completeExchange } from "/signin/pkce.js";

/**
 * Exchange the authorization code for an ID token, then trade that for a session cookie.
 *
 * The ID token exists in this page's memory for the length of one fetch and is never stored. What
 * persists is the opaque, database-backed, revocable session cookie the API sets -- `HttpOnly`,
 * `Secure`, `SameSite=Strict` -- which is the shape `SECURITY_RELIABILITY_SPEC_V1` §5.2 requires
 * and the reason no token is ever put in browser storage.
 */
async function complete() {
  const status = document.getElementById("status");
  const parameters = new URLSearchParams(location.search);

  const failure = parameters.get("error");
  if (failure) {
    status.textContent = `Đăng nhập bị từ chối: ${parameters.get("error_description") || failure}`;
    return;
  }

  try {
    const verifier = completeExchange(parameters.get("state"));
    const code = parameters.get("code");
    if (!code) throw new Error("Phản hồi đăng nhập thiếu mã xác thực.");

    const tokenResponse = await fetch(`${ISSUER_PATH}/protocol/openid-connect/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: CLIENT_ID,
        code,
        redirect_uri: new URL(REDIRECT_PATH, location.origin).toString(),
        code_verifier: verifier,
      }),
    });
    if (!tokenResponse.ok) throw new Error("Nhà cung cấp danh tính từ chối mã xác thực.");
    const { id_token: idToken } = await tokenResponse.json();
    if (!idToken) throw new Error("Phản hồi không có ID token.");

    const sessionResponse = await fetch("/internal/v1/auth/session", {
      method: "POST",
      headers: { Authorization: `Bearer ${idToken}` },
      credentials: "same-origin",
    });
    if (sessionResponse.status === 429) {
      const retry = sessionResponse.headers.get("Retry-After") || "vài phút";
      throw new Error(`Đã thử đăng nhập quá nhiều lần. Hãy đợi ${retry} giây rồi thử lại.`);
    }
    if (!sessionResponse.ok) {
      // A rejected exchange is deliberately opaque -- one message for a bad token, a disabled
      // account, and a role that requires a second factor the session did not prove. The server
      // knows which; the browser is not told, because the browser is where an attacker is.
      throw new Error("Danh tính không được chấp nhận. Hãy liên hệ chủ tiệm.");
    }

    // Replace rather than assign: the callback URL carries a spent authorization code, and leaving
    // it in history invites a reload that fails confusingly.
    location.replace(CONSOLE_PATH);
  } catch (error) {
    status.textContent = error.message;
  }
}

complete();
