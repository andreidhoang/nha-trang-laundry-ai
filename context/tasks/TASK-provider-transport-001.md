# TASK-provider-transport-001 — real Responses provider transport

**Goal:** implement the network transport behind the `ResponsesProviderTransport` protocol without
changing the state machine, the tool boundary or any business authority.

**Domains:** `runtime_architecture`, `agent_tools`, `evaluation_release`

**Stable work item:** `PROVIDER-TRANSPORT-001`

**Stage:** M4A
**Risk:** HIGH — first component in this repository that talks to a third party.

## Context

`apps/worker/src/nha_trang_laundry_worker/responses_runtime.py` already implements the bounded finite
state machine and defines `ResponsesProviderTransport` as a `Protocol`. Every state-machine path is
already covered against a deterministic fake transport. **Only the network implementation is
missing.** This task adds one class; it does not touch the state machine.

## Required design

An HTTP client implementing the existing protocol, and nothing more:

- credential read from the secret mount at call time, never captured at import, never logged;
- the exact pinned model release from `runtime/model-registry-v1.yaml`; a moving alias is refused;
- explicit non-storage requested on every call;
- strict function tools derived from `agent-tools-v1.openapi.yaml`; provider built-in web, file,
  code, computer and MCP tools disabled; parallel tool calls disabled;
- absolute deadline honoured — the remaining budget passed by the caller is the ceiling, and a
  connect plus read timeout must not sum beyond it;
- one attempt per model call. **Ambiguous outcomes are not retried.**

## The part that must be exactly right

`ResponsesProviderAmbiguity` already exists in the module for a reason. A timeout or connection reset
after the request left the machine means the provider may have processed and billed it.

- ambiguous outcome raises the ambiguity error; the state machine already routes that to
  `REVOKE_BRIDGE -> settle budget -> RETURN_REQUIRE_HUMAN`;
- a retry, repair, response continuation or tool round trip **never** resets the model, tool, token,
  cost or deadline counters;
- a proven-rejected request (the provider demonstrably did not accept it) is the only case that may
  release rather than settle the reserved budget.

## Constraints

- No change to the state machine, `AgentToolBridgeSession`, tool contract, budgets or ledger.
- Never store or log: the credential, raw request bodies, raw response bodies, prompt contents,
  chain-of-thought, or provider reasoning artifacts. Provider response IDs are transport metadata.
- No PII in any development or staging call. Synthetic contacts only.
- Every capability stays `NOT_AUTHORIZED`. A successful provider call authorizes nothing.
- The effective-request capture is sanitized and schema-valid against
  `provider-data-evidence-v1.schema.json` — it records that non-storage was requested, not what was
  sent.

## Required tests

Against a local stub server, not the real provider:

- successful zero-tool draft and successful serial tool round trip;
- connect timeout, read timeout, and reset after send — each raises ambiguity, settles budget, and
  returns `REQUIRE_HUMAN`;
- provider 4xx rejection is a proven-not-accepted path and releases budget;
- provider 5xx and rate-limit responses do not silently retry;
- malformed response body, unknown output item and unexpected tool name are rejected;
- deadline exhaustion mid-call cancels rather than overruns;
- credential absent or unreadable fails closed before any network call;
- no credential, body or prompt appears in logs, traces or evidence for any of the above.

## Done when

- targeted tests pass with no network access to a real provider;
- one manual staging call against the real provider with a synthetic contact produces a schema-valid
  effective-request capture;
- Ruff, format, mypy, contracts, context drift and the PostgreSQL suite pass with no required skips;
- rollback is disabling the transport route, which returns the runtime to the deterministic fake and
  the system to degraded mode.
