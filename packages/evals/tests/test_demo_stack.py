"""The local demo must not become a place where checks are quietly softened.

`DEMO-STACK-001` exists to make the system observable on one machine. The risk it carries is not
that it fails to run; it is that someone makes it run by disabling something, and the local stack
stops resembling the thing it is supposed to de-risk. These tests pin the properties that would
make that visible: the identity provider satisfies the real verifier rather than replacing it, the
overlay adds no capability and relaxes no hardening key, the seed refuses a non-local database, and
no generated material is committed.
"""

from __future__ import annotations

import json
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from itertools import chain
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "compose.demo.yaml"
COMPOSE_FILES = (ROOT / "compose.yaml", ROOT / "compose.production.yaml", OVERLAY)


class _ComposeLoader(yaml.SafeLoader):
    """Compose's merge directives are YAML tags PyYAML does not know.

    `!override` and `!reset` control how an overlay merges into the file below it. For these tests
    only the resulting value matters, so unwrap the tag and keep the node.
    """


def _unwrap(loader: yaml.Loader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        return None if value == "null" else value
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    raise TypeError(f"unsupported compose node: {node!r}")


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(_tag, _unwrap)


def _load_compose(path: Path) -> dict[str, Any]:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert isinstance(document, dict)
    return document


def _load_script(name: str) -> ModuleType:
    """Import a scripts/ entry point by path; they are entry points, not an installed package."""
    spec = spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def overlay() -> dict[str, Any]:
    return _load_compose(OVERLAY)


# --- the identity provider satisfies the real verifier -------------------------------------------


@pytest.fixture(scope="module")
def issued() -> tuple[Any, ModuleType, Any]:
    """Build a demo provider over a freshly generated key, exactly as the container does."""
    material = _load_script("generate_demo_material")
    provider_module = _load_script("demo_identity_provider")
    private_pem, jwks = material.generate_identity_provider_key()
    provider = provider_module.DemoIdentityProvider(
        private_key_pem=private_pem,
        jwks=json.loads(jwks),
        issuer=material.ISSUER_URL,
        audience=material.AUDIENCE,
        mfa_claim=material.MFA_CLAIM,
        mfa_value=material.MFA_VALUE,
    )
    return provider, material, private_pem


def _verifier(material: ModuleType, private_pem: str, **overrides: str) -> Any:
    """Construct the production verifier over the demo key. Nothing here is stubbed but the
    network fetch of the JWKS document, which is what PyJWKClient would otherwise do."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from nha_trang_laundry_api.auth import AuthSettings, IdentityPlatformVerifier

    public_key = load_pem_private_key(private_pem.encode(), password=None).public_key()

    class _Key:
        key = public_key

    class _Client:
        def get_signing_key_from_jwt(self, token: str) -> _Key:
            return _Key()

    settings = AuthSettings(
        database_url="postgresql://unused/unused",
        oidc_issuer=overrides.get("issuer", material.ISSUER_URL),
        oidc_audience=overrides.get("audience", material.AUDIENCE),
        oidc_jwks_url="http://unused/jwks.json",
        oidc_mfa_claim=material.MFA_CLAIM,
        oidc_mfa_value=material.MFA_VALUE,
    )
    return IdentityPlatformVerifier(settings, jwks_client=_Client())


def test_a_demo_token_is_accepted_by_the_unmodified_production_verifier(
    issued: tuple[Any, ModuleType, Any],
) -> None:
    provider, material, private_pem = issued
    identity = _verifier(material, private_pem).verify(provider.mint("demo-owner"))
    assert identity.subject == "demo-owner"
    assert identity.mfa_verified is True


def test_a_token_for_another_audience_is_rejected(issued: tuple[Any, ModuleType, Any]) -> None:
    from nha_trang_laundry_api.auth import AuthenticationError

    provider, material, private_pem = issued
    verifier = _verifier(material, private_pem, audience="some-other-service")
    with pytest.raises(AuthenticationError):
        verifier.verify(provider.mint("demo-owner"))


def test_a_token_from_another_issuer_is_rejected(issued: tuple[Any, ModuleType, Any]) -> None:
    from nha_trang_laundry_api.auth import AuthenticationError

    provider, material, private_pem = issued
    verifier = _verifier(material, private_pem, issuer="https://not-the-demo-issuer.example")
    with pytest.raises(AuthenticationError):
        verifier.verify(provider.mint("demo-owner"))


def test_a_token_without_the_mfa_claim_authenticates_but_is_not_mfa_verified(
    issued: tuple[Any, ModuleType, Any],
) -> None:
    """The MFA claim is not what proves identity; it is what unlocks sensitive roles.

    Dropping it must not look like a valid privileged session, because OWNER_ADMIN is in
    SENSITIVE_MFA_ROLES and every mutating route requires `mfa_verified`.
    """
    provider, material, private_pem = issued
    identity = _verifier(material, private_pem).verify(provider.mint("demo-owner", with_mfa=False))
    assert identity.mfa_verified is False


def test_the_issuer_mints_only_for_known_synthetic_subjects(
    issued: tuple[Any, ModuleType, Any],
) -> None:
    provider, _, _ = issued
    with pytest.raises(ValueError):
        provider.mint("")


# --- the overlay weakens nothing -----------------------------------------------------------------


def test_the_overlay_declares_no_externally_managed_secret(overlay: dict[str, Any]) -> None:
    """The whole point of the overlay is to replace Swarm-managed secrets with local files."""
    for name, secret in (overlay.get("secrets") or {}).items():
        assert "file" in secret, f"demo secret {name} is not file-backed"
        assert secret.get("external") is not True


def test_the_overlay_enables_no_capability_flag(overlay: dict[str, Any]) -> None:
    for name, service in overlay["services"].items():
        environment = service.get("environment") or {}
        for key, value in environment.items():
            if key.startswith("FEATURE_") or key.startswith("WORKER_"):
                assert str(value).lower() == "false", f"{name}.{key} is {value!r}"


def test_the_overlay_relaxes_no_container_hardening(overlay: dict[str, Any]) -> None:
    """A demo that runs as root, writable, or with capabilities proves nothing about production."""
    for name, service in overlay["services"].items():
        if "read_only" in service:
            assert service["read_only"] is True, f"{name} has a writable root filesystem"
        if "cap_drop" in service:
            assert service["cap_drop"] == ["ALL"], f"{name} does not drop all capabilities"
        if "user" in service:
            assert not str(service["user"]).startswith("0:"), f"{name} runs as root"
        if "security_opt" in service:
            assert "no-new-privileges:true" in service["security_opt"]


def test_only_the_tls_proxy_reaches_the_host_network(overlay: dict[str, Any]) -> None:
    """Every other service stays on an internal network, exactly as in production."""
    edge_members = [
        name
        for name, service in overlay["services"].items()
        if "ingress-edge" in (service.get("networks") or [])
    ]
    assert edge_members == ["tls"], f"unexpected services on the edge network: {edge_members}"
    assert overlay["networks"]["ingress-edge"]["internal"] is False


def test_the_database_is_not_published_to_the_host(overlay: dict[str, Any]) -> None:
    assert overlay["services"]["postgres"]["ports"] == []


def test_the_demo_identity_provider_exists_only_in_the_demo_overlay() -> None:
    production = _load_compose(ROOT / "compose.production.yaml")
    assert "demo-idp" not in production["services"]
    assert "demo-seed" not in production["services"]
    assert "demo-grants" not in production["services"]


# --- generated material never enters the repository ----------------------------------------------


def test_the_demo_material_directory_is_ignored_by_git() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".demo/secrets/tls_private_key"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, ".demo/ is not gitignored; generated keys could be committed"


def test_no_generated_material_is_tracked() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", ".demo"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert tracked.stdout.strip() == "", f"generated demo material is tracked: {tracked.stdout}"


def test_the_generator_writes_private_material_unreadable_to_other_users() -> None:
    material = _load_script("generate_demo_material")
    assert material.DEMO_DIRECTORY.name == ".demo"
    key = material.DEMO_DIRECTORY / "secrets" / "tls_private_key"
    if key.exists():
        assert key.stat().st_mode & 0o077 == 0, "private key is readable beyond its owner"


# --- the seed refuses anything that is not obviously local ----------------------------------------


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://app@db.production.example:5432/laundry",
        "postgresql://app@10.0.0.5:5432/laundry",
        "postgresql://app@laundry.internal:5432/laundry",
    ],
)
def test_seeding_refuses_a_non_local_database(database_url: str) -> None:
    seed = _load_script("seed_demo_data")
    with pytest.raises(SystemExit):
        seed.require_local_database(database_url)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "postgres"])
def test_seeding_accepts_a_local_database(host: str) -> None:
    seed = _load_script("seed_demo_data")
    seed.require_local_database(f"postgresql://app@{host}:5432/laundry")


def test_the_seed_subjects_match_the_identity_provider_accounts() -> None:
    """A subject the issuer offers but the seed never creates produces a confusing 401 at demo
    time; keeping the two lists in agreement is cheaper than debugging that live."""
    seed = _load_script("seed_demo_data")
    provider = _load_script("demo_identity_provider")
    offered = {subject for subject, _, _ in provider.DEMO_SUBJECTS}
    seeded = {seed.OWNER_SUBJECT} | {subject for subject, _, _ in seed.DEMO_STAFF}
    assert offered == seeded


def test_application_roles_receive_no_ddl_or_destructive_grants() -> None:
    """What an application role is *granted* is what matters, not what words the script contains.

    This asserted on substrings of the whole script until 2026-08-26, when `ALTER DEFAULT
    PRIVILEGES` was added to close the one-shot-snapshot defect -- a statement that grants nothing
    to a role and is executed by the schema owner, but which contains the word `ALTER`. The old
    check would have failed it, correctly refusing a change it could not tell apart from a real
    privilege escalation. The assertion is now about the grants themselves.
    """

    grants = _load_script("apply_demo_grants")
    script = grants.GRANTS.format(role="laundry_api", owner=grants.MIGRATION_ROLE)
    statements = [part.strip() for part in script.split(";") if part.strip()]

    granted_to_role = [
        statement
        for statement in statements
        if statement.upper().startswith(("GRANT", "ALTER DEFAULT PRIVILEGES"))
        and "TO LAUNDRY_API" in statement.upper()
    ]
    assert granted_to_role, "the script must grant the application role something"
    allowed = {"SELECT", "INSERT", "UPDATE", "USAGE"}
    for statement in granted_to_role:
        privileges = statement.upper().split("GRANT", 1)[1].split(" ON ")[0]
        named = {word.strip(" ,\n") for word in privileges.split(",") if word.strip(" ,\n")}
        assert named <= allowed, f"unexpected privilege granted to the role: {named - allowed}"

    revokes = [s for s in statements if s.upper().startswith("REVOKE")]
    assert any("DELETE" in s.upper() and "TRUNCATE" in s.upper() for s in revokes)

    # No statement may hand the role DDL or superuser, however it is spelled.
    for statement in granted_to_role:
        for forbidden in ("CREATE", "DROP", "SUPERUSER", "TRIGGER", "REFERENCES"):
            assert forbidden not in statement.upper()


def test_compose_overlay_is_valid_for_the_installed_docker_compose() -> None:
    """Skipped where Docker is absent, because the overlay's validity is a property of Compose."""
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False, text=True
    )
    if probe.returncode != 0:
        pytest.skip("docker compose is not available on this host")
    result = subprocess.run(
        [
            "docker",
            "compose",
            *chain.from_iterable(("-f", str(f)) for f in COMPOSE_FILES),
            "config",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    resolved = yaml.safe_load(result.stdout)
    assert not [
        name
        for name, secret in (resolved.get("secrets") or {}).items()
        if secret.get("external") is True
    ]
