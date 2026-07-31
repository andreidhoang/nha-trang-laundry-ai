"""Manage the local automation lease and durable per-work-item attempt state.

The JSON file is the machine-readable source. A Markdown projection is written
beside it for human inspection. This utility stores no credentials or secrets.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = ROOT / ".openclaw"
STATE_JSON_NAME = "state.json"
STATE_MARKDOWN_NAME = "state.md"
MUTEX_NAME = ".state.mutex"
SCHEMA_VERSION = 1
EXIT_BUSY = 3
EXIT_STATE_ERROR = 4
MAX_TTL_SECONDS = 86_400
DEFAULT_TTL_SECONDS = 3_900
MAX_SUMMARY_LENGTH = 2_000

OWNER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
ATTEMPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
WORK_ITEM_PATTERN = re.compile(r"^[A-Z]+(?:-[A-Z]+)*-[0-9]{3}$")
RESULT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SENSITIVE_SUMMARY_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|key|password|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{4,}"),
    re.compile(
        r"(?<![A-Za-z0-9_-])"
        r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
        r"(?![A-Za-z0-9_-])"
    ),
)


class AutomationStateError(RuntimeError):
    """Raised when persisted automation state is invalid or an operation is unsafe."""


class LeaseBusyError(AutomationStateError):
    """Raised when another owner holds the current lease."""

    def __init__(self, owner: str, expires_at: datetime) -> None:
        super().__init__(f"automation lease is held by {owner} until {_format_time(expires_at)}")
        self.owner = owner
        self.expires_at = expires_at


@dataclass(frozen=True)
class Lease:
    owner: str
    acquired_at: datetime
    expires_at: datetime
    ttl_seconds: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "acquired_at": _format_time(self.acquired_at),
            "expires_at": _format_time(self.expires_at),
            "ttl_seconds": self.ttl_seconds,
        }


@dataclass
class WorkItemState:
    attempts: int = 0
    attempt_id: str | None = None
    last_attempt_at: datetime | None = None
    last_result: str | None = None
    last_summary: str | None = None
    last_result_at: datetime | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "attempt_id": self.attempt_id,
            "last_attempt_at": _optional_time(self.last_attempt_at),
            "last_result": self.last_result,
            "last_summary": self.last_summary,
            "last_result_at": _optional_time(self.last_result_at),
        }


@dataclass
class AutomationState:
    updated_at: datetime
    lease: Lease | None = None
    work_items: dict[str, WorkItemState] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _format_time(self.updated_at),
            "lease": self.lease.to_mapping() if self.lease is not None else None,
            "work_items": {
                item_id: self.work_items[item_id].to_mapping()
                for item_id in sorted(self.work_items)
            },
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_time(value: datetime | None) -> str | None:
    return _format_time(value) if value is not None else None


def _parse_time(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise AutomationStateError(f"{field_name} must be an RFC 3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AutomationStateError(f"{field_name} must be an RFC 3339 string") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AutomationStateError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _parse_optional_time(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_time(value, field_name)


def _string_mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AutomationStateError(f"{field_name} must be a mapping with string keys")
    return cast(dict[str, object], value)


def _exact_keys(mapping: dict[str, object], expected: set[str], field_name: str) -> None:
    if set(mapping) != expected:
        raise AutomationStateError(f"{field_name} has unexpected or missing fields")


def _parse_lease(value: object) -> Lease | None:
    if value is None:
        return None
    mapping = _string_mapping(value, "lease")
    _exact_keys(
        mapping,
        {"owner", "acquired_at", "expires_at", "ttl_seconds"},
        "lease",
    )
    owner = mapping["owner"]
    ttl_seconds = mapping["ttl_seconds"]
    if not isinstance(owner, str) or OWNER_PATTERN.fullmatch(owner) is None:
        raise AutomationStateError("lease.owner is invalid")
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 1 <= ttl_seconds <= MAX_TTL_SECONDS
    ):
        raise AutomationStateError("lease.ttl_seconds is invalid")
    acquired_at = _parse_time(mapping["acquired_at"], "lease.acquired_at")
    expires_at = _parse_time(mapping["expires_at"], "lease.expires_at")
    if expires_at <= acquired_at:
        raise AutomationStateError("lease.expires_at must be later than acquired_at")
    return Lease(
        owner=owner,
        acquired_at=acquired_at,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
    )


def _parse_work_item(item_id: str, value: object) -> WorkItemState:
    if WORK_ITEM_PATTERN.fullmatch(item_id) is None:
        raise AutomationStateError(f"invalid work item ID in state: {item_id}")
    mapping = _string_mapping(value, f"work_items.{item_id}")
    _exact_keys(
        mapping,
        {
            "attempts",
            "attempt_id",
            "last_attempt_at",
            "last_result",
            "last_summary",
            "last_result_at",
        },
        f"work_items.{item_id}",
    )
    attempts = mapping["attempts"]
    attempt_id = mapping["attempt_id"]
    last_result = mapping["last_result"]
    last_summary = mapping["last_summary"]
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        raise AutomationStateError(f"work_items.{item_id}.attempts is invalid")
    if attempt_id is not None and (
        not isinstance(attempt_id, str) or ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None
    ):
        raise AutomationStateError(f"work_items.{item_id}.attempt_id is invalid")
    if last_result is not None and (
        not isinstance(last_result, str) or RESULT_PATTERN.fullmatch(last_result) is None
    ):
        raise AutomationStateError(f"work_items.{item_id}.last_result is invalid")
    if last_summary is not None and (
        not isinstance(last_summary, str)
        or not last_summary.strip()
        or len(last_summary) > MAX_SUMMARY_LENGTH
        or "\x00" in last_summary
    ):
        raise AutomationStateError(f"work_items.{item_id}.last_summary is invalid")
    last_attempt_at = _parse_optional_time(
        mapping["last_attempt_at"], f"work_items.{item_id}.last_attempt_at"
    )
    last_result_at = _parse_optional_time(
        mapping["last_result_at"], f"work_items.{item_id}.last_result_at"
    )
    if attempts == 0 and any(
        value is not None
        for value in (attempt_id, last_attempt_at, last_result, last_summary, last_result_at)
    ):
        raise AutomationStateError(f"work_items.{item_id} has result data without an attempt")
    if attempts > 0 and (attempt_id is None or last_attempt_at is None):
        raise AutomationStateError(f"work_items.{item_id} lacks current attempt data")
    if (last_result is None) != (last_summary is None) or (last_result is None) != (
        last_result_at is None
    ):
        raise AutomationStateError(f"work_items.{item_id} has an incomplete result")
    return WorkItemState(
        attempts=attempts,
        attempt_id=attempt_id,
        last_attempt_at=last_attempt_at,
        last_result=last_result,
        last_summary=last_summary,
        last_result_at=last_result_at,
    )


def _parse_state(value: object) -> AutomationState:
    mapping = _string_mapping(value, "automation state")
    _exact_keys(
        mapping,
        {"schema_version", "updated_at", "lease", "work_items"},
        "automation state",
    )
    if mapping["schema_version"] != SCHEMA_VERSION:
        raise AutomationStateError("unsupported automation state schema_version")
    work_items_raw = _string_mapping(mapping["work_items"], "work_items")
    return AutomationState(
        updated_at=_parse_time(mapping["updated_at"], "updated_at"),
        lease=_parse_lease(mapping["lease"]),
        work_items={
            item_id: _parse_work_item(item_id, item_value)
            for item_id, item_value in work_items_raw.items()
        },
    )


def _load_state(state_dir: Path, now: datetime) -> AutomationState:
    state_path = state_dir / STATE_JSON_NAME
    if not state_path.exists():
        return AutomationState(updated_at=now)
    try:
        raw: object = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AutomationStateError(f"cannot read valid automation state: {error}") from error
    return _parse_state(raw)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _markdown_cell(value: str | None) -> str:
    if value is None:
        return "—"
    return " ".join(value.split()).replace("\\", "\\\\").replace("|", "\\|")


def _render_markdown(state: AutomationState) -> str:
    lines = [
        "# Automation state",
        "",
        f"- Updated: `{_format_time(state.updated_at)}`",
        "",
        "## Lease",
        "",
    ]
    if state.lease is None:
        lines.append("- No lease is currently recorded.")
    else:
        lines.extend(
            [
                f"- Owner: `{state.lease.owner}`",
                f"- Acquired: `{_format_time(state.lease.acquired_at)}`",
                f"- Expires: `{_format_time(state.lease.expires_at)}`",
                f"- TTL: `{state.lease.ttl_seconds}` seconds",
            ]
        )
    lines.extend(
        [
            "",
            "## Work items",
            "",
            "| Work item | Attempts | Attempt ID | Last attempt | Last result "
            "| Result time | Summary |",
            "|---|---:|---|---|---|---|---|",
        ]
    )
    if not state.work_items:
        lines.append("| — | 0 | — | — | — | — | — |")
    else:
        for item_id in sorted(state.work_items):
            item = state.work_items[item_id]
            lines.append(
                "| "
                + " | ".join(
                    (
                        item_id,
                        str(item.attempts),
                        _markdown_cell(item.attempt_id),
                        _markdown_cell(_optional_time(item.last_attempt_at)),
                        _markdown_cell(item.last_result),
                        _markdown_cell(_optional_time(item.last_result_at)),
                        _markdown_cell(item.last_summary),
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "_This projection contains orchestration metadata only. "
            "Do not place credentials or secrets here._",
            "",
        ]
    )
    return "\n".join(lines)


def _write_state(state_dir: Path, state: AutomationState) -> None:
    json_text = json.dumps(
        state.to_mapping(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    _atomic_write(state_dir / STATE_JSON_NAME, f"{json_text}\n")
    _atomic_write(state_dir / STATE_MARKDOWN_NAME, _render_markdown(state))


def _lock_mutex(handle: IO[bytes]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl_attributes = vars(importlib.import_module("fcntl"))
        flock = cast(Callable[[int, int], None], fcntl_attributes["flock"])
        lock_exclusive = cast(int, fcntl_attributes["LOCK_EX"])
        flock(handle.fileno(), lock_exclusive)


def _unlock_mutex(handle: IO[bytes]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl_attributes = vars(importlib.import_module("fcntl"))
        flock = cast(Callable[[int, int], None], fcntl_attributes["flock"])
        lock_un = cast(int, fcntl_attributes["LOCK_UN"])
        flock(handle.fileno(), lock_un)


@contextmanager
def _state_mutex(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    mutex_path = state_dir / MUTEX_NAME
    with mutex_path.open("a+b") as mutex_file:
        _lock_mutex(mutex_file)
        try:
            yield
        finally:
            _unlock_mutex(mutex_file)


def _validated_owner(owner: str) -> str:
    if OWNER_PATTERN.fullmatch(owner) is None:
        raise AutomationStateError("owner must be a safe 1-128 character identifier")
    return owner


def _validated_work_item(work_item: str) -> str:
    if WORK_ITEM_PATTERN.fullmatch(work_item) is None:
        raise AutomationStateError("work item must use stable UPPERCASE[-UPPERCASE]-NNN form")
    return work_item


def _validated_attempt_id(attempt_id: str) -> str:
    if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:
        raise AutomationStateError("attempt ID must be a safe 1-128 character identifier")
    return attempt_id


def _validated_result(result: str) -> str:
    if RESULT_PATTERN.fullmatch(result) is None:
        raise AutomationStateError("result must use UPPERCASE_UNDERSCORE form")
    return result


def _validated_summary(summary: str) -> str:
    if not summary.strip():
        raise AutomationStateError("summary must not be empty")
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise AutomationStateError(f"summary exceeds {MAX_SUMMARY_LENGTH} characters")
    if "\x00" in summary:
        raise AutomationStateError("summary contains a prohibited NUL character")
    if any(pattern.search(summary) is not None for pattern in SENSITIVE_SUMMARY_PATTERNS):
        raise AutomationStateError("summary appears to contain sensitive credential material")
    return summary


def _normalized_now(now: datetime | None) -> datetime:
    current = now or _utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise AutomationStateError("operation time must include a timezone")
    return current.astimezone(UTC)


def _require_active_owner(state: AutomationState, owner: str, now: datetime) -> Lease:
    lease = state.lease
    if lease is None:
        raise AutomationStateError("no automation lease is currently held")
    if lease.owner != owner or lease.expires_at <= now:
        raise LeaseBusyError(lease.owner, lease.expires_at)
    return lease


def acquire_lease(
    state_dir: Path,
    owner: str,
    ttl_seconds: int,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise AutomationStateError(f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}")
    current = _normalized_now(now)
    with _state_mutex(state_dir):
        state = _load_state(state_dir, current)
        previous = state.lease
        if previous is not None and previous.expires_at > current and previous.owner != owner:
            raise LeaseBusyError(previous.owner, previous.expires_at)
        if previous is None:
            action = "ACQUIRED"
            acquired_at = current
        elif previous.expires_at <= current:
            action = "STALE_TAKEOVER"
            acquired_at = current
        else:
            action = "RENEWED"
            acquired_at = previous.acquired_at
        state.lease = Lease(
            owner=owner,
            acquired_at=acquired_at,
            expires_at=current + timedelta(seconds=ttl_seconds),
            ttl_seconds=ttl_seconds,
        )
        state.updated_at = current
        _write_state(state_dir, state)
        return {
            "action": action,
            "owner": owner,
            "expires_at": _format_time(state.lease.expires_at),
        }


def release_lease(
    state_dir: Path,
    owner: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    current = _normalized_now(now)
    with _state_mutex(state_dir):
        state = _load_state(state_dir, current)
        lease = state.lease
        if lease is None:
            raise AutomationStateError("no automation lease is currently held")
        if lease.owner != owner:
            raise LeaseBusyError(lease.owner, lease.expires_at)
        state.lease = None
        state.updated_at = current
        _write_state(state_dir, state)
        return {"action": "RELEASED", "owner": owner}


def begin_attempt(
    state_dir: Path,
    owner: str,
    work_item: str,
    attempt_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    current = _normalized_now(now)
    with _state_mutex(state_dir):
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, current)
        item = state.work_items.setdefault(work_item, WorkItemState())
        if item.attempt_id == attempt_id:
            return {
                "action": "ATTEMPT_ALREADY_BEGUN",
                "owner": owner,
                "work_item": work_item,
                "attempt_id": attempt_id,
                "attempt": item.attempts,
            }
        item.attempts += 1
        item.attempt_id = attempt_id
        item.last_attempt_at = current
        item.last_result = None
        item.last_summary = None
        item.last_result_at = None
        state.updated_at = current
        _write_state(state_dir, state)
        return {
            "action": "ATTEMPT_BEGUN",
            "owner": owner,
            "work_item": work_item,
            "attempt_id": attempt_id,
            "attempt": item.attempts,
        }


def record_result(
    state_dir: Path,
    owner: str,
    work_item: str,
    attempt_id: str,
    result: str,
    summary: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    result = _validated_result(result)
    summary = _validated_summary(summary)
    current = _normalized_now(now)
    with _state_mutex(state_dir):
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, current)
        item = state.work_items.get(work_item)
        if item is None or item.attempts == 0:
            raise AutomationStateError("record-result requires a prior begin-attempt")
        if item.attempt_id != attempt_id:
            raise AutomationStateError("record-result attempt ID is not the current attempt")
        if item.last_result is not None:
            if item.last_result == result and item.last_summary == summary:
                return {
                    "action": "RESULT_ALREADY_RECORDED",
                    "owner": owner,
                    "work_item": work_item,
                    "attempt_id": attempt_id,
                    "attempt": item.attempts,
                    "result": result,
                }
            raise AutomationStateError(
                "current attempt already has a different immutable result or summary"
            )
        item.last_result = result
        item.last_summary = summary
        item.last_result_at = current
        state.updated_at = current
        _write_state(state_dir, state)
        return {
            "action": "RESULT_RECORDED",
            "owner": owner,
            "work_item": work_item,
            "attempt_id": attempt_id,
            "attempt": item.attempts,
            "result": result,
        }


def status(
    state_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current = _normalized_now(now)
    with _state_mutex(state_dir):
        state = _load_state(state_dir, current)
        payload = state.to_mapping()
        lease_payload = payload["lease"]
        if isinstance(lease_payload, dict) and state.lease is not None:
            lease_payload["status"] = "ACTIVE" if state.lease.expires_at > current else "STALE"
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Directory for state.json and state.md.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    acquire = commands.add_parser("acquire", help="Acquire, renew, or take over a stale lease.")
    acquire.add_argument("--owner", required=True)
    acquire.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)

    release = commands.add_parser("release", help="Release the current lease as its owner.")
    release.add_argument("--owner", required=True)

    attempt = commands.add_parser("begin-attempt", help="Increment a work item's attempt counter.")
    attempt.add_argument("--owner", required=True)
    attempt.add_argument("--work-item", required=True)
    attempt.add_argument("--attempt-id", required=True)

    result = commands.add_parser("record-result", help="Record the latest work-item result.")
    result.add_argument("--owner", required=True)
    result.add_argument("--work-item", required=True)
    result.add_argument("--attempt-id", required=True)
    result.add_argument("--result", required=True)
    result.add_argument("--summary", required=True)

    commands.add_parser("status", help="Print the current state as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    state_dir = cast(Path, arguments.state_dir).resolve()
    try:
        if arguments.command == "acquire":
            output = acquire_lease(
                state_dir,
                cast(str, arguments.owner),
                cast(int, arguments.ttl_seconds),
            )
        elif arguments.command == "release":
            output = release_lease(state_dir, cast(str, arguments.owner))
        elif arguments.command == "begin-attempt":
            output = begin_attempt(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
            )
        elif arguments.command == "record-result":
            output = record_result(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
                cast(str, arguments.result),
                cast(str, arguments.summary),
            )
        else:
            output = status(state_dir)
    except LeaseBusyError as error:
        print(
            json.dumps(
                {
                    "error": "LEASE_BUSY",
                    "owner": error.owner,
                    "expires_at": _format_time(error.expires_at),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return EXIT_BUSY
    except AutomationStateError as error:
        print(
            json.dumps({"error": "AUTOMATION_STATE_ERROR", "message": str(error)}, sort_keys=True),
            file=sys.stderr,
        )
        return EXIT_STATE_ERROR
    print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
