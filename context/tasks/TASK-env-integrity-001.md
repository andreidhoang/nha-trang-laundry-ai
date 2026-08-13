# TASK-env-integrity-001 — make workspace imports independent of host file attributes

**Goal:** stop the verification environment from silently losing every workspace package, so that a
gate result means what it says.

**Domains:** `platform`

**Stable work item:** `ENV-INTEGRITY-001`

**Stage:** M4A
**Risk:** MEDIUM — no product behaviour changes, but this item is the reason any other item's
evidence can be trusted.

## Why this exists

`CLAUDE.md` carries a "known environment defect": a freshly synced `.venv` survives roughly one
pytest run, after which "the editable `.pth` files stay on disk but stop being applied and every
workspace package vanishes from `sys.path`, producing phantom failures (observed: 15 and 28
'failures' where the true count was 0). Not yet root-caused."

It is now root-caused. Three facts compose:

1. The repository lives under `~/Desktop`, which on this host is managed by the iCloud file
   provider. Within seconds of creation, every file inside the dot-directory `.venv/` is given the
   BSD `UF_HIDDEN` flag. This is not `.pth`-specific — a plain `.txt` probe written into
   `site-packages` was flagged the same way — and it is not caused by `uv`, which leaves its own
   cache copies unflagged.
2. CPython 3.12.11 hardened `site.addpackage`: before reading a `.pth` file it calls `os.lstat` and
   returns early if `st_flags & stat.UF_HIDDEN` is set, emitting only a `-v` trace line
   `Skipping hidden .pth file: ...`. Nothing is written to stderr on a normal run.
3. Every workspace package is installed as an editable whose only link to `sys.path` is a
   `_editable_impl_*.pth` file. When those are skipped, the nine workspace packages are simply
   absent, while their `dist-info` directories remain — which is exactly the "on disk but not
   applied" symptom.

The observed decay time is under ten seconds, so the previous mitigation (`rm -rf .venv && uv sync`
between suites) can only ever work by accident. Measured impact: a full `pytest` run made with the
flag set reported **37 failed, 482 passed**; the identical run with the workspace `src` directories
on `PYTHONPATH` reported **519 passed, 0 failed**. Every one of the 37 was phantom, and each was a
test that shells out to a subprocess — the parent interpreter had started while the flag was
momentarily clear, and the children had not.

## Required design

Do not fight the flag; remove the dependency on it.

- A repository-owned way to put the nine workspace `src` directories on the interpreter path that
  does not rely on `.pth` processing, applied by the documented gate invocation.
- A check that detects the hidden-`.pth` condition and reports it as itself, rather than letting it
  surface as a `ModuleNotFoundError` for an unrelated package. The check must be a no-op on Linux
  and Windows, where `UF_HIDDEN` does not exist, so CI behaviour is unchanged.
- `CLAUDE.md` and `AGENTS.md` updated: replace the incorrect "survives exactly one full pytest run"
  guidance and the `rm -rf .venv` remedy with the root cause and the actual invocation.

## Constraints

- Change no interpreter, no lockfile resolution and no package version. Upgrading `uv` may also
  avoid the interaction, but that is a toolchain decision with lock-resolution consequences and
  belongs to the owner, not to this item.
- The fix must hold for subprocesses. Tests in `packages/evals/tests` spawn `sys.executable` against
  `scripts/`; those children must import the workspace as reliably as the parent.
- Do not mutate the developer's environment as a side effect of running tests.
- No capability, decision or release state moves.

## Required tests

- the detection check reports a hidden `.pth` on Darwin and is inert elsewhere;
- a subprocess launched the way `packages/evals/tests` launches one imports every workspace package;
- the documented gate invocation succeeds with the hidden flag deliberately set.

## Done when

- the full gate battery passes with no required skips and no phantom failure;
- a second full run, made without re-syncing the environment, passes identically;
- `CLAUDE.md` no longer instructs anyone to delete `.venv` between suite runs;
- rollback is reverting the commit: the repository returns to depending on `.pth` processing, and no
  evidence, contract or delivery state was touched to make the change.
