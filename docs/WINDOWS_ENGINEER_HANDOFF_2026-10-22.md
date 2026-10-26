# Windows deployment handoff — October 22, 2026

**Start here.** The owner changed the deployment target from this Mac to a **Windows machine**
on October 22. The Mac remains the engineering/demo environment. Windows deployment is **blocked,
not performed or verified**. This handoff supersedes the target choice in the October 16 Mac plan;
it preserves that plan and its evidence as history.

Repository: <https://github.com/andreidhoang/nha-trang-laundry-ai> (public when checked October 22).
Application/handoff branch: **`codex/engineer-setup`**, based on `e97e8bc`. Do not deploy `main`
assuming it contains these changes; use the reviewed branch/commit and retain its exact SHA.
The separate sales-agent drafts in the owner's original checkout are excluded at their request.

## Current verification refresh — October 26, 2026

Read [the daily workflow review](audit/DAILY_WORKFLOW_REVIEW_2026-10-26.md) and the portable
[`summary.json`](../evidence/handoff/2026-10-26/summary.json) and
[`workflow-recordings.json`](../evidence/handoff/2026-10-26/workflow-recordings.json) before reusing
the dated results below. The clean full snapshot passed **5,197 tests, five skipped**. Later affected
rechecks passed **12**, then **127 with four skips**; the unique test-ID union is **5,201 passed,
five skipped**, not another single full run on the final tree. Separate real-role checks passed 63.
The final daily browser walks passed **82 each on desktop and phone**. Core conformance passed
**872/871** on the earlier frontend image; the later affected recovery checks passed **104**.
Separate real-API QR screenshot pixels passed **28 offline decode checks**; this is not bank-transfer
acceptance. Source/image bindings and availability are recorded in the portable evidence.

These are local synthetic Mac/Linux-container results. The Windows target remains unverified;
repeat clean checks and acceptance on its exact reviewed source/images. Start with the actual host
inventory and Linux runtime choice, then the target scheduler/preflight, trusted MFA browser and
private exposure, exact-image scans, separate encrypted archive/off-host key custody and timed
restore, observed alerts and owner counter/cash acceptance in §5. No production activation,
authorization/capability change or additional Gemini allowance follows from this refresh. All 13
capabilities remain `NOT_AUTHORIZED`; the existing 16-request allowance is exhausted.

## 1. Product and release scope

R1 is a private staff console for laundry operations. Deterministic Python/PostgreSQL code controls
price, permissions, SLA, state, money and audit. Named staff with MFA operate the counter. The
smallest initial real workflow is a walk-in through quote, acceptance, intake, production, payment,
collection, receipt and cash reconciliation. Publish only the actual owner's applicable shop rules.

Customer AI, public channels and automatic sends stay disabled. All thirteen capabilities remain
`NOT_AUTHORIZED`. The optional Gemini classifier is synthetic-only and off by default. Its
October 15 five-case live check passed on a dedicated Free-tier project; **all sixteen authorized
generation attempts, including diagnosis/failed runs, are exhausted**. No new allowance or
production data-use approval accompanies this handoff. Obtain new credentials through the approved
secret process; no key is supplied in GitHub or copied from this Mac.

The owner authorized committing/pushing application and deployment work, not production activation.
This branch includes the accumulated source fixes and tests, contracts, lockfile, recovery changes,
container/CI hardening, specification notes and the Windows blocker register. Existing legal,
policy-publication, signer and release authorities retain their boundaries.

## 2. Read order and current state

1. `AGENTS.md`, `CLAUDE.md`, `BUILD_ENGINEERING_SPEC.md` and `context/INVARIANTS.md`.
2. This handoff, then `docs/CODEX_CROSS_MACHINE_HANDOFF.md` for generic setup.
3. `context/PROJECT_CONTINUATION.md`, `delivery/WORK_QUEUE.yaml`, `delivery/LOOP_STATE.yaml` and
   `delivery/CAPABILITY_STATUS.yaml`; machine state outranks narrative status.
4. `docs/CORE_BUSINESS_WORKFLOWS_V1.md`, `docs/CORE_OPERATIONS_COMPLETION_SPEC_V1.md`, applicable
   approved Vietnamese specifications and `specs/contracts/`.
5. `docs/audit/DAILY_WORKFLOW_REVIEW_2026-10-26.md` and `evidence/handoff/2026-10-26/`, then
   `docs/audit/LOCAL_READINESS_2026-10-15.md`,
   `docs/audit/GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md` and the portable
   `evidence/handoff/2026-10-22/verification-summary.json`.
6. `context/tasks/TASK-pilot-release-preflight-001.md` and
   `context/tasks/TASK-windows-deployment-001.md` before continuing their items.

Current queue handoff, October 26: `PILOT-RELEASE-PREFLIGHT-001` is `COMPLETE`, recorded through the
canonical controller with [its evidence](../evidence/delivery-loop/PILOT-RELEASE-PREFLIGHT-001.yaml).
`WINDOWS-DEPLOYMENT-001` is `BLOCKED` because the actual target is unavailable/unverified; no
independent work item is selected. Read the October 26 evidence, obtain the target and rerun clean
verification there. Unblock the Windows item through a fresh generation/CAS only when its
prerequisite evidence exists, then retain all remaining target acceptance conditions in §5.

The older production-readiness assessment and Mac plan contain dated observations, not current
Windows acceptance. No business schema migration was added by this setup/review work: migrations
still span `0001`–`0072`, with `0045`/`0069` intentionally absent (70 files).

## 3. Windows runtime decision

Keep the application in **Linux containers**, preserving the specified Linux production environment
and the existing R1 network/identity/database boundaries. Native Windows containers are not supplied.
Inventory Windows edition/build, CPU architecture, virtualization, RAM, disk, Docker/Compose and
operator/startup requirements before selecting the execution layer.

For a supported Windows desktop, WSL2 plus Docker Desktop is the proposed **engineering/rehearsal**
path. It is not yet proven suitable for unattended shop operation. Windows Server needs a reviewed
Linux VM/engine or another supported deployment arrangement; Docker explicitly does not support
Docker Desktop on Windows Server. Check current requirements and licensing before installation;
this handoff does not accept commercial terms or authorize paid services.

Primary references checked October 22:
[Docker Windows installation](https://docs.docker.com/desktop/setup/install/windows-install/),
[Docker WSL2 backend](https://docs.docker.com/desktop/features/wsl/),
[Microsoft filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems), and
[Microsoft systemd in WSL](https://learn.microsoft.com/en-us/windows/wsl/systemd/).
Systemd support alone does not prove that Windows, WSL and Docker start or recover together.

## 4. Fresh engineering checkout — synthetic data only

In the selected Linux environment, use a local Linux filesystem path such as `~/laundry`, outside
OneDrive and `/mnt/c`. Retain `.gitattributes` (LF source/shell files, CRLF only `.bat`/`.cmd`).
Install Git, `uv`, Docker with Compose v2 and Node **24** for complete console tests. The Linux test
environment also needs `age`, `age-keygen`, `gzip` and `tar` for real recovery regressions. Then:

```bash
git clone --branch codex/engineer-setup --single-branch \
  https://github.com/andreidhoang/nha-trang-laundry-ai.git ~/laundry
cd ~/laundry
git switch -c codex/windows-deployment
git rev-parse HEAD
git config core.autocrlf false
uv sync --frozen --all-packages --all-groups
uv run python scripts/check_context_drift.py
uv run python scripts/run_delivery_loop.py --format controller-json
uv run python scripts/report_delivery_status.py
uv run python scripts/start_demo.py --preflight-only
```

Do not reset an existing checkout with uncommitted work. Fetch, inspect its status and use a separate
branch/worktree. Do not transplant the Mac `.venv`, absolute paths, TLS trust, `.shop`, `.demo`,
environment, API key or database volumes.

When the synthetic prerequisites pass, `uv run python scripts/start_demo.py` prepares a new local
demo. Follow `docs/runbooks/demo-stack.md`; verify the printed address in the actual Windows browser.
The demo identity provider can mint synthetic identities and must never authenticate a real shop.

## 5. Deployment blockers and required evidence

| Blocker | Required correction / observation | Completion evidence |
|---|---|---|
| Actual Windows host unknown/unverified | Record supported OS/build, architecture, resources, virtualization and chosen Linux layer | Sanitized target inventory; approved topology |
| No Windows operating scheduler | Replace/adapt Mac `launchctl` installation; handle startup order, missed runs, overlap, wake/reboot and Docker/WSL shutdown | Forced failure/recovery and restart logs on the target; checks and backups execute at declared intervals |
| Mac-only preflight | Implement target checks for filesystem, archive mounts, CA trust, hostname, permissions and listener ownership | Negative tests and actual target output; do not report the BSD `find`/macOS `security` script as a Windows pass |
| Host/browser trust and exposure | Consistent origin, CA, OIDC issuer/redirect, CORS and firewall; prove host and guest forwarding boundaries | Trusted Windows-browser MFA sign-in plus outside-host connection attempts; DB/IdP admin never publicly reachable |
| Target image security | Build every deployed service for the target architecture, including Keycloak; generate exact-image SBOM/provenance and fresh scans | No HIGH/CRITICAL findings waived; old arm64 image results do not certify Windows amd64 images |
| Recovery destination/key custody | Stable external/off-host archive visible to the Linux runtime; missing drive refuses; private recovery identity kept off the production host | Encryption/read-back, custody record and separate failure-domain evidence |
| Timed actual restore | Recover the target's schema/business/audit/idempotency data onto an isolated recovery instance | RPO <=15 minutes and RTO <=4 hours, including key-holder handover; no destructive restore over the trading DB |
| Actual identity policy | Named Keycloak staff, mandatory MFA, DB role/store membership, policy convergence and read-back | `REALM-POLICY-001`, real repeated sign-in, denial/revocation tests and grant verification |
| Alert delivery | Backups/disk/readiness failures reach the named person through the approved alert channel | Operator watches one forced alert arrive and observes recovery; mocks cannot close this |
| Owner cutover | Publish actual shop settings/notices and train staff; perform one owner-observed counter transaction and daily reconciliation | `SHOP-CUTOVER-001`, exact receipt/payment/state and paper fallback acceptance |

`deploy/shop-till/install.sh` and `scripts/preflight_shop_till.py` remain **Mac-specific**. Running
the app in WSL does not make those host operations portable. Keep their existing behavior intact
and implement a separately tested Windows adaptation. Preserve the internal DB network; never
solve a scheduler/network problem by exposing PostgreSQL or disabling certificate checks.

## 6. What has and has not been verified

- **October 15 application source:** 5,127 full Python/PostgreSQL tests passed, five documented
  skips, zero failures; 63 separate real-role tests passed. Broad browser and recording evidence
  has distinct source/image bindings in the dated report.
- **Gemini:** five live synthetic intent cases, 74 browser assertions, 19 persistence/invariant
  assertions; restart preservation and desktop/phone sign-in also passed. Model facts/actions
  remain deterministic. This is not the production customer-agent evaluation.
- **October 16 preparation:** 129 focused tests passed. Real encrypted base/WAL recovery on one
  isolated PostgreSQL 16.15 Docker host promoted both latest and a time target, preserving/excluding
  the expected marker rows. This had no full application schema, off-host drill or Windows evidence.
- **Keycloak:** the pinned arm64 image built; no completed vulnerability scan was retained.
  Missing Java DB caused failed attempts. An empty findings file does **not** mean zero findings.
  The CI image loop now includes Keycloak so that omission no longer silently passes.
- **Interrupted checks:** the October 16 full-run helper failed before tests; its TLS smoke used
  an inadequate trust input and failed certificate validation. Neither is reported as a pass.
  The October 22 full attempt finished with **5,178 passed, one failed and five skipped**. Its sole
  failure was the context test still expecting 203 work items after the queue grew to 205. The
  explicit count guard was corrected; all six affected context tests and 63 real-role tests then
  passed. Application/deployment code did not change after the full attempt. A clean full run
  after that test-only correction had **not** been performed at the October 22 handoff. The
  October 26 refresh above records later local verification separately. Exact dated outcomes,
  original failure and source hashes remain in the October 22 portable summary.

No Windows host, fresh Windows browser workflow, Windows scheduler or production installation was
observed. No billing was enabled, new generation allowance granted, public capability activated or
real customer data admitted by this handoff.

## 7. Verify, freeze, then deploy

After reading the October 26 evidence, rerun these checks in the actual Windows target's selected
Linux environment: lint/format, `uv run mypy apps packages`, contracts, context and delivery status.
Run `CI=true uv run pytest --require-postgres-integration` with `DATABASE_URL` pointing at a fresh isolated
test DB, never a demo/shop DB. Node 24 and actual PostgreSQL must be available; bare pytest can
silently skip database integrations. `CI=true` also makes missing recovery tools fail rather than
skip; the PostgreSQL-required flag alone does not guard those tool-dependent tests. Run the separate
API/worker role tests with their own URLs.
The generic `scripts/staging_smoke.py` requires TLS and `/readyz`; readiness proves DB connectivity,
not migrations, correct pricebook or completed business workflows.

Build a reviewed immutable candidate from the recorded Git SHA using the repository Dockerfiles.
Apply migrations, then role grants and `verify_database_grants.py`. Provision a separate empty shop
database and real Keycloak. Rehearse with synthetic acceptance data before admitting real records.
Do not merge this branch or mark a launch/capability gate passed merely because it was pushed.
Preserve the existing queue/controller; advance only with its declared evidence and generation CAS.

A branch push alone does not establish hosted CI: the quality workflow runs for pull requests,
pushes to `main`, or an explicit manual dispatch. This handoff does not merge the branch or claim
a hosted run occurred; obtain passing exact-commit hosted evidence before release.

## 8. Evidence transfer and secrets

GitHub includes source, tests, specifications, a sanitized verification summary, selected non-secret
recovery results and an artifact hash inventory. Large videos, raw logs, images, caches, credentials,
recovery identities and volumes remain outside Git. Some dated audit links point into ignored
`.demo/` artifacts on the Mac and will not resolve in a fresh clone; that is explicitly local
evidence, not a missing source dependency. Use the committed inventory to request an allowlisted
transfer or rerun the workflow. Never zip/copy `.demo` or `.shop` wholesale.

The Mac's demos on ports 8081, 8089 and 8091 are retained. Their URLs and absolute paths are useful
only on that Mac, and their synthetic database must not become the Windows production ledger.
