# TASK-shop-till-001 — the pilot week with the shop's own Mac as the till

**Goal:** make the deterministic console genuinely runnable for a week of real trading on the
shop's own Mac, used as the till itself, and fix whatever stands in the way of that.

**Domains:** `platform`, `business_truth`

**Stable work item:** `SHOP-TILL-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. One change is to the WAL archive path, which is the mechanism the pilot's data
safety rests on; the rest is a compose overlay, scheduling, and documentation.

---

## 1. Why this exists

`SHOP-CUTOVER-001` is BLOCKED on a provisioned host, and `docs/runbooks/shop-pilot.md` already
answers that with the shop's own machine. But that page is written for a small server on the shop
LAN with staff on tablets: a fixed address, router DNS, a private CA installed and trusted on every
device, and `crontab`.

The owner chose a narrower shape — **one Mac at the counter, staff working on it directly** — and a
local encrypted archive on an external drive rather than an object store. Both choices are strictly
safer than the documented one: there is no network path to the console at all, and the archive needs
no third party. Neither was buildable from the existing pages without guessing.

## 2. Scope

**The topology.** A second compose overlay that mounts the archive destination, alongside the
existing secrets overlay. Nothing else changes: `compose.r1.yaml` already publishes the console on
loopback by default, so the till shape *removes* configuration rather than adding it.

**The archive.** `rclone` is already in the PostgreSQL image and speaks a local filesystem remote,
so the shipped upload wrapper needs no change to write to a drive — only a destination it can see.
Prove the whole chain by round trip rather than by reading it: compress, integrity-check, encrypt,
upload, fetch, decrypt, compare bytes.

**The schedule.** `crontab` is wrong on macOS and fails silently: a job whose time passes while the
machine is asleep is never run and never catches up, so the nightly base backup would simply never
happen on a laptop. `launchd` runs a missed calendar job once on wake.

**The counter.** A printed page in Vietnamese covering the shift, the three things that are easy to
get wrong, what each refusal means, and the two procedures that stay on paper this week.

## 3. What this task may not do

- Bring the stack up, move the checkout, or run anything needing `sudo`. Those are the owner's.
- Generate the backup identity. `DEC-026` places the private half away from the machine it protects.
- Decide either of the two open questions that keep work on paper — range prices (`DEC-001`) and
  incident intake — or paper over the fact that they do.
- Move any AI capability. All thirteen stay `NOT_AUTHORIZED`.

## 4. Verification

The repository gates, plus an executed round trip of the archive chain against the shipped image,
and a failing reproduction for the archiver defect before its fix.
