# TASK-workflow-conformance-001 — write down the workflows, then prove the console runs them

**Goal:** state the shop's core operating workflows explicitly, with citations to the specs and the
deterministic code that implement them, then drive every one of them through the staff console in a
real browser against a real API and a real database — control by control — and fix whatever fails
to behave as the workflow says it must.

**Domains:** `platform`, `orders_audit`, `business_truth`

**Stable work item:** `WORKFLOW-CONFORMANCE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM until findings are known. The audit itself is read-only; the fixes it produces are
scoped by what it finds and are assessed individually.

---

## 1. Why this exists

Two audit rounds have already been run against this console, and both were organised by *layer* —
refusal mapping, money parsing, offline behaviour, the service worker. That is a good way to find
defects in a component and a poor way to find defects in a **workflow**, because a workflow is the
thing a shop actually performs: a sequence that crosses screens, roles, and hours, and that can be
broken at a seam where every component on either side is individually correct.

Nothing in this repository currently writes those sequences down. `specs/` describes the domain and
the API; `docs/STAFF_CONSOLE_ENGINEERING_SPEC_V1.md` describes the console; the runbooks describe a
deployment. The step-by-step of *"a customer walks in at 08:40 and here is every control the counter
touches until that order is closed"* exists only as tacit knowledge distributed across the tests.
A workflow that is never written down cannot be verified as a whole, and cannot be handed to the
person who will run the shop.

## 2. Scope

**Part one — the layout.** Derive the core workflows from the specs, the deterministic domain code,
the API contract and the console screens, and publish them as a single reviewed document. Each
workflow states its trigger, its actor and required capability, its preconditions, the exact console
path and controls it touches, the domain rules that govern it, its state transitions across the
three orthogonal order dimensions, its terminal outcomes, and its failure modes. Every claim cites
the file that makes it true. **Where the specification and the code disagree, that is recorded as a
finding — never smoothed over in the prose.** The document describes what the software does; policy
is not invented here.

**Part two — the conformance run.** Enumerate every interactive control on every console screen into
a coverage matrix, then drive each workflow end to end in a real browser, signed in as the role that
performs it, against the running stack. A test claims the matrix entries it exercises; anything left
unclaimed is reported as uncovered rather than assumed fine.

**Part three — the fixes.** Every finding is adversarially verified by an agent whose default is
REFUTED before any code moves. What survives gets a failing reproduction first, then a fix, then a
re-run of the whole suite and all three verification scripts.

## 3. What this task may not do

- Move any AI capability. All 13 stay `NOT_AUTHORIZED`; the correct behaviour of the agent system in
  this release is that it **refuses**, and the conformance run verifies the refusal rather than
  exercising an action.
- Decide policy. A workflow whose correct behaviour is genuinely unsettled is recorded as a decision
  request, not resolved by whoever writes the test.
- Touch frozen items or rewrite recorded evidence.

## 4. Verification

`ruff`, `mypy`, the full suite with `--require-postgres-integration`, `verify_contracts.py`,
`check_context_drift.py`, plus the three browser/API drivers — `verify_counter_transaction.py`,
`verify_console_interaction.py`, `verify_daily_operations.py` — and the new workflow conformance
driver this task produces.
