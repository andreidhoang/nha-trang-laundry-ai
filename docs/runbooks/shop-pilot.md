# Runbook — the pilot week, on the shop's own machine

**Written:** 2026-09-06 · **Status:** the stack has been built and validated locally; no shop has
run a week on it yet.

Run the console on a machine in the shop, on the shop's own network, for a week of real trading —
before paying a hosting bill and before committing a business to infrastructure nobody has used.

**This is not a detour, and that is the point.** The week ends by restoring the shop's real data
onto the cloud host, which *is* `docs/runbooks/restore-drill.md`. `BACKUP-RESTORE-001` completes on
a drill result and never on configuration, so the drill has to happen anyway. Doing it as the
migration means it happens once, with real consequences, on data somebody cares about — which is
the only kind of drill that finds anything.

It also starts two clocks that nothing else can start. `SHOP-INSTRUMENT-001` needs ten timed loads,
twenty delivery logs and a measured cost per kilo, and its task packet calls that **four to six
weeks of real operation**. `G2` needs thirty completed real orders. Neither can be simulated, and
both begin on day one of this week rather than after a cloud deploy.

---

## What it costs

Nothing, if you already own a computer that can stay switched on.

| | |
|---|---|
| Machine | Anything with **8 GB RAM** (16 GB comfortable) and ~30 GB free. A Mac mini, an old laptop, a small desktop. It must stay awake — disable sleep, and prefer wired ethernet, because wifi power-saving drops the console mid-shift. |
| Software | Docker Desktop, or Docker Engine on Linux. |
| Network | A DHCP reservation so the machine keeps one address, and a DNS entry on the router for the console name. |
| Off-site archive | Cloudflare R2's free tier: **10 GB storage, 1 million writes a month, no egress charge, no expiry.** The compressed archive is around a gigabyte for the full 35-day retention and roughly 4,400 writes a month, so the shop fits inside the free tier permanently rather than for a trial. |

The stack is about 3 GB of RAM at rest: PostgreSQL, Keycloak, the API, the worker and the proxy.

## What it gives you that the cloud does not

- **Residency is not a question.** The data is physically in the shop, in Vietnam. `DEC-027` and
  `DECISION-HOSTING-001` both go quiet for the week.
- **ADR-0007 §1 is satisfied more strictly, not less.** The requirement is that the console is
  unreachable from the internet and staff reach it over the shop network. On a shop machine behind
  a router with no port forwarding, that is true by construction rather than by firewall rule.
- **A rollback that takes ten seconds.** Turn the machine off and the shop is back on paper, which
  is where it is today. No provider, no contract, no data anywhere else.

## What it does not give you, stated plainly

- **One machine, one building.** Fire, theft or a dropped laptop takes the shop's records with it
  **unless the archive is running.** That is why §3 is not optional and comes before staff, not
  after.
- **A power cut is an outage.** Nha Trang loses power; the shop falls back to paper for the
  duration, which it can, but somebody has to notice. Consider a small UPS — an unclean shutdown is
  survivable (`full_page_writes` is on) but a repeated one is asking for trouble.
- **No 24/7 anything.** Nobody is trading at 03:00, so this matters less than it sounds.

---

## 1. The backup key, first, on your own laptop

Not on the shop machine. `DEC-026` places the private half of this key away from the thing it
protects, and ADR-0007 §3 requires encryption "with a key that is not stored on Host A".

```bash
age-keygen -o ~/laundry-backup-identity.txt
grep 'public key' ~/laundry-backup-identity.txt        # this half, and only this half, travels
```

Private half into your password manager **and** one offline copy kept away from the shop.
**Lose both and every backup is permanently unrecoverable** — no vendor to call, by design.

## 2. Prepare the machine

```bash
git clone <repo> ~/laundry && cd ~/laundry
uv run python scripts/bootstrap_shop_local.py --backup-recipient 'age1...'
```

That writes the database passwords, the OIDC settings and a private CA with a certificate for
`console.giatlasachcong.lan`, all `0600` under `.shop/`, which is gitignored before it exists. It
refuses to overwrite anything, so re-running after the shop has been trading cannot rotate a
password out from under a live system. It prints the `CREATE ROLE` statements for §4.

Then the two steps that turn into a mysterious morning if they are skipped:

- **DNS.** Point `console.giatlasachcong.lan` at this machine on the shop router. An iPad has no
  `/etc/hosts`, so it has to be DNS.
- **`.shop/ca/ca.crt` on every tablet, installed *and separately trusted*.** On iOS that is two
  actions: install the profile, then Settings → General → About → **Certificate Trust Settings** and
  switch it on. Miss the second and the console will not load, with no useful error, and the
  offline service worker will not register either.

## 3. The archive, before anyone trades

Create an R2 bucket and a token that can **write and read but not delete** — the archive command
writes `--if-not-exists` so a compromised machine cannot destroy history, and a delete-capable
credential quietly undoes that.

The credential is an **rclone configuration file**, because that is what the shipped upload,
list and fetch scripts read. `<your rclone/aws wrapper>` used to appear here with no step that
produced a wrapper, and the postgres image had no object-store client at all to wrap — so the
archiving this section calls the point the pilot's safety rests on could not run.

```bash
cat > .shop/secrets/backup_repository_credential <<'CONF'
[archive]
type = s3
provider = Cloudflare
access_key_id = <key>
secret_access_key = <secret>
endpoint = <account>.r2.cloudflarestorage.com
CONF
chmod 600 .shop/secrets/backup_repository_credential

export R1_BACKUP_REPOSITORY='archive:laundry-archive'
# Defaulted in compose.r1.yaml; set it only to substitute a different client.
# export R1_BACKUP_UPLOAD_COMMAND=/usr/local/bin/r1-archive-upload.sh
```

The key that writes the archive must not be able to delete from it (`DEC-026`). On R2 that is a
token scoped to Object Read & Write on this one bucket, with the bucket's own lifecycle rules doing
any expiry.

**Do not skip this and start trading.** One machine with no archive is one accident away from
losing a week of the shop's money records, and the customer's paper ticket is not a substitute for
the settlement.

## 4. Bring it up

```bash
export R1_CONSOLE_HOST=console.giatlasachcong.lan
export R1_CONSOLE_BIND_IP=<this machine's LAN address>     # never 0.0.0.0

C="-f compose.r1.yaml -f compose.shop-local.yaml --profile self-managed-database"
docker compose $C build
docker compose $C up -d postgres
docker compose $C exec postgres psql -U laundry_migrate -d postgres   # paste the CREATE ROLEs
docker compose $C up -d migrate                                       # must exit 0
docker compose $C up -d api worker keycloak tls

# `postgres` publishes no port and sits only on internal networks, so nothing on the host can
# connect to it, and the API image carries no `scripts/` directory. Both of those are deliberate.
# The SQL therefore arrives on stdin -- `docker cp` also fails, because the root filesystem is
# read-only. `scripts/emit_shop_database_setup.py` prints exactly the statements to pipe, reading
# the passwords back from `.shop/secrets/` so they match what the containers will present.
uv run python scripts/emit_shop_database_setup.py \
  | docker compose -f compose.r1.yaml -f compose.shop-local.yaml \
      --profile self-managed-database exec -T postgres \
      psql -U laundry_migrate -d nha_trang_laundry -v ON_ERROR_STOP=1

# Keycloak and the worker fail to authenticate until the roles above exist, so restart them once.
docker compose -f compose.r1.yaml -f compose.shop-local.yaml \
  --profile self-managed-database restart worker keycloak
```

`compose.shop-local.yaml` changes exactly one thing: where the secrets are read from. A shop laptop
has no Docker Swarm, so `external: true` cannot work and files are used instead — which ADR-0007 §5
admits by name, "a file-based store with restrictive ownership" qualifying alongside a provider's
secret store. A contract test asserts the overlay changes nothing else, so the week rehearses the
deployment rather than resembling it.

**It is not `compose.demo.yaml`.** That file seeds synthetic staff holding `OWNER_ADMIN` and wires a
development identity provider that mints a token for any subject asked of it. Running a real shop on
it would be handing out owner access.

## 5. The shop's own records

Staff accounts in Keycloak first (`production-deploy-day.md` §2a) — everyone sets their own TOTP on
first sign-in. Then:


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
./scripts/shop-admin bootstrap_store.py --name 'Giặt Là Sạch Cộng — 3A Lê Đại Hành'
# Write the printed identifier down. Re-running without `--store-id` mints a *second* shop, and a
# pilot week is exactly when a step gets run twice.
./scripts/shop-admin bootstrap_owner.py --oidc-subject '<your Keycloak user id>' --display-name 'Chủ tiệm'
./scripts/shop-admin publish_pricebook.py --actor-id '<owner staff uuid>'
```

`OWNER_ADMIN` is not implicitly a member of the store — assign yourself from the console. Until the
pricebook is published, `POST /quotes` answers 503 by design and the shop cannot quote.

Then one whole transaction by hand, before any customer: ticket → quote → *khách đã chốt giá* →
order → intake → production → released → settlement → `COMPLETED`.

## 6. The week

Cron every five minutes, from day one:

```bash
DATABASE_URL=... R1_PGDATA_PATH=<the pgdata volume path> \
R1_BASE_BACKUP_MARKER=<the pgbackupstaging volume path>/last-success \
R1_RECOVERY_MODE=self-managed \
R1_CONSOLE_HEALTH_URL=https://console.giatlasachcong.lan:8443/healthz \
  uv run python scripts/check_shop_operations.py
```

`http://127.0.0.1:8000/healthz` was here and nothing serves it — `api` publishes no ports and is
only on internal networks — so the console check would have read failed every five minutes of the
pilot. And add the daily base backup to cron alongside it, or the WAL being archived has nothing
to be replayed onto:

```bash
30 2 * * * cd <repo> && docker compose -f compose.r1.yaml -f compose.shop-local.yaml \
  --profile self-managed-database exec -T postgres /usr/local/bin/base-backup.sh
```

The volume check is the one most likely to save the week: a failing `archive_command` pins WAL
segments, the disk fills, PostgreSQL stops accepting writes, **and the counter cannot take an
order.** On a laptop with 30 GB free that arrives faster than on a server.

While trading, collect what only real operation can produce, because it is the long pole on
everything after this:

- **ten timed wash-and-dry loads** — start to finish, wall clock
- **twenty delivery legs** with distance and what the trip actually cost
- **cost per kilo**, measured rather than assumed
- the **safe capacity** of each machine, in kilos, confirmed by whoever runs it

That is `SHOP-INSTRUMENT-001`, and its packet is explicit that it cannot be simulated or back-filled.

## 7. Ending the week — which is the restore drill

Do not copy files to the cloud host. **Restore onto it**, following
`docs/runbooks/restore-drill.md`, and time it.

```
start the clock when you start fetching the backup key from wherever DEC-026 put it
restore onto the cloud host from the R2 archive
validate:  printf '%s' 'postgresql://.../restored' > /tmp/restore-dsn && chmod 600 /tmp/restore-dsn
           uv run python scripts/validate_restore_drill.py --evidence drill.json \
             --database-url-file /tmp/restore-dsn
```

The validator checks what can be checked rather than attested: quote snapshots recomputed and
hash-identical, no aggregate whose event count falls short of its highest version, the restored
database reaching the recovery point it claims, and no duplicate send. Environment reads
`PRODUCTION`.

If it passes, you have simultaneously migrated the shop, satisfied `BACKUP-RESTORE-001`, and learned
your real recovery time on data that matters. If it needs improvisation it is a **failed drill** —
correct the runbook and run it again, on the machine that is still working, with the shop still
trading. That is the cheapest possible place to find out.

## 8. What to tell whoever runs the counter

The same four things as any other day of this system, plus one:

- **If the console does not load, use paper and tell me.** The machine is in the shop; it is not
  broken forever, and nothing is lost while it is off.

The rest is unchanged: delivery orders are paid in full at the counter; a production exception is
recorded and recovered from rather than worked around; cancelling after work has begun asks what
happened to the laundry and the money, and there is no option for *washed, walked away, no money*;
no customer is remembered beyond a ticket number; and nothing is sent to anyone, because no channel
is connected and no model has ever been invoked.
