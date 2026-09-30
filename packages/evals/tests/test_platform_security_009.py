"""`PLATFORM-SECURITY-009` P1 and P5 deployment artefacts (P4: `test_shop_ca_constraints.py`).

- **P1** the worker container is not given the deployment hash key, and the SQL the self-managed
  host pipes into `psql` grants the worker its own narrow set rather than the API's.
- **P5** CI creates the application roles, applies the grants, verifies them, and runs the
  role-separated smoke as those roles -- not as the superuser every other test runs as.

The database side of P1/P5 (every worker path executed as `laundry_worker`, the API's write paths
as `laundry_api`) is `apps/worker/tests/test_worker_least_privilege.py` and
`packages/db/tests/test_api_role_write_paths.py`.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
HOST = "console.giatlasachcong.lan"


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(f"_ps009_evals_{name}", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


# --- P1: the key and the grant ------------------------------------------------------------------


class _ComposeLoader(yaml.SafeLoader):
    """Compose's `!reset` / `!override` merge tags, kept as their value (as the build contract)."""


def _tagged_value(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(_tag, _tagged_value)


@pytest.mark.parametrize("compose_file", ["compose.production.yaml", "compose.r1.yaml"])
def test_the_worker_container_is_not_given_the_hash_key_and_the_api_still_is(
    compose_file: str,
) -> None:
    document = yaml.load((ROOT / compose_file).read_text("utf-8"), Loader=_ComposeLoader)
    services = document["services"]

    def targets(service: str) -> set[str]:
        return {
            entry["target"] if isinstance(entry, dict) else entry
            for entry in services[service].get("secrets", [])
        }

    assert "hash_key" not in targets("worker"), f"{compose_file} mounts the hash key into worker"
    assert "database_url" in targets("worker")
    assert "hash_key" in targets("api"), "the API writes keyed digests and must keep its key"


def test_the_worker_settings_read_no_hash_key() -> None:
    from nha_trang_laundry_worker.host import WorkerSettings

    assert not [name for name in WorkerSettings.model_fields if "hash" in name or "key" in name]


def test_the_shop_setup_sql_grants_the_worker_its_own_set_not_the_apis() -> None:
    from nha_trang_laundry_db.role_grants import WORKER_GRANTS

    emitted = _load_script("emit_shop_database_setup")._grants()
    statements = [part.strip() for part in emitted.split(";") if part.strip()]
    worker = [s for s in statements if s.endswith("laundry_worker")]

    assert not [s for s in worker if s.startswith("GRANT") and "ALL TABLES" in s], (
        "the worker must not receive a grant on every table"
    )
    assert not [s for s in worker if s.startswith("ALTER DEFAULT PRIVILEGES") and " GRANT " in s], (
        "the worker must not receive future tables by default"
    )
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM laundry_worker" in worker
    for grant in WORKER_GRANTS:
        assert grant.statement("laundry_worker") in worker
    # The API's grant is unchanged.
    assert (
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO laundry_api" in statements
    )
    # And the revoke comes before the grants, or a re-run would take away what it just gave.
    first_grant = min(i for i, s in enumerate(worker) if s.startswith("GRANT"))
    assert all(i < first_grant for i, s in enumerate(worker) if s.startswith("REVOKE"))


# --- P5: CI proves the roles --------------------------------------------------------------------


def _ci_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/quality.yml").read_text("utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"]["python-quality"]["steps"]
    return steps


def _index(steps: list[dict[str, Any]], pattern: str) -> int:
    for position, step in enumerate(steps):
        if re.search(pattern, str(step.get("run", ""))):
            return position
    raise AssertionError(f"no CI step runs {pattern!r}")


def test_ci_applies_and_verifies_the_grants_and_runs_the_role_separated_smoke() -> None:
    steps = _ci_steps()
    roles = _index(steps, r"CREATE ROLE laundry_api LOGIN")
    migrate = _index(steps, r"scripts/apply_migrations\.py")
    grants = _index(steps, r"scripts/apply_demo_grants\.py")
    verify = _index(steps, r"scripts/verify_database_grants\.py")
    smoke = _index(steps, r"test_worker_least_privilege\.py")
    full = _index(steps, r"pytest --require-postgres-integration$")
    assert roles < grants and migrate < grants < verify < smoke < full

    assert "CREATE ROLE laundry_worker LOGIN" in steps[roles]["run"]
    assert "--owner" in steps[grants]["run"], (
        "CI's migrations run as its superuser, not laundry_migrate"
    )
    smoke_step = steps[smoke]
    assert "test_api_role_write_paths.py" in smoke_step["run"]
    assert "--require-postgres-integration" in smoke_step["run"]
    environment = smoke_step.get("env", {})
    # The smoke logs in as the roles; SET ROLE from the superuser is the local fallback only.
    assert environment.get("LAUNDRY_API_DATABASE_URL", "").startswith("postgresql://laundry_api:")
    assert environment.get("LAUNDRY_WORKER_DATABASE_URL", "").startswith(
        "postgresql://laundry_worker:"
    )
