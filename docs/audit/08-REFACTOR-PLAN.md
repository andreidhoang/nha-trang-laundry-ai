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

---

# Execution log — 2026-09-17, commit `1ee6002`

Approved and executed. Every claim below was verified by driving the running application; none is
from reading code.

| Item | State | Evidence |
|---|---|---|
| **R-01** projection + decision controls | **DONE** | `POST /decisions` → `200 APPROVED` from a real click; maker `OPS_APPROVER`, checker `OWNER_ADMIN` |
| **R-02** what an approver sees | **RESOLVED IN ENGINEERING, no owner decision needed** | see below |
| **R-03** `Tạo đơn từ báo giá này` | **DONE** | 10 actions, 17s, 0 clipboard uses, `201 POST /orders` |
| **R-04** copyable contact id | **DONE** | intake result card and every list row |
| **R-05** platform guard | **DONE** | `5 passed, 1 skipped`; `mypy` clean on Linux (223 files) |
| **R-06** phone overflow | **DONE** | 390px: **0px overflow, 0 clipped** on all 12 screens (was 82–92px on three) |
| **R-08** Vietnamese filter | **DONE** | `nguyen`→`Nguyễn`, `don`→`đơn hàng`, `ĐƠN`→`đơn`, negative case all pass |
| **R-07** bind disclosures | **BLOCKED** | needs `scripts/generate_console_disclosure_registry.py`; permission refused |
| R-09, R-10, R-11, R-13 | not started | P2/P3, unblocked, next |
| **R-12** tap targets | **WITHDRAWN** | not a defect — `10-RETRACTED.md` R-05 |
| **D-04** incident intake | **BLOCKED** | open owner decision; not guessed |

## R-02 resolved without an owner decision

The question was: returning the three hashes makes the button pressable, but approving a
`MESSAGE_DRAFT` you cannot read is still blind approval. Rather than put that to you, it is
answered by a rule the server already implements — **decide only what the console can show you**:

- `ORDER` → `_require_resolvable_resource` verifies at request time that the order exists, belongs
  to the store, and that its stored digest is the one being approved. `#/orders/:orderId` renders
  it. **Enabled**, with a link to the resource beside the buttons.
- `QUOTE_REVISION` → deliberately excluded. `#/quotes` shows a total and a hash but there is no GET
  for a single revision and no endpoint returns `quote_lines`, so the approver would never see the
  lines being priced. Blind approval in a politer font.
- `MESSAGE_DRAFT` → **disabled**, type named. Nothing stores the body, and `approvals.py:616`
  states that comparing `rendered_hash` against anything "would be theatre".

The UI boundary is now the server's own trust boundary rather than a separate judgment. If you want
`MESSAGE_DRAFT` decidable, the work is a draft-content read model — not a UI change.

## Test state at `1ee6002`

`1186 passed, 3 failed, 3 skipped`. All three failures are the stale generated disclosure registry,
and they are the framework working: `approvals.js` now issues a `POST` while still carrying a
`READ_ONLY_MODULE` claim, and its copy changed. R-07 clears all three.

## Update — commit `71f5d08`: suite green

`1189 passed, 4 skipped, 0 failed.`

R-07 is done. The three tests left red by `1ee6002` were the disclosure framework refusing a stale
honesty claim, and clearing them meant retiring the claim rather than re-pointing it:

| Was | Now | Why |
|---|---|---|
| `READ_ONLY_MODULE: 1` | **absent**, asserted absent | the screen decides; the claim died with the limitation |
| `SERVER_GATE: 11` | **12** | `APPROVALS_DECIDE` binds to `require_approval_staff` rather than landing as prose |
| bound total `15` | **16** | |
| registry total `191` | **194** | the approvals refusal narrowed rather than vanished, and a narrower true sentence costs more slots |

`verify_contracts.py`: 19 JSON + 3 YAML contracts, 669 combinatorial cases, 44 API operations, 194
disclosures. `ruff`, `ruff format`, `mypy` clean. `check_context_drift.py` passes.

Re-verified at runtime **after** the registry change, not assumed:

- counter journey — 10 actions, 17s, 0 clipboard, order created
- approval decision — `OPS_APPROVER` opens, `OWNER_ADMIN` approves, `200 APPROVED`, confirmation
  rendered above the queue and surviving the reload

### Honest note on B-03

`DESCRIPTIVE` is **169 of 194 (87%)** — unchanged in proportion. R-07 cleared the red and bound the
one new capability; it did **not** do the larger job of promoting the unbound majority, and the two
empty categories (`ABSENT_ROUTE`, `ABSENT_WRITER`) are still empty, so those two parametrised tests
still assert nothing. That is now the whole of B-03 and it remains open.

## Update — commit `2d05aca`: R-09 done, D-05 reclassified

**R-09 done.** `gatedFields()` disables every field in a region the caller may not submit, carrying
the same `data-denied` contract `gated()` uses. Verified as AUDITOR: `/exceptions` 0 of 11 fields
enabled, `/orders` 1 of 15 — the board filter, which is a read and correctly stays live. As
OWNER_ADMIN: 15/15 and 11/11, all 5 submits enabled.

**D-05 was mis-scoped in this plan and in commit `1ee6002`. Correcting it.**

`03-TRACE.md` D-05 said the manual-send envelope was blocked by the same missing projection as
F-01, and R-01 claimed to unblock both. The cause was right; the fix was not:

- `prepare` refuses any approval that is not already `APPROVED` (`manual_sends.py`)
- the queue lists `WHERE s.status = 'REQUESTED'`

The two sets are **disjoint**. The row carrying `resource_version` / `snapshot_hash` /
`rendered_hash` is never the row that may be sent. Projecting them does not make the manual-send
form fillable, and no prefill was ever wired.

**D-05 remains open.** Closing it needs a read route for approved approvals — its own item, with
its own store-scope and status reasoning. It is not a UI change.

### And it falsified a live disclosure

The manual-send panel told operators those four values *"không đọc lại được từ bất kỳ đường nào của
bảng điều khiển: danh sách duyệt chỉ trả phong bì_hash"*. R-01 made half of that false. Nothing
caught it — the sentence is one of the 169 `DESCRIPTIVE` entries with no machine binding, so
`verify_contracts.py` passed green over a false statement on a compliance surface.

This is **B-03 demonstrated rather than argued**, inside the branch that caused it, and it raises
R-07's remaining work from a tidiness item to a correctness one. The sentence is corrected by hand
in `2d05aca`; the class of failure is not.

### Withdrawn from this plan

- **R-12** — not a defect (`10-RETRACTED.md` R-05).
- **R-11, R-13** — still valid, deliberately not in this branch. R-11 needs a dependency refactor
  across every store-scoped write route; R-13 adds a column via migration and belongs sequenced
  with the delivery queue. Bundling either into a 33-file branch closing two P0s would trade a
  reviewable change for an unreviewable one.

### CI reality

`quality.yml` and `release-supply-chain.yml` are both `active` with **zero runs, ever**. GitHub
Actions has never executed on this repository, so no check gates any PR here. Every step of
`quality.yml` was run locally instead, including `report_delivery_status.py` and the Node plugin
build and tests. That is a gap worth its own item: this repository's quality bar is currently
enforced by whoever remembers to run it.
