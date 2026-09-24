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

The final target uses an isolated, replaceable public agent-runtime cell behind
`ConstrainedAgentRuntime`. A bounded custom OpenAI Responses adapter is the preferred production
target; OpenClaw remains an `EVAL_ONLY` comparison/rollback implementation until parity and retirement
gates pass. Python/PostgreSQL remain the business, security and side-effect authority; channel
adapters are independent and only the outbox worker may send.

Public channels and autonomous sends remain gated.

## Run the complete staff application from a fresh clone

**Docker alone is not sufficient.** A new machine needs Git, Docker Desktop (with Docker Compose),
and `uv`. Git downloads the repository, Docker runs the application services, and `uv` installs the
pinned Python workspace and generates machine-local synthetic credentials. Codex is a useful
engineering assistant but is not a runtime dependency. Node.js 24 is required only to build and test
the isolated, `EVAL_ONLY` OpenClaw comparison plugin; it is not required to operate the deterministic
R1 staff console.

VI: Ba phần tối thiểu có vai trò khác nhau: Git lấy mã nguồn, Docker chạy các dịch vụ, còn `uv` tạo
môi trường Python và dữ liệu bí mật giả lập chỉ dùng trên máy đó. Có Docker nhưng thiếu `uv` thì chưa
thể khởi tạo bản demo theo đúng hợp đồng của dự án.

### Prerequisites

| Required software | Minimum purpose |
|---|---|
| Git | Clone and update the repository |
| Docker Desktop or a compatible Docker Engine with Compose v2 | Build and run the service topology |
| `uv` | Install the declared Python version and locked workspace dependencies |
| A modern browser | Use the staff console |
| Node.js 24 | Complete repository verification, including the optional OpenClaw comparison plugin |
| Codex | Optional; use it to inspect, verify or change the project safely |

The reproducible application mode is supported on a local, non-cloud-synced checkout. The real-shop
runbook is macOS-specific; Linux and Windows/WSL2 machines should run synthetic engineering/demo mode
unless the owner explicitly commissions that machine as the replacement shop host.

### Clone, start and verify

Run these commands from a terminal. The first network command is needed only once per Docker host; if
Docker reports that the network already exists, inspect and reuse it.

```bash
git clone https://github.com/andreidhoang/nha-trang-laundry-ai.git ~/laundry
cd ~/laundry
uv sync --all-packages --all-groups
uv run python scripts/workspace_env.py --check
docker network create --internal nha-trang-laundry-staging-database-private
uv run python scripts/generate_demo_material.py
docker compose -f compose.yaml -f compose.production.yaml -f compose.demo.yaml up -d --wait --build
uv run python scripts/verify_demo_stack.py
```

Then open `http://localhost:8081/demo-idp/`, choose a synthetic identity, and sign in. The owner
identity can exercise every staff workflow; the auditor identity must remain read-only. This starts
the PostgreSQL database, forward-only migration job, role-grant job, synthetic-data seed job, API,
transactional-outbox worker, synthetic OIDC identity provider, Caddy edge proxy, and Staff PWA. No
component of the deterministic daily-operations path is omitted.

VI: Trang đăng nhập giả lập là điểm vào của người dùng thật khi kiểm thử. Các job migration, phân
quyền và seed chạy trước API; worker xử lý outbox độc lập; trình duyệt chỉ gọi qua edge proxy. Vì vậy
lệnh xác minh kiểm tra cả chuỗi từ giao diện đến cơ sở dữ liệu, không chỉ kiểm tra trang HTML mở được.

If `.demo/` already contains generated material, do not overwrite it while the stack is running.
Stop the reusable demo without deleting its database volume:

```bash
docker compose -f compose.yaml -f compose.production.yaml -f compose.demo.yaml down
```

Use `down -v` only when the synthetic demo data is intentionally disposable. Never use it for the
real shop. See the [demo runbook](./docs/runbooks/demo-stack.md) for browser workflows and teardown,
and the [cross-machine handoff](./docs/CODEX_CROSS_MACHINE_HANDOFF.md) for the full guarded test suite,
known security blockers, operating-system notes, and the exact Codex continuation prompt.

### What GitHub deliberately does not contain

The repository contains the entire reviewed source, migrations, contracts, test suites, Compose
topology, runbooks and synthetic-data generators. It intentionally excludes `.shop/`, `.demo/`,
`.env`, database volumes, customer records, passwords, private keys and certificates. Therefore any
engineer can reproduce the full synthetic system, but cloning must never create a second production
ledger. Only the selected shop Mac—or an owner-authorized replacement restored through the encrypted
backup procedure—may hold real operational data.

VI: “Đầy đủ mã nguồn” không có nghĩa là đưa dữ liệu khách hàng hoặc bí mật vận hành lên GitHub. Đây
là ranh giới an toàn bắt buộc: mã và cấu hình mẫu có thể tái tạo, còn trạng thái kinh doanh thật chỉ
được chuyển bằng quy trình backup/restore có chủ sở hữu phê duyệt.

All thirteen public/AI capabilities must remain `NOT_AUTHORIZED` on a new clone. That is the expected
secure state, not an installation error. The R1 staff console works without public messaging or model
calls; pricing, money, permissions, SLA, order state and side effects remain deterministic in
Python/PostgreSQL.

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
Python-first under [ADR-0001](./docs/adr/0001-python-control-plane.md); browser assets are served by
the Staff PWA, while TypeScript is isolated to the non-authoritative OpenClaw comparison plugin.

## Start here

- [Codex and engineer cross-machine handoff](./docs/CODEX_CROSS_MACHINE_HANDOFF.md)
- [English build engineering specification](./BUILD_ENGINEERING_SPEC.md)
- [Staging readiness, measured](./docs/STAGING_READINESS_2026-09.md)
- [Engineering continuation brief](./context/PROJECT_CONTINUATION.md)
- Live delivery status: `uv run python scripts/report_delivery_status.py` -- generated from the
  queue on every run, so it cannot go stale the way the hand-written board and status page did
- [Stable program plan](./delivery/PROGRAM_PLAN.yaml)
- [Release gate registry](./delivery/GATE_REGISTRY.yaml)
- [Production agent runtime ADR](./docs/adr/0002-production-agent-runtime-and-trust-boundaries.md)
- [Provider-neutral runtime and channel-operations ADR](./docs/adr/0003-provider-neutral-agent-runtime-and-channel-operations.md)
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
