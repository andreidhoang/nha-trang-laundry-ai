"""Film a real-browser walkthrough, with what is being checked written on the screen.

The browser scripts that drive the console against a real API (`verify_daily_operations.py`,
`verify_workflow_conformance.py`, `verify_counter_money.py`) print a pass/fail line per check. That
is the record an engineer reads. The owner asked for something else: to *watch* the shop's
workflows being run, and see for themselves that each one does what the documents claim. A log
line cannot show that "Khách đã nhận đồ" was pressed on the screen a counter would see, or that the
refusal appeared where a staff member would read it. A recording can.

`--video DIR` turns it on. Each browser context is filmed by Playwright into its own file, the
section and the latest check are drawn in a caption bar along the bottom, and every action is
slowed by `--slow-mo` milliseconds so a person can follow it.

**The caption must not change what is being tested.** It lives in a *closed* shadow root on the
document element, outside `<body>`: Playwright's locators pierce open shadow roots but not closed
ones, `page.content()` sees an empty custom element, `innerText` of the body does not include it,
and it takes no pointer events, so it can neither be found by a check nor intercept a click. With
Its styles are a constructed stylesheet and CSSOM properties, not an inline `<style>`: the
console's `style-src 'self'` refuses the latter, and the refusal would land in the page-error list
the scripts report. With `--video` omitted every function here is a no-op and the scripts behave
exactly as before.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
from typing import Any

_CAPTION_SCRIPT = r"""
(() => {
  if (window.__ntlCaption) return;
  const install = () => {
    if (window.__ntlCaption || !document.documentElement) return;
    const host = document.createElement('ntl-caption');
    for (const [k, v] of [['position', 'fixed'], ['left', '0'], ['right', '0'],
        ['bottom', '0'], ['z-index', '2147483647'], ['pointer-events', 'none']]) {
      host.style.setProperty(k, v);
    }
    const root = host.attachShadow({mode: 'closed'});
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(`
        .bar{font:15px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;color:#fff;
             background:rgba(17,24,39,.92);padding:8px 14px;border-top:3px solid #6366f1}
        .sec{font-weight:700;font-size:16px;letter-spacing:.2px}
        .chk{margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .ok{color:#86efac}.fail{color:#fca5a5}.tally{float:right;opacity:.85}
        .big{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
             background:rgba(17,24,39,.9);font:700 34px system-ui,sans-serif;color:#fff;
             text-align:center;white-space:pre-line}
        .big[hidden]{display:none}
`);
    root.adoptedStyleSheets = [sheet];
    root.innerHTML = `
      <div class="bar"><span class="tally"></span>
        <div class="sec"></div><div class="chk"></div></div>
      <div class="big" hidden></div>`;
    const sec = root.querySelector('.sec');
    const chk = root.querySelector('.chk');
    const tally = root.querySelector('.tally');
    const big = root.querySelector('.big');
    const render = (state) => {
      sec.textContent = state.section || '';
      chk.textContent = state.check || '';
      chk.className = 'chk ' + (state.kind || '');
      tally.textContent = state.tally || '';
      big.hidden = !state.banner;
      big.textContent = state.banner || '';
    };
    window.__ntlCaption = (state) => {
      try { sessionStorage.setItem('__ntlCaption', JSON.stringify(state)); } catch (_) {}
      render(state);
    };
    document.documentElement.appendChild(host);
    try { render(JSON.parse(sessionStorage.getItem('__ntlCaption') || '{}')); } catch (_) {}
  };
  if (document.documentElement) install();
  else document.addEventListener('DOMContentLoaded', install);
})();
"""


class Recorder:
    """Owns the video directory, the caption state, and the pages being filmed."""

    def __init__(self, video_dir: str, slow_mo: int, name: str) -> None:
        self.directory = pathlib.Path(video_dir) if video_dir else None
        self.slow_mo = slow_mo if self.directory else 0
        self.name = name
        self.pages: list[Any] = []
        self.films: list[tuple[str, Any]] = []
        self.state: dict[str, str] = {}
        self.passed = 0
        self.failed = 0
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def launch_options(self) -> dict[str, object]:
        return {"slow_mo": self.slow_mo} if self.enabled else {}

    def context_options(self) -> dict[str, object]:
        if not self.directory:
            return {}
        return {
            "record_video_dir": str(self.directory / ".raw"),
            "record_video_size": viewport(),
        }

    def film(self, context: Any, page: Any, label: str) -> Any:
        """Register a page for captions; its video is named `<script>-<n>-<label>.webm`."""

        if self.enabled:
            context.add_init_script(_CAPTION_SCRIPT)
            with contextlib.suppress(Exception):
                page.evaluate(_CAPTION_SCRIPT)
            self.pages.append(page)
            self.films.append((label, page))
            self._push()
        return page

    def section(self, title: str) -> None:
        self.state = {**self.state, "section": title, "check": "", "kind": ""}
        self._push(pause=900)

    def check(self, name: str, passed: bool) -> None:
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        mark = "✓" if passed else "✗ FAIL"
        self.state = {**self.state, "check": f"{mark}  {name}", "kind": "ok" if passed else "fail"}
        self._push(pause=450 if passed else 2500)

    def note(self, text: str) -> None:
        self.state = {**self.state, "check": f"··  {text}", "kind": ""}
        self._push(pause=250)

    def finish(self) -> None:
        verdict = "TẤT CẢ ĐẠT" if not self.failed else f"{self.failed} KIỂM TRA HỎNG"
        self.state = {
            **self.state,
            "banner": f"{self.name}\n{self.passed} đạt · {self.failed} hỏng\n{verdict}",
        }
        self._push(pause=3500)

    def save(self) -> list[pathlib.Path]:
        """Call after each filmed context is closed: give each video a name a person can read."""

        saved: list[pathlib.Path] = []
        if not self.directory:
            return saved
        for index, (label, page) in enumerate(self.films, start=1):
            try:
                source = pathlib.Path(page.video.path())
            except Exception:
                continue
            if source.exists():
                target = self.directory / f"{self.name}-{index}-{label}.webm"
                source.replace(target)
                saved.append(target)
        return saved

    def _tally(self) -> str:
        return f"{self.passed} đạt · {self.failed} hỏng"

    def _push(self, pause: int = 0) -> None:
        if not self.enabled:
            return
        self.state = {**self.state, "tally": self._tally()}
        payload = json.dumps(self.state)
        for page in list(self.pages):
            try:
                if page.is_closed():
                    self.pages.remove(page)
                    continue
                page.evaluate(f"window.__ntlCaption && window.__ntlCaption({payload})")
                if pause:
                    page.wait_for_timeout(pause)
            except Exception:
                pass


def viewport() -> dict[str, int]:
    """The browser size a walk runs at: a desk (default) or, with `CONSOLE_VIEWPORT=phone`, the
    390x844 phone the counter actually uses. The same walk, the same checks — only the size moves,
    so a phone run proves the tab bar, sheets and sticky action bars carry every workflow."""

    if os.environ.get("CONSOLE_VIEWPORT") == "phone":
        return {"width": 390, "height": 844}
    return {"width": 1280, "height": 900}


def add_arguments(parser: Any) -> None:
    parser.add_argument("--video", default="", help="directory for recorded walkthrough videos")
    parser.add_argument(
        "--slow-mo", type=int, default=250, help="milliseconds per action while recording"
    )


_DEFECT_SCRIPT = r"""
(() => {
  if (window.__ntlDefectWatch) return;
  window.__ntlDefectWatch = true;
  const PATTERNS = ['[object Object]', 'undefined ₫', 'NaN ₫', 'NaN'];
  const scan = (node) => {
    const text = (node && node.textContent) || '';
    for (const pattern of PATTERNS) {
      const at = text.indexOf(pattern);
      if (at >= 0) {
        const hash = location.hash || '#/';
        window.__ntlDefect && window.__ntlDefect(
          `${hash}: ${JSON.stringify(text.slice(Math.max(0, at - 60), at + 40))}`);
        return;
      }
    }
  };
  const start = () => {
    const main = document.body;
    if (!main) return;
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') scan(record.target);
        for (const node of record.addedNodes || []) scan(node);
      }
    }).observe(main, {subtree: true, childList: true, characterData: true});
    scan(main);
  };
  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
"""


def watch_render_defects(context: Any) -> list[str]:
    """Collect every piece of text a screen renders that only a rendering bug produces.

    Always on, recording or not. `[object Object]` is what a browser prints when a structure is
    handed to the DOM as text; `NaN` and `undefined ₫` are what arithmetic or a missing field look
    like on a money line. The owner's `DEC-029` review printed the first for the band a price was
    checked against, with every API test, contract test and stubbed browser check green -- so the
    real-API walk now watches for all three on every screen it opens, not only where a check
    happens to look.
    """

    found: list[str] = []

    def record(_source: Any, detail: str) -> None:
        if detail not in found:
            found.append(detail)

    context.expose_binding("__ntlDefect", record)
    context.add_init_script(_DEFECT_SCRIPT)
    return found
