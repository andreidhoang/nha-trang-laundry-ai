"""Cold console starts must not issue store-scoped requests before store resolution.

The route guard runs before the session request finishes. Letting a route render in that state made
eager list screens call ``/stores/null/...`` and produce a recoverable but noisy 422 on every cold
deep link. The browser checks exercise the behaviour; this small contract keeps the guard from
silently returning to the unsafe state when those checks are not installed.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps" / "web" / "app.js"


def test_unknown_session_renders_a_loading_guard() -> None:
    source = APP.read_text(encoding="utf-8")

    assert 'if (state.status === "unknown") return sessionLoadingScreen();' in source
    assert 'if (state.status === "unknown") return null;' not in source
    assert '"aria-busy": "true"' in source
    assert '"aria-hidden": "true"' in source
    assert "ariaBusy" not in source
    assert "ariaHidden" not in source
