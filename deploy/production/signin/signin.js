import { CLIENT_ID, ISSUER_PATH, REDIRECT_PATH } from "/signin/config.js";
import { beginExchange } from "/signin/pkce.js";

/**
 * Send the browser to the identity provider.
 *
 * A top-level navigation, not a fetch: the login page is the provider's, staff type their password
 * and their second factor into it, and this page never sees either. `scope=openid` is what makes
 * the response carry an ID token, which is the only thing the API accepts.
 */
async function start() {
  const button = document.getElementById("start");
  const status = document.getElementById("status");
  button.disabled = true;
  try {
    const { state, challenge } = await beginExchange();
    const parameters = new URLSearchParams({
      client_id: CLIENT_ID,
      response_type: "code",
      scope: "openid profile email",
      redirect_uri: new URL(REDIRECT_PATH, location.origin).toString(),
      state,
      code_challenge: challenge,
      code_challenge_method: "S256",
    });
    location.assign(`${ISSUER_PATH}/protocol/openid-connect/auth?${parameters}`);
  } catch (error) {
    button.disabled = false;
    status.textContent = `Không mở được trang đăng nhập: ${error.message}`;
  }
}

document.getElementById("start").addEventListener("click", start);
