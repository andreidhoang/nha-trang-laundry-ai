import { CLIENT_ID, ISSUER_PATH, REDIRECT_PATH } from "/signin/config.js";
import { beginExchange } from "/signin/pkce.js";

/**
 * Send the browser to the identity provider.
 *
 * A top-level navigation, not a fetch: the login page is the provider's, staff type their password
 * and their second factor into it, and this page never sees either. `scope=openid` is what makes
 * the response carry an ID token, which is the only thing the API accepts.
 *
 * **`acr_values=mfa` is load-bearing and was missing.** The realm sets `minimum.acr.value` on the
 * client and the comment beside it claimed that made the second factor "server-enforced, not
 * something a client may decline". Measured against a real Keycloak, it is not enforced on the
 * SSO-cookie path: a second authorization request from the same browser returned a code with no
 * password, no OTP and no click at all, and an ID token carrying `acr: "0"`. Two consequences, both
 * reproduced end to end -- on a shop tablet the next person to press this button got a full
 * 24-hour session as the previous operator, presenting nothing; and a privileged member, whose role
 * requires MFA, was refused by our own API and shown an opaque "contact the owner" message on their
 * second sign-in of the day, with nothing anywhere explaining why.
 *
 * Asking for it explicitly makes the realm step up: with this parameter the same request presents
 * the password form again. The realm-side setting stays as defence in depth, but the thing that
 * actually holds is this line.
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
      acr_values: "mfa",
    });
    location.assign(`${ISSUER_PATH}/protocol/openid-connect/auth?${parameters}`);
  } catch (error) {
    button.disabled = false;
    status.textContent = `Không mở được trang đăng nhập: ${error.message}`;
  }
}

document.getElementById("start").addEventListener("click", start);
