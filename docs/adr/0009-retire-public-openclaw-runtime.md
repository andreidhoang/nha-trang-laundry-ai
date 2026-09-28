# ADR-0009: Retire the public OpenClaw runtime now, not after runtime parity

**Status:** accepted — owner decision, 2026-09-28 ("yes remove and execute the best for the app";
"execute option 1", which approved the registry v2 rewrite and the release-blocker change below)

**Date:** 2026-09-28
**Amends:** ADR-0004 §3 (dependency graph) and §5 (`OPENCLAW-RETIRE-001`), and the preconditions of
`context/tasks/TASK-openclaw-retire-001.md`. ADR-0004 §1 (frozen evidence track) and §2 (`AGENT-002`)
are unchanged. ADR-0002 and ADR-0003 trust-boundary, business-authority and provider-data decisions
are unchanged.

## Context

ADR-0004 kept `OPENCLAW-RETIRE-001` behind `RUNTIME-PARITY-001`, which sits behind `AGENT-002`, which
is blocked on `DEC-006` (provider data residency). That ordering was inherited from ADR-0003, where
OpenClaw was the *rollback runtime*: you do not remove a rollback before its replacement is proven.

ADR-0004 itself ended that role: *"If the custom adapter fails its absolute P0 bar, OpenClaw is **not**
the fallback. The fallback is deterministic degraded mode plus a new ADR."* The ordering outlived its
reason. Measured on `main` at `a43ffac`:

- **Production does not run OpenClaw.** The worker constructs `BoundedResponsesRuntime` directly
  (`apps/worker/.../pipeline.py`); there is no runtime selector. `DisabledOpenClawProviderRuntime`
  was constructed only by tests; `bridge_api.py`, the loopback server the OpenClaw plugin called,
  was imported only by tests. No compose file, deploy file or Dockerfile names OpenClaw.
- **It cost something every day.** 39.6 MB of tracked tarballs; three CI jobs and a Node toolchain
  on every pull request; and the `release-supply-chain / supply-chain` check red on every `main`
  run since at least run 13, because `npm audit` finds high-severity advisories in the frozen
  plugin tree — a red gate everyone learns to ignore, which is worse than no gate.
- **It shaped live contracts.** The runtime registry schema *required* an `openclaw:` block and
  `agent_runtime_id: openclaw`; production hashed that file into every run's execution pins. Three
  of the ten release blockers described OpenClaw components.

## Decision

1. **`OPENCLAW-RETIRE-001` no longer depends on `RUNTIME-PARITY-001`.** It depends on nothing
   pending. `RUNTIME-PARITY-001` keeps its own absolute P0 bar (ADR-0004 §4); retirement never
   compared against it and no longer waits for it.
2. **The runtime registry moves to schema v2** (`runtime/model-registry-v2.yaml`). It describes the
   runtime that runs — `agent_runtime_id: nha-trang-responses-runtime`, the worker image — and refuses
   a v1 (OpenClaw) file at load, which is how a stale or implicit OpenClaw route fails closed.
3. **Release blockers go from ten to eight, and none of the eight is weaker:**

   | v1 blocker | Disposition | Why no protection is lost |
   |---|---|---|
   | `OPENCLAW_STORE_FALSE_ROUTE_NOT_VERIFIED` | removed | It existed because OpenClaw forced `store: true` unless overridden. `ResponsesRequest.store` is `Literal[False]`, so this runtime cannot ask the provider to keep a response (tested). Whether the provider honours it remains `EFFECTIVE_PROVIDER_REQUEST_NOT_VERIFIED`. |
   | `SANDBOX_IMAGE_NOT_VERIFIED` | removed | The OpenClaw tool sandbox container. This runtime has no sandbox: its tools are the ten fixed facade operations. |
   | `PUBLIC_CELL_RUNTIME_IMAGE_NOT_VERIFIED` | re-pointed | Now `AGENT_RUNTIME_IMAGE_NOT_VERIFIED`, bound to `nha-trang-laundry-worker`, the image the runtime runs in. |

   The other seven are unchanged. Every capability stays `NOT_AUTHORIZED`.
4. **Supply-chain evidence moves to schema v2.** v1 demanded "at least two lockfiles", the number of
   dependency trees the repository happened to have; it can never be met again and could never
   notice a third tree added without an audit. v2 states the rule the number stood for: the audited
   lockfiles are *exactly* the lockfiles present in the repository. That is stricter than v1.
5. **Provider data evidence moves to schema v2**, with the same review and every status unchanged;
   only the OpenClaw scope field, source, observations and override row are gone.
6. **History is kept, and kept checkable.** The three frozen items, every file under `evidence/`,
   ADR-0002–0004 and the OpenClaw schemas are untouched. The repackage manifests and plugin
   inventory are preserved byte-for-byte under `evidence/openclaw-retirement/`. Every retired file is
   listed with its SHA-256 and git blob in `evidence/openclaw-retirement/retired-artifacts-v1.yaml`,
   pinned to the last commit that holds it, and a test re-verifies each one. The local synthetic
   evidence is re-derived as v5 (EVIDENCE-REPIN-001 pattern); v4 is retained.
7. **Out of scope and untouched:** `.openclaw/`, the owner-side delivery-automation state (ADR-0004 §5).

## Consequences

- One reasoning runtime, one image, one dependency tree. The supply-chain gate can be green, and a
  red one means something again.
- Rollback of this decision is `git revert`: every byte is in history and named in the record.
  Reinstating OpenClaw as a *runtime* would reopen every supply-chain obligation ADR-0004 closed and
  needs its own ADR, exactly as ADR-0003 already required for any agent framework.
- `RUNTIME-PARITY-001`, `AGENT-002` and `DEC-006` are unchanged and still gate any provider-backed
  release.
