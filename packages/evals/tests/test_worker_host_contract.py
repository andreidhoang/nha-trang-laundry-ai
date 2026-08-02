from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_worker_container_uses_dependency_readiness_not_liveness() -> None:
    dockerfile = (ROOT / "apps/worker/Dockerfile").read_text(encoding="utf-8")
    main = (ROOT / "apps/worker/src/nha_trang_laundry_worker/main.py").read_text(encoding="utf-8")

    assert "/readyz" in dockerfile
    assert '"/livez"' in main
    assert '"/readyz"' in main
    assert '"provider_send_available": False' in main


def test_worker_source_has_no_channel_or_provider_send_client() -> None:
    source_root = ROOT / "apps/worker/src/nha_trang_laundry_worker"
    rendered = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(source_root.glob("*.py"))
    ).casefold()

    for prohibited in (
        "import openai",
        "from openai",
        "import requests",
        "import httpx",
        "message.send_requested",
        "zalo",
    ):
        assert prohibited not in rendered


def test_production_worker_keeps_all_release_flags_disabled() -> None:
    compose = yaml.safe_load((ROOT / "compose.production.yaml").read_text(encoding="utf-8"))
    environment = compose["services"]["worker"]["environment"]

    assert environment["FEATURE_PUBLIC_CHANNELS_ENABLED"] == "false"
    assert environment["FEATURE_AUTOMATED_SENDS_ENABLED"] == "false"
    assert environment["FEATURE_AGENT_RUNTIME_ENABLED"] == "false"
    assert environment["WORKER_INTERNAL_OUTBOX_ENABLED"] == "false"
    assert environment["WORKER_AGENT_QUEUE_ENABLED"] == "false"
