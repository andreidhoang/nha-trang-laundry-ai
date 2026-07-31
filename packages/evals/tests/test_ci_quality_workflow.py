from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/quality.yml"
PLUGIN_DIRECTORY = "runtime/openclaw/public-cell/plugin"


def _python_quality_job() -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("python-quality")
    assert isinstance(job, dict)
    return job


def _run_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _command_index(steps: list[dict[str, Any]], command: str) -> int:
    return next(index for index, step in enumerate(steps) if step.get("run") == command)


def test_python_quality_job_uses_healthy_synthetic_postgres_16() -> None:
    job = _python_quality_job()
    services = job.get("services")
    assert isinstance(services, dict)
    postgres = services.get("postgres")
    assert isinstance(postgres, dict)
    assert str(postgres.get("image", "")).startswith("postgres:16.")

    service_environment = postgres.get("env")
    assert isinstance(service_environment, dict)
    assert service_environment == {
        "POSTGRES_DB": "nha_trang_laundry_ci",
        "POSTGRES_USER": "ci_app",
        "POSTGRES_PASSWORD": "ci-only-password",
    }

    options = str(postgres.get("options", ""))
    assert "--health-cmd" in options
    assert "pg_isready -U ci_app -d nha_trang_laundry_ci" in options
    assert "--health-interval" in options
    assert "--health-timeout" in options
    assert "--health-retries" in options

    job_environment = job.get("env")
    assert isinstance(job_environment, dict)
    database_url = urlparse(str(job_environment.get("DATABASE_URL", "")))
    assert database_url.scheme == "postgresql"
    assert database_url.hostname in {"127.0.0.1", "localhost"}
    assert database_url.port == 5432
    assert database_url.username == service_environment["POSTGRES_USER"]
    assert database_url.password == service_environment["POSTGRES_PASSWORD"]
    assert database_url.path == f"/{service_environment['POSTGRES_DB']}"


def test_migrations_precede_guarded_database_enabled_pytest() -> None:
    steps = _run_steps(_python_quality_job())
    migration = "uv run python scripts/apply_migrations.py"
    database_tests = "uv run pytest --require-postgres-integration"

    assert _command_index(steps, migration) < _command_index(steps, database_tests)


def test_postgres_guard_rejects_the_repository_database_skip(tmp_path: Path) -> None:
    synthetic_test = tmp_path / "test_database_skip.py"
    synthetic_test.write_text(
        "\n".join(
            (
                "import pytest",
                "",
                "def test_database_integration():",
                '    pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")',
                "",
            )
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql://synthetic:synthetic@127.0.0.1:1/synthetic"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(ROOT / "pyproject.toml"),
            "-p",
            "conftest",
            "--require-postgres-integration",
            str(synthetic_test),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "PostgreSQL integration coverage is required" in result.stdout


def test_plugin_lockfile_install_build_and_tests_are_mandatory() -> None:
    steps = _run_steps(_python_quality_job())
    plugin_commands = ("npm ci", "npm run build", "npm test")
    indexes = []
    for command in plugin_commands:
        index = _command_index(steps, command)
        indexes.append(index)
        assert steps[index].get("working-directory") == PLUGIN_DIRECTORY
    assert indexes == sorted(indexes)


def test_existing_quality_contract_and_least_privilege_gates_remain() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}

    steps = _run_steps(_python_quality_job())
    commands = {step.get("run") for step in steps}
    assert {
        "uv sync --all-packages --all-groups",
        "uv run ruff format --check .",
        "uv run ruff check .",
        "uv run mypy apps packages",
        "uv run python scripts/verify_contracts.py",
        "uv run python scripts/check_context_drift.py",
        "uv run python scripts/report_delivery_status.py",
    }.issubset(commands)

    actions = {str(step["uses"]) for step in steps if "uses" in step}
    assert {
        "actions/checkout@v4",
        "astral-sh/setup-uv@v7",
        "actions/setup-node@v4",
    }.issubset(actions)
