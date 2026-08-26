# Runbook — deploy day

**Written:** 2026-08-26 · **Status:** untested against a real host. Every command below is real and
most have been run locally; the sequence has never been executed end to end on a provisioned
machine, because no machine exists. Treat step ordering as verified and step *outcomes* as expected.

This exists because the distance between this repository and a shop using it is now mostly not code.
It is eleven secrets, one host, and the sequence below.

## 0. Before you start — what must already be true

| | Prerequisite | Who |
|---|---|---|
| 1 | A host is chosen and provisioned, per ADR-0007's three-zone two-host topology | owner — `DEC-HOSTING`, unsigned |
| 2 | An OIDC issuer exists with an MFA claim, and the shop's staff have accounts | owner |
| 3 | A TLS certificate and key for the console hostname | owner |
| 4 | An off-host encrypted PostgreSQL backup repository in a separate failure domain | owner — `BACKUP-RESTORE-001`, blocked on this |
| 5 | The owner's own OIDC subject, to bind the first `OWNER_ADMIN` | owner |

**None of these is an engineering task and none can be substituted.** A deploy that skips 4 is a
deploy with no recovery, and this repository will not pretend otherwise: `BACKUP-RESTORE-001`
requires a *reviewed restore drill*, not a configured backup.

## 1. Secrets

`compose.production.yaml` declares eleven external Docker secrets. External means the compose file
never contains them and they are never in this repository — see `docs/runbooks/provider-credentials.md`.

```bash
printf '%s' '<value>' | docker secret create staging_migration_database_url -
printf '%s' '<value>' | docker secret create staging_api_database_url -
printf '%s' '<value>' | docker secret create staging_worker_database_url -
printf '%s' '<value>' | docker secret create staging_oidc_issuer -
printf '%s' '<value>' | docker secret create staging_oidc_audience -
printf '%s' '<value>' | docker secret create staging_oidc_jwks_url -
printf '%s' '<value>' | docker secret create staging_oidc_mfa_claim -
printf '%s' '<value>' | docker secret create staging_oidc_mfa_value -
docker secret create staging_tls_certificate  ./fullchain.pem
docker secret create staging_tls_private_key  ./privkey.pem
```

**Three database URLs, deliberately.** The migration identity owns the schema; the API and worker
identities should not. **The migrations do not create those roles.** No `CREATE ROLE`, `GRANT` or
`REVOKE` exists anywhere in `0001`–`0032` — that is `DEC-020`, still open. Until it is decided, the
honest options are to point all three at one role (and accept that the API can drop tables) or to
create the roles by hand outside the migration set and record what you granted. Neither is good.
This is the largest known gap between this runbook and a deployment worth trusting.

## 2. Bring the stack up

```bash
docker compose -f compose.production.yaml up -d migrate   # runs and exits
docker compose -f compose.production.yaml up -d api worker tls
```

`migrate` is a one-shot container running `python -m nha_trang_laundry_db.migration_job`. It must
exit 0 before the API starts; the API does not migrate on boot, on purpose.

Every capability flag is forced false in the compose file: `FEATURE_PUBLIC_CHANNELS_ENABLED`,
`FEATURE_AUTOMATED_SENDS_ENABLED`, `FEATURE_AGENT_RUNTIME_ENABLED`. Do not set them. They are
governed by `delivery/GATE_REGISTRY.yaml` and no gate has been passed.

## 3. Bind the first owner

```bash
DATABASE_URL=... uv run python scripts/bootstrap_owner.py \
  --oidc-subject '<the owner's subject from the IdP>' \
  --display-name 'Chủ tiệm'
```

Binds one named `OWNER_ADMIN` to an already-verified OIDC subject. It creates no password and holds
no credential. Everyone else is added by the owner through the console afterwards.

## 4. Publish the price list

```bash
DATABASE_URL=... uv run python scripts/publish_pricebook.py --actor-id '<owner staff uuid>'
```

**Until this runs, the shop cannot quote anything** — `POST /quotes` returns 503 `pricebook
unavailable`, by design. The source is `templates/services-pricebook.csv`; pass `--source` for a
different file. Publishing is a human act with an attributed actor, never a boot-time side effect.

Re-run it whenever a price changes. No redeploy is needed: the API reads the published configuration
per request and verifies its digest.

## 5. Prove it before letting staff in

```bash
uv run python scripts/staging_smoke.py            # TLS-verifying operator-boundary check
uv run python scripts/verify_contracts.py
uv run python scripts/report_delivery_status.py   # every capability must read NOT_AUTHORIZED
```

Then one real transaction by hand, at the counter, on the real host:

1. **Tiếp nhận** → *Phát phiếu* → a number appears
2. **Báo giá** → weigh, pick the service, `Khách tự mang tới và tự lấy` → a total appears
3. *Khách đã chốt giá* → the revision becomes `APPROVED_EXACT`
4. Create the order, walk it through intake → production → released
5. Record the settlement
6. The order reads `COMPLETED`

That sequence is proven working against a live database (2026-08-26). If any step refuses on the
real host, the cause is configuration, not the code path.

## 6. What this deployment cannot do on day one

Say these out loud to whoever is running the counter, because the console says them too but a person
should hear them first:

- **Delivery orders cannot be completed.** A delivered order can be taken and worked, but it cannot
  be settled or closed — `DEC-010` does not support a non-counter settlement, and no delivery leg
  can be recorded. See `docs/DECISION_REQUEST_DELIVERY_COMPLETION_2026-08.md`. Take delivery jobs on
  paper until that is signed and built.
- **No customer is remembered.** A walk-in is a ticket number and nothing else, by `DEC-013`. Two
  visits by the same person are two unrelated tickets.
- **Nothing is sent to any customer.** No channel is connected and automated sends are gated off.
- **The AI does nothing.** No model has ever been invoked by this system. The assistant on the
  console is deterministic and says so.

## 7. If it goes wrong

Rollback is `docker compose -f compose.production.yaml down` and restoring the previous image tag.
**Migrations are forward-only and do not roll back.** `0031` in particular drops a CHECK constraint
that cannot be restored while any row written since would violate it, so a rollback past it needs a
new forward migration — see `evidence/delivery-loop/QUOTE-ACCEPT-001.yaml`.

There is no tested restore path for the database itself. That is `BACKUP-RESTORE-001` and it is the
first thing to fix after the shop is running.
