# Runbook — deploy today

**Written:** 2026-09-03 · **Corrected:** 2026-09-06 · **Status:** the commands that need no
host have been run, and after the adversarial round that claim is true. It was made before,
and three of the invocations on this page were argument-parsing failures that needed no host
to discover. `packages/evals/tests/test_backup_restore_contract.py` now checks every flag any
runbook passes to a script against that script's own arguments.

`production-deploy-day.md` is the reference: it explains each step and why it exists. **This page is
the order to do them in, on one afternoon, with nothing left to decide.** Where the two disagree,
check both against `compose.r1.yaml` and the script's own `--help` before believing either. The
blanket "that one is right and this one is stale" was itself wrong on the secret list — this page
had all fourteen and that one was missing two, including the one without which Keycloak never
starts — so it sent the operator to the list that could not bring the stack up.

Roughly three hours if the host is ready, most of it waiting. The two long poles are the certificate
trust on tablets and the restore drill.

---

## Before you touch a terminal — the two things only you can do

**1. The machine.** FPT Cloud `STANDARD-02` — 4 vCPU, 8 GB RAM, 100 GB SSD, in Vietnam
(`DECISION-HOSTING-001`). Their pricing page quotes by sales, so start that now; if it will not
land today, take Vultr or DigitalOcean Singapore, ~US$48/month, and migrate before a channel goes
live. Migrating is the restore drill in §7 pointed at a different machine — it is a rehearsed
procedure, not an escape hatch.

**2. The backup key.** Generate it on **your own laptop**, never on the server:

```bash
age-keygen -o ~/laundry-backup-identity.txt        # keep this. Losing it loses every backup.
grep 'public key' ~/laundry-backup-identity.txt    # this half goes to the server
```

Private half into your password manager **and** one offline copy stored away from both the shop and
the server (`DEC-026`). Only the public half ever reaches the host, which is why nothing on the
server can read its own archive — proven, not assumed: a 16 MiB WAL segment archives to 211 KiB,
round-trips byte-identical, and the recipient key on the host **cannot** decrypt it.

Also needed, and none is engineering: object storage in a **different** failure domain from the host
with a credential that **cannot delete**; a DNS record for the console name; and a TLS certificate
for it. The hostname is internal, so no public CA can issue — see §3.

---

## 1. The host, locked down first

```bash
ssh root@<host>
adduser --disabled-password --gecos "" laundry && usermod -aG sudo laundry
# copy your key to ~laundry/.ssh/authorized_keys, then:
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/; s/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh

ufw default deny incoming && ufw default allow outgoing
ufw allow from <your-admin-ip> to any port 22 proto tcp
ufw allow from <shop-lan-or-vpn-cidr> to any port 8443 proto tcp
ufw enable
```

**Port 8443 is never open to the world.** ADR-0007 §1: staff reach the console over the shop network
or a VPN, never a public hostname. The compose file defaults its bind address to loopback so an
unset variable produces a console nobody can reach rather than one everybody can — §4 sets it.

Install Docker Engine plus the compose plugin from Docker's own repository, then log out and back in.

## 2. The code

```bash
git clone <repo> /opt/laundry && cd /opt/laundry
docker compose -f compose.r1.yaml --profile self-managed-database build
```

Five images: API, worker, Caddy, Keycloak, and PostgreSQL with `age`, `gzip` and `rclone` pinned
in for the archiving. Keycloak is the slowest and was omitted from this count; start the build
before you make coffee, not after.

## 3. The certificate, and the step that catches everyone

The console hostname is internal, so **no public CA can issue for it**. Make a private CA once, on
your laptop:

```bash
openssl req -x509 -newkey rsa:4096 -days 3650 -nodes -keyout ca.key -out ca.crt \
  -subj "/CN=Giat La Sach Cong Internal CA"
openssl req -newkey rsa:2048 -nodes -keyout console.key -out console.csr \
  -subj "/CN=console.giatlasachcong.lan"
printf "subjectAltName=DNS:console.giatlasachcong.lan" > san.cnf
openssl x509 -req -in console.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -extfile san.cnf -out console.crt
```

Then, and this is the part that turns into a mysterious morning if it is skipped:

- **DNS** on the shop router must resolve `console.giatlasachcong.lan` to the host. An iPhone or iPad
  has no `/etc/hosts`, so it must be DNS.
- **`ca.crt` installed *and explicitly trusted* on every device.** On iOS that is two separate steps
  — install the profile, then Settings → General → About → **Certificate Trust Settings** and switch
  it on. Miss the second and the console simply will not load, with no useful error, and the service
  worker will not register either.

## 4. Secrets

Fourteen declared in `compose.r1.yaml`, plus one file for alerts that is not a Docker secret.
External Docker secrets: never in the repository, never in an image layer, never in a compose file.

```bash
docker swarm init            # required for `docker secret`, single node is fine

for n in migration api worker; do
  printf '%s' "postgresql://laundry_${n}:<password>@postgres:5432/nha_trang_laundry" \
    | docker secret create r1_${n}_database_url -
done
printf '%s' '<postgres superuser password>' | docker secret create r1_postgres_password -

printf '%s' 'https://console.giatlasachcong.lan:8443/idp/realms/nhatrang' | docker secret create r1_oidc_issuer -
printf '%s' 'staff-console'                                               | docker secret create r1_oidc_audience -
printf '%s' 'http://keycloak:8080/idp/realms/nhatrang/protocol/openid-connect/certs' | docker secret create r1_oidc_jwks_url -
printf '%s' 'acr' | docker secret create r1_oidc_mfa_claim -
printf '%s' 'mfa' | docker secret create r1_oidc_mfa_value -

docker secret create r1_tls_certificate ./console.crt
docker secret create r1_tls_private_key ./console.key
printf '%s' '<keycloak db password>'    | docker secret create r1_keycloak_database_password -

printf '%s' 'age1...'                   | docker secret create r1_backup_encryption_recipients -
docker secret create r1_backup_repository_credential ./rclone.conf
```

`r1_keycloak_bootstrap_admin_password` used to be created here and is not any more: no compose file
mounts it, nothing reads it, and it was a live admin credential sitting in the swarm store for no
reason. `SHOP-FIRST-START-001` removed the mount and this line outlived it. The admin is created
interactively instead — `production-deploy-day.md` §2a step 0.

`r1_backup_repository_credential` is an **rclone configuration file**, not a bare token: it is what
`RCLONE_CONFIG` points at inside the postgres container, and the credential in it must be able to
write and must not be able to delete (`DEC-026`).

`oidc_mfa_claim=acr` and `oidc_mfa_value=mfa` are not arbitrary. Measured against the real Keycloak
with a real browser: `amr` is absent entirely and `acr` is the string `mfa` once the realm's
level-of-authentication condition runs. Bind the wrong one and **every** privileged sign-in fails
with a generic 401 that looks exactly like a bad password.

## 5. Bring it up


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
export R1_CONSOLE_HOST=console.giatlasachcong.lan
export R1_CONSOLE_BIND_IP=<the shop-LAN or VPN address of this host>   # never 0.0.0.0
export R1_BACKUP_REPOSITORY=<s3://bucket/laundry>
export R1_BACKUP_UPLOAD_COMMAND=<your object-store client wrapper>

docker compose -f compose.r1.yaml --profile self-managed-database up -d postgres
docker compose -f compose.r1.yaml exec postgres psql -U laundry_migrate -d postgres -c "
  CREATE ROLE laundry_api    LOGIN PASSWORD '<...>';
  CREATE ROLE laundry_worker LOGIN PASSWORD '<...>';
  CREATE ROLE laundry_backup LOGIN REPLICATION PASSWORD '<...>';
  CREATE ROLE keycloak       LOGIN PASSWORD '<...>';
  CREATE DATABASE keycloak OWNER keycloak;"

docker compose -f compose.r1.yaml up -d migrate          # runs once and exits; must exit 0
docker compose -f compose.r1.yaml --profile self-managed-database up -d api worker keycloak tls

SUPERUSER_DATABASE_URL=... uv run python scripts/apply_demo_grants.py
./scripts/shop-admin verify_database_grants.py
```

**`apply_demo_grants.py` is the production grant script despite its name, and it must run after
every migration** — `GRANT ... ON ALL TABLES` is a one-shot snapshot, and skipping it surfaces as
`permission denied` on a write path days after the deploy that caused it.

## 6. The shop's own records

```bash
./scripts/shop-admin bootstrap_store.py --name 'Giặt Là Sạch Cộng — 3A Lê Đại Hành'
./scripts/shop-admin bootstrap_owner.py --oidc-subject '<your Keycloak user id>' --display-name 'Chủ tiệm'
./scripts/shop-admin publish_pricebook.py --actor-id '<owner staff uuid>'
```

Staff accounts are created in Keycloak first (`production-deploy-day.md` §2a) — each person sets
their own TOTP on first sign-in, and the second factor is required for everyone because roles live
only in the database and the issuer cannot know who is privileged. **`OWNER_ADMIN` is not implicitly
a member of the store**: assign yourself from the console once you are in.

**Until the pricebook is published, `POST /quotes` answers 503 by design.** The shop cannot quote.

## 7. The restore drill — before staff, not after

```bash
# `--expect-host` does not exist; the arguments are --base-url and --ca-file, both required.
uv run python scripts/staging_smoke.py \
  --base-url https://console.giatlasachcong.lan:8443 --ca-file ./ca.crt
./scripts/shop-admin verify_database_grants.py
uv run python scripts/report_delivery_status.py     # all 13 must read NOT_AUTHORIZED
```

Then `docs/runbooks/restore-drill.md`, timed, on a clean machine. **`BACKUP-RESTORE-001` completes
on a drill result and never on configuration**, and a backup that has never been restored is a
belief. Start the clock when you start fetching the key from wherever `DEC-026` put it — that
handover is inside the four hours, and a drill that begins with the key already in hand measures a
restore that will never happen that way.

## 8. Watch it

The alert token is a plain file on the host, readable only by whoever runs cron. It is not a
Docker secret because nothing in a container reads it — `check_shop_operations.py` runs on the
host. Create it before the first run, or alerts are silently never delivered:

```bash
install -m 0600 /dev/null /etc/nha-trang-laundry/alert-telegram-token
printf '%s' '<bot token from @BotFather>' > /etc/nha-trang-laundry/alert-telegram-token
```

```bash
DATABASE_URL=... R1_PGDATA_PATH=/var/lib/docker/volumes/nha-trang-laundry-shop_pgdata/_data \
R1_BASE_BACKUP_MARKER=/var/lib/docker/volumes/nha-trang-laundry-shop_pgbackupstaging/_data/last-success \
R1_RECOVERY_MODE=self-managed \
R1_CONSOLE_HEALTH_URL=https://console.giatlasachcong.lan:8443/healthz \
  R1_CONSOLE_CA_FILE=/srv/nha-trang-laundry/.shop/ca/ca.crt \
R1_ALERT_TELEGRAM_TOKEN_FILE=/etc/nha-trang-laundry/alert-telegram-token \
R1_ALERT_TELEGRAM_CHAT_ID=<your chat> \
  uv run python scripts/check_shop_operations.py
```

Every five minutes from cron. **Five** checks now, and the one most likely to save you is the
volume: a failing `archive_command` pins WAL segments forever, the disk fills, PostgreSQL stops
accepting writes, **and the counter cannot take an order.**

`R1_CONSOLE_HEALTH_URL` said `http://127.0.0.1:8000/healthz` until the adversarial round. Nothing
serves that: `api` publishes no ports and sits only on `internal: true` networks, so the check this
runbook calls the most important one would have failed every five minutes forever. It goes through
the proxy, by the console's own name, exactly as a tablet reaches it.

Alerts are quiet 21:00–07:00 **shop time** for console-unreachable only; everything else wakes you
at any hour. That comparison was made in UTC until the adversarial round, which inverted the window
by seven hours — an outage at 07:45 as the shop opened was suppressed.

## 9. One real transaction, by hand, before anyone else touches it

Tiếp nhận → *Phát phiếu* → a number appears. Báo giá → weigh, pick the service, *Khách tự mang tới
và tự lấy* → a total. *Khách đã chốt giá*. Create the order, walk it intake → production → released,
then on **Đơn hàng** use *Chuyển trạng thái đơn*: Nhận đồ to `Đã nhận, chờ kiểm` then `Đã nhận`,
Thương mại to `Đang chạy`, Sản xuất through to `Đã giao ra`. Record the settlement, and watch it
read `COMPLETED`. Intake first is not a preference: `Đang chạy` is refused until intake is accepted.

That sequence is proven against a live database. **If a step refuses on the real host, the cause is
configuration, not the code path.**

## 10. Say these out loud to whoever runs the counter

- **Delivery orders work.** Paid in full at the counter before the laundry leaves; a named person
  records the leg when it arrives. A failed attempt is a failed leg, nothing is charged, the goods
  come back.
- **A production exception is recoverable.** Record it, deal with the stain, resume — including
  rewashing, which is the only way production moves backwards.
- **Cancelling after work has started asks what happened to the laundry and the money.** There is no
  option for *washed, walked away, no money*: that customer pays and collects, or the laundry stays
  here.
- **No customer is remembered.** A walk-in is a ticket number. Two visits are two unrelated tickets.
- **Nothing is sent to anyone, and the AI does nothing.** No channel is connected and no model has
  ever been invoked. The assistant on the console is deterministic and says so.
