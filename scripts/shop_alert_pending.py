"""Alerts that could not be delivered, kept until they are (`PLATFORM-RESIDUAL-009B` L4).

A shop alert is one Telegram message (`DEC-025`). When it cannot be sent -- Telegram down, the
till's internet gone, the token revoked -- the relay exits 3 and logs it, and that used to be the
end of it. For a check that measures a *state* (the WAL archive gap, the disk) the next run fails
again and tries again. For the application check it was the end: it counts each log line exactly
once, so a burst of 500s whose alert did not go out was never mentioned again.

So an undelivered alert is written here, and every later run -- passing or failing -- sends it
again, with the time it was first raised, until a send succeeds. Then it is gone. Nothing here
decides anything about the shop: it keeps the words the check already chose, verbatim.

Pure where it can be (`compose`, `after_failure`, `after_success` take the clock); the file is
replaced in one step, and an unreadable one is moved aside and reported rather than trusted or
silently dropped.

Used by `scripts/relay_shop_alert.py` (every scheduled run) and by
`scripts/check_shop_operations.py`'s direct-delivery path. Imports nothing from the application,
like the relay itself.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PENDING_SCHEMA = "nha-trang-laundry.shop-alert-pending.v1"
#: Two hours of five-minute runs. Past it the oldest are dropped -- and the message says how many,
#: so the owner knows the list is not whole -- because a list that grows without bound becomes a
#: message Telegram refuses, which would stop every later alert too.
MAX_PENDING = 24
ALERT_HEADING = "Bảng vận hành — cần xem ngay:"
REDELIVERY_HEADING = "Cảnh báo trước đó chưa gửi được:"
SHOP_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

#: Where the file lives: this directory if set, else beside `R1_ALERT_LOG_FILE`.
DIRECTORY_VARIABLE = "R1_ALERT_PENDING_DIRECTORY"
LOG_FILE_VARIABLE = "R1_ALERT_LOG_FILE"


@dataclass(frozen=True)
class PendingAlert:
    first_failed_at: datetime
    last_failed_at: datetime
    #: How many sends of it have failed so far.
    attempts: int
    text: str
    checks: tuple[str, ...]


@dataclass(frozen=True)
class PendingState:
    alerts: tuple[PendingAlert, ...] = ()
    #: Alerts dropped because the list was full, not yet reported in a delivered message.
    dropped: int = 0

    @property
    def empty(self) -> bool:
        return not self.alerts and not self.dropped


def pending_path(label: str, environment: Mapping[str, str]) -> Path | None:
    """The file for this schedule's label; `None` when neither variable says where."""

    directory = environment.get(DIRECTORY_VARIABLE, "").strip()
    if not directory:
        log_file = environment.get(LOG_FILE_VARIABLE, "").strip()
        if not log_file:
            return None
        directory = str(Path(log_file).parent)
    return Path(directory) / f"alert-pending-{label}.json"


def load(path: Path, *, now: datetime) -> tuple[PendingState, str | None]:
    """The saved state, and a problem to report (an unreadable file, moved aside)."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PendingState(), None
    except OSError as error:
        return PendingState(), f"cannot read {path} ({error.strerror}); earlier alerts not resent"
    try:
        document = json.loads(text)
        if not isinstance(document, dict) or document.get("schema") != PENDING_SCHEMA:
            raise ValueError("not a pending-alert document")
        alerts = tuple(
            PendingAlert(
                first_failed_at=_aware(item["first_failed_at"]),
                last_failed_at=_aware(item["last_failed_at"]),
                attempts=int(item["attempts"]),
                text=str(item["text"]),
                checks=tuple(str(name) for name in item.get("checks", [])),
            )
            for item in document.get("alerts", [])
        )
        return PendingState(alerts, int(document.get("dropped", 0))), None
    except (ValueError, KeyError, TypeError) as error:
        aside = path.with_name(f"{path.name}.unreadable-{now.strftime('%Y%m%dT%H%M%SZ')}")
        try:
            os.replace(path, aside)
        except OSError:
            aside = path
        return PendingState(), (
            f"{path} was unreadable ({type(error).__name__}) and was moved to {aside.name}; "
            "the alerts in it were not resent"
        )


def save(path: Path, state: PendingState) -> None:
    """Replace the file in one step; an empty state removes it."""

    if state.empty:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": PENDING_SCHEMA,
        "dropped": state.dropped,
        "alerts": [
            {
                "first_failed_at": alert.first_failed_at.isoformat(),
                "last_failed_at": alert.last_failed_at.isoformat(),
                "attempts": alert.attempts,
                "text": alert.text,
                "checks": list(alert.checks),
            }
            for alert in state.alerts
        ],
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", "utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _aware(value: object) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError("no time zone")
    return moment


def _body(text: str) -> str:
    """The alert's lines without its heading, which the redelivery section replaces."""

    lines = text.splitlines()
    if lines and lines[0].strip() == ALERT_HEADING:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _entry(alert: PendingAlert) -> str:
    at = alert.first_failed_at.astimezone(SHOP_TIMEZONE).strftime("%H:%M %d/%m")
    return f"— lúc {at} ({alert.attempts} lần chưa gửi được):\n{_body(alert.text)}"


def compose(
    current: str | None, state: PendingState, *, limit: int
) -> tuple[str | None, int, bool]:
    """(message, how many pending alerts it carries, whether it reports the dropped count).

    The current alert first, as the check wrote it; then the undelivered ones, oldest first, as
    many as fit within `limit`. Those that do not fit stay pending for the next run.
    """

    parts: list[str] = []
    if current is not None:
        parts.append(current[:limit])
    if state.empty:
        return (parts[0] if parts else None), 0, False
    section = [REDELIVERY_HEADING if parts else f"{ALERT_HEADING}\n{REDELIVERY_HEADING}"]
    included = 0
    for alert in state.alerts:
        if current is not None and alert.text == current:
            included += 1  # the same words are already in the message, as the current alert
            continue
        candidate = "\n\n".join([*parts, "\n".join([*section, _entry(alert)])])
        if len(candidate) > limit:
            break
        section.append(_entry(alert))
        included += 1
    reported = False
    if state.dropped and included == len(state.alerts):
        note = f"(và {state.dropped} cảnh báo cũ hơn đã bị bỏ vì danh sách đầy)"
        if len("\n\n".join([*parts, "\n".join([*section, note])])) <= limit:
            section.append(note)
            reported = True
    if len(section) == 1:
        return (parts[0] if parts else None), included, False
    return "\n\n".join([*parts, "\n".join(section)]), included, reported


def after_failure(
    state: PendingState, current: str | None, checks: tuple[str, ...], *, now: datetime
) -> PendingState:
    """Every pending alert failed once more; the current one joins them (once, if repeated)."""

    alerts = [
        replace(alert, attempts=alert.attempts + 1, last_failed_at=now) for alert in state.alerts
    ]
    if current is not None:
        same = next((index for index, alert in enumerate(alerts) if alert.text == current), None)
        if same is None:
            alerts.append(PendingAlert(now, now, 1, current, checks))
    dropped = state.dropped
    if len(alerts) > MAX_PENDING:
        dropped += len(alerts) - MAX_PENDING
        alerts = alerts[-MAX_PENDING:]
    return PendingState(tuple(alerts), dropped)


def after_success(state: PendingState, included: int, reported_dropped: bool) -> PendingState:
    """What is left once a message carrying the first `included` pending alerts was delivered."""

    return PendingState(state.alerts[included:], 0 if reported_dropped else state.dropped)


__all__ = [
    "MAX_PENDING",
    "PENDING_SCHEMA",
    "PendingAlert",
    "PendingState",
    "after_failure",
    "after_success",
    "compose",
    "load",
    "pending_path",
    "save",
]
