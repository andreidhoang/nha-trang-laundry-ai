# Task packet: STAFF-OPS-001

Goal: complete the internal mobile Shadow workflow for quotes, orders, approvals, manual-send
attestation, incidents, and queue recovery without adding public or automatic delivery.

All mutations use strict typed requests, server-bound objects, RBAC/MFA, idempotency, optimistic
concurrency, and atomic mutation/event/audit/outbox semantics. Offline UI is read-only; it never queues
authoritative mutations.

