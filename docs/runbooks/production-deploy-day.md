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
| 2 | ~~An OIDC issuer exists with an MFA claim~~ — **built**: Keycloak in Zone C, `SHOP-IDENTITY-001`. The owner still creates the staff accounts, in the Keycloak admin console, after step 2 | owner |
| 3 | A TLS certificate and key for the console hostname | owner |
| 4 | An off-host encrypted PostgreSQL backup repository in a separate failure domain | owner — `BACKUP-RESTORE-001`, blocked on this |
| 5 | The owner's own OIDC subject, to bind the first `OWNER_ADMIN` | owner |

**None of these is an engineering task and none can be substituted.** A deploy that skips 4 is a
deploy with no recovery, and this repository will not pretend otherwise: `BACKUP-RESTORE-001`
requires a *reviewed restore drill*, not a configured backup.

## 1. Secrets

`compose.r1.yaml` declares eleven external Docker secrets. External means the compose file never
contains them and they are never in this repository — see `docs/runbooks/provider-credentials.md`.

```bash
printf '%s' '<value>' | docker secret create r1_migration_database_url -
printf '%s' '<value>' | docker secret create r1_api_database_url -
printf '%s' '<value>' | docker secret create r1_worker_database_url -
printf '%s' '<value>' | docker secret create r1_oidc_issuer -
printf '%s' '<value>' | docker secret create r1_oidc_audience -
printf '%s' '<value>' | docker secret create r1_oidc_jwks_url -
printf '%s' '<value>' | docker secret create r1_oidc_mfa_claim -
printf '%s' '<value>' | docker secret create r1_oidc_mfa_value -
docker secret create r1_tls_certificate  ./fullchain.pem
docker secret create r1_tls_private_key  ./privkey.pem
```

A twelfth, `r1_postgres_password`, only when the hosting decision selects a self-managed database —
the `self-managed-database` profile. A provider-managed endpoint with PITR leaves the profile off.

**Use `compose.r1.yaml`, not `compose.production.yaml`.** The latter is honestly named
`nha-trang-laundry-private-staging` and its `tls` service is attached only to an `internal: true`
network, so Docker accepts its published port and silently discards it — measured, on this host:

```
service   HostConfig.PortBindings                                   NetworkSettings.Ports
broken    {"8443/tcp":[{"HostIp":"127.0.0.1","HostPort":"18443"}]}  {"8443/tcp":[]}
fixed     {"8443/tcp":[{"HostIp":"127.0.0.1","HostPort":"18444"}]}  {"8443/tcp":[{...18444}]}
```

The console would not answer a single request. `SHOP-DEPLOY-001` fixed that in `compose.r1.yaml`
and left the staging file alone, because its acceptance commands are cited by closed evidence.

**Three database URLs, deliberately.** The migration identity owns the schema; the API and worker
identities do not. The migrations still do not create the roles — that part of `DEC-020` is open —
so create them once, by hand, before step 2:

```sql
CREATE ROLE laundry_migrate LOGIN PASSWORD '...';
CREATE ROLE laundry_api     LOGIN PASSWORD '...';
CREATE ROLE laundry_worker  LOGIN PASSWORD '...';
CREATE DATABASE nha_trang_laundry OWNER laundry_migrate;
```

Then, **after** the migration in step 2 and again after every future migration:

```bash
SUPERUSER_DATABASE_URL=... uv run python scripts/apply_demo_grants.py
DATABASE_URL=...           uv run python scripts/verify_database_grants.py
```

**Despite its name, `apply_demo_grants.py` is the production grant script**, and it must run after
**every** migration, not only this one. The name is a trap on the one morning nobody can afford to
skip a step, and it is recorded here rather than quietly renamed: the file is cited by recorded
evidence for two completed items.

The second command is not optional and is the reason the first is safe to forget. `GRANT ... ON ALL
TABLES` is a one-shot snapshot: before 2026-08-26 every migration that added a table left the
application roles with **no privileges on it**, and the first symptom was `permission denied` on a
write path in production long after the deploy that caused it. `ALTER DEFAULT PRIVILEGES` now closes
that for new tables, and the verifier proves the result across every table rather than trusting it.

What stays open in `DEC-020` is who may execute a purge. DELETE is granted to nobody, which is the
fail-closed state that decision exists to change, and the verifier fails if that ever stops being
true.

## 2. Bring the stack up

```bash
export R1_CONSOLE_HOST=console.giatlasachcong.lan   # the name on the certificate
export R1_CONSOLE_BIND_IP=10.8.0.1                  # the VPN or shop-LAN address, never 0.0.0.0

docker compose -f compose.r1.yaml up -d migrate   # runs and exits
docker compose -f compose.r1.yaml up -d api worker tls
```

`R1_CONSOLE_BIND_IP` defaults to `127.0.0.1`: an unset variable produces a console nobody can reach
rather than one everybody can. Two things must also be true before a tablet at the counter can load
it, and neither is in this repository:

- the shop's DNS resolves the console hostname to the host. An iOS device has no `/etc/hosts`, so
  this must be DNS;
- the private CA that issued the certificate is installed **and explicitly trusted** on every
  device. On iOS that is a second step under Settings → General → About → Certificate Trust
  Settings, and its failure mode is a console that will not load with no useful error.

`migrate` is a one-shot container running `python -m nha_trang_laundry_db.migration_job`. It must
exit 0 before the API starts; the API does not migrate on boot, on purpose.

Every capability flag is forced false in the compose file: `FEATURE_PUBLIC_CHANNELS_ENABLED`,
`FEATURE_AUTOMATED_SENDS_ENABLED`, `FEATURE_AGENT_RUNTIME_ENABLED`. Do not set them. They are
governed by `delivery/GATE_REGISTRY.yaml` and no gate has been passed.

## 2a. The identity provider

Keycloak comes up with the stack and imports `deploy/production/keycloak/realm-nhatrang.json`, which
carries no users and no client secret. Three things to do before anybody signs in:

1. **Create the staff accounts.** Reach the admin console from the host only — it is deliberately
   not routed on the shop network:

   ```bash
   docker compose -f compose.r1.yaml exec keycloak /opt/keycloak/bin/kcadm.sh      config credentials --server http://localhost:8080/idp --realm master      --user admin --password "$(cat /path/to/bootstrap-admin-password)"
   ```

   Each account gets a username and a temporary password. Every account configures TOTP on first
   sign-in — the realm makes `CONFIGURE_TOTP` a default required action, and the browser flow makes
   the second factor **required**, not conditional on the account having one. A conditional second
   factor is skipped for exactly the account an attacker would choose.

2. **Note each staff member's OIDC subject.** It is the `id` of the Keycloak user, and it is what
   `bootstrap_owner.py` binds in step 4 and what the console binds for everyone else. Roles are
   never read from the token: `staff_role_assignments` in PostgreSQL is the only role source.

3. **Delete the bootstrap admin** once the owner has their own admin account with a second factor.
   Nothing enforces this, and a bootstrap password that outlives its purpose is a password nobody
   is rotating.

**If staff cannot sign in and the console is up**, the issuer is the first thing to check. Sign-in
fetches the signing keys live on every exchange and fails closed if Keycloak is down — but existing
sessions are opaque database rows and keep working for up to 8 hours idle / 24 hours absolute, so
the symptom is "nobody new can sign in" rather than "everybody is thrown out". That is a decision,
recorded on `get_identity_service` in `apps/api/.../main.py`, not an accident.

## 3. Create the store

```bash
DATABASE_URL=... uv run python scripts/bootstrap_store.py --name 'Giặt Là Sạch Cộng — 3A Lê Đại Hành'
```

Prints the store's identifier. Everything in this system that belongs to a shop carries it, and
since `STORE-REGISTRY-001` it is a foreign key: before that a store was a UUID somebody typed into
the console, and a typo made a staff member a member of a store that did not exist -- refused
everywhere, shown nothing, with no error saying why.

Run it again with `--store-id` and it is a no-op, so a re-run of this runbook is safe. Pass
`--actor-id` with the owner's staff UUID once step 4 has produced one, if you would rather the
record name a person than say `SYSTEM`; ordering it that way is fine too.

## 4. Bind the first owner

```bash
DATABASE_URL=... uv run python scripts/bootstrap_owner.py \
  --oidc-subject '<the owner's subject from the IdP>' \
  --display-name 'Chủ tiệm'
```

Binds one named `OWNER_ADMIN` to an already-verified OIDC subject. It creates no password and holds
no credential. Everyone else is added by the owner through the console afterwards.

**`OWNER_ADMIN` is not implicitly a member of the store.** The owner assigns themselves from the
console -- `POST /internal/v1/staff/{id}/stores/{store_id}` -- and that assignment is audited like
any other, including who granted it. Until it exists the owner can administer staff and see
nothing else.

## 5. Publish the price list

```bash
DATABASE_URL=... uv run python scripts/publish_pricebook.py --actor-id '<owner staff uuid>'
```

**Until this runs, the shop cannot quote anything** — `POST /quotes` returns 503 `pricebook
unavailable`, by design. The source is `templates/services-pricebook.csv`; pass `--source` for a
different file. Publishing is a human act with an attributed actor, never a boot-time side effect.

Re-run it whenever a price changes. No redeploy is needed: the API reads the published configuration
per request and verifies its digest.

## 6. Prove it before letting staff in

```bash
uv run python scripts/staging_smoke.py            # TLS-verifying operator-boundary check
uv run python scripts/verify_database_grants.py   # role separation actually enforced
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

## 7. What this deployment cannot do on day one

Say these out loud to whoever is running the counter, because the console says them too but a person
should hear them first:

- **Delivery orders can be taken, paid for and closed.** `DEC-023` was signed on 2026-08-26 and
  `FULFILMENT-001` built it: the customer pays the exact quoted total at the counter before the
  laundry leaves, and a named staff member records the delivery leg when it arrives. No driver
  carries money. A failed attempt is recorded as a failed leg, nothing is charged, and the goods
  come back to the shop; a retry is a new leg. *(This section said the opposite until
  `SHOP-CUTOVER-001` corrected it — it was written before `DEC-023` and never revisited.)*
- **An order cannot leave production once an exception is recorded, and a confirmed order can be
  cancelled without the money being recorded.** Both are `DEC-024`, open. Until it is signed, do not
  record a production exception in the system, and treat a late cancellation as a paper matter.
- **No customer is remembered.** A walk-in is a ticket number and nothing else, by `DEC-013`. Two
  visits by the same person are two unrelated tickets.
- **Nothing is sent to any customer.** No channel is connected and automated sends are gated off.
- **The AI does nothing.** No model has ever been invoked by this system. The assistant on the
  console is deterministic and says so.

## 8. If it goes wrong

Rollback is `docker compose -f compose.r1.yaml down` and restoring the previous image tag.
**Migrations are forward-only and do not roll back.** `0031` in particular drops a CHECK constraint
that cannot be restored while any row written since would violate it, so a rollback past it needs a
new forward migration — see `evidence/delivery-loop/QUOTE-ACCEPT-001.yaml`.

There is no tested restore path for the database itself. That is `BACKUP-RESTORE-001` and it is the
first thing to fix after the shop is running.
