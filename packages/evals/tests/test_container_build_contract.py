from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILES = (
    ROOT / "apps/api/Dockerfile",
    ROOT / "apps/worker/Dockerfile",
    ROOT / "apps/public-agent-tools/Dockerfile",
)
SERVICES = ("api", "worker", "agent-tools")


def test_images_pin_base_install_from_lock_and_run_non_root() -> None:
    for path in DOCKERFILES:
        content = path.read_text(encoding="utf-8")
        assert re.search(r"python:3\.12\.13-alpine3\.23@sha256:[0-9a-f]{64}", content)
        assert '"uv==${UV_VERSION}"' in content
        assert "uv sync --frozen --no-dev --no-install-workspace" in content
        assert "USER 10001:10001" in content
        assert "HEALTHCHECK" in content
        assert 'ENTRYPOINT ["python", "-m", "uvicorn"]' in content
        assert "COPY . ." not in content
        assert "USER root" not in content
        assert "--build-arg" not in content
        assert ".env" not in content


def test_build_context_excludes_private_state_secrets_and_raw_material() -> None:
    ignored = set(
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )

    assert {
        ".git",
        ".agents",
        ".openclaw",
        ".env",
        ".env.*",
        "evidence",
        "releases",
        "templates",
        "POLICY_RISK_REVIEW.md",
        "BUSINESS_TRUTH_INTAKE.md",
    }.issubset(ignored)


def test_production_compose_is_unprivileged_private_and_disabled_by_default() -> None:
    compose = yaml.safe_load((ROOT / "compose.production.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["postgres"]["profiles"] == ["local-database"]
    assert compose["networks"]["control-private"]["internal"] is True
    for name in SERVICES:
        service = services[name]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service.get("privileged") is not True
        assert service.get("network_mode") != "host"
        assert service["networks"] == ["control-private"]
        environment = service["environment"]
        assert environment["FEATURE_PUBLIC_CHANNELS_ENABLED"] == "false"
        assert environment["FEATURE_AUTOMATED_SENDS_ENABLED"] == "false"
        assert environment["FEATURE_AGENT_RUNTIME_ENABLED"] == "false"
        serialized = yaml.safe_dump(service)
        assert "/var/run/docker.sock" not in serialized
        assert ".openclaw" not in serialized
        assert "owner" not in serialized.casefold()


def test_worker_health_host_is_explicitly_non_authoritative() -> None:
    content = (ROOT / "apps/worker/src/nha_trang_laundry_worker/main.py").read_text(
        encoding="utf-8"
    )

    assert '"automation": "disabled"' in content
    assert "process liveness only" in content.casefold()
    assert "run_once" not in content
