/**
 * Where the identity provider lives and which client this is.
 *
 * Same origin as the console by necessity, not by preference: the API sets `connect-src 'self'`
 * and `form-action 'self'` on every response it serves, and `apps/web/README.md` records that the
 * browser is not and cannot become an OIDC client of a foreign origin. Mounting Keycloak at
 * `/idp/` on the console origin is what makes the whole flow same-origin without relaxing a single
 * header.
 */
export const ISSUER_PATH = "/idp/realms/nhatrang";
export const CLIENT_ID = "staff-console";
export const REDIRECT_PATH = "/signin/callback.html";
export const CONSOLE_PATH = "/staff/";
