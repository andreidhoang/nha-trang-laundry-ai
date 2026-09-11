# Runbook — the pilot week with a Mac as the till

**Written:** 2026-09-10 · **Status:** the topology is built and validated locally; no shop has run a
week on it yet.

One Mac at the counter, staff working on it directly. The console is published on **loopback only**,
so it is reachable from that machine and from nowhere else. This is the shape
`docs/runbooks/shop-pilot.md` describes, minus the network: no LAN address, no router DNS, no
certificate to install on tablets, no port on the shop wifi.

**Read `shop-pilot.md` first for what the pilot week is *for*** — the thirty real orders `G2` needs,
the timed loads `SHOP-INSTRUMENT-001` needs, and why the week ends with a restore drill. This page
is only the difference for a single-till Mac, and it is four things: where the checkout lives, where
the archive goes, how the console name resolves, and how the schedule is run.

---

## 0. Move the checkout out of iCloud, before anything else

**This is the step that decides whether the week works.** If your checkout is under `~/Desktop`,
`~/Documents` or `~/Downloads` and iCloud Drive is on, three separate things break, each of them
intermittently and none of them with a useful error:

- **Docker bind mounts fail.** iCloud evicts files it thinks are idle, leaving a *dataless*
  placeholder. `compose.r1.yaml` mounts `deploy/production/signin` and reads the Caddyfile, the
  PostgreSQL configuration and the three archive scripts from the checkout. A dataless file reads
  fine from the Finder and fails inside a container with `EDEADLK`, so a container that started
  yesterday refuses to start today and nothing in the repository changed.
- **Python imports vanish mid-run.** The file provider sets the BSD `UF_HIDDEN` flag on everything
  inside `.venv/`, and CPython silently skips a hidden `.pth` file. `CLAUDE.md` records the day this
  produced 37 phantom test failures when the true count was zero.
- **The scheduled checks are refused.** macOS TCC blocks a launch agent from reading those three
  directories, with no error a person would recognise.

```bash
# `mv`, not a fresh clone. `.shop/` holds the database passwords the running PostgreSQL already
# has, and it is gitignored -- a clone would arrive without it, `bootstrap_shop_local.py` would
# mint new ones, and nothing would be able to open the database that holds the shop's orders.
mv ~/Desktop/nha-trang-laundry-ai ~/laundry
cd ~/laundry
uv sync --all-packages --all-groups     # a moved virtualenv has stale absolute paths inside it
```

Verify it took:

```bash
uv run python scripts/workspace_env.py --check     # exits non-zero while .pth files are hidden
```

**If any container from the old path is already running, stop it first.** Compose resolves bind
mounts when a container is *created*, so a stack started from the old location keeps pointing there
after the move. It does not fail loudly: the container stays up and the files simply are not there
any more. Measured on 2026-09-11 — PostgreSQL kept running and its `archive_command` began failing
every ten seconds with `sh: /usr/local/bin/archive-wal.sh: not found`, exit 127, while WAL piled up
behind it. `pg_stat_archiver` still read `failed_count = 0`, because the failures land in the log
rather than that counter, so the one number an operator would check looked fine.

```bash
docker compose -f compose.r1.yaml -f compose.shop-local.yaml --profile self-managed-database stop
# ... move, then bring it up from the new path with §2, which recreates every container.
```

Nothing is lost by getting this wrong — the data lives in named volumes, which are not affected by
where the checkout is, and PostgreSQL recovers cleanly from an unclean stop because
`full_page_writes` is on. But the stack has to be **recreated**, not restarted: `docker start` on a
container whose bind-mount source has moved does not repair it.

## 1. The archive drive

Attach it, and make a directory on it. **A different disk from the database** — an archive on the
same SSD as the thing it protects survives neither a dropped laptop nor a failed disk.

```bash
export R1_LOCAL_ARCHIVE_PATH="/Volumes/<your drive>/laundry-archive"
mkdir -p "$R1_LOCAL_ARCHIVE_PATH"
```

The archive is encrypted with `age` to a recipient whose **private half is not on this Mac**
(`DEC-026`). If you have not generated it yet, do it on a different device — your phone's password
manager is a legitimate home for the private half:

```bash
age-keygen -o ~/laundry-backup-identity.txt   # on your OTHER machine
grep 'public key' ~/laundry-backup-identity.txt
```

Lose both copies of the private half and every backup is permanently unrecoverable. There is no
vendor to call; that is the design, not an oversight.

The credential the archive client reads is an rclone configuration. For a local drive it holds no
secret at all:

```bash
cat > .shop/secrets/backup_repository_credential <<'CONF'
[archive]
type = local
CONF
chmod 600 .shop/secrets/backup_repository_credential

export R1_BACKUP_REPOSITORY='archive:/archive'
```

`rclone` is already in the PostgreSQL image and speaks a local filesystem remote natively, so the
shipped `r1-archive-upload.sh` needs no change — only a destination it can see, which is what
`compose.shop-till.yaml` mounts.

## 1b. Check it before you build

```bash
uv run python scripts/preflight_shop_till.py
```

Eight checks, none of which changes anything and none of which reads a secret. It exists because
six of the steps on this page fail in ways that do not name themselves — a bind mount to a missing
directory becomes an empty root-owned one and every archive write fails; an untrusted certificate
authority makes the console refuse to load with no error a person would recognise.

The exit status is the number of blocking problems, so it composes:

```bash
uv run python scripts/preflight_shop_till.py && docker compose $C up -d --build
```

One check it deliberately cannot make: whether the archive credential is the *local* remote. It
reads no secrets, so it can only say the file is there. If you have used this checkout against an
object store before, replace it with the two lines in §1.

## 2. Bring it up

```bash
cd ~/laundry
export R1_CONSOLE_HOST=console.giatlasachcong.lan
export R1_CONSOLE_BIND_IP=127.0.0.1          # the default; stated so it is a decision
export R1_LOCAL_ARCHIVE_PATH="/Volumes/<your drive>/laundry-archive"

C="-f compose.r1.yaml -f compose.shop-local.yaml -f compose.shop-till.yaml --profile self-managed-database"
docker compose $C up -d --build
```

Then the database roles, exactly as `shop-pilot.md` §4 describes — `postgres` publishes no port and
the API image carries no `scripts/` directory, so the SQL arrives on stdin:

```bash
uv run python scripts/emit_shop_database_setup.py \
  | docker compose $C exec -T postgres psql -U laundry_migrate -d nha_trang_laundry -v ON_ERROR_STOP=1
docker compose $C restart worker keycloak
```

## 3. Two commands that need `sudo`, once

The console is served over TLS by its own name even on loopback, so that moving to tablets later is
a configuration change rather than a rebuild. Two things have to know about that name on this Mac:

```bash
# The name resolves to this machine. A tablet would need router DNS; a till needs one line.
echo "127.0.0.1 console.giatlasachcong.lan" | sudo tee -a /etc/hosts

# Safari and Chrome trust the private CA that issued the console's certificate.
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain ~/laundry/.shop/ca/ca.crt
```

Without the second, the console will not load and the offline service worker will not register,
with no error that explains why.

Check it:

```bash
curl --cacert .shop/ca/ca.crt https://console.giatlasachcong.lan:8443/healthz
open https://console.giatlasachcong.lan:8443/staff/
```

## 4. The shop's own records

Staff accounts in Keycloak first (`production-deploy-day.md` §2a) — everyone sets their own TOTP on
first sign-in, including you. Then:

```bash
./scripts/shop-admin bootstrap_store.py --name 'Giặt Là Sạch Cộng'
# Write the printed identifier down. Re-running without --store-id mints a SECOND shop, and a
# pilot week is exactly when a step gets run twice.
./scripts/shop-admin bootstrap_owner.py --oidc-subject '<your Keycloak user id>' --display-name 'Hoài Ngọc'
./scripts/shop-admin publish_pricebook.py --actor-id '<owner staff uuid>'
```

`OWNER_ADMIN` is not implicitly a member of the store — assign yourself from the Nhân sự screen.
Until the pricebook is published, pricing answers 503 by design and the shop cannot quote.

Then **one whole transaction by hand before any customer**: ticket → quote → *khách đã chốt giá* →
order → nhận đồ → giặt → sẵn sàng → giao ra → tất toán → hoàn tất.

## 5. The schedule

```bash
export R1_LOCAL_ARCHIVE_PATH="/Volumes/<your drive>/laundry-archive"
./deploy/shop-till/install.sh
```

Launch agents, not `crontab`. On macOS a cron job whose time passes while the machine is asleep is
never run and never catches up, so the nightly base backup would silently never happen on a laptop
that sleeps. `deploy/shop-till/README.md` has the detail and the two other macOS traps.

**Keep the Mac awake during shop hours.** System Settings → Lock Screen → *Turn display off* is
fine; sleep is not. Closing the lid stops the containers.

## 6. What stays on paper this week

Two counter events the software cannot record yet. Neither is a defect to be fixed by an engineer;
both are questions waiting on you, and both have a decision packet.

| Situation | This week |
|---|---|
| A customer brings **a suit, a coat, áo dài, leather shoes or bags, a pillow, a sofa cover, a carpet, or asks for stain treatment** — 20 of the 43 published services are price *ranges* | The console refuses to turn a range into a single number by design (`DEC-001`). Agree the price with the customer, write it on the ticket, and keep the ticket. Wash-and-dry by the kilo and the 22 fixed-price services quote normally. |
| A customer **complains** | The incident form cannot be completed — it requires two values nothing in the system produces (`docs/DECISION_REQUEST_INCIDENT_INTAKE_2026-09.md`). Write the complaint in the book against the ticket number and tell the owner the same day. |

Both are listed on the console's own **Chưa hỗ trợ** screen, so staff can read them there rather
than remembering.

## 7. Ending the week

The week ends by restoring the shop's real data somewhere else, which *is*
`docs/runbooks/restore-drill.md`. `BACKUP-RESTORE-001` completes on a drill result and never on
configuration — so do it once, with real consequences, on data somebody cares about.
