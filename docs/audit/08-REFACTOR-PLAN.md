# 08 — Refactor plan

**Nothing in this plan has been executed.** Per §1.5 and §11 of the directive, audit and mutation
are separate; the working tree is unchanged apart from these `docs/audit/*` files. This plan stops
here for approval.

Ordering: P0 correctness/blocks-daily-work → P1 rework → P1 AI/human ambiguity → P2 friction →
P2 subtraction → P3 polish.

---

## R-01 · Project the three decision fields onto `ApprovalResponse` · **P0**

- **Findings:** F-01, D-05 · **Spec:** §8 A2 gate; `gaps.js:298`
- **Files:** `apps/api/src/nha_trang_laundry_api/main.py` (`ApprovalResponse`, `_approval_response`),
  `apps/api/src/nha_trang_laundry_api/operations.py` (list projection),
  `packages/db/src/nha_trang_laundry_db/approvals.py` (SELECT column list),
  `specs/contracts/internal-api-v1.openapi.yaml`, `apps/web/src/screens/approvals.js`,
  `apps/web/src/screens/exceptions.js`, `specs/contracts/console-disclosures-v1.yaml`
- **Exact change:** add `resource_version: int`, `snapshot_hash: str`, `rendered_hash: str` to
  `ApprovalResponse`; read the three existing NOT NULL columns; enable the `Duyệt`/`Từ chối`
  controls; prefill the `/exceptions` manual-send envelope form from the queue row; delete the
  now-false disclosure (S-01) and the `RESPONSE_SHAPE` registry entry that asserts the field is
  absent.
- **Why:** unblocks the entire human-approved customer-communication path. Three columns already
  exist and are already selected into the stored envelope; nothing is computed, migrated or decided.
- **Risk:** low mechanically. **One genuine risk, escalated as F-01b:** enabling the button lets a
  human approve a `MESSAGE_DRAFT` they still cannot read. See R-02.
- **Rollback:** revert; the fields are additive and no client requires them.
- **Verify:** `uv run python scripts/verify_contracts.py`; the disclosure contract test at
  `test_console_disclosure_contract.py` must now *fail* on the stale entry and pass after it is
  removed — that failure is the feature; re-run the seeded-approval journey and observe a `200` from
  `POST /approvals/{id}/decisions`.
- **Owner:** AI-executable, **except** the F-01b decision.

## R-02 · Decide what an approver sees before approving · **P0 · HUMAN-REQUIRED**

- **Finding:** F-01b · Blocks the *completion* of R-01, not its mechanics.
- Approving on a hash alone is `duyệt mù`. The queue must either render the draft body or link to
  it. That is a policy decision about what a `MESSAGE_DRAFT` exposes to an approver and who may see
  it. **§14 escalation — I will not pick a winner.**

## R-03 · `Tạo đơn từ báo giá này` on the accepted-quote card · **P0**

- **Findings:** D-01, U-01, U-09, S-04
- **Files:** `apps/web/src/screens/quotes.js`, `apps/web/src/screens/orders.js`
- **Exact change:** on a revision with `finality === "APPROVED_EXACT"`, render a
  `Tạo đơn từ báo giá này` action carrying `quote_id`, `revision`, `snapshot_hash` **and the
  `contact_binding_id` already held in the selected order-request banner**, into the `/orders`
  create form (or submit directly). Delete the false hint *"Chép từ màn hình Tiếp nhận"*. Keep the
  manual form behind a `nâng cao` disclosure, as `/quotes` already does for manual request entry.
- **Why:** makes the counter journey completable. This is the single change that turns the product
  from a demo into something a shift can run on.
- **Risk:** low — no new route, no new server behaviour, `POST /orders` already verified working
  (`201`, order `ccc9deb4…fb18`).
- **Rollback:** revert; the manual form is untouched underneath.
- **Verify:** regression test that fails before / passes after — a Playwright journey asserting
  intake → quote → accept → order in ≤10 clicks with zero clipboard use.
- **Owner:** AI-executable.

## R-04 · Expose the contact id as a copyable value · **P0 (companion to R-03)**

- **Finding:** D-01 · **Files:** `apps/web/src/screens/orderRequests.js:91,145`
- Replace `shortId(contact_binding_id)` with `copyable({ value, display: shortId(value) })` on the
  result card and each list row. Independent of R-03 and worth doing anyway: it restores the paste
  path `copyable()`'s own docstring (`components.js:909`) says is the interim design.
- **Verify:** DOM probe asserting a full UUID is reachable from `/order-requests` after submit.
- **Owner:** AI-executable.

## R-05 · Fix the platform guard on the workspace-env tests · **P1**

- **Findings:** B-01, B-02 · **Files:** `packages/evals/tests/test_workspace_env.py:86,105`,
  `scripts/workspace_env.py:96,117`
- Guard on `hasattr(os, "chflags")` rather than `hasattr(stat, "UF_HIDDEN")` — `stat.UF_HIDDEN` is
  defined on every platform, `os.chflags` is the BSD-only symbol. Add `# type: ignore[attr-defined]`
  with a comment at the two `st_flags` reads, or gate them behind `sys.platform == "darwin"`.
- **Why:** `uv run pytest` and `uv run mypy apps packages` — both documented in `CLAUDE.md` — cannot
  pass on Linux today. A documented command that cannot pass trains people to ignore it.
- **Verify:** clean-clone run on Linux green three consecutive times (§13.9).
- **Owner:** AI-executable.

## R-06 · Wrap long Vietnamese labels; stop the phone overflow · **P1**

- **Findings:** U-04, G-03 · **Files:** `apps/web/styles/components.css`, `layout.css`
- `overflow-wrap: anywhere` on `.copyable__text`; allow `.form__actions` to wrap; let
  `.field--span` shrink below its content width. **Do not shorten the Vietnamese labels** — the long
  forms carry the meaning.
- **Verify:** assert `documentElement.scrollWidth === clientWidth` at 390px on all 12 screens.
- **Owner:** AI-executable.

## R-07 · Bind the unbound disclosures · **P1**

- **Finding:** B-03 · **Files:** `specs/contracts/console-disclosures-v1.yaml`,
  `apps/api/tests/test_console_disclosure_contract.py`
- Two parametrised contract tests assert nothing because `ABSENT_ROUTE` and `ABSENT_WRITER` have
  zero entries; 166 of 191 disclosures are unfalsifiable `DESCRIPTIVE` prose. Register the absent
  routes and writers the console already claims (`gaps.js` names them concretely, which is most of
  the work), and make an empty parameter set a **failure** rather than a skip.
- **Why:** `CLAUDE.md` — *"a green test run is not completion evidence."* This is that rule applied
  to the suite that enforces the rule.
- **Owner:** AI-executable; category assignment for ambiguous entries needs a reviewer.

## R-08 · Vietnamese-aware filter helper · **P1**

- **Findings:** G-02, S-03 · **Files:** `core/format.js` + four call sites
- One `matchesFilter()` folding NFD marks **and** `đ/Đ` explicitly (NFD alone misses them).
- **Verify:** unit test — `nguyen` matches `Nguyễn`, `don` matches `đơn`, and the reverse.
- **Owner:** AI-executable.

## R-09 · Disable inputs on forms whose submit is denied · **P2**

- **Finding:** D-03 · **Files:** `core/rbac.js` (`gated()`), the five screens with gated forms.
- **Owner:** AI-executable.

## R-10 · Vietnamese microcopy for `422` validation bodies · **P2**

- **Findings:** B-05, G-06 · **Files:** `core/errors.js`
- Map Pydantic `{type, loc, msg}` to a Vietnamese sentence naming the field via `enumLabel`; keep
  the raw body behind the existing details disclosure for support.
- **Owner:** AI-executable; wording needs native CSKH sign-off (§12).

## R-11 · Order 403 before 422 on store-scoped writes · **P3**

- **Finding:** B-04 · Resolve store membership in a dependency so authorization precedes body
  validation. No data leaks today; this is boundary consistency.
- **Owner:** AI-executable.

## R-12 · Tap targets ≥ 40px on disclosure toggles · **P3**

- **Finding:** U-08 · **Files:** `apps/web/styles/base.css`
- **Owner:** AI-executable.

## R-13 · Add a model-identity column to `assistant_turns` · **P2, do before the first provider call**

- **Finding:** B-06 · Forward migration adding `model_id` / `model_version`, nullable, unused until
  a provider is authorized. Free now, a migration on live data later.
- **Owner:** AI-executable, but it touches `packages/db/migrations` — sequence it with the delivery
  queue rather than folding it into a UX slice.

---

## Not in this plan, deliberately

- **D-04 (incidents)** — blocked on `docs/DECISION_REQUEST_INCIDENT_INTAKE_2026-09.md`. An
  engineering guess at `contact_scope_hash` semantics would be exactly the "guess to make a flow
  complete" that `CLAUDE.md` forbids. **§14 escalation.**
- **F-02 / S-05 (renaming "Trợ lý AI", restructuring `GIÁM SÁT AI`)** — product naming and
  information architecture. Recommended, not executed.
- **The `compose.production.yaml` TLS defect** confessed at `compose.demo.yaml:126-139` — the
  production `tls` service is attached only to an `internal: true` network, so the host listener on
  `127.0.0.1:8443` that `docs/runbooks/private-staging.md` promises does not exist and nothing
  errors. Out of this audit's scope (deployment topology), but it invalidates STAGING-001's evidence
  and should be its own queue item.
