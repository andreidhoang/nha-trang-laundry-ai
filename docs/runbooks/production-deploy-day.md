# Runbook — deploy day

**Written:** 2026-08-26 · **Status:** untested against a real host. Every command below is real and
most have been run locally; the sequence has never been executed end to end on a provisioned
machine, because no machine exists. Treat step ordering as verified and step *outcomes* as expected.

This exists because the distance between this repository and a shop using it is now mostly not code.
It is fourteen secrets, one host, and the sequence below.

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

`docker secret` requires a swarm, and a host that has never joined one refuses the first command
below with `This node is not a swarm manager`. One node is a swarm:

```bash
docker swarm init
```

`compose.r1.yaml` declares **fourteen** external Docker secrets. External means the compose file
never contains them and they are never in this repository — see
`docs/runbooks/provider-credentials.md`.

```bash
printf '%s' '<value>' | docker secret create r1_migration_database_url -
printf '%s' '<value>' | docker secret create r1_api_database_url -
printf '%s' '<value>' | docker secret create r1_worker_database_url -
printf '%s' '<value>' | docker secret create r1_oidc_issuer -
printf '%s' '<value>' | docker secret create r1_oidc_audience -
printf '%s' '<value>' | docker secret create r1_oidc_jwks_url -
printf '%s' '<value>' | docker secret create r1_oidc_mfa_claim -
printf '%s' '<value>' | docker secret create r1_oidc_mfa_value -
printf '%s' '<value>' | docker secret create r1_keycloak_database_password -
docker secret create r1_tls_certificate  ./fullchain.pem
docker secret create r1_tls_private_key  ./privkey.pem
```

`r1_keycloak_database_password` was missing from this list until the adversarial round, and it is
not optional: it is mounted into the `keycloak` service and read by `keycloak-entrypoint.sh`, the
service is unprofiled, and `tls` depends on it — so `up -d api worker tls` pulls it in and the
stack never becomes ready. The same value must be the password of the `keycloak` database role
created in §1a.

Three more only when the hosting decision selects a self-managed database — the
`self-managed-database` profile. A provider-managed endpoint with PITR leaves the profile off and
owns its own backups.

```bash
printf '%s' '<value>' | docker secret create r1_postgres_password -
# The age *public* key (or keys) the archive is encrypted to. Public: nothing on this host can
# read back what it writes, which is what ADR-0007 §3 requires.
docker secret create r1_backup_encryption_recipients ./recipients.txt
# An rclone configuration naming the archive repository, with a credential that can write and
# cannot delete (DEC-026).
docker secret create r1_backup_repository_credential ./rclone.conf
```

## 1a. Database roles

Four roles, not three. `keycloak` owns the identity provider's own schema and is separate from
every application role — it is a different system with a different lifecycle, and the migration
role must never be able to rewrite staff credentials.

```bash
docker compose -f compose.r1.yaml exec -T postgres \
  psql -U laundry_migrate -d nha_trang_laundry <<'SQL'
CREATE ROLE keycloak LOGIN PASSWORD '<the r1_keycloak_database_password value>';
CREATE DATABASE keycloak OWNER keycloak;
SQL
```

`docker cp` into these containers fails — the root filesystem is read-only — so SQL arrives on
stdin, as above.

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


> **On the self-managed branch, use `./scripts/shop-admin` instead of `uv run python`.**
> `postgres` publishes no port and sits only on `internal: true` networks, so nothing on the host
> can reach it — which is what ADR-0007 §1 asks for, and which meant every `DATABASE_URL=... uv run`
> line below was a command that could not be executed on the machine this page describes. The
> wrapper runs the same script inside the database's own network:
>
> ```bash
> ./scripts/shop-admin bootstrap_store.py --name 'Giặt Là Sạch Cộng — 3A Lê Đại Hành'
> ```
>
> On a provider-managed endpoint the plain `uv run` form is correct and this wrapper is unnecessary.

```bash
SUPERUSER_DATABASE_URL=... uv run python scripts/apply_demo_grants.py
./scripts/shop-admin verify_database_grants.py
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

0. **Create the admin that everything below needs.** `SHOP-FIRST-START-001` removed the bootstrap
   admin secret, correctly — but no step then created an admin any other way, so following this
   runbook end to end left the operator with no admin, therefore no staff accounts, therefore no
   sign-in. Run once, on the host, interactively:

   ```bash
   docker compose -f compose.r1.yaml exec keycloak \
     /opt/keycloak/bin/kc.sh bootstrap-admin user
   ```

   It prompts for a username and password and stores neither anywhere this repository can see.
   Do not pass the password on the command line: the shell expands it before exec, so it appears
   in the host's `docker` process arguments where `ps` can read it.

1. **Create the staff accounts.** Reach the admin console from the host only — it is deliberately
   not routed on the shop network:

   ```bash
   docker compose -f compose.r1.yaml exec keycloak /opt/keycloak/bin/kcadm.sh \
     config credentials --server http://localhost:8080/idp --realm master --user <admin>
   ```

   `kcadm.sh` prompts for the password when `--password` is omitted, which is the form to use.

   **The enrolment ceremony, and why it is not optional.** Every account configures TOTP on first
   sign-in — the realm makes `CONFIGURE_TOTP` a default required action, and the browser flow makes
   the second factor **required**, not conditional on the account having one. But an account that
   has not yet enrolled is offered the *enrolment* form, so **whoever holds the temporary password
   first enrols the factor**. Measured: knowing only the password, an unenrolled account was taken
   over, the attacker's own TOTP secret was registered, and the resulting token asserted `acr=mfa`.
   This cannot be closed in Keycloak configuration — an unenrolled user has to be able to enrol.

   So: **never send a temporary password to anybody.** Create the account and have the staff member
   enrol their authenticator at the counter, on their own phone, with you present, in that sitting.
   Then, and only then, grant their role in step 5. Roles live in `staff_role_assignments`, not in
   the issuer, so a session with no granted role can do nothing — that separation is what makes
   this ceremony an actual control rather than advice.

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
./scripts/shop-admin bootstrap_store.py --name 'Giặt Là Sạch Cộng — 3A Lê Đại Hành'
```

**Write the identifier down before you run anything else, and pass it back with `--store-id` on
every later run.** Without `--store-id` this mints a *new* store each time, so a runbook step run
twice — which is what runbook steps are for — leaves two shops and staff assigned to whichever one
they were told about.

Prints the store's identifier. Everything in this system that belongs to a shop carries it, and
since `STORE-REGISTRY-001` it is a foreign key: before that a store was a UUID somebody typed into
the console, and a typo made a staff member a member of a store that did not exist -- refused
everywhere, shown nothing, with no error saying why.

Run it again with `--store-id` and it is a no-op, so a re-run of this runbook is safe. Pass
`--actor-id` with the owner's staff UUID once step 4 has produced one, if you would rather the
record name a person than say `SYSTEM`; ordering it that way is fine too.

## 4. Bind the first owner

```bash
./scripts/shop-admin bootstrap_owner.py \
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
./scripts/shop-admin publish_pricebook.py --actor-id '<owner staff uuid>'
```

**Until this runs, the shop cannot quote anything** — `POST /quotes` returns 503 `pricebook
unavailable`, by design. The source is `templates/services-pricebook.csv`; pass `--source` for a
different file. Publishing is a human act with an attributed actor, never a boot-time side effect.

Re-run it whenever a price changes. No redeploy is needed: the API reads the published configuration
per request and verifies its digest.

## 6. Prove it before letting staff in

```bash
# Both arguments are required; run bare it exits 2 with "the following arguments are required".
# The CA file is the private authority that issued the console certificate — the same one every
# tablet has to trust — so this check fails exactly where a tablet would.
uv run python scripts/staging_smoke.py \
  --base-url https://console.giatlasachcong.lan:8443 \
  --ca-file ./ca.crt
./scripts/shop-admin verify_database_grants.py   # role separation actually enforced
uv run python scripts/verify_contracts.py
uv run python scripts/report_delivery_status.py   # every capability must read NOT_AUTHORIZED
```

Then one real transaction by hand, at the counter, on the real host:

1. **Tiếp nhận** → *Phát phiếu* → a number appears
2. **Báo giá** → weigh, pick the service, `Khách tự mang tới và tự lấy` → a total appears
3. *Khách đã chốt giá* → the revision becomes `APPROVED_EXACT`
4. Create the order, then on **Đơn hàng** use *Chuyển trạng thái đơn*: pick the dimension
   (Thương mại / Nhận đồ / Sản xuất) and the target. Nhận đồ first — `Đã nhận, chờ kiểm` then
   `Đã nhận` — because an order cannot become `Đang chạy` until intake is accepted. Then Thương mại
   to `Đang chạy`, then Sản xuất through to `Đã giao ra`. *(Until `CONSOLE-LIFECYCLE-001` there was
   no screen for the intake or production dimensions at all, so this step could not be performed
   from the console and dead-ended on `409 intake is not accepted`.)*
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
- **A production exception is recoverable, and a late cancellation has to say what happened.**
  `DEC-024`, decided 2026-09-03 and built by `ORDER-EXIT-001`. Recording an exception remembers
  where the work was interrupted, so a re-treated stain resumes and the order still completes; an
  order may be rewashed from an exception, which is the only way production moves backwards.
  Cancelling is free while nothing has been received, started or paid, and otherwise routes through
  cancellation review where a named staff member states one of three resolutions. Say the third one
  out loud at the counter: **there is no option for "washed, walked away, no money"** — a customer
  whose laundry has been washed pays and collects, or the laundry stays here.
- **No customer is remembered.** A walk-in is a ticket number and nothing else, by `DEC-013`. Two
  visits by the same person are two unrelated tickets.
- **Nothing is sent to any customer.** No channel is connected and automated sends are gated off.
- **The AI does nothing.** No model has ever been invoked by this system. The assistant on the
  console is deterministic and says so.

## 7a. What watches it once staff are in

```bash
DATABASE_URL=... \
R1_PGDATA_PATH=/var/lib/docker/volumes/nha-trang-laundry-shop_pgdata/_data \
R1_BASE_BACKUP_MARKER=/var/lib/docker/volumes/nha-trang-laundry-shop_pgbackupstaging/_data/last-success \
R1_RECOVERY_MODE=self-managed \
R1_CONSOLE_HEALTH_URL=https://console.giatlasachcong.lan:8443/healthz \
  uv run python scripts/check_shop_operations.py
```

Three of these were wrong until the adversarial round and each failure was silent:

- **`R1_CONSOLE_HEALTH_URL` pointed at `http://127.0.0.1:8000/healthz`**, which nothing serves:
  `api` publishes no ports and sits only on `internal: true` networks, so the check the runbook
  calls "the console is the business" would have read `passed=False` every five minutes forever.
  The console is reached through the proxy, by its own name, exactly as a tablet reaches it.
- **`R1_RECOVERY_MODE` is required on the self-managed branch.** `archive_mode = off` used to
  return a green tick on both branches; undeclared is now a refusal rather than a guess.
- **`R1_BASE_BACKUP_MARKER` is new**: there was a WAL check and no base-backup check, and a WAL
  chain restores nothing on its own.

Five checks, every five minutes from a host scheduler.

### The base backup, daily

`archive_command` ships WAL continuously from inside the database container; the base backup those
segments are replayed onto is taken by the host scheduler against the same container. There is no
sidecar service — `DEC-026` says there is one, and there never was.

```bash
# 02:30 daily, on the host's crontab.
30 2 * * * cd /srv/nha-trang-laundry && docker compose -f compose.r1.yaml \
  --profile self-managed-database exec -T postgres /usr/local/bin/base-backup.sh
```

It stages and verifies the artifact before anything leaves the host, then writes the marker
`R1_BASE_BACKUP_MARKER` above reads. A failure exits non-zero and uploads nothing, so cron mail is
the floor and the `base_backup_age` check is the ceiling.

 Non-zero exit means at least one failed, and
each one also emits a structured line to stdout — which, since `SHOP-OBSERVABILITY-001`, actually
reaches a stream. Before that every `record()` call in the API was a no-op in the container.

Of the seven paging conditions in `specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §4.1, five have no
referent here: there is no channel, no sender, no provider and no agent cell, so there is nothing to
be unsure about, nothing to suppress, no token to refresh and no cell to watch. The four that do
matter are:

| Check | Why it matters here |
|---|---|
| WAL archive gap | The recovery guarantee is silently gone. Nothing else in the system looks at the age of the last archive. |
| Database volume | **This is how a correctly configured archiver closes the shop.** A failing `archive_command` pins WAL segments, the disk fills, PostgreSQL refuses writes, and the counter cannot take an order. |
| Capability flags | A flag reading true on a *running container* without a signed manifest in `releases/gates/`. `report_delivery_status.py` reads repository YAML and cannot see a host. §4.1 calls this a security incident. |
| Console reachable | In R1 the console is the business. |

**There is no metrics collector and that is deliberate.** No exporter is declared, so the
OpenTelemetry instruments record into a no-op provider; the record is the structured stdout stream
plus these exit codes. `MONITORING-001` adds a collector at the AI stage, where there is more to
watch than a shop counter.

**Delivery to a person is `DEC-025`, decided 2026-09-03: one Telegram message to the owner.** Two
environment values switch it on, and until they are set the checks still exit non-zero for the host
scheduler, which is the floor the decision kept:

```bash
export R1_ALERT_TELEGRAM_TOKEN_FILE=/run/secrets/alert_telegram_token
export R1_ALERT_TELEGRAM_CHAT_ID=...        # the owner's chat, one recipient
```

Three properties of it that matter more than the mechanism. It is a **separate bot** from any
future customer bot, with **no inbound handler** and one hardcoded recipient. It posts directly
over HTTPS from the check script — never through the outbox, the channel adapter or the consent
machinery — so it is not an automated send, and `FEATURE_AUTOMATED_SENDS_ENABLED` stays false and
stays meaningful. And a failed alert never masks the failure it was carrying: the exit code and the
structured line stand on their own.

The WAL gap, the volume and the capability flags alert at any hour. Console-unreachable alerts
between 07:00 and 21:00 only: a console down at 03:00 that is back before opening needs nobody
woken, and one down at 07:45 needs everybody.

**The backup key is `DEC-026`, decided the same day.** Generate the `age` key pair **on your own
device** — not on this host and not through an agent. The public half becomes the
`r1_backup_encryption_recipients` secret; the private half goes into your password manager, with one
offline copy stored physically away from both the shop and this server. Nothing on the host can
decrypt what it archives, which is the point. **Lose both copies and every backup is permanently
unrecoverable** — no vendor to call, by design.

## 8. If it goes wrong

Rollback is `docker compose -f compose.r1.yaml down` and restoring the previous image tag.
**Migrations are forward-only and do not roll back.** `0031` in particular drops a CHECK constraint
that cannot be restored while any row written since would violate it, so a rollback past it needs a
new forward migration — see `evidence/delivery-loop/QUOTE-ACCEPT-001.yaml`.

There is no tested restore path for the database itself. That is `BACKUP-RESTORE-001` and it is the
first thing to fix after the shop is running.
