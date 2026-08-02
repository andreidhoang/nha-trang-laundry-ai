# Task packet: TELEMETRY-001

Goal: add vendor-neutral OpenTelemetry traces and metrics plus executable SLO/alert contracts.

Metrics cover API, database, queue age/depth, retries, DLQ, agent budgets, approvals, suppression,
kill-switch freshness, cost, backup age, and restore age. Raw PII, prompts, tool bodies, secrets, and
unbounded identifiers are prohibited labels or span attributes.

