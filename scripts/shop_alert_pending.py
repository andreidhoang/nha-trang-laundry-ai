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

**`DEC-025`'s hours hold for a resent alert as for a new one.** The console checks alert only
between 07:00 and 21:00. A console alert raised at 20:50 whose send failed used to be resent by the
next run whatever the hour -- the owner was paged at 02:00 about an outage the decision says waits
for opening, and which had likely ended (round-9b verifier, P1). So the rule lives here, beside the
resend, and the check reads it from here too: one copy of the hours. A pending alert made only of
quiet-hours checks waits, untried, until 07:00; an alert that mixed them with a check that alerts at
any hour is kept as two, so the any-hour part goes now and the console part waits.

**Every run that can send makes progress.** An alert too long to fit beside the redelivery heading
used to be skipped -- and, being first, blocked every alert behind it, while each run failed with
"nothing to send" (round-9b verifier, P2). Now a kept alert is stored clipped to the message limit,
and the oldest one waiting is always carried, clipped if it must be, so the queue drains.

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
from typing import NamedTuple
from zoneinfo import ZoneInfo

PENDING_SCHEMA = "nha-trang-laundry.shop-alert-pending.v1"
#: Two hours of five-minute runs. Past it the oldest are dropped -- and the message says how many,
#: so the owner knows the list is not whole -- because a list that grows without bound becomes a
#: message Telegram refuses, which would stop every later alert too.
MAX_PENDING = 24
ALERT_HEADING = "Bảng vận hành — cần xem ngay:"
REDELIVERY_HEADING = "Cảnh báo trước đó chưa gửi được:"
SHOP_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
#: Telegram's limit is 4096 characters; a clipped alert is still an alert. A kept alert is stored
#: no longer than this, so it can always be sent.
MAX_MESSAGE_CHARACTERS = 3800
CLIPPED = "…"

#: `DEC-025`. `console_reachable` is the only check whose failure is a visible outage rather than a
#: silent loss of a guarantee, and a console down at 03:00 that recovers before opening does not
#: need anybody woken. `console_certificate` (the `DEC-052` renewal notice) is weeks ahead of its
#: date and needs nobody woken either. Everything else alerts at any hour: a stale archive and a
#: filling disk are losses nobody would otherwise notice, and a capability flag enabled without a
#: signed manifest is a security incident under the operations spec.
QUIET_HOURS_CHECKS = frozenset({"console_reachable", "console_certificate"})
QUIET_HOURS_START = 21
QUIET_HOURS_END = 7

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


class Composition(NamedTuple):
    """One message, and what delivering it settles. A `NamedTuple`: the tests load this module by
    path, and a dataclass needs its module registered in `sys.modules`."""

    #: `None` when there is nothing to send this run.
    text: str | None
    #: The positions, in `PendingState.alerts`, of the kept alerts this message carries.
    carried: tuple[int, ...]
    #: Whether the message reports the dropped count.
    reported_dropped: bool
    #: Kept alerts left waiting for opening hours (`DEC-025`), untried.
    waiting: int


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


def in_quiet_hours(now: datetime) -> bool:
    """`DEC-025`'s night, in the shop's own clock: from 21:00 until 07:00 in Nha Trang."""

    hour = now.astimezone(SHOP_TIMEZONE).hour
    return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


def waits_for_opening(alert: PendingAlert, now: datetime) -> bool:
    """A kept alert made only of quiet-hours checks, at night: it waits, untried, until 07:00."""

    return bool(alert.checks) and set(alert.checks) <= QUIET_HOURS_CHECKS and in_quiet_hours(now)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - len(CLIPPED), 0)] + CLIPPED


def _body(text: str) -> str:
    """The alert's lines without its heading, which the redelivery section replaces."""

    lines = text.splitlines()
    if lines and lines[0].strip() == ALERT_HEADING:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _bullet_check(line: str) -> str | None:
    """`• name: detail` -> `name`, the shape `check_shop_operations.alert_text` writes."""

    if not line.startswith("• "):
        return None
    name, separator, _ = line[2:].partition(":")
    return name.strip() if separator and name.strip() else None


def split_for_hours(text: str, checks: tuple[str, ...]) -> list[tuple[str, tuple[str, ...]]]:
    """One alert as the parts `DEC-025` times differently: any-hour checks, then quiet-hours ones.

    Only an alert that mixes the two is split, and only when every line can be attributed to its
    check; otherwise it is kept whole (and, holding an any-hour check, is never held).
    """

    quiet = tuple(name for name in checks if name in QUIET_HOURS_CHECKS)
    loud = tuple(name for name in checks if name not in QUIET_HOURS_CHECKS)
    if not quiet or not loud:
        return [(text, checks)]
    lines = text.splitlines()
    heading = lines[0] if lines and lines[0].strip() == ALERT_HEADING else None
    groups: dict[bool, list[str]] = {True: [], False: []}
    target: bool | None = None
    for line in lines[1:] if heading is not None else lines:
        name = _bullet_check(line)
        if name is not None:
            target = name in QUIET_HOURS_CHECKS
        elif target is None:
            return [(text, checks)]
        groups[target].append(line)
    if not groups[True] or not groups[False]:
        return [(text, checks)]
    prefix = [heading] if heading is not None else []
    return [
        ("\n".join([*prefix, *groups[False]]), loud),
        ("\n".join([*prefix, *groups[True]]), quiet),
    ]


def _parts_of(current: str | None) -> frozenset[str]:
    """The texts a kept alert would have if it were `current`, whole or split."""

    if current is None:
        return frozenset()
    checks = tuple(
        name for line in current.splitlines() if (name := _bullet_check(line)) is not None
    )
    return frozenset({current, *(text for text, _ in split_for_hours(current, checks))})


def _entry(alert: PendingAlert) -> str:
    at = alert.first_failed_at.astimezone(SHOP_TIMEZONE).strftime("%H:%M %d/%m")
    return f"— lúc {at} ({alert.attempts} lần chưa gửi được):\n{_body(alert.text)}"


def compose(
    current: str | None,
    state: PendingState,
    *,
    limit: int = MAX_MESSAGE_CHARACTERS,
    now: datetime,
) -> Composition:
    """The message for this run: the current alert first, as the check wrote it; then the kept
    ones, oldest first, as many as fit within `limit`.

    A kept alert of quiet-hours checks only is left untried at night (`DEC-025`). The oldest kept
    alert that may go is always carried -- clipped if it must be, and with the current alert
    clipped to half the message to make room -- so a long one can neither be skipped forever nor
    stop the ones behind it. Those that do not fit stay for the next run.
    """

    duplicates = _parts_of(current)
    carried: list[int] = []
    others: list[int] = []
    waiting = 0
    for index, alert in enumerate(state.alerts):
        if waits_for_opening(alert, now):
            waiting += 1
        elif alert.text in duplicates:
            carried.append(index)  # the same words are already in the message, as the current
        else:
            others.append(index)

    parts: list[str] = []
    if current is not None:
        parts.append(_clip(current, limit // 2 if others else limit))
    section = [REDELIVERY_HEADING if parts else f"{ALERT_HEADING}\n{REDELIVERY_HEADING}"]
    note = f"(và {state.dropped} cảnh báo cũ hơn đã bị bỏ vì danh sách đầy)"
    if not others:
        if state.dropped and not waiting:
            message = "\n\n".join([*parts, "\n".join([*section, note])])
            if len(message) <= limit:
                return Composition(message, tuple(carried), True, waiting)
        return Composition(parts[0] if parts else None, tuple(carried), False, waiting)

    def length_with(line: str) -> int:
        return len("\n\n".join([*parts, "\n".join([*section, line])]))

    for position, index in enumerate(others):
        entry = _entry(state.alerts[index])
        if length_with(entry) > limit:
            if position:
                break
            # The oldest that may go always goes, clipped to the room there is.
            entry = _clip(entry, limit - length_with(""))
        section.append(entry)
        carried.append(index)
    reported = False
    every_other_carried = carried[-1] == others[-1]
    if state.dropped and not waiting and every_other_carried and length_with(note) <= limit:
        section.append(note)
        reported = True
    message = "\n\n".join([*parts, "\n".join(section)])
    return Composition(message, tuple(sorted(carried)), reported, waiting)


def after_failure(
    state: PendingState,
    current: str | None,
    checks: tuple[str, ...],
    *,
    now: datetime,
    attempted: tuple[int, ...] | None = None,
) -> PendingState:
    """The kept alerts this send carried (`attempted`; all when not given) failed once more; the
    current one joins them -- once, if repeated; in two parts, if it mixes `DEC-025`'s hours."""

    tried = set(range(len(state.alerts)) if attempted is None else attempted)
    alerts = [
        replace(alert, attempts=alert.attempts + 1, last_failed_at=now) if index in tried else alert
        for index, alert in enumerate(state.alerts)
    ]
    if current is not None:
        for text, part_checks in split_for_hours(current, checks):
            kept = _clip(text, MAX_MESSAGE_CHARACTERS)
            if all(alert.text != kept for alert in alerts):
                alerts.append(PendingAlert(now, now, 1, kept, part_checks))
    dropped = state.dropped
    if len(alerts) > MAX_PENDING:
        dropped += len(alerts) - MAX_PENDING
        alerts = alerts[-MAX_PENDING:]
    return PendingState(tuple(alerts), dropped)


def after_success(
    state: PendingState, carried: tuple[int, ...], reported_dropped: bool
) -> PendingState:
    """What is left once a message carrying the kept alerts at `carried` was delivered."""

    gone = set(carried)
    return PendingState(
        tuple(alert for index, alert in enumerate(state.alerts) if index not in gone),
        0 if reported_dropped else state.dropped,
    )


__all__ = [
    "MAX_MESSAGE_CHARACTERS",
    "MAX_PENDING",
    "PENDING_SCHEMA",
    "QUIET_HOURS_CHECKS",
    "QUIET_HOURS_END",
    "QUIET_HOURS_START",
    "SHOP_TIMEZONE",
    "Composition",
    "PendingAlert",
    "PendingState",
    "after_failure",
    "after_success",
    "compose",
    "in_quiet_hours",
    "load",
    "pending_path",
    "save",
    "split_for_hours",
    "waits_for_opening",
]
