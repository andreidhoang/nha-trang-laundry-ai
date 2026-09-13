"""Cross-machine checks for operator-facing verification entry points."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_console_interaction_verifier_uses_the_checkout_it_was_run_from() -> None:
    source = (ROOT / "scripts" / "verify_console_interaction.py").read_text(encoding="utf-8")

    assert 'Path("/Users/' not in source
    assert "ROOT = Path(__file__).resolve().parents[1]" in source
    assert 'WEB = ROOT / "apps" / "web"' in source


def test_console_interaction_verifier_resolves_held_requests_before_shutdown() -> None:
    source = (ROOT / "scripts" / "verify_console_interaction.py").read_text(encoding="utf-8")

    assert "held_ticket_routes.append(route)" in source
    assert "for held_route in held_ticket_routes:" in source
    assert "held_ticket_routes.clear()" in source
