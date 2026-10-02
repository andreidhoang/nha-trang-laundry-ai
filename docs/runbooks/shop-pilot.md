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
`console.giatlasachcong.lan`, all `0600` under `.shop/`, which is gitignored before it exists.
The CA is **name-constrained** to that host (a tablet told to trust it still rejects a certificate
it signs for any other site), and **its private key is never kept** (`DEC-052`): it signs the
console certificate in memory and is dropped — not written to this machine, not exported anywhere.
`--export-ca-key` and `--ca-key` are refused, with this reason, whatever is already on disk. Every
run reads `.shop/ca/ca.crt` and says what it is: a CA an earlier version made has no name
constraints and is reported as **"CA cũ không giới hạn tên miền — hãy tạo lại"**; a `.shop/ca/ca.key`
left by an earlier version is reported too. The script refuses to overwrite anything, so re-running
after the shop has been trading cannot rotate a password out from under a live system. It prints
the `CREATE ROLE` statements for §4.

**Renewal, and when.** The console certificate lives 825 days (the most an iPad accepts). From 60
days before it expires the till's hourly check (`checks-daily`, §6: `check_shop_operations.py
--check certificate`) sends the owner one Telegram message a shop day, the first hour from 07:00
the till is awake ("chứng chỉ console hết hạn ngày … (còn N ngày)"); it remembers the day it told
in `--certificate-notice-state`, and it never sends at night (`DEC-025`). Every run of this script
says so too. Then:

```bash
uv run python scripts/bootstrap_shop_local.py --backup-recipient 'age1...' --new-ca
docker compose -f compose.r1.yaml -f compose.shop-local.yaml -f compose.shop-till.yaml \
  --profile self-managed-database up -d --force-recreate tls   # serve the new certificate
```

`--new-ca` moves the old `ca.crt` and the console's certificate and key to `.shop/retired/<time>/`
(and deletes a legacy `ca.key` outright), mints a new CA and certificate, and changes nothing else.
Install the new `.shop/ca/ca.crt` on **every** tablet and switch trust on, exactly as below (and in
the till Mac's keychain, `shop-till-mac.md`); once
every tablet opens the console, delete `.shop/retired/<time>/` and remove the old CA profile from
each tablet. To undo before then, move the three retired files back. The new CA lives only as long
as its one certificate (plus a month), so an old CA left on a tablet stops being trusted by itself.

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
# the passwords back from `.shop/secrets/` so they match what the containers will present. The
# worker's grant is its own narrow set (role_grants.WORKER_GRANTS), not the API's; re-piping it on
# an existing shop narrows a worker provisioned under the old grant.
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

Cron every five minutes, from day one.

**The paths below are the checkout from §"The code" — `~/laundry`, written `$HOME/laundry` because
that is what a crontab expands.** They used to read `/srv/nha-trang-laundry`, a directory no step on
this page creates, so both entries failed on `cd` from the first run and said so nowhere: cron mails
its output to a local mailbox nobody on this machine reads. If you cloned somewhere else, change all
four occurrences, not the two obvious ones.

**The checks split, because the things they look at live in different places.** Three of them read
the database and its volumes, which on the self-managed branch nothing on the host can reach; two of
them need Docker and the console's TLS name, which nothing inside the network has. That is two cron
entries, not one, and the split is a consequence of the topology rather than a preference.

**Both entries go through `scripts/relay_shop_alert.py`, and the data checks must.** They run on
`database-private`, which is `internal: true` and has no route to Telegram, so a check that posts
its own alert from in there fails every time -- and this page used to pass it the Telegram settings
as if it could (`SHOP-ALERT-DELIVERY-001`). Now the check prints its alert as one JSON line
(`--emit-alert`) and sends nothing; the relay, on the host, delivers it with the credentials in
`.shop/secrets/alert_telegram_token` and `.shop/secrets/alert_telegram_chat_id` (how to make them:
`shop-till-mac.md` §5). A delivery that fails is exit 3 and a line in `alert-delivery.log`, and
cron mails that too.

The database URL reaches the container on **stdin** (`--stdin-file`, `-i`, `--database-url-stdin`).
It used to be `-e DATABASE_URL="$(cat …)"`, which put the migration identity's password in `ps` and
`docker inspect` (`OPS-HARDENING-002`).

```bash
# Every five minutes. The data checks, inside the database's own network, as the postgres uid --
# the base-backup marker lives in a 0700 directory owned by it.
*/5 * * * * cd $HOME/laundry && \
  R1_ALERT_TELEGRAM_TOKEN_FILE=$HOME/laundry/.shop/secrets/alert_telegram_token \
  R1_ALERT_TELEGRAM_CHAT_ID_FILE=$HOME/laundry/.shop/secrets/alert_telegram_chat_id \
  R1_ALERT_LOG_FILE=$HOME/laundry/.shop/alert-delivery.log \
  .venv/bin/python scripts/relay_shop_alert.py --label checks-data \
  --stdin-file .shop/secrets/migration_database_url -- \
  docker run --rm -i \
  --network nha-trang-laundry-shop_database-private --user 70:70 \
  -v "$PWD:/repo:ro" -w /repo -e HOME=/tmp \
  -v nha-trang-laundry-shop_pgdata:/pgdata:ro \
  -v nha-trang-laundry-shop_pgbackupstaging:/staging:ro \
  -e R1_RECOVERY_MODE=self-managed \
  -e R1_PGDATA_PATH=/pgdata -e R1_BASE_BACKUP_MARKER=/staging/last-success \
  --entrypoint python nha-trang-laundry-api:local \
  scripts/check_shop_operations.py --database-url-stdin \
  --check wal --check base --check volume --check outbox --emit-alert

# Every five minutes. The host checks: capability flags need the Docker socket, and the console
# check has to reach the console the way a tablet does, by name, over TLS -- and asks `/readyz`,
# which answers 503 when the console is up and its database is not.
*/5 * * * * cd $HOME/laundry && \
  R1_ALERT_TELEGRAM_TOKEN_FILE=$HOME/laundry/.shop/secrets/alert_telegram_token \
  R1_ALERT_TELEGRAM_CHAT_ID_FILE=$HOME/laundry/.shop/secrets/alert_telegram_chat_id \
  R1_ALERT_LOG_FILE=$HOME/laundry/.shop/alert-delivery.log \
  .venv/bin/python scripts/relay_shop_alert.py --label checks-host -- \
  env R1_CONSOLE_HEALTH_URL=https://console.giatlasachcong.lan:8443/readyz \
  R1_CONSOLE_CA_FILE=$HOME/laundry/.shop/ca/ca.crt \
  R1_APP_SIGNAL_CURSOR=$HOME/laundry/.shop/app-signal-cursor.json \
  .venv/bin/python scripts/check_shop_operations.py --check flags --check console \
  --check app --app-logs $HOME/laundry/.shop/logs/api/api.jsonl --emit-alert

# Every hour; one message a shop day. The console certificate: from 60 days before it expires,
# the first run from 07:00 tells the owner to renew, and the notice record keeps the rest of the
# day's runs quiet (DEC-052; the renewal is §2's --new-ca). Hourly rather than once at 09:00:
# a single daily run that lands at night is held by DEC-025 and nothing would run again that day.
0 * * * * cd $HOME/laundry && \
  R1_ALERT_TELEGRAM_TOKEN_FILE=$HOME/laundry/.shop/secrets/alert_telegram_token \
  R1_ALERT_TELEGRAM_CHAT_ID_FILE=$HOME/laundry/.shop/secrets/alert_telegram_chat_id \
  R1_ALERT_LOG_FILE=$HOME/laundry/.shop/alert-delivery.log \
  .venv/bin/python scripts/relay_shop_alert.py --label checks-daily -- \
  .venv/bin/python scripts/check_shop_operations.py --check certificate \
  --console-certificate $HOME/laundry/.shop/secrets/tls_certificate \
  --certificate-notice-state $HOME/laundry/.shop/certificate-notice.json --emit-alert
```

**An alert that could not be sent is sent again** (`PLATFORM-RESIDUAL-009B` L4). The relay keeps
it in `alert-pending-<label>.json` — in `R1_ALERT_PENDING_DIRECTORY`, or beside
`R1_ALERT_LOG_FILE` when that is not set — and every later run, passing or failing, sends it under
"Cảnh báo trước đó chưa gửi được:" with the time it was first raised and how many sends failed,
until one gets through; then the file is gone. The same alert failing run after run is one entry,
not a pile. Up to 24 wait (two hours); past that the oldest are dropped and the next message says
how many. A kept **console** alert keeps `DEC-025`'s hours: between 21:00 and 07:00 it waits, untried,
and goes with the first run from 07:00 — a console alert raised at 20:50 whose send failed does not
page anyone at 02:00. An alert that mixed a console line with an any-hour one is kept as two, so the
any-hour part still goes at once. The oldest kept alert always goes with the next message that can
be sent (clipped to fit if it is very long), so one long alert cannot hold up the rest. A run whose
send fails exits 3; a run that only has console alerts waiting for the morning does not.

**`--check outbox`** reports how many rows the outbox holds (`DEC-051`: record-only rows are kept,
not deleted). Past 1 000 000 rows it **warns** — `WARN outbox_rows` in the schedule's log and a
`WARNING` line in `alert-delivery.log` — without paging anybody: retention is then due to be
decided, and nothing is broken.

**`--check app` is the one that watches the application rather than the machine**
(`OPS-OBSERVABILITY-009`). It reads the API's own structured log from the file the API writes on
the host — `.shop/logs/api/api.jsonl` and its rotated `.1` … `.49`, about 17 busy days in 100 MiB
(`compose.r1.yaml`, where the arithmetic is written out, Docker's own healthcheck included). Not
`docker compose logs api`: Docker deletes that log with the container, and every image update
recreates the container, so it is a live view and not the record (`PLATFORM-RESIDUAL-009B` L4). It
fails when a count reaches its figure. **Each run counts what the previous runs have not**: it
keeps its position in `R1_APP_SIGNAL_CURSOR` (on the till, `~/Library/Application
Support/giatlasachcong/app-signal-cursor.json`, outside the checkout), re-reads the 15 minutes
behind it for lines that arrived late, and knows a line by its event, request id and time rather
than its bytes. So nothing written between two runs is missed, a line that arrives after a newer
one is still counted, a run launchd delayed or skipped is caught up by the next one, and one
incident alerts once. The two rates are judged over any 5 minutes, wherever a run boundary falls. If that
file is ever unreadable the check fails and says so; deleting it makes the next run start from the
last 5 minutes, and anything older than that is then never checked. The figures live in one place,
`APP_SIGNAL_THRESHOLDS` in `scripts/check_shop_operations.py`; a test holds this table to them.

| Signal | Alert at | What it is | What to do |
|---|---|---|---|
| `server_errors` | 1 | an answer 500–599, except a 503 the API wrote `database.request_refused` for (the next row) and `/readyz`'s 503 (the console check's finding, quiet at night by `DEC-025`). A 500 told staff "Máy chủ gặp lỗi. Đừng thử lại": the outcome is unknown. Any other 503 — "staff identity unavailable", "operations unavailable" — is an outage nothing else reports: nobody can sign in, or the API is missing a service it needs | Find the request by time in `.shop/logs/api/api.jsonl` (`grep '"status_code":500'`). A 500: check the order it touched before anyone retries it. A 503 on `/internal/v1/auth/session`: the identity provider; anything else: the API's configuration |
| `database_refusals` | 5 in 5 minutes | `database.request_refused`: the database was busy or unreachable, nothing was written, the console asked staff to retry | One is a normal collision. Five in five minutes: `--check volume`, `--check wal`, then the database container |
| `browser_boundary_rejections` | 10 in 5 minutes | `auth.browser_boundary`: a request refused for its origin or CSRF token | A tab left open across an update makes a few. Ten means something other than the console is sending requests |

It also fails when the API wrote **no line at all** in the last 15 minutes. The console check asks
`/readyz` every five minutes and Docker's healthcheck asks `/healthz` every thirty seconds, so a
healthy API always has lines; none means the log is not reaching the check, and that is not the same
as nothing having gone wrong.

Measured against the running pilot stack: `wal_archive_gap` OK, `database_volume` OK at 14.4% free
of 58 GiB, `base_backup_age` OK at 2.4h, `capability_flags` OK — every flag false on the running
containers. A check named and unable to run now refuses rather than skipping, so a wrong path here
fails loudly on the first tick instead of reading healthy forever.


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

## Running it every day

Everything above gets the shop trading once. This is what makes it keep trading without anybody
thinking about it.

### The machine

| | |
|---|---|
| **Docker must start itself** | Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**. Off by default, and it is the whole game: the containers carry `restart: unless-stopped`, so they come back on their own — but only once Docker is running. Measured on the development Mac: `AutoStart: False` and Docker absent from the login items, which means a power cut leaves the counter with no console until somebody opens an application. |
| **The Mac must not sleep** | System Settings → Lock Screen and Energy → never sleep on power. A sleeping machine is an offline console, and staff will assume the software is broken. |
| **It must log back in by itself** | System Settings → Users & Groups → Automatic login, *if* the machine is somewhere only staff reach. Without it a power cut stops at the login screen and Docker never starts. Weigh that against who can walk up to the machine. |
| **Wired, not wifi** | Wifi power-saving drops the console mid-shift, and the failure looks like the software hanging. |

`restart: unless-stopped` and not `always`, deliberately: if you stop the stack on purpose it stays
stopped, and does not fight you at the next reboot. The cost is that `docker compose stop` survives
a restart — use `start` to bring it back, not a reboot.

### Every morning, in ten seconds

Staff do not need this. You do, once, with coffee:

```bash
docker compose -f compose.r1.yaml -f compose.shop-local.yaml \
  --profile self-managed-database ps
```

Five services, all `Up`. If `postgres` is up and the others are not, the roles or secrets moved; if
everything is up and the console will not load, it is DNS or the certificate on that tablet, not the
software.

### What actually breaks, and what it looks like

| Symptom | Cause, in order of likelihood | What to do |
|---|---|---|
| Console will not load on one tablet | That tablet lost the CA trust, or the router forgot the DNS entry | Re-install `.shop/ca/ca.crt` and switch trust on; check the router |
| Sign-in answers 429 on every tablet at once | The sign-in throttle. **All tablets share one bucket** — they reach the API through Caddy, so the server sees the proxy's address for every one of them. Only *failed* exchanges count; a verified sign-in resets the count. So this means something produced a run of failures: Keycloak restarting while tablets retried, an expired token, or the wrong realm | **Wait it out.** The `Retry-After` header on the 429 says how long, at most five minutes. Do not restart anything: a restart clears the in-memory bucket but also every staff session, which is worse. If it recurs, look at Keycloak rather than at the console |
| Console will not load on *any* tablet | The Mac slept, rebooted without Docker, or lost the network | Wake it; check Docker is running; `... up -d` |
| "Không thể nhận đơn" / writes refused | Disk full — almost always a failing `archive_command` pinning WAL | `--check volume` and `--check wal`; fix the archive credential before clearing anything |
| Nobody can sign in, existing sessions fine | Keycloak is down | It is the issuer, not the console: existing sessions last 8h idle / 24h absolute, so you have time |
| One person cannot sign in | Their authenticator drifted, or their role was never granted | Roles live in `staff_role_assignments`, not the issuer |

The pattern worth internalising: **the console being unreachable and the console being broken look
identical to staff.** The five-minute checks exist so you learn which it is from your phone rather
than from someone shouting across the shop.

### The three things that must be true on any given day

1. `docker compose ... ps` shows five services up.
2. `check_shop_operations.py` exited 0 on its last run — WAL archived within 15 minutes, a base
   backup inside 26 hours, disk above 10%, no capability flag true, console answering, and no
   application signal over its figure.
3. Somebody other than you could do (1) and (2) from this page.

The third is the one that gets skipped and the one that matters on the day you are not there.

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
