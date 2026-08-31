# TASK-shop-observability-001 — a system that can say what happened to it

**Goal:** make the structured log reach a log, and build the four checks that can actually fire in a
one-host deterministic deployment.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `SHOP-OBSERVABILITY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW. Logging configuration and read-only host checks; no behaviour change to any write
path.

## Why this exists

### Nothing this system logs ever reaches a log

`SafeStructuredLogger` sinks to `logging.getLogger("nha_trang_laundry.structured").info`. Under
uvicorn's default `LOGGING_CONFIG`, which the image applies, that logger's effective level is
WARNING and the root logger has no handler. Measured: `isEnabledFor(INFO)` is `False`,
`logging.getLogger().handlers == []`.

**Every `_LOGGER.record(...)` call in the API is a no-op in the container**, including the browser
security boundary's CSRF and origin rejections. The redaction machinery, the event schema and the
correlation IDs are all correct and all discarded. `LOG_LEVEL` appears in `.env.example` and is read
by nothing.

### Metrics are recorded into the void

`packages/observability/.../telemetry.py` declares the metric contracts and the API wires meter and
tracer providers, but no exporter package is declared in any `pyproject.toml`, no `OTEL_*` variable
appears in any compose file, there is no collector and no `/metrics` endpoint. The default providers
are no-ops. `backup_age_s` and `restore_test_age_s` are declared contracts with no producer.

## What must be true when this is done

1. A structured event emitted by the API appears on stdout in the container, proven by a test that
   asserts a line reaches the sink under uvicorn's configuration — the failure is invisible
   otherwise, which is how it survived this long.
2. The telemetry question is answered explicitly rather than left ambiguous: either an exporter and
   a collector, or "no collector; structured stdout is the record", recorded as a decision with its
   consequence for the declared metric contracts.
3. Four checks exist that can fire in R1. Of the seven paging alerts in
   `specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §4.1, five have no referent here — there is no channel,
   no sender, no provider and no agent cell — and saying so is more useful than shipping alerts that
   can never fire.
   - the WAL archive gap exceeding the recovery point objective;
   - any capability flag reading true on a running container;
   - the database volume filling, which is how a correctly configured archiver kills the shop: a
     failing `archive_command` pins WAL segments forever, the disk fills, PostgreSQL stops accepting
     writes, and the counter cannot take an order;
   - the console being unreachable — in R1 the console is the business, with no channel and no
     fallback path.
4. The alert-routing question is opened as a decision rather than answered by adding a counterparty.
   There is deliberately no channel, no SMTP and no paging provider. Until it is answered the checks
   exit non-zero for a host scheduler and the console says when one last failed. An alert nobody
   receives is not an alert.

## Boundary

`MONITORING-001` remains the G1-scoped item for a collector, retention and paging contracts. This
item does not re-wire it.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
