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
  the fix, and the jobs tolerate one failed tick either way.

## Install

```bash
export R1_LOCAL_ARCHIVE_PATH=/Volumes/<your drive>/laundry-archive
./deploy/shop-till/install.sh
```

It writes three agents into `~/Library/LaunchAgents`, loads them, and prints what it scheduled. It
is idempotent: run it again after moving the checkout or changing the archive path.

| Agent | When | What |
|---|---|---|
| `com.giatlasachcong.checks-data` | every 5 minutes | WAL archive gap, base-backup age, database volume free space |
| `com.giatlasachcong.checks-host` | every 5 minutes | every capability flag false on the running containers; the console answers |
| `com.giatlasachcong.base-backup` | 02:30 daily, or on the next wake | a full base backup into the archive |

## Watching it

```bash
tail -f ~/Library/Logs/giatlasachcong/checks-data.log
launchctl list | grep giatlasachcong          # a non-zero second column is the last exit status
```

A check that cannot run refuses rather than skipping, so a wrong path here fails loudly on the first
tick instead of reading healthy for a week.

## Removing it

```bash
./deploy/shop-till/install.sh --uninstall
```
