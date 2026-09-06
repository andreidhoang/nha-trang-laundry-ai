# Decision request — hosting provider admissibility verdict (DEC-HOSTING)

**Date:** 2026-08-18
**Status:** admissibility framework only. **No candidate is selected by this document** — see §0 and
`context/tasks/TASK-decision-hosting-001.md`, which an agent may not exceed: "An agent may assemble the
packet. An agent may not select a vendor, accept terms, assert residency, or sign the approval."
**Trigger:** `DEPLOY-TARGET-001`, `MONITORING-001`, `BACKUP-RESTORE-001`, `SLO-VERIFY-001`, and
`OPS-RUNBOOK-001` all wait on this.
**Assessments this rests on:** `docs/adr/0007-production-deployment-topology.md` (the A1–A5
constraints), `specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §3.

---

## 0. What this request does not do, and why every cost/residency cell below is marked UNVERIFIED

Every commercial price and every residency claim below is **general public information as of this
model's training, not a verified quote** — I have no live account with any of these providers and
cannot pull current pricing or read an actual data-processing agreement. Per the task packet's own
instruction: "If a candidate's restore feasibility cannot be established without an account, say so and
mark it `UNVERIFIED`; do not create the account to find out." That instruction is followed literally
here — this table narrows the field and states exactly what still needs a human to go verify with a
real account, not what to sign.

## 0a. What R1 changed about this request (2026-09-03)

`DEC-027` scoped the first release to the deterministic staff console: Zone C only, no public
ingress, no agent cell. That changes this packet in three ways, and they all make the decision
smaller:

- **A1 asks for two isolated compute units because of Zone A.** Zone A is the public agent runtime
  processing untrusted language, and R1 has none — no channel is connected and no model is invoked.
  **R1 needs one host.** ADR-0007 §1 permits exactly this: *"If cost forces one host, the correct
  response is to delay public ingress, not to collapse the boundary."* The second host returns as a
  requirement at G2, along with the ingress it exists to isolate.
- **A3 is the whole ingress story, not a subset of it.** There is no public path to keep the console
  away from, because there is no public path. Staff reach it over the shop network or a VPN address
  and `R1_CONSOLE_BIND_IP` defaults to loopback so an unset value fails closed.
- **A4's residency question is narrower than it looks.** R1 stores **no customer personal data**:
  no address column exists in any migration, a walk-in is a counter ticket (`DEC-013`), there is no
  customer-record layer (`DEC-015`), and with no channel there are no message bodies. What the host
  holds is order financial records and staff identity — the latter self-hosted in Keycloak under
  `DEC-011`, so no staff PII leaves the host either.

A5 and A2 are unchanged and are the ones that still decide the answer: a restore drill inside four
hours, and an encrypted archive in a separate failure domain. `SHOP-RECOVERY-001` built the
archiving against a repository-client interface deliberately not bound to a vendor, so the client
that ships with the chosen provider is a wrapper rather than a rewrite.

**One question to add to the verification, which the original table does not ask:** whether the
candidate's object storage supports **append-only or versioned writes with a credential that cannot
delete**. The archive command writes with `--if-not-exists` for a reason — an archive a compromised
host can overwrite is an archive a compromised host can destroy — and a provider whose only
credential grants delete undermines that.

## 1. The five hard constraints (ADR-0007)

- **A1** two isolated compute units with private networking between them
- **A2** encrypted off-host archive in a separate failure domain, reachable by egress only
- **A3** ability to keep the staff console off the public internet
- **A4** a documented data-residency position sufficient for DEC-006
- **A5** restoration of a full instance within the 4-hour RTO, demonstrable in a drill

## 2. Candidate verdict (framework, pending live verification)

| Candidate | A1 private net | A2 off-host archive | A3 console isolation | A4 residency | A5 restore | Cost |
|---|---|---|---|---|---|---|
| **Vietnam-domestic** (Viettel IDC, VNG Cloud, FPT Cloud) | Plausible — most offer VPC-equivalent private networking | `UNVERIFIED` — object-storage and cross-failure-domain maturity varies by vendor, needs a real account check | Plausible | **Strongest candidate by construction** — data never leaves Vietnam, cleanest possible answer to DEC-006's residency question | `UNVERIFIED` — PITR/managed-Postgres restore tooling maturity varies and needs a timed check | `UNVERIFIED` |
| **DigitalOcean (Singapore, SGP1)** | Yes — VPC with private networking between droplets is a standard, well-documented feature | Yes — Spaces object storage in a separate region is a standard pattern | Yes — firewall rules keep a droplet off public ingress | Singapore, not Vietnam — needs an explicit DEC-006-pattern residency judgment, not automatically disqualifying but not the clean answer domestic hosting gives | Yes in principle — managed Postgres offers PITR; a **timed drill is still required**, not assumed | Historically the cheapest of the non-domestic options; **figure not verified today** |
| **AWS (ap-southeast-1, Singapore)** | Yes — VPC, security groups, and private subnets are mature, well-documented primitives | Yes — S3 cross-region replication is a standard, provable pattern | Yes | Same as DigitalOcean: Singapore, not Vietnam | Yes — RDS PITR is mature and drillable | Historically the most expensive and most complex of the four; **figure not verified today** |
| **Hetzner (EU)** | Yes — strong private-networking primitives | Yes | Yes | Weakest residency story of the four — EU, farther from Vietnam's likely legal comfort zone than even Singapore | Plausible, `UNVERIFIED` for this specific stack | Historically the cheapest overall; **figure not verified today** |

## 3. Recommendation (verification order, not a selection)

Verify in this order, because it front-loads the constraint most likely to eliminate a candidate
outright (A4) before spending time on the others:

1. **Check one Vietnam-domestic provider first** (Viettel IDC or FPT Cloud are the more
   internationally documented of the domestic options) — if it genuinely offers VPC-equivalent private
   networking and a provable PITR/off-host-archive story, it resolves A4 more cleanly than any
   non-domestic option ever can, which is valuable given DEC-006 already carries an unresolved
   cross-border question for the *model provider*; adding a second cross-border question for *hosting*
   compounds it for no engineering benefit.
2. **If no domestic provider clears A1/A2/A5 with real verification, fall back to DigitalOcean
   Singapore** as the pragmatic option — mature primitives, closest non-domestic region, and the
   least operational complexity to actually run and drill.
3. **Keep AWS and Hetzner as reference points**, not because either is disqualified, but because
   AWS's complexity and Hetzner's residency distance both cut against this business's scale (two
   Zone-C/Zone-A hosts, one shop) without a benefit the other two candidates lack.

## 4. What only the owner can do (repeated from the task packet, because it matters)

- Select one admissible candidate.
- Accept its commercial terms.
- Record `owner_written_approval` — dated, named, written.

## 4a. Selection, 2026-09-03 — FPT Cloud, with a same-day fallback

Delegated by the owner to the engineer on record. Researched against live provider pages and
current legal guidance rather than recalled figures; every figure below carries its source.

**Selected: FPT Cloud `STANDARD-02` — 4 vCPU, 8 GB RAM, 100 GB SSD, in Vietnam.** Archive to a
second provider or a second FPT region, never the same failure domain as the host.

Three things decided it, in this order.

**1. The residency question is not hypothetical, and it is about the deployment's future rather
than its present.** Decree 53/2022 obliges *domestic enterprises* — defined as those established
under Vietnamese law with a head office in Vietnam, which A & T CARE is — to store regulated data
in Vietnam, and unlike the obligations on foreign enterprises it carries **no triggering
conditions**. Regulated data is personal information, data generated by users in Vietnam, and data
about their relationships.

R1 itself holds almost none of that: `DEC-013` and `DEC-015` mean no customer name, phone or
address is stored anywhere, and no channel is connected, so the only personal data is staff
identity in a self-hosted Keycloak. **So R1 could lawfully run abroad.** The point is that it will
not stay R1. The day `CHANNEL-TELEGRAM-001` or an official Zalo OA carries a real conversation, the
database holds customer personal data and the obligation is unambiguous — and that is a bad moment
to be migrating a live shop's production database across a border.

**2. VND invoicing with VAT, for a company that keeps ten-year accounting records.** `DEC-008` sets
order financial records at a 3650-day schedule because Vietnamese accounting requires it. A
company keeping books to that standard should not have its own infrastructure billed as a foreign
currency charge on a personal card. FPT bills in VND and issues a VAT invoice; prices exclude 8%
VAT, which the business reclaims.

**3. Support in Vietnamese, at 07:45, when the counter is waiting.** Not a rounding error for a
two-person shop with one technical founder.

### What did not decide it, and why

**Latency did not.** Singapore is ~30–50 ms from Vietnam and domestic is ~10–20 ms; both are
invisible at a counter. It *did* eliminate Europe: Hetzner is the cheapest option in the original
table and sits ~250 ms away, which is visibly sluggish on every weigh-price-accept step with a
customer standing there. Hetzner is out on user experience rather than on price or residency.

**Migration cost weighed less than expected, and that is worth recording**, because it is the
argument that would otherwise have forced a rushed decision. The whole stack is `docker compose`
plus external secrets plus one PostgreSQL database, and `SHOP-RECOVERY-001` built a restore that
brings the system up on a clean host from an encrypted archive. **Changing provider is the restore
drill pointed at a different machine.** That makes the host genuinely replaceable and means this
decision is reversible at the cost of one rehearsed procedure — which is also why the fallback
below is safe rather than a trap.

### The fallback, because "deploy today" is a real constraint

FPT publishes the `STANDARD-02` specification but **not its price**: the pricing page directs to
sales for a quote. If that quote does not land the same day, deploying today beats waiting, and the
fallback is a self-serve provider in Singapore — Vultr, DigitalOcean or Akamai, all ~US$48/month
for 8 GB / 4 vCPU with S3-compatible object storage alongside.

That is not a compromise to be embarrassed about. R1 holds no customer personal data, so nothing in
Decree 53's regulated categories leaves Vietnam; and the move to FPT later is the restore drill,
which has to be rehearsed anyway. **Record the trigger rather than the intention:** migrate before
`CHANNEL-TELEGRAM-001` or `CHANNEL-ZALO-001` completes, because that is the item that creates
regulated data.

Bizfly Cloud was the other domestic candidate with self-serve provisioning and published prices
(from 95,000đ/month, servers in 30 seconds). Its published tiers stop at 2 vCPU / 4 GB / 40 GB,
which is under-sized on disk once Docker images, WAL and the 10%-free volume alarm are counted.

### A5 and A2 remain the ones that decide whether this was right

Neither is answered by choosing a vendor. `BACKUP-RESTORE-001` completes on a **timed restore
drill**, and the archive must sit in a different failure domain from the host. One question the
original table does not ask and the operator must: **does the object storage support append-only or
versioned writes with a credential that cannot delete?** `archive-wal.sh` writes
`--if-not-exists` precisely so a compromised host cannot destroy history, and a provider whose only
credential grants delete undermines that.

## 5. Signature block

Fill and commit only after live verification against a real account for the chosen candidate — not
from this table alone.

```yaml
decision_dec_hosting:
  candidate:                  # verified admissible candidate name
  owner: BUSINESS_OWNER
  decided_at:
  verified_cost_pilot:        # figure + date read
  verified_cost_12mo:         # figure + date read
  verified_residency_terms:   # written terms, not a marketing claim
  restore_drill_feasible:     # yes/no, basis
  owner_written_approval: true
```
