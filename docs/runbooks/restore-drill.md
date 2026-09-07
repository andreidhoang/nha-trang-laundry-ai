# Runbook — restore drill

**Written:** 2026-09-03 · **Status:** never executed. `specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §7
requires this runbook and it did not exist; §3.4 requires the drill and it has not been run. Every
command here is real; the sequence has not been walked on a provisioned host, because no host
exists.

**`BACKUP-RESTORE-001` completes on a drill result, never on configuration.** A configured backup
that has never been restored is a belief, not a recovery capability. This is the procedure that
turns one into the other.

## 0. Before you start

| | Prerequisite | Who |
|---|---|---|
| 1 | The archive repository holds at least one base backup and continuous WAL | operator |
| 2 | **The decryption identity**, fetched from wherever `DEC-026` placed it | its holder |
| 3 | A clean host with an empty data directory. Never the host being recovered from | operator |
| 4 | A stopwatch. The whole point is the wall-clock number | the engineer running it |

**Start the clock when you start step 1, not when the restore command runs.** The recovery-time
objective is four hours "by one engineer following the runbook", and fetching the key from its
holder is inside that. A drill that begins with the key already in hand measures a restore that
will never happen that way.

## 1. Simulate the failure

Note the last commit the source database accepted, and the moment you are recovering to:

```bash
DATABASE_URL=... psql -Atc "SELECT max(occurred_at) FROM domain_events"
```

That value is `source_latest_commit_at` in the evidence. The recovery point you select is
`selected_recovery_point_at`, and the difference between them is the achieved recovery point — the
number `deploy/backup/backup-policy-v1.json` bounds at 900 seconds.

## 2. Restore

```bash
export BACKUP_IDENTITY_FILE=/secure/path/age-identity.txt   # from step 0, item 2
export BACKUP_REPOSITORY_PREFIX=...                          # the same prefix the host archives to
export RECOVERY_TARGET_TIME='2026-09-03 14:05:00+07'
export PGDATA=/var/lib/postgresql/restore                    # must be empty; the script refuses otherwise

# These two were missing from this runbook and `restore.sh` requires both with `:?` under `set -eu`,
# so the drill halted on its own first command — on the stopwatch. They are how the script lists
# and fetches objects; the repository shipped `r1-archive-upload.sh` for writing and these are the
# read side of the same rclone configuration.
export BACKUP_LIST_COMMAND=/usr/local/bin/r1-archive-list.sh
export BACKUP_FETCH_COMMAND=/usr/local/bin/r1-archive-fetch.sh

# And this one must also be in **PostgreSQL's** environment when you start it in step 2b, not only
# in this shell: `restore_command` runs as a child of the server and fetches every WAL segment, so
# without it recovery stops at the base backup with `could not locate required checkpoint record`.
export RCLONE_CONFIG=/path/to/the/rclone.conf naming the archive

deploy/production/backup/restore.sh
```

**The identity path must not contain `%`, a quote, `$`, a backtick or a backslash.** The script
refuses those before writing anything, because all three of the ways they broke a restore reported
success and failed hours later, when PostgreSQL was started, inside the four-hour clock. Spaces are
fine — `/Volumes/USB DRIVE/age-identity.txt` works, and is the expected shape given the key arrives
on removable media.

**The base backup is chosen by the target, not by recency.** A backup taken *after* the recovery
target cannot be recovered from -- PostgreSQL refuses with `could not locate required checkpoint
record` -- and that is the ordinary case, not an edge one: backups run nightly, so undoing
something from yesterday afternoon means reaching back past last night's backup. `restore.sh`
picks the newest backup at or before the target and prints the ones it skipped and why. If it says
none is eligible, the target is older than the oldest backup you hold.

Then start PostgreSQL against that directory and wait for promotion. The script writes
`recovery_target_action = 'promote'`, so the server promotes itself and stops replaying. Watch for
`restored log file ... from archive` lines: that is the archive being read back, and their absence
means `restore_command` cannot reach it.

`restore_command` decrypts each WAL segment as it fetches it, so the identity is needed for the
whole of recovery — not only to open the base backup.

## 3. Prove it, rather than declare it

```bash
# `--database-url-file`, not `--database-url`: the flag takes a *path to a file* holding the DSN,
# and the script rejects anything that is not a regular file. Printed in the wrong form in three
# places until the adversarial round, on the command that produces the evidence
# `BACKUP-RESTORE-001` completes on.
printf '%s' 'postgresql://.../restored' > /run/restore-dsn
chmod 0600 /run/restore-dsn
uv run python scripts/validate_restore_drill.py \
  --evidence drill-2026-09-03.json \
  --database-url-file /run/restore-dsn
```

The validator connects read-only and checks what can be checked in code rather than attested:

| §3.4 proof | How it is established |
|---|---|
| Recovery point ≤ 15 min | computed from the two timestamps you recorded |
| Recovery time ≤ 4 h | computed, wall-clock, from when you started |
| Quote snapshots hash-identical | recomputed through `canonical_document` and compared |
| Audit chain has no gap | for the six aggregate types whose writers promise contiguous versions, none whose highest version differs from its count of *distinct* versions, **and** the restored database reaches at least the recovery point it claims |
| No duplicate send | duplicate `idempotency_key` in `outbox_events`, duplicate `provider_message_id`, and zero rows stuck in `PROCESSING` |
| Object evidence fetchable | attested — the objects are outside the database |

The audit-gap and recovery-point checks were added by `SHOP-RECOVERY-001`. Before that the proof
was "at least one event exists for one correlation id", which a restore to the wrong point would
also satisfy.

## 4. Record it

Environment must read `PRODUCTION` for a production drill. Until `SHOP-RECOVERY-001` the validator
rejected that value outright, so a production drill could not be validated at all — and the contract
test hardcoded `STAGING`, so it would have kept passing while proving nothing.

## 5. A drill that needed improvisation is a failed drill

`specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §3.4 is explicit: correct the runbook and run it again.
Improvisation means the runbook is wrong, and the person improvising at the drill will be somebody
else at 03:00 with the shop closed.

Two things this procedure deliberately does not do:

- **It does not touch the source host.** A restore that can write to what it is recovering from is
  not a restore.
- **It does not delete anything.** Not the old data directory, not the archive, not the evidence
  from the previous drill.

## 6. What restoring also restores

The database holds Keycloak's realm, including its signing keys. Restoring it restores the ability
to mint staff sessions.

That is not a §3.3 violation — §3.3 names release-signing keys, provider credentials and channel
credentials, none of which are in the database — but it is the reason the archive encryption key
matters as much as it does. **A stolen archive plus the decryption identity is a full identity
compromise**, which is why `DEC-026` keeps the identity off the host and out of this repository.
