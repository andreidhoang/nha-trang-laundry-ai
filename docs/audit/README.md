# Audit 2026-09-17 — full-stack correctness + Vietnamese UX

Run against HEAD `aeaba6c` on branch `claude/focused-mayer-on6a2j`, 2026-09-17.

## Evidence discipline

Every row in these documents carries one of three states, and they are never blurred:

- **VERIFIED** — executed against a running stack; the transcript, screenshot or query result is
  named. The four screenshots that carry a P0 are committed under `docs/audit/shots/`; the rest of
  the run (60+ captures across 12 screens × 2 viewports × 4 roles) was session-local. Every image
  shows synthetic demo data only — no real customer, phone number or address exists in this system.
- **INFERRED** — read from source or contract; the file:line is named. Not executed.
- **BLOCKED** — could not be reached, with the reason and what is needed.

A finding that could not be evidenced was deleted rather than softened. Three candidate findings
were retracted mid-audit when the evidence turned out to indict the harness rather than the code;
they are listed in `10-RETRACTED.md` so the record is honest about them.

## How the stack was run

Docker is unavailable in this environment (`docker info` → no `/var/run/docker.sock`), and the
repository's supported path is Compose-only. The demo topology was therefore reproduced natively:

| Component | How |
|---|---|
| PostgreSQL 16 | `initdb` + `pg_ctl` as the `postgres` user on `127.0.0.1:5432` |
| Schema | `scripts/apply_migrations.py` → `0001…0037` |
| Grants | `scripts/apply_demo_grants.py` → 10 statements, 2 roles |
| Seed | `scripts/seed_demo_data.py` → store `1111…5555`, 4 staff, pricebook `JCS-SHA256-V1:e9580d74…` |
| Identity | `scripts/demo_identity_provider.py --port 9000`, unmodified |
| API | `uvicorn nha_trang_laundry_api.main:app --port 8000`, unmodified |
| Edge | a 60-line Python stand-in for the `:8080` listener in `deploy/demo/Caddyfile`, on `:8081` |
| Browser | Playwright 1.56.1 + `/opt/pw-browsers/chromium`, viewport 1366×768 and 390×844 |

**No verifier, origin check, CSP header, cookie flag or auth setting was relaxed.** Sign-in is a
real RS256 token from the demo issuer accepted by the unmodified production verifier
(`POST /internal/v1/auth/session` → `200`, `HttpOnly; SameSite=strict; Secure` session cookie).

## Index

| Doc | Phase | Coverage |
|---|---|---|
| `00-SPEC-LEDGER.md` | A | Partial — scoped to the surfaces exercised at runtime. Stated honestly in the doc. |
| `01-MAP.md` | B | Complete for routes, screens, orphans, duplicates |
| `02-BACKEND.md` | C | Auth/tenancy, idempotency, concurrency, error taxonomy, observability |
| `03-TRACE.md` | D | Every interactive element on all 12 screens, owner + auditor |
| `04-UX-FINDINGS.md` | E | CSKH, owner/admin, auditor, mobile, adversarial |
| `05-AI-HUMAN-BOUNDARY.md` | F | A0/A1/A2/H0 per surface |
| `06-VI-COPY.md` | G | Terminology, IME, collation, expansion, formats |
| `07-SUBTRACTION.md` | H | Delete / merge / keep |
| `08-REFACTOR-PLAN.md` | I | Ordered, gated slices — **not executed, awaiting approval** |
| `09-TASK-SPLIT.md` | §12 | Claude-executable vs human-required |
| `10-RETRACTED.md` | §1.1 | Candidate findings deleted for want of evidence |

## Committed evidence

| File | Proves |
|---|---|
| `shots/approvals-with-item.png` | F-01 — `Duyệt` / `Từ chối` rendered disabled against a live pending approval |
| `shots/dead-incidents.png` | D-04 — *"Biểu mẫu này chưa dùng được — không phải do bạn"* |
| `shots/j9-01-create-filled.png` | D-01 — the `Tạo đơn` form, filled with values no operator can obtain |
| `shots/j5-01-quote-result.png` | the working half — a server-priced quote, `120.000 ₫`, `ƯỚC TÍNH` badges, 6kg cliff disclosed |
