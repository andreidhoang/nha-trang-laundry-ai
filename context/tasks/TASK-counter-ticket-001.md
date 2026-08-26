# TASK-counter-ticket-001 — a person who walks in off the street can be served

**Goal:** implement `DEC-013` and `DEC-015` as ratified — a walk-in is identified by a
counter-issued number and nothing about them is stored — and make `orders.bound_contact_id` mean
something.

**Domains:** `orders_audit`, `privacy_consent`

**Stable work item:** `COUNTER-TICKET-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Small in code, large in consequence: it is the last of four conditions that
refused every order, and it is the first schema this project adds that exists to *not* hold data.

## Why this exists

The owner ratified three decisions on 2026-08-26. `DEC-013`: a walk-in is identified by a number the
counter issues, with no name, phone or address stored. `DEC-015`: no customer-record layer is built
yet. `DEC-014`: the existing takings gate is the chosen one — no code.

Until now the shop could price laundry, agree a price and create an order — but only for a customer
who had already messaged it through a channel, and no channel is connected. A person walking in had
no route into the system at all, deliberately: creating a contact at the counter meant recording
personal data with no consent behind it.

**The decision that made this cheap was the owner's, and it is worth recording why.** Ticket-only was
chosen over name-and-phone on reversibility: adding a name later is additive and needs consent;
un-collecting a name already taken is impossible. It also costs nothing today, because the only
thing a phone number buys is messaging a customer, and no channel exists.

## A hole found while implementing

`orders.bound_contact_id` has been required since the table existed, has no foreign key, and
**nothing ever checked it**. Any UUID was accepted. That is the same shape of hole `approval_id`
carried until migration `0029`, and it was invisible because no order had ever been created.

It is checked now, against both legitimate sources. Not one foreign key, because there are two
sources and `DEC-015` explicitly declines to unify them behind a party layer — unifying them is the
customer-record layer that decision says not to build.

## What must be true when this is done

1. `counter_tickets` holds no column that identifies a person, asserted against the schema.
2. Numbers restart daily per store and never repeat within a day.
3. An order naming a reference nobody issued or bound is refused.
4. A walk-in can be taken in, quoted, chốt and turned into an order, end to end.
5. The `#/gaps` entry saying a walk-in cannot be served comes down.
6. The intake screen offers "Phát phiếu" and still has no name or phone field.

## Boundary

**No customer-record layer.** No `parties`, no contact points, no addresses. `DEC-015` is "not yet",
and this item does not smuggle a CRM in behind a ticket table.

**No personal data, anywhere on this path.** The route takes no request body, the domain event
carries no customer facts, and the table has no column to put one in.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus a walk-in served end to end against the live database.
