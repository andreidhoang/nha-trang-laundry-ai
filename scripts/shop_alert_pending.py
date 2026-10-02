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

**Every run that can send makes progress, and nothing is cut to make room.** An alert too long to
fit beside the redelivery heading used to be skipped -- and, being first, blocked every alert behind
it, while each run failed with "nothing to send" (round-9b verifier, P2). The first fix carried the
oldest kept alert clipped and cut the current one to half the message whenever anything was kept,
even when everything fitted, and sent neither tail anywhere (round-9b verifier, round 2). Now a
kept alert is stored short enough to go whole in a message of its own, and every message carries
the oldest one waiting, whole. The current alert goes first and whole whenever it fits beside that
one; when the two together are too long, the oldest goes now, the message says a newer one follows,
and the newer one is kept and goes whole next run.

**A kept alert is the text its first send carried, not less** (round-9b verifier, round 3). The
first send carries an alert of up to `MAX_MESSAGE_CHARACTERS` whole; the kept copy used to be
clipped 200 characters shorter, to leave room for the redelivery heading, so an alert of 3 601 to
3 800 characters lost its tail exactly when it was resent. Now the kept copy is the same length as
the first send, and a message carrying the oldest kept alert alone may grow past
`MAX_MESSAGE_CHARACTERS` by the headings around it -- up to `MESSAGE_CEILING`, which stays under
Telegram's own limit (`TELEGRAM_MESSAGE_CHARACTERS`). The one cut left is the first send's: an alert
longer than `MAX_MESSAGE_CHARACTERS` goes, and is kept, clipped to it.

**A pending file that cannot be written does not hide a failed send** (round-9b verifier, round 3).
`try_save` returns the reason instead of raising, so the caller still logs "ALERT NOT DELIVERED" and
exits 3 -- with "not kept" and why -- when the disk is full or the directory is not one.

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
#: Telegram's own limit on one message.
TELEGRAM_MESSAGE_CHARACTERS = 4096
#: The longest alert text: what a check's alert is clipped to on its first send (a clipped alert is
#: still an alert), and the room a message fills when it packs several alerts together.
MAX_MESSAGE_CHARACTERS = 3800
#: A kept alert is stored no longer than the first send carried it -- so a resend never carries
#: less than the first attempt did.
KEPT_CHARACTERS = MAX_MESSAGE_CHARACTERS
#: The longest message ever composed: a kept alert of `KEPT_CHARACTERS` sent alone, with both
#: headings, its "when, how many tries" line and the "a newer alert follows" note, fits under it
#: (`test_a_kept_alert_of_the_longest_length_is_resent_whole`). Kept under Telegram's limit with a
#: margin, because Telegram counts UTF-16 units and a character outside the BMP is two of them.
MESSAGE_CEILING = TELEGRAM_MESSAGE_CHARACTERS - 96
CLIPPED = "…"
#: The last line of a message that carried the oldest kept alert instead of the current one.
NEWER_FOLLOWS = "(còn một cảnh báo mới hơn — gửi ở tin sau, vì tin này đã đầy)"

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
    #: The current alert is not in this message: it did not fit beside the oldest kept alert. The
    #: caller keeps it (`after_success(..., deferred=...)` or `after_failure(..., deferred=True)`)
    #: and the next run sends it whole.
    deferred: bool = False


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
    """Replace the file in one step; an empty state removes it. Raises `OSError`; see `try_save`."""

    if state.empty:
        # Under a "directory" that is a file there can be no file to remove either.
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
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
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", "utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError:
        # A full disk leaves a half-written temporary file; the old file is untouched.
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def try_save(path: Path, state: PendingState) -> str | None:
    """`save`, with a failure returned as the words to log rather than raised.

    The callers run right after a failed send, and a pending file that cannot be written -- the
    disk the volume check warns about is full, the directory is a file -- used to raise out of the
    relay before it logged "ALERT NOT DELIVERED": a traceback, and exit 1, which the relay's
    contract defines as "delivered" (round-9b verifier, round 3).
    """

    try:
        save(path, state)
    except OSError as error:
        reason = error.strerror or type(error).__name__
        return f"cannot write {path} ({reason})"
    return None


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
    """The texts a kept alert would have if it were `current`, whole or split -- and as stored,
    clipped to `KEPT_CHARACTERS`, so a current alert longer than that still recognises its own kept
    copy (round-9b verifier, round 2: both went, the same words twice)."""

    if current is None:
        return frozenset()
    checks = tuple(
        name for line in current.splitlines() if (name := _bullet_check(line)) is not None
    )
    texts = {current, *(text for text, _ in split_for_hours(current, checks))}
    return frozenset({*texts, *(_clip(text, KEPT_CHARACTERS) for text in texts)})


def _entry(alert: PendingAlert) -> str:
    at = alert.first_failed_at.astimezone(SHOP_TIMEZONE).strftime("%H:%M %d/%m")
    why = (
        "chưa gửi — tin trước đã đầy"
        if alert.attempts == 0
        else f"{alert.attempts} lần chưa gửi được"
    )
    return f"— lúc {at} ({why}):\n{_body(alert.text)}"


def _kept_parts(
    current: str, checks: tuple[str, ...], *, now: datetime, attempts: int, present: set[str]
) -> list[PendingAlert]:
    """`current` as the kept alerts it becomes -- split for `DEC-025`'s hours, each stored clipped
    to `KEPT_CHARACTERS` -- leaving out any whose text is in `present`."""

    kept: list[PendingAlert] = []
    for text, part_checks in split_for_hours(current, checks):
        stored = _clip(text, KEPT_CHARACTERS)
        if stored not in present:
            present.add(stored)
            kept.append(PendingAlert(now, now, attempts, stored, part_checks))
    return kept


def _capped(alerts: list[PendingAlert], dropped: int) -> PendingState:
    if len(alerts) > MAX_PENDING:
        dropped += len(alerts) - MAX_PENDING
        alerts = alerts[-MAX_PENDING:]
    return PendingState(tuple(alerts), dropped)


def compose(
    current: str | None,
    state: PendingState,
    *,
    limit: int = MAX_MESSAGE_CHARACTERS,
    ceiling: int = MESSAGE_CEILING,
    now: datetime,
) -> Composition:
    """The message for this run: the current alert first, whole, as the check wrote it; then the
    kept ones, oldest first, each whole, as many as fit within `limit`. The oldest kept alert goes
    whole even when it alone fills more than `limit`, up to `ceiling`.

    A kept alert of quiet-hours checks only is left untried at night (`DEC-025`). The oldest kept
    alert that may go is always carried, so a long one can neither be skipped forever nor stop the
    ones behind it. When it does not fit beside the current alert, it goes alone and the current
    alert is `deferred`: kept by the caller and sent whole next run. Nothing is cut to make room.
    """

    duplicates = _parts_of(current)
    sendable: list[int] = []
    waiting = 0
    for index, alert in enumerate(state.alerts):
        if waits_for_opening(alert, now):
            waiting += 1
        else:
            sendable.append(index)
    # The same words as the current alert are already in the message when the current one goes.
    same = [index for index in sendable if state.alerts[index].text in duplicates]
    others = [index for index in sendable if index not in same]

    first = _clip(current, limit) if current is not None else None
    deferred = (
        first is not None
        and bool(others)
        and len(_message([first], [_entry(state.alerts[others[0]])])) > limit
    )
    if deferred:
        parts: list[str] = []
        carried: list[int] = []
        others = sendable  # the current's own kept copies go in their turn, as kept alerts
    else:
        parts = [first] if first is not None else []
        carried = list(same)

    note = f"(và {state.dropped} cảnh báo cũ hơn đã bị bỏ vì danh sách đầy)"
    if not others:
        if state.dropped and not waiting:
            message = _message(parts, [note])
            if len(message) <= limit:
                return Composition(message, tuple(carried), True, waiting)
        return Composition(parts[0] if parts else None, tuple(carried), False, waiting)

    tail = [NEWER_FOLLOWS] if deferred else []
    entries: list[str] = []
    for position, index in enumerate(others):
        entry = _entry(state.alerts[index])
        if len(_message(parts, [*entries, entry, *tail])) > limit:
            if position:
                break
            # The oldest goes whatever its length: whole, in the room between `limit` and
            # `ceiling` the headings need. Only an alert stored longer than `KEPT_CHARACTERS` (by an
            # older version of this file) can still be too long: it goes clipped, so nothing wedges.
            if len(_message(parts, [entry, *tail])) > ceiling:
                entry = _clip(entry, ceiling - len(_message(parts, [*tail])) - 1)
        entries.append(entry)
        carried.append(index)
    reported = False
    every_other_carried = carried[-1] == others[-1]
    if (
        state.dropped
        and not waiting
        and every_other_carried
        and len(_message(parts, [*entries, note, *tail])) <= limit
    ):
        entries.append(note)
        reported = True
    message = _message(parts, [*entries, *tail])
    return Composition(message, tuple(sorted(carried)), reported, waiting, deferred)


def _message(parts: list[str], section: list[str]) -> str:
    """The current alert (if any), then the redelivery section holding `section`'s lines."""

    heading = REDELIVERY_HEADING if parts else f"{ALERT_HEADING}\n{REDELIVERY_HEADING}"
    return "\n\n".join([*parts, "\n".join([heading, *section])])


def after_failure(
    state: PendingState,
    current: str | None,
    checks: tuple[str, ...],
    *,
    now: datetime,
    attempted: tuple[int, ...] | None = None,
    deferred: bool = False,
) -> PendingState:
    """The kept alerts this send carried (`attempted`; all when not given) failed once more; the
    current one joins them -- once, if repeated; in two parts, if it mixes `DEC-025`'s hours. A
    `deferred` current alert was not in the failed message, so it joins with no failed attempt."""

    tried = set(range(len(state.alerts)) if attempted is None else attempted)
    alerts = [
        replace(alert, attempts=alert.attempts + 1, last_failed_at=now) if index in tried else alert
        for index, alert in enumerate(state.alerts)
    ]
    if current is not None:
        present = {alert.text for alert in alerts}
        alerts += _kept_parts(
            current, checks, now=now, attempts=0 if deferred else 1, present=present
        )
    return _capped(alerts, state.dropped)


def after_success(
    state: PendingState,
    carried: tuple[int, ...],
    reported_dropped: bool,
    *,
    deferred: str | None = None,
    checks: tuple[str, ...] = (),
    now: datetime | None = None,
) -> PendingState:
    """What is left once a message carrying the kept alerts at `carried` was delivered -- plus the
    current alert when it was `deferred` (not in that message), unless its words were carried."""

    gone = set(carried)
    alerts = [alert for index, alert in enumerate(state.alerts) if index not in gone]
    dropped = 0 if reported_dropped else state.dropped
    if deferred is not None:
        if now is None:
            raise ValueError("a deferred alert is kept with the time it was raised: pass `now`")
        present = {alert.text for alert in state.alerts}
        alerts += _kept_parts(deferred, checks, now=now, attempts=0, present=present)
    return _capped(alerts, dropped)


__all__ = [
    "KEPT_CHARACTERS",
    "MAX_MESSAGE_CHARACTERS",
    "MAX_PENDING",
    "MESSAGE_CEILING",
    "NEWER_FOLLOWS",
    "PENDING_SCHEMA",
    "QUIET_HOURS_CHECKS",
    "QUIET_HOURS_END",
    "QUIET_HOURS_START",
    "SHOP_TIMEZONE",
    "TELEGRAM_MESSAGE_CHARACTERS",
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
    "try_save",
    "waits_for_opening",
]
