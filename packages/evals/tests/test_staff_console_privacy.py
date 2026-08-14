"""SHADOW-CONSOLE-001: the client must not persist customer data on the device.

The original version of this file asserted equality against four hard-coded asset paths. That was
correct and became wrong the moment the console was split into modules — and it would have become
wrong silently in the other direction too, since a new module simply would not have been listed.

So the assertions here are now about the *property* rather than the list: the precache contains
exactly the static assets that exist on disk, contains nothing under any API path, and the worker
has no branch anywhere that writes a response into a cache. The expected shell is derived from the
filesystem by the same generator the service worker is produced from, which means adding a module
can no longer drift this check, and removing one can no longer leave a stale entry behind.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
SERVICE_WORKER = WEB / "sw.js"

SHELL_SUFFIXES = {".js", ".css", ".webmanifest", ".svg"}


def declared_shell() -> list[str]:
    source = SERVICE_WORKER.read_text(encoding="utf-8")
    block = re.search(r"const SHELL = \[(.*?)\];", source, re.S)
    assert block is not None, "the service worker has no SHELL array"
    return [item.strip().strip('"') for item in block.group(1).split(",") if item.strip()]


def assets_on_disk() -> list[str]:
    paths = ["/staff/"]
    for path in sorted(WEB.rglob("*")):
        if path.is_file() and path.suffix in SHELL_SUFFIXES and path.name != "sw.js":
            paths.append(f"/staff/{path.relative_to(WEB).as_posix()}")
    return paths


def test_the_precache_is_exactly_the_static_shell_that_exists() -> None:
    """Neither a missing module nor a deleted one may pass unnoticed."""

    assert declared_shell() == assets_on_disk()


def test_the_precache_names_no_api_path() -> None:
    for entry in declared_shell():
        assert entry.startswith("/staff/"), entry
        assert "/internal/" not in entry, entry


def test_the_service_worker_never_writes_a_response_into_the_cache() -> None:
    """A network-first shell may read the cache. Writing an API response would leave customer data
    on a shared device, which the Shadow console must never do."""

    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert "cache.put" not in source
    assert "caches.put" not in source
    assert ".put(" not in source


def test_the_service_worker_declines_every_request_outside_the_shell_path() -> None:
    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert 'pathname.startsWith("/staff/")' in source
    assert '"/internal/' not in source


def test_the_service_worker_ignores_mutations_entirely() -> None:
    """A worker that intercepted a POST could replay it. This one returns before it can."""

    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert 'event.request.method !== "GET"' in source


def test_the_client_registers_no_background_sync() -> None:
    """Background sync is the mechanism that would deliver a stale command hours later."""

    for source in WEB.rglob("*.js"):
        text = source.read_text(encoding="utf-8")
        assert "sync.register" not in text, source
        assert "SyncManager" not in text, source
        assert "periodicSync" not in text, source
