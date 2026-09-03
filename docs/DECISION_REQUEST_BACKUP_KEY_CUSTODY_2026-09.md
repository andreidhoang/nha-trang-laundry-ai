# Decision request — who holds the key that can read the shop's backups

**Date:** 2026-09-03
**Status:** OPEN. Registered as `DEC-026`, owner `BUSINESS_OWNER`, fail-closed: no archiving is
enabled until it is signed.
**Trigger:** `SHOP-RECOVERY-001` built the archiving. It cannot be switched on without a recipient
public key, and the private half is the thing this decision is about.

---

## 0. The constraint that forces the question

ADR-0007 §3 and `PRODUCTION_OPERATIONS_SPEC_V1` §3.1 both say the archive is encrypted "before
leaving Host A with a key that is not stored on Host A."

Taken literally — and it should be — that excludes a passphrase. A passphrase present on the host
to write the archive also decrypts every archive ever taken, so whoever compromises the host reads
all of the shop's history. That is the exact failure the sentence exists to exclude.

So the archiving uses public-key encryption: the host holds a **recipient** key that can only
write, and the **identity** that can read is somewhere else. Somewhere else is what this decides.

## 1. What is actually being decided

1. **Who physically holds the identity.** A file, on something that is not the shop's server.
2. **Where the second copy lives.** One copy is a single point of failure in the other direction.
3. **Who may authorise its use in a restore**, and how they are reached at 06:00 on a Tuesday.

And the acknowledgement that makes it real:

> **Losing this key makes every backup permanently unrecoverable.** Not difficult — impossible.
> There is no recovery path, no vendor to call, and no copy on the server by design.

## 2. Options

| Answer | Consequence |
|---|---|
| **A. The owner holds it; a second copy in a sealed envelope somewhere physically separate** (recommended) | No third party, no subscription, and it matches the scale of a two-person shop. Costs: a restore waits for the owner to be reachable, and "somewhere physically separate" has to be a real place that is named, not a drawer in the same building as the server. |
| **B. A password manager the business already pays for** | Reachable from anywhere, survives a lost laptop, and the vendor's own recovery process becomes the backstop. Introduces a counterparty that can, in principle, read the shop's entire history — which is the thing §3.1 was written to prevent, moved rather than removed. |
| **C. Split across two people** | No single person can read the archives alone. For a business with two staff who both work the counter, this mostly means no restore happens when one of them is on holiday. |
| **D. Defer** | Archiving stays off. **This is the worst option and should be said plainly:** the deployment runs with no recovery at all, and `docs/runbooks/production-deploy-day.md` §0 already says a deploy that skips the backup repository is a deploy with no recovery. |

**Recommendation: A**, with the second location named in writing rather than described.

## 3. What happens once it is signed

- The owner generates the key pair on their own device — not on the server, and not by an agent.
  The public half goes into the `r1_backup_encryption_recipients` secret; the private half never
  touches the host, this repository, or CI.
- `deploy/production/backup/restore.sh` takes the identity as a path supplied at drill time, which
  is why `docs/runbooks/restore-drill.md` counts fetching it as a **timed step inside the four-hour
  recovery objective** rather than as a preamble.
- The restore drill is run. `BACKUP-RESTORE-001` completes on a drill result, never on
  configuration.

## 4. Signature block

```yaml
decision_dec_026_backup_key_custody:
  answer:                    # A | B | C | D
  holder:                    # by name
  second_copy_location:      # a place, written down
  restore_authoriser:        # by name, and how they are reached out of hours
  acknowledged_loss_is_final: # true -- losing the key makes every backup unrecoverable
  owner: BUSINESS_OWNER
  decided_at:
  owner_written_approval:
```
