# Shop Mac pilot: release plan and acceptance specification

> Superseded target choice, 2026-10-22: the owner will deploy on a Windows machine. This document
> retains the Mac preparation plan and observations as history. Use
> [the Windows handoff](WINDOWS_ENGINEER_HANDOFF_2026-10-22.md) for the current deployment target,
> blocker register and engineer instructions. No Mac production installation was completed.

**Date:** 2026-10-16 (Asia/Ho_Chi_Minh). **Target:** this dedicated Mac, selected by the owner
in this chat. **State:** preparation, not permission to admit real records or enable AI.

## 1. Release decision and authority

Ship the smallest useful shop release first: authenticated staff run counter operations on this
Mac, on loopback, using deterministic prices, state transitions and ledgers. Use the existing
modular monolith, PostgreSQL, Keycloak, Docker Compose and macOS launch agents. Do not introduce a
cloud bill, a second database, a new framework or a parallel delivery controller for this pilot.

This follows the existing [Mac till runbook](runbooks/shop-till-mac.md),
[pilot specification](runbooks/shop-pilot.md), `SHOP-CUTOVER-001` and ADR-0007's private R1 scope.
The earlier FPT Linux selection remains the server option; this owner-selected Mac is the pilot
target, not a claim that server deployment has been verified.

The owner delegated routine engineering choices. This document records engineering choices and
observed facts, not their signature on legal terms, policy publications, capability manifests or
business acceptance. Authoritative published configuration and machine contracts retain priority.
Unpublished shop policies continue to refuse the affected action.

### Included in first use

- Named staff sign-in with MFA, owner/operator role separation and session revocation.
- Walk-in ticket, deterministic quote, acceptance, intake, production, payment, collection and
  completion; printed receipt and cash reconciliation. Staff verify actual payment.
- The owner's published pricebook and applicable service rules. Start with the smallest set of
  services the owner can actually fulfill; do not invent capacity or delivery promises.
- Paper fallback during outage, with a controlled reconciliation before entering delayed records.
- Encrypted recoverable backups, operating checks, and a named person who receives failures.

### Later releases

Customer records, monthly accounts, delivery, bank QR and invoice-request workflows are admitted
only when their existing publication and operational prerequisites are satisfied. Real bank and
invoice-provider automation are not required to demonstrate a cash walk-in. The owner/bookkeeper
must confirm the actual shop's accounting and invoicing obligations before choosing pilot scope.

Gemini remains a separate synthetic evaluation on port 8091. Its five successful cases do not
authorize customer-data processing. The free-project key is not transferred to the shop stack.
The local allowance is exhausted; no further model call is included in this plan's execution.
Public ingress, customer AI and automatic sends stay off, all thirteen capabilities stay
`NOT_AUTHORIZED`. Real AI and official Zalo require their existing privacy/provider/channel gates;
the shop can establish operational evidence while those remain closed.

## 2. Requirements to verification

| Requirement | User workflow | Implementation / authority | Expected result | Verification | Current state |
|---|---|---|---|---|---|
| Preserve work and demos | Continue preparation on this Mac | `codex/engineer-setup`, existing volumes | Existing work retained; 8081/8089/8091 remain usable | Baseline file hashes, branch/HEAD and HTTP checks | Baseline saved; three demos returned 200 |
| Reject false readiness | Deploy, then open counter | `staging_smoke.py`, API `/readyz` | A live API with unavailable DB fails smoke | Deterministic failure tests and actual TLS smoke | Correction in this work item; `/readyz` proves DB connectivity only |
| Refuse invalid recovery data | Restore base backup and WAL | `restore.sh`, existing restore-drill contract | Fetch/decrypt/decompress failure cannot produce prepared recovery or successful WAL restore | Corruption, upstream failure, fallback and interruption regressions | Reproduced hidden upstream failure; correction in progress |
| Fail closed on Mac prerequisites | Prepare the actual till | `preflight_shop_till.py`, Compose secret paths | Missing/unknown prerequisites block, secret values unread | Focused tests plus read-only Mac preflight | Five actual prerequisite groups currently fail |
| Verify every deployed image | Build R1 | Release supply-chain workflow, pinned Dockerfiles | Staff identity image also receives SBOM and HIGH/CRITICAL gate | Workflow contract; local exact-image scan | Keycloak omitted from loop; correction and scan in progress |
| Real identity and trusted TLS | Staff signs in twice, then signs out/revokes | Keycloak realm, BFF/CSRF and private CA | MFA each sign-in; private admin boundary; trusted app | Actual target browser/role/realm read-back and negative access tests | No R1 realm, shop material or trusted shop CA yet |
| Recover the shop's records | Lose the live instance | Age recipient, separate archive, restore drill | Recovery point <=15 min; restore <=4 h with integrity checks | Operator-timed drill on isolated restored volume | Archive/key custody and target drill not ready |
| Failures reach a person | Archive/disk/console fails | Existing launch agents and alert relay | Named operator receives one forced failure and observes recovery | Owner watches real delivery; retain sanitized evidence | Not verified on this Mac |
| First real transaction | Owner serves one customer | Existing UI/API/domain/DB | Exact money/state/history and receipt; correct closing count | Owner-observed end-to-end counter transaction | `SHOP-CUTOVER-001`, blocked until prior conditions |
| Controlled pilot | Trade for a week, review daily | Existing pilot log and instrumentation | No unresolved discrepancy; backups/checks actually run | Daily reconciliation, recovery evidence and owner review | Not started; no synthetic result substitutes |

## 3. Execution order and completion criteria

### A. Close local engineering defects

1. Repair recovery pipeline status handling for both base backups and WAL. Check each stage;
   private temporary files are removed after completion/failure/interruption. A failed newest
   backup may fall back only to a verified eligible earlier candidate.
2. Require DB readiness, the expected bounded response and no-store headers in TLS smoke. This
   remains a narrow unauthenticated probe; migration/config correctness and real business use
   require their separate checks.
3. Make Mac preflight refuse an incomplete secret-file set, an uninspectable dataless scan,
   a synced checkout or an archive on the data disk. Presence does not prove credential validity.
4. Include Keycloak in the existing exact-image supply-chain loop; fix the release-verifier
   command's missing supply-chain input without weakening its signature requirements.
5. Run focused regressions, static/contracts/context checks and full PostgreSQL suite on isolated
   data. Retain evidence and update the existing delivery queue through its controller.

**Exit:** corrected behavior supported by tests; every unresolved runtime finding named.

### B. Provision recovery before creating shop state

The Mac currently has 16 GiB RAM, eight logical CPUs, Docker running and FileVault on. The checkout
is outside iCloud. These observations establish a plausible pilot host, not capacity certification.
Only the system volume is currently mounted; no separate archive is configured.

Choose an existing separate backup drive or an approved off-host repository. Create/hold the age
recovery identity on another device/password manager; supply only its public recipient to this Mac.
Keep a second recovery copy away from the shop. An attached drive protects disk failure but does
not by itself prove protection from theft/fire; record the separate/off-site failure-domain plan.
Do not generate the private recovery identity on this Mac merely to bypass this requirement.

**Exit:** destination attached/reachable, writable, separate failure domain understood, public
recipient available, custody and operator named. No paid storage is provisioned implicitly.

### C. Prepare a separate shop stack

Use `compose.r1.yaml` + `compose.shop-local.yaml` + `compose.shop-till.yaml`. Keep shop data in its
own project/volumes; never promote or copy synthetic demo records into the shop database. Bootstrap
shop credentials only after the recipient is ready; review the generated public CA before installing
trust. Create a named owner and counter staff in real Keycloak; converge the existing realm policy
and save authenticated read-back (`REALM-POLICY-001`). Migrate, then apply and verify role grants.

The required shop port 8443 is currently occupied by the preserved original demo's TLS listener.
Do not stop that demo or improvise an origin change. Before activation, prepare a reviewed transition
that retains its 8081 demo service while freeing 8443, or a fully consistent alternative origin
across Compose, certificates, OIDC issuer/client/redirect, CORS and operating checks. This is a
cutover dependency, not a reason to disable TLS verification or reuse demo authentication.

**Exit:** actual trusted sign-in and denied-role tests, correct schema/grants, closed flags,
private listeners and frozen/scanned source/image binding. No staff admitted yet.

### D. Rehearse failure and operation on the target

Run TLS smoke, actual counter workflows with synthetic shop acceptance data, restart persistence,
and a timed isolated restore using the exact target images and archive. Prove restored business,
audit and idempotency consistency. Test the four launch agents, wake/Docker-start behavior and
one forced alert delivered to the designated person. Do not disable FileVault or macOS protections
to get an unattended restart: record the unlock/sign-in/operator procedure.

**Exit:** all target evidence passes; no HIGH/CRITICAL finding waived; restore <=4 h and recovery
point <=15 min; named owner can explain the paper fallback and recover without the engineer.

### E. Owner-observed first use and pilot

Publish only the applicable real shop configuration and notices, then the owner performs one real
transaction through receipt/payment/completion. Reconcile the money and garment count. Start with
one till and staff who completed training. Use the existing daily pilot log for a week; review
money discrepancies, missing/duplicate transitions, backup freshness and alerts each shop day.

Stop new digital intake on wrong money, missing goods/state, unauthorized access, an unrecoverable
archive or unreceived critical alert. Preserve records, switch to paper and reconcile deliberately;
never delete a transaction to make the report agree. Roll back code only to a schema-compatible
image; never reverse a deployed migration or restore over a trading database to hide a defect.

**Exit:** owner acceptance and reviewed pilot evidence. Performance/capacity claims follow actual
measurement. The longer instrumentation and AI release gates retain their own denominators.

## 4. Durable tracking and evidence

Local preparation is `PILOT-RELEASE-PREFLIGHT-001` in the existing queue. Actual host/realm/alert/
cutover items keep their own required evidence; no completed/frozen history is rewritten. The
October 15 [local application review](audit/LOCAL_READINESS_2026-10-15.md) and
[Free-tier evaluation](audit/GEMINI_FREE_TIER_VERIFICATION_2026-10-15.md) remain separately bound.

Ignored execution artifacts belong under `.demo/deployment-preparation-2026-10-16/`. Exclude private
shop configuration, recovery keys and credentials from evidence exports. The current source is
uncommitted; a reviewed immutable release snapshot and exact image mapping are still required
before activation. Planning/specification changes and green tests are not a deployment approval.
