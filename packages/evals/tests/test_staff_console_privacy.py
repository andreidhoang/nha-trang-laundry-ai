"""SHADOW-CONSOLE-001: the client must not persist customer data on the device."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVICE_WORKER = ROOT / "apps/web/sw.js"


def test_the_service_worker_precaches_only_the_application_shell() -> None:
    source = SERVICE_WORKER.read_text(encoding="utf-8")

    shell = re.search(r"const SHELL = \[(.*?)\];", source, re.S)
    assert shell is not None
    entries = [item.strip().strip('"') for item in shell.group(1).split(",") if item.strip()]
    assert entries == [
        "/staff/",
        "/staff/styles.css",
        "/staff/app.js",
        "/staff/manifest.webmanifest",
    ]


def test_the_service_worker_never_writes_a_response_into_the_cache() -> None:
    """A network-first shell may read the cache. Writing an API response would persist customer
    data on a shared device, which the Shadow console must never do."""
    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert "cache.put" not in source
    assert "caches.put" not in source


def test_the_service_worker_declines_every_request_outside_the_shell_path() -> None:
    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert 'pathname.startsWith("/staff/")' in source
    assert '"/internal/' not in source
