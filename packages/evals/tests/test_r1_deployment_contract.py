"""`SHOP-DEPLOY-001`: the R1 topology, and the listener the staging file only promised.

`compose.production.yaml` attaches `tls` only to `ingress-private`, which is `internal: true`.
Docker accepts the port declaration and silently discards it -- `HostConfig.PortBindings` asks for
the binding, `NetworkSettings.Ports` comes back empty, the network has no gateway, and nothing
reports an error. The host listener `docs/runbooks/private-staging.md` promises does not exist, and
`compose.demo.yaml` recorded that while deliberately leaving production alone.

These tests pin the fix and everything the fix must not cost. The staging file and
`test_staging_deployment_contract.py` are left exactly as they are: that file honestly describes a
private staging host, and its acceptance commands are cited by the recorded evidence of three
completed items.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = "compose.r1.yaml"
DISABLED_FLAGS = {
    "FEATURE_PUBLIC_CHANNELS_ENABLED": "false",
    "FEATURE_AUTOMATED_SENDS_ENABLED": "false",
    "FEATURE_AGENT_RUNTIME_ENABLED": "false",
}


def _config(*profiles: str) -> dict[str, object]:
    command = ["docker", "compose", "-f", COMPOSE_FILE]
    for profile in profiles:
        command.extend(("--profile", profile))
    command.extend(("config", "--format", "json"))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"docker compose is unavailable: {result.stderr.strip()[:200]}")
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    return cast(dict[str, object], document)


@pytest.fixture(scope="module")
def shop_config() -> dict[str, object]:
    return _config()


@pytest.fixture(scope="module")
def self_managed_config() -> dict[str, object]:
    return _config("self-managed-database")


def test_the_tls_listener_exists_and_is_the_only_thing_on_the_host(
    shop_config: dict[str, object],
) -> None:
    """The whole point of this file: one service reachable from the host, and it is the proxy.

    A published port on a service attached only to `internal: true` networks is not a listener.
    Asserting that `ingress-edge` exists would not catch a regression; asserting that `tls` is the
    only member of it, and that the network is not internal, is what makes the port real and keeps
    it alone.
    """

    services = _mapping(shop_config, "services")
    networks = _mapping(shop_config, "networks")

    # `docker compose config` omits `internal` when it is false, so the assertion is "not
    # internal" rather than "internal is False" -- the absence is the default and the default is
    # what makes the published port real.
    assert _mapping(networks, "ingress-edge").get("internal") is not True
    assert _mapping(networks, "ingress-private")["internal"] is True
    assert _mapping(networks, "database-private")["internal"] is True

    on_edge = {
        name
        for name in services
        if "ingress-edge" in _mapping(_mapping(services, name), "networks")
    }
    assert on_edge == {"tls"}
    # Keycloak in particular: it holds every staff identity and has no outbound need.
    assert set(_mapping(_mapping(services, "keycloak"), "networks")) == {
        "ingress-private",
        "database-private",
    }

    ports = _mapping(services, "tls")["ports"]
    assert isinstance(ports, list) and len(ports) == 1
    published = ports[0]
    assert isinstance(published, dict)
    assert published["target"] == 8443
    assert published["published"] == "8443"
    # An unset variable must produce a console nobody can reach rather than one everybody can.
    assert published["host_ip"] == "127.0.0.1"

    for name in services:
        if name != "tls":
            assert "ports" not in _mapping(services, name) or not _mapping(services, name)["ports"]


def test_no_capability_is_enabled_by_deploying_the_code(shop_config: dict[str, object]) -> None:
    services = _mapping(shop_config, "services")
    for name in ("api", "worker"):
        environment = _mapping(_mapping(services, name), "environment")
        assert {key: environment[key] for key in DISABLED_FLAGS} == DISABLED_FLAGS
        # The DSN arrives as a mounted secret file, never as an environment variable.
        assert "DATABASE_URL" not in environment
    worker_environment = _mapping(_mapping(services, "worker"), "environment")
    assert worker_environment["WORKER_INTERNAL_OUTBOX_ENABLED"] == "false"
    assert worker_environment["WORKER_AGENT_QUEUE_ENABLED"] == "false"


def test_the_hardening_the_staging_file_established_is_unchanged(
    shop_config: dict[str, object],
) -> None:
    services = _mapping(shop_config, "services")
    assert set(services) == {"api", "keycloak", "migrate", "tls", "worker"}
    for name in services:
        service = _mapping(services, name)
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["init"] is True
        pids_limit = service["pids_limit"]
        assert isinstance(pids_limit, int) and pids_limit <= 128
        tmpfs = service["tmpfs"]
        assert isinstance(tmpfs, list) and all(
            isinstance(item, str) and "noexec" in item and "nosuid" in item for item in tmpfs
        )
    assert {str(_mapping(services, name)["user"]) for name in services} == {
        "10001:10001",
        "10002:10002",
        "10003:10003",
        "10004:10004",
        "10005:10005",
    }

    secrets = _mapping(shop_config, "secrets")
    assert all(_mapping(secrets, name)["external"] is True for name in secrets)
    assert {
        item["source"]
        for name in ("api", "migrate", "worker")
        for item in _secret_list(_mapping(services, name))
        if str(item["source"]).endswith("database_url")
    } == {
        "r1_api_database_url",
        "r1_migration_database_url",
        "r1_worker_database_url",
    }
    for name in ("api", "worker"):
        depends_on = _mapping(_mapping(services, name), "depends_on")
        assert _mapping(depends_on, "migrate")["condition"] == "service_completed_successfully"


def test_the_console_is_reached_by_one_name_and_the_healthcheck_still_works(
    shop_config: dict[str, object],
) -> None:
    """`127.0.0.1` in the trusted hosts is load-bearing, not leftover.

    The API container's own HEALTHCHECK calls `/healthz` on the loopback address.
    `TrustedHostMiddleware` rejects a host it does not know, so dropping `127.0.0.1` leaves the
    container permanently unhealthy for a reason nothing in the logs explains.
    """

    environment = _mapping(_mapping(_mapping(shop_config, "services"), "api"), "environment")
    origins = str(environment["STAFF_ALLOWED_ORIGINS"])
    hosts = str(environment["API_TRUSTED_HOSTS"]).split(",")

    assert origins.startswith("https://")
    assert "staging.internal" not in origins
    assert "127.0.0.1" in hosts
    assert any(host not in {"localhost", "127.0.0.1"} for host in hosts)

    caddyfile = (ROOT / "deploy/production/Caddyfile").read_text(encoding="utf-8")
    assert "auto_https off" in caddyfile
    assert "admin off" in caddyfile
    # Anything that is not the console name learns nothing about what runs here.
    assert "421" in caddyfile


def test_the_database_branch_is_a_profile_rather_than_a_second_file(
    shop_config: dict[str, object], self_managed_config: dict[str, object]
) -> None:
    """The hosting decision picks provider-managed PITR or self-managed WAL archiving.

    Both are the same topology with one service present or absent, so they are one file. A second
    file would drift, and the drift would be discovered on the morning somebody needs to restore.
    """

    assert "postgres" not in _mapping(shop_config, "services")
    postgres = _mapping(_mapping(self_managed_config, "services"), "postgres")
    assert postgres["read_only"] is True
    assert postgres["cap_drop"] == ["ALL"]
    assert not postgres.get("ports")
    assert set(_mapping(postgres, "networks")) == {"database-private"}
    environment = _mapping(postgres, "environment")
    # The password arrives as a mounted file; an environment variable would reach the image layer,
    # `docker inspect`, and every log line that dumps the environment.
    assert "POSTGRES_PASSWORD" not in environment
    assert environment["POSTGRES_PASSWORD_FILE"] == "/run/secrets/postgres_password"


def test_the_staging_topology_is_left_exactly_as_it_was() -> None:
    """Its name is honest and its acceptance commands are cited by closed evidence.

    `STAGING-001`, `CONTAINER-001` and `DEMO-STACK-001` are COMPLETE and their recorded evidence
    names `compose.production.yaml`. Renaming it, or adding R1's services to it, would rewrite what
    those items ran.
    """

    staging = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    assert staging.startswith("name: nha-trang-laundry-private-staging")
    assert "ingress-edge" not in staging


def _mapping(parent: dict[str, object], key: str) -> dict[str, object]:
    value = parent[key]
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _secret_list(service: dict[str, object]) -> list[dict[str, object]]:
    value = service["secrets"]
    assert isinstance(value, list)
    return cast(list[dict[str, object]], value)
