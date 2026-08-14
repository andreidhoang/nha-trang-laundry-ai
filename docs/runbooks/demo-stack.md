# Local demo stack

**What this is:** the private staging topology, running on one machine, so the system can be
watched rather than inferred from a test report.

**What this is not.** It authorizes nothing. All thirteen capabilities in
`delivery/CAPABILITY_STATUS.yaml` remain `NOT_AUTHORIZED`. No customer channel exists, no model is
called (`provider_backed: false`), nothing is ever sent. Every account, store and record it creates
is synthetic. Local image tags are **never** acceptable deployment or rollback evidence — see
`private-staging.md`.

The demo overlay weakens nothing to make the stack run. Authentication is a real RS256 OIDC
exchange against the unmodified verifier, containers stay non-root with read-only roots and all
capabilities dropped, CSRF and origin checks are untouched, and every feature flag stays false.
`packages/evals/tests/test_demo_stack.py` fails if any of that stops being true.

## Prerequisites

Docker Desktop (or another Compose runtime) and `uv`. One host-level line is required, because the
TLS proxy refuses any request that does not name `staging.internal` and the demo does not relax
that check:

```text
echo "127.0.0.1 staging.internal" | sudo tee -a /etc/hosts
```

Use Chrome or Firefox. The session and CSRF cookies are `Secure`, and Safari will not store them
for this certificate.

## Bring it up

```text
docker network create --internal nha-trang-laundry-staging-database-private
uv run python scripts/generate_demo_material.py
docker compose -f compose.yaml -f compose.production.yaml -f compose.demo.yaml build
docker compose -f compose.yaml -f compose.production.yaml -f compose.demo.yaml up -d --wait
```

The first command is the same one `private-staging.md` gives; the network is external by design so
that the database zone is provisioned deliberately rather than by a compose file.
`generate_demo_material.py` writes keys, certificates and passwords into `.demo/`, which is
gitignored — the generator is committed, its output never is.

Three one-shot jobs run in order and must all exit 0: `migrate` applies the 23 forward-only
migrations as the migration identity, `demo-grants` gives the API and worker roles read/write on
the migrated tables and nothing else, and `demo-seed` creates four synthetic staff and one store.
Re-running `up --wait` re-runs all three; they are idempotent.

## Sign in

Open **<https://staging.internal:8443/demo-idp/>** and accept the private certificate authority
(it is `.demo/ca.crt`).

Pick one of four synthetic accounts. The page fetches a real RS256 token for that subject and
exchanges it at `POST /internal/v1/auth/session`, which is why the sign-in must happen in the
browser: the response sets `Secure; HttpOnly; SameSite=Strict` cookies that a terminal cannot plant
in the browser's jar.

| Account | Role | What it can do |
|---|---|---|
| `demo-owner` | `OWNER_ADMIN` | everything, including granting store access |
| `demo-operations` | `OPERATOR` | orders, incidents, draft decisions |
| `demo-approver` | `OPS_APPROVER` | approval queue, queue recovery |
| `demo-auditor` | `AUDITOR` | read only — every write must be refused |

You are redirected to the console at `/staff/`. Paste the store UUID the seed printed:

```text
11111111-2222-4333-8444-555555555555
```

There is no store list endpoint, so the console requires this by hand. That is a real gap, recorded
as the missing `stores` table in `docs/PRODUCTION_READINESS_ASSESSMENT.md`, not a demo shortcut.

## What to look at

- **Ten screens** render, all store-scoped reads return data, and `demo-auditor` is refused on
  every write with the same 403 a non-member would receive.
- **The worker** is up and leasing; `docker compose ... logs worker` shows the supervisor cycle.
- **Audit** — every mutation writes a row, a domain event, an audit entry and an outbox event in
  one transaction.

- **Pricing** (`QUOTE-COMMAND-001`) — the "Báo giá" panel prices a garment through the deterministic
  engine. The seed publishes the owner-confirmed pricebook as configuration version 1; without it
  the route answers 503 rather than guessing a price.

  Worth trying, because the refusals are the interesting part:

  | Enter | What happens |
  | --- | --- |
  | `STANDARD_WASH_DRY`, `6`, KG | 120,000 ₫ of service — and **no total**, because the delivery fee is unresolved |
  | `STANDARD_WASH_DRY`, `5.999`, KG | 149,975 ₫. Less weight costs *more*: the 6 kg tier is a real rule |
  | `STANDARD_WASH_DRY`, `1 bao tải to` | Refused, `MISSING_REQUIRED_FACT`. A sack is not a weight |
  | `BED_PILLOW`, `1`, ITEM | Refused, `RANGE_PRICE_REQUIRES_HUMAN`. Range prices need a person |
  | Sign in as `demo-auditor` and price | 403, worded identically to a store-membership refusal |

One thing is still visibly missing, and it is the next item in the queue: no order can reach
`COMPLETED`, because nothing records payment or collection (`SETTLEMENT-001`).

## Verify

```text
uv run python scripts/staging_smoke.py --base-url https://staging.internal:8443 --ca-file .demo/ca.crt
uv run python scripts/verify_demo_stack.py
uv run pytest packages/evals/tests/test_demo_stack.py
```

`verify_demo_stack.py` runs 24 live checks, including every refusal above. It is safe to re-run.

## Tear down

```text
docker compose -f compose.yaml -f compose.production.yaml -f compose.demo.yaml down
```

Add `-v` to discard the database volume; the role-initialisation script only runs on a fresh data
directory, so a `-v` teardown is required after regenerating material with `--force`.

## Known deviations from production

Recorded rather than hidden. Each is a place where the local stack is not identical to the deployed
one, and someone reading demo output should know which properties it does not prove.

1. **The TLS proxy needs an extra network here.** `compose.production.yaml` attaches `tls` only to
   `ingress-private`, which is `internal: true`. Docker accepts the `127.0.0.1:8443` port binding
   and then silently discards it — `HostConfig.PortBindings` holds the request while
   `NetworkSettings.Ports` comes back empty. **The only host listener that `private-staging.md`
   promises therefore does not exist, and nothing reports an error.** The demo overlay adds a
   non-internal `ingress-edge` network carrying the proxy alone. The production topology is not
   changed here; that belongs to its own reviewed item, and `STAGING-001`'s evidence should be
   re-read against this finding.
2. **Secret file ownership is not enforced.** Compose outside Swarm ignores the `uid`, `gid` and
   `mode` fields on secrets and configs, and says so on every run. The demo therefore does not
   prove that each service can read only its own secret.
3. **Table grants are coarser than the runbook's split.** `private-staging.md` describes per-service
   grants scoped to each repository's declared operations. `demo-grants` gives both application
   roles `SELECT, INSERT, UPDATE` on all migrated tables and revokes `DELETE`, `TRUNCATE`,
   `REFERENCES` and `TRIGGER`. The meaningful separation holds — neither role owns anything and
   neither has DDL — but a repository reaching for a table it should not would pass here.
4. **The identity provider is not an identity platform.** It signs correct tokens for four fixed
   synthetic subjects. It has no login, no session management, no key rotation and no revocation.
