# Staff operations console

The internal console for daily laundry operations and for supervising the agent. Same-origin,
staff-only, mobile-first, Vietnamese. Served by the API at `/staff/` from this directory.

Full design rationale, screen inventory and the honest gap list:
[`docs/STAFF_CONSOLE_ENGINEERING_SPEC_V1.md`](../../docs/STAFF_CONSOLE_ENGINEERING_SPEC_V1.md).

## Layout

```
index.html            the shell: app bar, banner slot, main outlet, nav. No screen markup.
app.js                entry module: session, store scope, navigation, standing banners
sw.js                 GENERATED — service worker, precaches the shell and nothing else
manifest.webmanifest
styles/               tokens · base · components · layout · print (media="print" only)
src/core/             api, errors, session, rbac, router, dom, format, i18n
src/ui/               the shared component vocabulary every screen is built from
src/screens/          one module per screen, registered in screens/index.js
```

## No build step, and why

There is no `package.json` here, no bundler and no TypeScript. `index.html` loads `app.js` as an ES
module and the browser resolves the imports.

This is a deliberate deviation from ADR-0001, which reserves TypeScript for "the future React/Vite
staff PWA". The deviation is recorded rather than assumed, and it is reversible. Four things in this
repository make a second npm tree cost more than it looks:

1. `scripts/audit_dependency_licenses.py` takes a single `--package-lock`. A second lockfile is not
   scanned, and the fail-closed licence check passes anyway.
2. `npm audit` in `release-supply-chain.yml` is pinned with `--prefix` to the OpenClaw plugin. A
   second tree is never audited for advisories.
3. The supply-chain evidence bundle lists lockfiles by hand and its schema requires `minItems: 2`
   with no coverage constraint, so an omission validates cleanly.
4. Trivy detects npm dependencies from manifests inside the image. A minified bundle carries none,
   so the per-image SBOM would certify an image whose entire frontend dependency tree is invisible.

Together those produce a green `evidence.json` over an unscanned dependency tree, which is the exact
shape of failure `CLAUDE.md` calls out: *a green test run is not completion evidence*.

Two further constraints point the same way. The content security policy is `script-src 'self';
style-src 'self'; connect-src 'self'` with no `unsafe-inline` and no nonce, and the session cookies
are `SameSite=Strict` on one origin — so a Vite dev server on another port cannot hold a session and
its HMR socket violates `connect-src`. And the console's privacy gates are source-text assertions
over authored JavaScript; minified output weakens them even where the paths are fixed.

**Adopting Vite later is legitimate.** The prerequisites are the four call sites above, plus a
digest-pinned Node builder stage in `apps/api/Dockerfile` and a decision about whether the
cross-platform byte-identical reproduction standard applied to the OpenClaw artifact extends to a
new build stage. Doing that as its own queue item with its own evidence is defensible; doing it
implicitly as a side effect of "add React" is not.

## Generated files

`sw.js` is generated. Never edit it by hand.

```bash
uv run python scripts/generate_staff_console_manifest.py          # regenerate
uv run python scripts/generate_staff_console_manifest.py --check  # fail on drift
```

Add or remove any `.js`, `.css` or `.webmanifest` file here and regenerate, or
`apps/api/tests/test_staff_console_contract.py` fails.

## Signing in

The console is **not** an OIDC client and cannot become one: `connect-src 'self'` forbids the browser
from fetching an identity provider's discovery document. A session is established by an identity
surface that exchanges a bearer token at `POST /internal/v1/auth/session` and receives `HttpOnly`
cookies; the console only ever reads `GET /internal/v1/session`.

Point a deployment's staff at that surface by setting the same-origin path in `index.html`:

```html
<meta name="console-signin-path" content="/demo-idp/">
```

Left empty, the signed-out screen explains the flow instead of offering a dead link. A value that is
not a same-origin absolute path is ignored.

## Invariants the tests enforce

- Every API path the console references is a route the application serves
  (`apps/api/tests/test_staff_console_contract.py`, read from `app.routes`, not from a schema —
  there is no served schema).
- No `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` or `new Function`
  anywhere. Agent-authored drafts and customer text are rendered as text nodes by construction.
- No arithmetic on any `*_vnd` field. Money is decided in `packages/domain` and only formatted here.
- No `indexedDB`, `sessionStorage`, background sync or write queue. `localStorage` holds one store
  UUID and nothing else.
- The service worker precaches exactly the assets on disk, never an API path, and has no branch that
  writes a response into a cache (`packages/evals/tests/test_staff_console_privacy.py`).
- Every screen's declared capability exists in `src/core/rbac.js`; every store-scoped screen declares
  `needsStore`.
- Role names in user-facing copy gloss through the role map in `src/core/i18n.js` (`enumLabel` →
  `Gloss (TOKEN)`) — the same dual-language rule as every other enum token, never a bare token and
  never a hand translation per screen.
- The `Trợ lý AI` screen (`src/screens/assistant.js`, capability `ASSISTANT`) calls no model: the
  brain is deterministic and server-side, the SSE stream is paced replay of an answer that was
  persisted before the stream started, and conversation history is read from the server only — the
  browser keeps no transcript.
- Every module parses (`node --check` against a `.mjs` copy, skipped where node is absent).

## What this console deliberately does not do

It never sends a customer message, never decides a price, never decides fault or a remedy, never
computes a metric, and never retries a write. Capabilities the API cannot support are not hidden —
they each have a screen under `#/gaps` naming what is missing and what blocks it.
