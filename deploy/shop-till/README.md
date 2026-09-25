# The till schedule, on macOS

Three jobs have to run while the shop trades: the operational checks, and the daily base backup that
gives the archived WAL something to be replayed onto.

**`cron` is the wrong mechanism on a Mac, and the failure is silent.** `docs/runbooks/shop-pilot.md`
§"The week" schedules them with `crontab`, which is correct on the Linux server that section was
written for. On macOS a job whose time passes while the machine is asleep is simply **not run** —
cron does not catch up on wake — and a laptop at a shop counter is asleep every night. The daily
base backup at 02:30 would never fire, and nothing would say so: cron mails its output to a local
mailbox nobody reads.

`launchd` with `StartCalendarInterval` runs a missed job **once, on wake**. That is the behaviour
this needs, so these are launch agents rather than crontab lines.

Two further macOS specifics, both of which produce a job that looks scheduled and never runs:

- **Full Disk Access.** A launch agent that reads the repository under `~/Documents` or `~/Desktop`
  is refused by TCC with no useful error. Grant `/bin/bash` — or move the checkout outside those
  directories, which is simpler and what `install.sh` recommends.
- **Docker Desktop must already be running.** These jobs talk to the Docker socket; if Docker has
  not started yet the job fails. Docker Desktop's *Start Docker Desktop when you sign in* setting is
  the fix. A data check that cannot run at all is itself reported to your phone (below), so this
  is noisy rather than silent.

And one that was in this directory until `SHOP-ALERT-DELIVERY-001`: the plists `install.sh` wrote
were not valid XML for two of the three agents (an unescaped `&&`), which `launchctl bootstrap`
refuses outright. A test now loads every plist it writes with a parser as strict as launchd's.

## Install

Make the alert bot's two files first — `docs/runbooks/shop-till-mac.md` §5a — then:

```bash
export R1_LOCAL_ARCHIVE_PATH=/Volumes/<your drive>/laundry-archive
./deploy/shop-till/install.sh
```

It writes three agents into `~/Library/LaunchAgents`, loads them, and prints what it scheduled. It
is idempotent: run it again after moving the checkout or changing the archive path. It warns if
either alert file is missing; until they exist, every failing check ends in `ALERT NOT DELIVERED`.

| Agent | When | What |
|---|---|---|
| `com.giatlasachcong.checks-data` | every 5 minutes | WAL archive gap, base-backup age, database volume free space |
| `com.giatlasachcong.checks-host` | every 5 minutes | every capability flag false on the running containers; the console answers `/readyz` |
| `com.giatlasachcong.base-backup` | 02:30 daily, or on the next wake | a full base backup into the archive |

## How an alert reaches a person

`DEC-025`: one Telegram message to the owner, from a separate bot, one recipient, no inbound handler,
never through the outbox.

The data checks run in a container on `database-private`, which is `internal: true` — it has no
route to the internet, and that is ADR-0007 §1, not an accident to work around. So neither check
agent sends anything itself. Each runs its check with `--emit-alert`, which prints the alert as one
JSON line, and `scripts/relay_shop_alert.py` — running on the Mac, which has internet — delivers it
with the token and chat id from `.shop/secrets/alert_telegram_token` and
`.shop/secrets/alert_telegram_chat_id`. The plists hold those *paths*, never the values.

| Exit | Meaning |
|---|---|
| 0 | every check passed |
| 1 | a check failed and you were told (or it was the console between 21:00 and 07:00, held by `DEC-025`) |
| 2 | the relay was invoked wrongly; nothing ran |
| 3 | **a check failed and the alert was NOT delivered** — `alert-delivery.log` says why |

A check that cannot run at all — Docker stopped, the image missing, the secret file gone — is
reported as an alert in its own right: that is exactly when the backup checks are blind.

The migration identity's database URL reaches the data-check container on **stdin**, read by the
relay from `.shop/secrets/migration_database_url`. It used to be `-e DATABASE_URL="$(cat …)"`,
which put the password in `ps` and in `docker inspect` (`OPS-HARDENING-002`); `scripts/shop-admin`
does the same now.

**Not proven here:** delivery to a real phone. The tests use a local stand-in for Telegram. The
first real message is `shop-till-mac.md` §5c, and it is the owner's step.

## Watching it

```bash
tail -f ~/Library/Logs/giatlasachcong/checks-data.log
tail -f ~/Library/Logs/giatlasachcong/alert-delivery.log   # one line per alert: DELIVERED / NOT DELIVERED
launchctl list | grep giatlasachcong          # the second column is the last exit status; 3 = not told
```

A check that cannot run refuses rather than skipping, so a wrong path here fails loudly on the first
tick instead of reading healthy for a week.

There is no mail floor on a Mac: `launchd` does not mail anyone. The floor is the exit status above
and `alert-delivery.log`, which is why step §5c — watching one alert arrive — is not optional.

## Removing it

```bash
./deploy/shop-till/install.sh --uninstall
```
