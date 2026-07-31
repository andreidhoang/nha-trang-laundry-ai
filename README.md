# Nha Trang Laundry AI

Internal operations and constrained AI concierge for **Giặt Là Sạch Cộng** /
**CÔNG TY TNHH A & T CARE**.

## Current status

`DOMAIN_CORE_ACTIVE — PUBLIC_AUTOMATION_NOT_AUTHORIZED — SPEC_APPROVED_WITH_EXECUTION_GATES`

The repository contains verified business truth, price/promotion/SLA seed data, an implementation-ready
specification pack, Python workspace, locally validated PostgreSQL/identity and operations control,
canonical catalog registry, exact pricebook import manifest, deterministic pricing/promotion/delivery/
SLA engines, and a constrained EVAL_ONLY agent/tool boundary. Provider-backed agent evidence remains
externally blocked while the independent production-hardening queue is ready. There is no deployed
customer-facing agent.

Approved next build scope:

1. repository, CI and architecture-decision foundation;
2. PostgreSQL domain model and immutable configuration publication;
3. deterministic catalog/pricing/promotion/delivery/SLA engines;
4. pilot instrumentation;
5. internal Staff PWA, approval queue, audit and transactional outbox;
6. synthetic and internal Shadow evaluation.

The final target uses an isolated Public OpenClaw cell as the customer-agent runtime from the agent
integration phase onward. Python/PostgreSQL remain the business, security and side-effect authority;
only the outbox worker may send.

Public channels and autonomous sends remain gated.

## Local M0 verification

Python 3.12 and dependencies are managed with `uv`:

```text
uv sync --all-packages --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages
uv run pytest
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
```

For local PostgreSQL, install Docker Desktop and run `docker compose up -d postgres`. See the
[local development runbook](./docs/runbooks/local-development.md). The backend/evaluation stack is
Python-first under [ADR-0001](./docs/adr/0001-python-control-plane.md); TypeScript is reserved for the
future browser PWA.

## Start here

- [English build engineering specification](./BUILD_ENGINEERING_SPEC.md)
- [Current delivery and production status](./docs/STATUS.md)
- [Engineering continuation brief](./context/PROJECT_CONTINUATION.md)
- [Delivery board](./docs/DELIVERY_BOARD.md)
- [Stable program plan](./delivery/PROGRAM_PLAN.yaml)
- [Release gate registry](./delivery/GATE_REGISTRY.yaml)
- [Production agent runtime ADR](./docs/adr/0002-production-agent-runtime-and-trust-boundaries.md)
- [Engineering specification index](./specs/README.md)
- [Team review and go/no-go report](./specs/TEAM_REVIEW_REPORT_V1.md)
- [Production architecture](./specs/production-architecture-v1.html)
- [Implementation roadmap](./specs/IMPLEMENTATION_ROADMAP_V1.md)
- [Canonical agent-tool OpenAPI](./specs/contracts/agent-tools-v1.openapi.yaml)
- [Release gate-manifest schema](./specs/contracts/release-gate-manifest-v1.schema.json)
- [Evaluation manifest](./specs/evals/eval-manifest-v1.yaml)

## Source-of-truth warning

`POLICY_RISK_REVIEW.md` is internal risk-analysis material and must never be included in a
customer-facing retrieval corpus. Unresolved policy facts remain `REQUIRE_HUMAN` or `DENY`; the
system must not infer them.
