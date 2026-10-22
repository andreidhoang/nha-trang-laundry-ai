# Portable engineer-handoff evidence

Start with `docs/WINDOWS_ENGINEER_HANDOFF_2026-10-22.md`.

- `verification-summary.json` distinguishes passing, failed, interrupted/unavailable and unrun
  verification. It does not authorize deployment, provider use or public capabilities.
- `recovery-replay-result.json` and `source-bindings.json` are non-secret copies of the
  isolated synthetic PostgreSQL latest/time-target replay result and seven source hashes.
- `local-artifact-inventory.json` records hashes of selected evidence retained on the Mac. Paths
  marked `LOCAL_ONLY_NOT_IN_GIT` are deliberately absent from a fresh clone. Ask for an allowlisted
  transfer or rerun the documented checks; do not copy `.demo/` or `.shop/` wholesale.

Videos, screenshots, raw logs, caches, image archives and database volumes are not committed here.
Private runtime configuration, provider credentials and recovery identities are never part of a
handoff bundle. Older audit links into `.demo/` describe local evidence availability only.

Source and target-architecture differences matter. A Mac/arm64 test or scan does not establish
Windows/amd64 runtime correctness. The actual Windows scheduler, trust, backup and cutover conditions
are unresolved until the assigned engineer records target-host evidence.
