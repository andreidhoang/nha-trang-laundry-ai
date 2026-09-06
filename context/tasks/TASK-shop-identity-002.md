# TASK-shop-identity-002 — the second factor is asked for, and signing out ends both sessions

**Goal:** the second factor is asked for, and signing out ends both sessions.

**Domains:** `platform`

**Stable work item:** `SHOP-IDENTITY-002`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

Measured against a real Keycloak importing the committed realm: `minimum.acr.value` is not enforced
once an SSO cookie exists. A second authorization request returned a code with no password, no OTP
and no click, carrying `acr: "0"` — a shop tablet handing the next person a 24-hour session as
whoever used it last, and a privileged member locked out of their own console behind an opaque
message on their second sign-in of the day.

## What must be true when this is done

1. The authorization request asks for the second factor by name.
2. Signing out of the console ends the session at the issuer too.
3. What configuration cannot close is written down as a ceremony, not left implied.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
