# TASK-clone-and-run-001 — one command from a fresh clone to a running application

**Goal:** a person who has just cloned this repository on their own machine reaches a working staff
console with one command, and a gate proves that claim still holds.

**Domains:** `platform`

**Stable work item:** `CLONE-AND-RUN-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW to the running shop — it adds an entry point and changes no product behaviour. The
risk it carries is a documentation risk: a bring-up claim that nothing executes is a claim that
rots, and this repository has already been bitten by that twice (`RUNBOOK-TRUTH-001`, `-002`).

## Why this exists

The owner's goal, stated 2026-09-18: *push the project on GitHub, and any machine can pull the code
and start running it for daily business operation.*

Most of that already works and it is worth being precise about how much. `generate_demo_material.py`
mints the CA, the certificates, the identity-provider key and every database password **fresh on
each run into a gitignored directory**, so there is no credential in the repository and a clone is
safe to run. `bootstrap_shop_local.py` does the same for a real shop and refuses to overwrite, so a
re-run cannot rotate passwords out from under a trading till. `HASH-KEYING-001` added a deployment
key and generated it in both, rather than asking a human to invent one, for exactly this reason.

What is missing is not capability. It is that the path is eight commands in a runbook, and **nothing
executes those commands on a clean checkout**, so the first person to find a broken step is a person
trying to open a shop.

## The distinction this item must not blur

A clone is safe to run as **its own** shop. It is not a second copy of an existing one.

`docs/CODEX_CROSS_MACHINE_HANDOFF.md` §2 already records why: two machines each running their own
database are two competing sources of truth about money, and an order taken on one is invisible to
the other. Several devices serving **one** shop is a different topology and is already built —
`compose.r1.yaml` puts the database and API on one machine and the console in a browser on the rest.

So this item delivers one command for each, and the entry point must say which one it is starting.
It must be impossible to reach a second live ledger by running the wrong script by accident.

## Required design

**`scripts/start_demo.py` — synthetic, disposable, safe on any machine.** One command that runs the
existing sequence in order and stops at the first thing that is wrong, naming it:

1. preflight — `uv`, Docker, Node, an unsynced working directory, the ports it needs;
2. `uv sync --all-packages --all-groups`;
3. `generate_demo_material.py`;
4. the Docker network, created only if absent;
5. the compose bring-up;
6. `verify_demo_stack.py`;
7. print the console URL and where the demo credentials were written.

Idempotent: a second run on a healthy stack reports healthy and changes nothing.

**Preflight failures are the product here.** "Docker is not running" and "this checkout is inside
iCloud Drive, which will corrupt `.venv` — see the `ENV-INTEGRITY-001` note in `CLAUDE.md`" are the
two most likely first experiences, and each must be one sentence a person can act on. A traceback is
a failure of this item, not a diagnostic.

**A real shop keeps its own entry point.** `bootstrap_shop_local.py` is not wrapped into this and is
not made one-command: it requires an `age` public key generated on the owner's own device, and
`DEC-026` deliberately keeps that off the machine that writes the archive. One command must not
become a way to skip a decision.

## Constraints

- No credential is ever written to a tracked file. The generators already write to gitignored
  directories; this item adds no new secret and no new place to put one.
- The script performs no deployment and selects no vendor. It starts a synthetic stack on the
  machine it is run on.
- The runbook and the script must not be able to drift: one is generated from the other, or a test
  asserts they name the same steps in the same order.
- Docker is not available to every environment this repository is worked on in. The script must
  degrade to a clear refusal, and the test must not require Docker to run.

## Required tests

- the step list in `scripts/start_demo.py` and the sequence in `docs/runbooks/demo-stack.md` agree,
  so neither can drift silently;
- preflight reports a missing prerequisite by name and exits non-zero, without a traceback;
- preflight refuses a checkout inside a known synced directory, naming the defect it prevents;
- the script never writes into the working tree outside the gitignored material directories;
- `git status` is clean after a run, so a bring-up cannot leave a diff behind.

## Done when

- a reader following `README.md` reaches a running console with one command;
- `docs/CODEX_CROSS_MACHINE_HANDOFF.md` §3 points at that command rather than a list;
- the two topologies are distinguishable at the entry point, and the synthetic one cannot be
  mistaken for a shop;
- the full gate battery passes with no required skips;
- rollback is deleting one script and one runbook section; no product code changes.
