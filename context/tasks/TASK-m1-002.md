# TASK-m1-002 — Staff identity, RBAC, and audit primitives

**Goal:** named OIDC staff identity with database-authoritative roles and revocable secure sessions.

**Domains:** `platform`, `orders_audit`, `privacy_consent`  
**Stable work item:** `IDENTITY-001`  
**Risk:** HIGH

## Constraints and done criteria

- OIDC tokens establish subject only; PostgreSQL controls roles, session revocation and authorization.
- Missing OIDC issuer/audience/JWKS/MFA configuration returns no authenticated session.
- Sensitive roles require configured MFA proof. No shared account, token logging or machine-role UI access.
- Tests prove session revoke and MFA denial against live PostgreSQL; all material writes use event/audit/outbox.

## Rollback

- Migration `0003` is additive and forward-only. Revoke/disable accounts instead of deleting records.
- Real Identity Platform configuration and production deployment remain separately gated.
