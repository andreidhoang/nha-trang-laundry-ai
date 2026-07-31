"""Repository-level pytest controls for non-skippable CI integration coverage."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from pluggy import Result

POSTGRES_SKIP_REASON = "DATABASE_URL is required for PostgreSQL integration tests"
POSTGRES_SKIP_FAILURE = (
    "PostgreSQL integration coverage is required; a database-backed test attempted to skip."
)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the explicit CI-only PostgreSQL integration guard."""
    group = parser.getgroup("nha-trang-laundry-ci")
    group.addoption(
        "--require-postgres-integration",
        action="store_true",
        default=False,
        help="Fail when DATABASE_URL is absent or a PostgreSQL integration test attempts to skip.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Reject a guarded run before collection when its synthetic database URL is missing."""
    if config.getoption("require_postgres_integration") and not os.environ.get("DATABASE_URL"):
        raise pytest.UsageError(
            "--require-postgres-integration requires DATABASE_URL; "
            "CI must provide a reachable synthetic PostgreSQL service."
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo[Any],
) -> Generator[None, Result[pytest.TestReport], None]:
    """Convert the repository's missing-PostgreSQL skip into a deterministic test failure."""
    outcome = yield
    report = outcome.get_result()
    guard_enabled = item.config.getoption("require_postgres_integration")
    if guard_enabled and report.skipped and POSTGRES_SKIP_REASON in str(report.longrepr):
        report.outcome = "failed"
        report.longrepr = POSTGRES_SKIP_FAILURE
