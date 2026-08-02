# Task packet: STAGING-001

Goal: provide a private, reproducible Linux staging topology with TLS termination, external secret
injection, deny-by-default networks, least-privilege identities, migration jobs, smoke checks, and a
tested rollback path.

The operator UI remains private. Public OpenClaw, customer ingress, channel credentials, and automatic
send remain absent. A staging deployment is engineering evidence, not release authority.

