# TASK-spec-route-surface-001 — govern the route surface that actually exists

**Goal:** make `specs/` describe the internal HTTP surface this system actually serves, so that a
route is a governed contract rather than an implementation detail.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `SPEC-ROUTE-SURFACE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW to write, HIGH to keep ignoring — an unspecified route cannot be validated, cannot
generate an eval case, and cannot be reviewed against a contract that does not mention it.

## Why this exists

`AGENTS.md` makes the machine-readable contracts normative. `CLAUDE.md` states that contracts win
over prose. Measured on 2026-08-18 against `HEAD`, the route layer satisfies neither.

| Direction | Measured |
|---|---|
| Routes served by `apps/api` under `/internal/v1` | **38** (17 `GET`, 21 write) |
| …of those, named anywhere in `specs/DOMAIN_DATA_API_SPEC_V1.md` | **1** |
| Endpoints named in spec §13.4 "Internal endpoints" | **20**, all `POST` |
| …of those, implemented | **2** |

Both numbers were produced by parsing the decorators out of `apps/api/src/nha_trang_laundry_api/`
and the fenced block under §13.4, not by reading prose. Path parameters were normalised before
comparison, so the mismatch is not a naming artefact.

**The two halves mean different things and must not be conflated.**

The 18 specified-but-unbuilt endpoints are *expected*. They are the command surface for the eight
bounded contexts `docs/PRODUCTION_READINESS_ASSESSMENT.md` §2.2 measures as empty — payments,
remedies, credits, custody, delivery legs, invoicing. A route cannot exist before the aggregate it
mutates. This half needs no action here; it resolves as those contexts get built.

The 37 built-but-unspecified routes are the gap. Every one of them is a surface the staff console
depends on, that no contract validates, that `scripts/verify_contracts.py` cannot check, and that no
eval case can be generated against. Among them are routes that move money and identity:
`POST /internal/v1/stores/{store_id}/quotes`, `POST /internal/v1/orders/{order_id}/settlement`,
`POST /internal/v1/staff/{staff_user_id}/roles`, `POST /internal/v1/auth/session`.

### The correction this packet records

An earlier note in `docs/AGENTIC_PRODUCTION_HARNESS_PLAYBOOK_2026-08.md` framed this as **two**
undocumented routes — `GET .../settlements/today` and `GET /internal/v1/pricebook/services`. That
was true and badly under-measured. Those two are not anomalies; they are two members of a set of 37,
and §13.4 documents no `GET` route at all because it is deliberately a *command* specification
("Use narrow commands, not generic CRUD for material state"). Framing the gap as two routes implied
a small edit. It is not a small edit, and pretending otherwise would have produced a fix that left
35 ungoverned surfaces in place.

## Required design

- A machine-readable contract for the internal surface — most plausibly
  `specs/contracts/internal-api-v1.openapi.yaml`, matching how `agent-tools-v1.openapi.yaml` already
  governs the ten Tool Facade operations, so `verify_contracts.py` gains a second surface to check
  with no new mechanism.
- Generated from or validated against the built `app` object, never hand-transcribed. A hand-written
  route list is a second source of truth and will drift within a week — this packet exists because
  that already happened once.
- A check that fails when a route exists without a contract entry, so the gap cannot silently reopen.
  That check is the actual deliverable; the document is the by-product.
- `specs/DOMAIN_DATA_API_SPEC_V1.md` §13.4 is an approved specification. **Do not edit it to match
  the code.** Amending an approved spec to describe what was built instead of what was decided
  inverts the authority relationship. If §13.4 is wrong, that is a specification amendment and it is
  the owner's; if it is right and simply unbuilt, it stays as written.

## Constraints

- Read-only with respect to `apps/api` behaviour: this item documents and validates, it does not
  change a route, a response shape, or an authorization rule.
- No route may be added to a contract as "intended" — the contract describes what is served today.
- The `GET`/read surface is in scope. §13.4's command-only framing is why 16 of 17 reads are
  unspecified, and leaving reads ungoverned is what let the console depend on two of them unnoticed.

## Required tests

- every route on the built `app` object appears in the contract, proven by enumerating the app
  rather than by grepping source;
- every contract entry corresponds to a served route, so the contract cannot describe a route that
  was deleted;
- adding a route without a contract entry fails the check, proven by a negative test;
- `verify_contracts.py` validates the new contract on every run.

## Done when

- the internal route surface has a machine-readable contract covering all 38 routes;
- the drift check fails closed when route and contract disagree;
- the full gate battery passes with no required skips;
- `specs/DOMAIN_DATA_API_SPEC_V1.md` is unedited, and any disagreement between it and the built
  surface is recorded as a finding for the owner rather than reconciled by an engineer.
