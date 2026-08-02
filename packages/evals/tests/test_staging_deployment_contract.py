from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import psycopg
import pytest
from nha_trang_laundry_db import migration_job

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = ("compose.yaml", "compose.production.yaml")
DISABLED_FLAGS = {
    "FEATURE_PUBLIC_CHANNELS_ENABLED": "false",
    "FEATURE_AUTOMATED_SENDS_ENABLED": "false",
    "FEATURE_AGENT_RUNTIME_ENABLED": "false",
}


@pytest.fixture(scope="module")
def staging_config() -> dict[str, object]:
    command = ["docker", "compose"]
    for compose_file in COMPOSE_FILES:
        command.extend(("-f", compose_file))
    command.extend(("config", "--format", "json"))
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    return document


def test_private_tls_topology_is_deny_by_default(staging_config: dict[str, object]) -> None:
    services = _mapping(staging_config, "services")
    assert set(services) == {"api", "migrate", "tls", "worker"}
    assert "openclaw" not in json.dumps(staging_config).casefold()

    tls = _mapping(services, "tls")
    assert tls["image"] == "nha-trang-laundry-tls:local"
    assert _mapping(tls, "build")["dockerfile"] == "deploy/staging/Caddy.Dockerfile"
    tls_dockerfile = (ROOT / "deploy/staging/Caddy.Dockerfile").read_text(encoding="utf-8")
    assert (
        "caddy:2.10.2-alpine@"
        "sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d" in tls_dockerfile
    )
    assert "setcap -r /usr/bin/caddy" in tls_dockerfile
    assert tls["entrypoint"] == ["caddy"]
    ports = tls["ports"]
    assert isinstance(ports, list) and ports == [
        {
            "name": "private-operator-https",
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 8443,
            "published": "8443",
            "protocol": "tcp",
        }
    ]
    for name in ("api", "migrate", "worker"):
        assert "ports" not in _mapping(services, name)

    networks = _mapping(staging_config, "networks")
    assert _mapping(networks, "ingress-private")["internal"] is True
    assert _mapping(networks, "database-private")["external"] is True
    assert set(_mapping(_mapping(services, "tls"), "networks")) == {"ingress-private"}
    assert set(_mapping(_mapping(services, "worker"), "networks")) == {"database-private"}


def test_secrets_identities_and_migration_job_are_least_privilege(
    staging_config: dict[str, object],
) -> None:
    services = _mapping(staging_config, "services")
    assert {str(_mapping(services, name)["user"]) for name in services} == {
        "10001:10001",
        "10002:10002",
        "10003:10003",
        "10004:10004",
    }
    for name in services:
        service = _mapping(services, name)
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["init"] is True
        pids_limit = service["pids_limit"]
        tmpfs = service["tmpfs"]
        assert isinstance(pids_limit, int) and pids_limit <= 128
        assert isinstance(tmpfs, list) and all(
            isinstance(item, str) and "noexec" in item and "nosuid" in item for item in tmpfs
        )

    secrets = _mapping(staging_config, "secrets")
    assert len(secrets) == 10
    assert all(_mapping(secrets, name)["external"] is True for name in secrets)
    assert {
        item["source"]
        for name in ("api", "migrate", "worker")
        for item in _secret_list(_mapping(services, name))
        if str(item["source"]).endswith("database_url")
    } == {
        "staging_api_database_url",
        "staging_migration_database_url",
        "staging_worker_database_url",
    }
    for name in ("api", "worker"):
        environment = _mapping(_mapping(services, name), "environment")
        assert {key: environment[key] for key in DISABLED_FLAGS} == DISABLED_FLAGS
        assert "DATABASE_URL" not in environment
    assert _mapping(services, "migrate")["entrypoint"] == [
        "python",
        "-m",
        "nha_trang_laundry_db.migration_job",
    ]
    for name in ("api", "worker"):
        depends_on = _mapping(_mapping(services, name), "depends_on")
        assert _mapping(depends_on, "migrate")["condition"] == "service_completed_successfully"


def test_migration_job_reads_only_the_bounded_secret_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = tmp_path / "database_url"
    secret.write_text("postgresql://synthetic:synthetic@database/staging\n", encoding="utf-8")
    observed: list[str] = []

    class SyntheticConnection:
        def __enter__(self) -> SyntheticConnection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def connect(database_url: str) -> SyntheticConnection:
        observed.append(database_url)
        return SyntheticConnection()

    monkeypatch.setattr(migration_job, "DATABASE_URL_SECRET", secret)
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(migration_job, "apply_migrations", lambda _: ("0019",))

    migration_job.main()

    output = capsys.readouterr().out
    assert observed == ["postgresql://synthetic:synthetic@database/staging"]
    assert "synthetic" not in output
    assert output == "Applied forward-only migrations: 1\n"


def test_smoke_and_rollback_contract_remain_private_and_forward_only() -> None:
    smoke = (ROOT / "scripts/staging_smoke.py").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/private-staging.md").read_text(encoding="utf-8")
    caddyfile = (ROOT / "deploy/staging/Caddyfile").read_text(encoding="utf-8")

    assert "ssl.create_default_context" in smoke
    assert "CERT_NONE" not in smoke and "check_hostname = False" not in smoke
    assert all(path in smoke for path in ("/healthz", "/staff/", "/openclaw", "/webhook"))
    assert "admin off" in caddyfile and "auto_https off" in caddyfile
    assert "reverse_proxy api:8000" in caddyfile
    assert "@sha256:<64 lowercase hex>" in runbook
    assert "never reverses a migration" in runbook
    assert "no release authority" in smoke.casefold()


def _mapping(parent: dict[str, object], key: str) -> dict[str, object]:
    value = parent[key]
    assert isinstance(value, dict)
    assert all(isinstance(item, str) for item in value)
    return cast(dict[str, object], value)


def _secret_list(service: dict[str, object]) -> list[dict[str, object]]:
    value = service["secrets"]
    assert isinstance(value, list) and all(isinstance(item, dict) for item in value)
    return cast(list[dict[str, object]], value)
