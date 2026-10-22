"""An uncaught browser exception must fail a walk, including in a second role's tab."""

from collections.abc import Callable
from typing import Any

from scripts.console_recording import watch_page_errors


class Events:
    def __init__(self, pages: list[Any] | None = None) -> None:
        self.pages = pages or []
        self.listeners: dict[str, list[Callable[..., Any]]] = {}

    def on(self, name: str, callback: Callable[..., Any]) -> None:
        self.listeners.setdefault(name, []).append(callback)

    def emit(self, name: str, value: Any) -> None:
        for callback in self.listeners.get(name, []):
            callback(value)


def test_uncaught_errors_from_existing_and_new_tabs_are_retained() -> None:
    owner = Events()
    context = Events([owner])
    errors = watch_page_errors(context)
    second_tab = Events()
    context.emit("page", second_tab)

    owner.emit("pageerror", RuntimeError("owner renderer failed"))
    second_tab.emit("pageerror", RuntimeError("second tab renderer failed"))

    assert errors == ["owner renderer failed", "second tab renderer failed"]


def test_second_role_shares_the_gate_but_console_refusals_do_not_fail_it() -> None:
    errors: list[str] = []
    context = Events()
    assert watch_page_errors(context, errors) is errors
    auditor = Events()
    context.emit("page", auditor)

    auditor.emit("console", "Failed to load resource: 403 Forbidden")
    assert not errors
    auditor.emit("pageerror", RuntimeError("auditor renderer failed"))
    assert errors == ["auditor renderer failed"]
