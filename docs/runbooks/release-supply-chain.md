# Release supply-chain gate

The release workflow scans repository content locally, audits both lockfiles and dependency licenses,
builds all three production images, and emits an SPDX SBOM plus strict scan evidence for each exact
image ID. It uploads only SBOMs, normalized zero-high/zero-critical scan records, the license summary,
and the hash-bound bundle. Raw repository content, environment variables, credentials, and raw scanner
logs are not artifacts.

The checked-in gitleaks configuration extends the maintained default rules. Its global allowlist has
only two exact source-line patterns for deterministic test identifiers previously verified as false
positives; it does not exempt files, commits, secret types, or variable-name families.

The bundle expires after 24 hours. `scripts/verify_release_candidate.py` requires its path to be one of
the artifacts covered by the three-function signed release manifest. It then independently checks the
release commit, timestamps, lockfile hashes, image digests, scan schema, scan timestamps, and SBOM
hashes. A missing, stale, malformed, mismatched, unsigned, high, or critical result fails closed.

Waivers cannot convert high or critical findings into a passing scan. Lower-risk or license waivers
must identify a human security approver and expiration; the model has no waiver authority.

Rollback is code-only: remove the release workflow and verification adapters. Existing images and
deployed services are unchanged. Keep capability automation disabled while the gate is absent.
