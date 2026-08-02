# Task packet: WORKER-HOST-001

Goal: turn the health-only worker image into a recoverable PostgreSQL-backed service host while
keeping every provider/channel send path absent and every agent provider path release-gated.

## Required behavior

- Supervise bounded internal-outbox and durable-agent claim loops with configurable polling.
- Use PostgreSQL as the only queue/lease authority; multiple processes must remain safe.
- Stop gracefully, revoke in-process authority at deadline, and expose separate liveness/readiness.
- Readiness must fail on missing configuration or database failure and must never imply automation
  authorization.
- Recover stale processing claims through an explicit audited operation; never blindly replay.
- Emit redacted correlation-aware events and PII-free metrics-ready state.
- No channel SDK, provider-send client, generic handler loading, or automatic fallback.

## Tests first

Cover database outage, two-worker contention, stale claim recovery, shutdown during polling, unknown
handler, retry exhaustion, disabled provider runtime, and container entry-point contracts.

