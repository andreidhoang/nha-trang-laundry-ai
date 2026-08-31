# TASK-shop-recovery-001 — the parts of recovery that can be built before a host exists

**Goal:** the archiving mechanism, the restore procedure, and a validator that can accept a
production drill.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `SHOP-RECOVERY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. New operational scripts and one validator change; no application code.

## Why this exists

`deploy/backup/backup-policy-v1.json` states the policy — recovery point 900s, recovery time
14400s, off-host, separate failure domain, AES-256 — and `scripts/validate_restore_drill.py`
validates externally produced evidence against it. **No backup mechanism exists.** No archiver, no
cron, no snapshot configuration, anywhere in the repository.

And the validator cannot accept a production drill: `recovery.py:107` rejects any evidence whose
`environment` is not `"STAGING"`, and the contract test hardcodes the same value, so it would pass
unchanged and prove nothing.

## What must be true when this is done

1. **The archive is encrypted with a key that cannot decrypt it.** ADR-0007 §3 and the operations
   spec both require encryption "before leaving Host A with a key that is not stored on Host A".
   Taken literally this excludes a symmetric passphrase: a value present on the host to write the
   archive also decrypts every archive ever taken, which is the exact failure the sentence exists to
   exclude. Encryption is therefore public-key, with only the recipient key on the host.
2. **`archive_command` returns success only when the segment is durably off-host.** The alternative
   — writing to a local spool and shipping asynchronously — makes the command lie: PostgreSQL is
   free to recycle a segment the moment the command returns, so a spool-and-ship design silently
   converts a recovery guarantee into a hope. The cost is that the database container needs egress,
   which is a real concession and is stated rather than hidden.
3. **An idle afternoon cannot produce a stale recovery point.** Without a bounded archive timeout, a
   shop with no orders between 14:00 and 18:00 has a four-hour-old recovery point while every check
   reports healthy.
4. `scripts/validate_restore_drill.py` accepts a `PRODUCTION` drill, and its audit-chain proof
   checks for the absence of a gap rather than the presence of a row.
5. `docs/runbooks/restore-drill.md` exists — required by `specs/PRODUCTION_OPERATIONS_SPEC_V1.md`
   §7 and absent — with the exact commands, timed, including the owner's handoff of the decryption
   key, which is inside the four-hour clock.
6. Key custody is opened as an owner decision, not chosen by an engineer. Losing the key makes every
   backup permanently unrecoverable, and an archive nobody can decrypt is worse than no archive
   because it produces false confidence.

## Boundary

**The drill itself needs a host and is not in this item.** `BACKUP-RESTORE-001` completes on a drill
result, never on configuration, and stays blocked on an operator-provisioned off-host repository.
This item builds what the drill will run.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
