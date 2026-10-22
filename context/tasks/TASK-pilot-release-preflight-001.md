# PILOT-RELEASE-PREFLIGHT-001 — trustworthy release preparation and target handoff

Target update, 2026-10-22: the owner selected Windows and requested a GitHub engineer handoff.
`docs/WINDOWS_ENGINEER_HANDOFF_2026-10-22.md` supersedes the Mac target. This item's local fixes,
regressions and preparation evidence remain in scope; actual Windows adaptation/acceptance is the
separate `WINDOWS-DEPLOYMENT-001` item. No target-host deployment evidence is inferred here.

Goal: close demonstrated false-success paths in release/recovery preparation, and specify the
existing dedicated-Mac pilot through real counter acceptance without inventing release evidence.

Domains: platform, evaluation_release, orders_audit.

Normative requirements: BUILD_ENGINEERING_SPEC.md invariants 5/16; ADR-0007 recovery/private R1;
docs/runbooks/shop-till-mac.md; docs/runbooks/restore-drill.md; SHOP-CUTOVER-001.
Execution specification: docs/SHOP_MAC_RELEASE_PLAN_2026-10-16.md.

The base-backup pipeline currently ignores upstream fetch/decrypt/gzip exit codes when tar succeeds.
The generated WAL command has the same status hazard. TLS smoke omits the DB-readiness route.
Mac preflight accepts an uninspectable dataless scan and checks an incomplete prerequisite set.
The release image scan omits the Keycloak service that the shop actually deploys. Correct these
with behavioral regressions and preserve existing source/user changes and all running demos.

Acceptance: targeted corrupt-stream/upstream-error/cleanup/fallback tests, truthful smoke refusal,
fail-closed Mac preflight tests, full database suite and repository static/contracts/context gates.
Read-only TLS smoke on a currently verified synthetic deployment supports the script path only;
the target R1 realm, external archive, alert delivery and actual counter transaction remain separate.

No policy publication, credential/key ceremony, paid provider work, public ingress, production
deployment, channel connection, capability authorization, signed manifest, push or PR is implied.
No new Gemini allowance exists. Keep 8081/8089/8091 demos running.
