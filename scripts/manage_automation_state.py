"""Manage the local automation lease and durable per-work-item attempt state.

The JSON file is the machine-readable source. A Markdown projection is written
beside it for human inspection. This utility stores no credentials or secrets.
"""

from __future__ import annotations

import argparse
import errno
import importlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
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
SCHEMA_VERSION = 2
EXIT_BUSY = 3
EXIT_STATE_ERROR = 4
MAX_TTL_SECONDS = 86_400
DEFAULT_TTL_SECONDS = 3_900
MAX_SUMMARY_LENGTH = 2_000
MAX_ATTEMPTS = 3
DEFAULT_MUTEX_TIMEOUT_SECONDS = 10.0
MUTEX_POLL_INTERVAL_SECONDS = 0.05

OWNER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
ATTEMPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
ATTEMPT_ORDINAL_PATTERN = re.compile(r"(?:^|[:/_-])attempt-0*([1-9][0-9]*)$")
LEASE_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
BRANCH_PATTERN = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\|\s))[^~^:?*\[\]]{1,255}(?<![/.])$")
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
RUN_ORDINAL_PATTERN = re.compile(r"(?:^|[:/_-])0*([1-9][0-9]*)$")
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
WORK_ITEM_PATTERN = re.compile(r"^[A-Z]+(?:-[A-Z]+)*-[0-9]{3}$")
ALLOWED_RESULTS = frozenset({"PASSED", "FAILED", "BLOCKED", "STOPPED", "TIMED_OUT"})
RETRYABLE_RESULTS = frozenset({"FAILED", "STOPPED", "TIMED_OUT"})
PHASES = frozenset(
    {
        "PREPARED",
        "CHILD_RUNNING",
        "VERIFYING",
        "TASK_COMMITTED",
        "MERGED",
        "DELIVERY_COMMITTED",
        "BLOCK_COMMITTED",
        "TERMINAL",
        "RECOVERY_REQUIRED",
    }
)
NONTERMINAL_PHASES = PHASES - {"TERMINAL"}
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


class AttemptLimitError(AutomationStateError):
    """Raised when a work item has exhausted its bounded retry budget."""

    def __init__(self, work_item: str, attempts: int) -> None:
        super().__init__(f"{work_item} has reached the {MAX_ATTEMPTS}-attempt limit")
        self.work_item = work_item
        self.attempts = attempts


@dataclass(frozen=True)
class Lease:
    lease_id: str
    owner: str
    acquired_at: datetime
    expires_at: datetime
    ttl_seconds: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
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
    phase: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    child_run_id: str | None = None
    child_session: str | None = None
    task_commit: str | None = None
    delivery_commit: str | None = None
    legacy_migrated: bool = False

    def to_mapping(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "attempt_id": self.attempt_id,
            "last_attempt_at": _optional_time(self.last_attempt_at),
            "last_result": self.last_result,
            "last_summary": self.last_summary,
            "last_result_at": _optional_time(self.last_result_at),
            "phase": self.phase,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "child_run_id": self.child_run_id,
            "child_session": self.child_session,
            "task_commit": self.task_commit,
            "delivery_commit": self.delivery_commit,
            "legacy_migrated": self.legacy_migrated,
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


def _migrated_lease_id(
    owner: str,
    acquired_at: datetime,
    expires_at: datetime,
) -> str:
    seed = f"{owner}:{_format_time(acquired_at)}:{_format_time(expires_at)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _parse_lease(value: object, *, schema_version: int) -> Lease | None:
    if value is None:
        return None
    mapping = _string_mapping(value, "lease")
    expected = {"owner", "acquired_at", "expires_at", "ttl_seconds"}
    if schema_version == SCHEMA_VERSION:
        expected.add("lease_id")
    _exact_keys(mapping, expected, "lease")
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
    if schema_version == SCHEMA_VERSION:
        lease_id = mapping["lease_id"]
        if not isinstance(lease_id, str) or LEASE_ID_PATTERN.fullmatch(lease_id) is None:
            raise AutomationStateError("lease.lease_id is invalid")
    else:
        lease_id = _migrated_lease_id(owner, acquired_at, expires_at)
    return Lease(
        lease_id=lease_id,
        owner=owner,
        acquired_at=acquired_at,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
    )


def _parse_attempt_common(
    item_id: str,
    mapping: dict[str, object],
) -> tuple[
    int,
    str | None,
    datetime | None,
    str | None,
    str | None,
    datetime | None,
]:
    field_prefix = f"work_items.{item_id}"
    attempts = mapping["attempts"]
    attempt_id = mapping["attempt_id"]
    last_result = mapping["last_result"]
    last_summary = mapping["last_summary"]
    if (
        not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or not 0 <= attempts <= MAX_ATTEMPTS
    ):
        raise AutomationStateError(f"{field_prefix}.attempts is invalid")
    if attempt_id is not None and (
        not isinstance(attempt_id, str) or ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None
    ):
        raise AutomationStateError(f"{field_prefix}.attempt_id is invalid")
    if last_result is not None and (
        not isinstance(last_result, str) or last_result not in ALLOWED_RESULTS
    ):
        raise AutomationStateError(f"{field_prefix}.last_result is invalid")
    if last_summary is not None:
        if not isinstance(last_summary, str):
            raise AutomationStateError(f"{field_prefix}.last_summary is invalid")
        try:
            last_summary = _validated_summary(last_summary)
        except AutomationStateError as error:
            raise AutomationStateError(
                f"{field_prefix}.last_summary contains prohibited content"
            ) from error
    last_attempt_at = _parse_optional_time(
        mapping["last_attempt_at"], f"{field_prefix}.last_attempt_at"
    )
    last_result_at = _parse_optional_time(
        mapping["last_result_at"], f"{field_prefix}.last_result_at"
    )
    if attempts == 0 and any(
        value is not None
        for value in (attempt_id, last_attempt_at, last_result, last_summary, last_result_at)
    ):
        raise AutomationStateError(f"{field_prefix} has result data without an attempt")
    if attempts > 0 and (attempt_id is None or last_attempt_at is None):
        raise AutomationStateError(f"{field_prefix} lacks current attempt data")
    if (last_result is None) != (last_summary is None) or (last_result is None) != (
        last_result_at is None
    ):
        raise AutomationStateError(f"{field_prefix} has an incomplete result")
    return (
        attempts,
        attempt_id,
        last_attempt_at,
        last_result,
        last_summary,
        last_result_at,
    )


def _optional_validated_string(
    value: object,
    pattern: re.Pattern[str],
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AutomationStateError(f"{field_name} is invalid")
    return value


def _validate_phase_shape(item_id: str, item: WorkItemState) -> None:
    field_prefix = f"work_items.{item_id}"
    phase = item.phase
    if item.attempts == 0:
        if item.legacy_migrated:
            raise AutomationStateError(
                f"{field_prefix} cannot mark an empty item as legacy-migrated"
            )
        if any(
            value is not None
            for value in (
                phase,
                item.branch,
                item.base_commit,
                item.child_run_id,
                item.child_session,
                item.task_commit,
                item.delivery_commit,
            )
        ):
            raise AutomationStateError(f"{field_prefix} has execution data without an attempt")
        return
    if phase not in PHASES:
        raise AutomationStateError(f"{field_prefix}.phase is invalid")
    if item.legacy_migrated and phase not in {"TERMINAL", "RECOVERY_REQUIRED"}:
        raise AutomationStateError(
            f"{field_prefix} legacy migration cannot enter an executable phase"
        )
    if phase not in {"TERMINAL", "RECOVERY_REQUIRED"} and (
        item.branch is None or item.base_commit is None or item.child_run_id is None
    ):
        raise AutomationStateError(f"{field_prefix} lacks recovery metadata")
    if phase in {
        "CHILD_RUNNING",
        "VERIFYING",
        "TASK_COMMITTED",
        "MERGED",
        "DELIVERY_COMMITTED",
    } and (item.child_session is None):
        raise AutomationStateError(f"{field_prefix} lacks child session metadata")
    if phase in {"TASK_COMMITTED", "MERGED", "DELIVERY_COMMITTED"} and (item.task_commit is None):
        raise AutomationStateError(f"{field_prefix} lacks task commit metadata")
    if phase not in {
        "TASK_COMMITTED",
        "MERGED",
        "DELIVERY_COMMITTED",
        "BLOCK_COMMITTED",
        "TERMINAL",
        "RECOVERY_REQUIRED",
    } and (item.task_commit is not None):
        raise AutomationStateError(f"{field_prefix} has task commit before commit phase")
    if phase in {"DELIVERY_COMMITTED", "BLOCK_COMMITTED"} and (item.delivery_commit is None):
        raise AutomationStateError(f"{field_prefix} lacks delivery commit metadata")
    if phase not in {"DELIVERY_COMMITTED", "BLOCK_COMMITTED", "TERMINAL"} and (
        item.delivery_commit is not None
    ):
        raise AutomationStateError(f"{field_prefix} has delivery commit before delivery phase")
    if phase == "PREPARED" and item.child_session is not None:
        raise AutomationStateError(f"{field_prefix} has child session before child phase")
    has_result = item.last_result is not None
    if (phase == "TERMINAL") != has_result:
        raise AutomationStateError(f"{field_prefix} phase and result are inconsistent")
    if phase == "TERMINAL":
        legacy_without_execution_metadata = item.legacy_migrated and all(
            value is None
            for value in (
                item.branch,
                item.base_commit,
                item.child_run_id,
                item.child_session,
                item.task_commit,
                item.delivery_commit,
            )
        )
        if (
            item.last_result == "PASSED"
            and not legacy_without_execution_metadata
            and any(
                value is None
                for value in (
                    item.branch,
                    item.base_commit,
                    item.child_run_id,
                    item.child_session,
                    item.task_commit,
                    item.delivery_commit,
                )
            )
        ):
            raise AutomationStateError(f"{field_prefix} passed without complete commit metadata")
        if (
            item.last_result == "BLOCKED"
            and not legacy_without_execution_metadata
            and any(
                value is None
                for value in (
                    item.branch,
                    item.base_commit,
                    item.child_run_id,
                    item.delivery_commit,
                )
            )
        ):
            raise AutomationStateError(f"{field_prefix} blocked without control commit metadata")
        if item.last_result in RETRYABLE_RESULTS and item.delivery_commit is not None:
            raise AutomationStateError(
                f"{field_prefix} retryable result has delivery commit metadata"
            )
        if not item.legacy_migrated and any(
            value is None
            for value in (
                item.branch,
                item.base_commit,
                item.child_run_id,
            )
        ):
            raise AutomationStateError(
                f"{field_prefix} terminal result lacks attempt recovery metadata"
            )
        if item.last_result in RETRYABLE_RESULTS and item.attempts >= MAX_ATTEMPTS:
            raise AutomationStateError(
                f"{field_prefix} exhausted retry budget without a blocked control commit"
            )


def _parse_work_item_v2(item_id: str, value: object) -> WorkItemState:
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
            "phase",
            "branch",
            "base_commit",
            "child_run_id",
            "child_session",
            "task_commit",
            "delivery_commit",
            "legacy_migrated",
        },
        f"work_items.{item_id}",
    )
    (
        attempts,
        attempt_id,
        last_attempt_at,
        last_result,
        last_summary,
        last_result_at,
    ) = _parse_attempt_common(item_id, mapping)
    phase = mapping["phase"]
    if phase is not None and (not isinstance(phase, str) or phase not in PHASES):
        raise AutomationStateError(f"work_items.{item_id}.phase is invalid")
    legacy_migrated = mapping["legacy_migrated"]
    if not isinstance(legacy_migrated, bool):
        raise AutomationStateError(f"work_items.{item_id}.legacy_migrated must be a boolean")
    item = WorkItemState(
        attempts=attempts,
        attempt_id=attempt_id,
        last_attempt_at=last_attempt_at,
        last_result=last_result,
        last_summary=last_summary,
        last_result_at=last_result_at,
        phase=phase,
        branch=_optional_validated_string(
            mapping["branch"], BRANCH_PATTERN, f"work_items.{item_id}.branch"
        ),
        base_commit=_optional_validated_string(
            mapping["base_commit"], COMMIT_PATTERN, f"work_items.{item_id}.base_commit"
        ),
        child_run_id=_optional_validated_string(
            mapping["child_run_id"], RUN_ID_PATTERN, f"work_items.{item_id}.child_run_id"
        ),
        child_session=_optional_validated_string(
            mapping["child_session"], SESSION_PATTERN, f"work_items.{item_id}.child_session"
        ),
        task_commit=_optional_validated_string(
            mapping["task_commit"], COMMIT_PATTERN, f"work_items.{item_id}.task_commit"
        ),
        delivery_commit=_optional_validated_string(
            mapping["delivery_commit"],
            COMMIT_PATTERN,
            f"work_items.{item_id}.delivery_commit",
        ),
        legacy_migrated=legacy_migrated,
    )
    _validate_phase_shape(item_id, item)
    return item


def _parse_work_item_v1(item_id: str, value: object) -> WorkItemState:
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
    (
        attempts,
        attempt_id,
        last_attempt_at,
        last_result,
        last_summary,
        last_result_at,
    ) = _parse_attempt_common(item_id, mapping)
    if attempts == 0:
        phase = None
    elif last_result is not None:
        phase = "TERMINAL"
    else:
        phase = "RECOVERY_REQUIRED"
    item = WorkItemState(
        attempts=attempts,
        attempt_id=attempt_id,
        last_attempt_at=last_attempt_at,
        last_result=last_result,
        last_summary=last_summary,
        last_result_at=last_result_at,
        phase=phase,
        legacy_migrated=attempts > 0,
    )
    _validate_phase_shape(item_id, item)
    return item


def _parse_state(value: object) -> AutomationState:
    mapping = _string_mapping(value, "automation state")
    _exact_keys(
        mapping,
        {"schema_version", "updated_at", "lease", "work_items"},
        "automation state",
    )
    schema_version = mapping["schema_version"]
    if schema_version not in {1, SCHEMA_VERSION}:
        raise AutomationStateError("unsupported automation state schema_version")
    work_items_raw = _string_mapping(mapping["work_items"], "work_items")
    parse_work_item = (
        _parse_work_item_v2 if schema_version == SCHEMA_VERSION else _parse_work_item_v1
    )
    work_items = {
        item_id: parse_work_item(item_id, item_value)
        for item_id, item_value in work_items_raw.items()
    }
    in_flight = [
        item_id for item_id, item in work_items.items() if item.phase in NONTERMINAL_PHASES
    ]
    if len(in_flight) > 1:
        raise AutomationStateError("automation state has more than one non-terminal attempt")
    return AutomationState(
        updated_at=_parse_time(mapping["updated_at"], "updated_at"),
        lease=_parse_lease(mapping["lease"], schema_version=schema_version),
        work_items=work_items,
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
                f"- Lease ID: `{state.lease.lease_id}`",
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
            "| Work item | Attempts | Attempt ID | Phase | Branch | Child session "
            "| Task commit | Delivery commit | Last result | Result time | Summary |",
            "|---|---:|---|---|---|---|---|---|---|---|---|",
        ]
    )
    if not state.work_items:
        lines.append("| — | 0 | — | — | — | — | — | — | — | — | — |")
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
                        _markdown_cell(item.phase),
                        _markdown_cell(item.branch),
                        _markdown_cell(item.child_session),
                        _markdown_cell(item.task_commit),
                        _markdown_cell(item.delivery_commit),
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
    # Every mutation must round-trip through the same parser used on recovery.
    # This prevents a transition from publishing state that the next process
    # would reject (for example, an incomplete block-commit phase).
    _parse_state(state.to_mapping())
    json_text = json.dumps(
        state.to_mapping(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    _atomic_write(state_dir / STATE_JSON_NAME, f"{json_text}\n")
    _atomic_write(state_dir / STATE_MARKDOWN_NAME, _render_markdown(state))


def _ensure_mutex_byte(handle: IO[bytes]) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())


def _try_lock_mutex(handle: IO[bytes]) -> bool:
    handle.seek(0)
    if os.name == "nt":
        msvcrt_attributes = vars(importlib.import_module("msvcrt"))
        locking = cast(Callable[[int, int, int], None], msvcrt_attributes["locking"])
        lock_nonblocking = cast(int, msvcrt_attributes["LK_NBLCK"])

        try:
            locking(handle.fileno(), lock_nonblocking, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    fcntl_attributes = vars(importlib.import_module("fcntl"))
    flock = cast(Callable[[int, int], None], fcntl_attributes["flock"])
    lock_exclusive = cast(int, fcntl_attributes["LOCK_EX"])
    lock_nonblocking = cast(int, fcntl_attributes["LOCK_NB"])
    try:
        flock(handle.fileno(), lock_exclusive | lock_nonblocking)
    except BlockingIOError:
        return False
    return True


def _unlock_mutex(handle: IO[bytes]) -> None:
    if os.name == "nt":
        msvcrt_attributes = vars(importlib.import_module("msvcrt"))
        locking = cast(Callable[[int, int, int], None], msvcrt_attributes["locking"])
        lock_un = cast(int, msvcrt_attributes["LK_UNLCK"])
        handle.seek(0)
        locking(handle.fileno(), lock_un, 1)
    else:
        fcntl_attributes = vars(importlib.import_module("fcntl"))
        flock = cast(Callable[[int, int], None], fcntl_attributes["flock"])
        lock_un = cast(int, fcntl_attributes["LOCK_UN"])
        flock(handle.fileno(), lock_un)


@contextmanager
def _state_mutex(
    state_dir: Path,
    *,
    timeout_seconds: float = DEFAULT_MUTEX_TIMEOUT_SECONDS,
) -> Iterator[None]:
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    state_dir.mkdir(parents=True, exist_ok=True)
    mutex_path = state_dir / MUTEX_NAME
    with mutex_path.open("a+b") as mutex_file:
        _ensure_mutex_byte(mutex_file)
        deadline = time.monotonic() + timeout_seconds
        while not _try_lock_mutex(mutex_file):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AutomationStateError(
                    f"timed out acquiring automation state mutex after {timeout_seconds:.3f}s"
                )
            time.sleep(min(MUTEX_POLL_INTERVAL_SECONDS, remaining))
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


def _attempt_ordinal(attempt_id: str) -> int:
    matched = ATTEMPT_ORDINAL_PATTERN.search(attempt_id)
    if matched is None:
        raise AutomationStateError("attempt ID must end with its attempt ordinal")
    return int(matched.group(1))


def _validated_lease_id(lease_id: str) -> str:
    if LEASE_ID_PATTERN.fullmatch(lease_id) is None:
        raise AutomationStateError("lease ID must be a canonical UUID")
    return lease_id


def _validated_branch(branch: str) -> str:
    if BRANCH_PATTERN.fullmatch(branch) is None:
        raise AutomationStateError("branch must be a safe Git branch name")
    return branch


def _validated_commit(commit: str, field_name: str) -> str:
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise AutomationStateError(f"{field_name} must be a 7-64 character hexadecimal commit ID")
    return commit.lower()


def _validated_child_run_id(child_run_id: str) -> str:
    if RUN_ID_PATTERN.fullmatch(child_run_id) is None:
        raise AutomationStateError("child run ID must be a safe 1-128 character identifier")
    return child_run_id


def _child_run_ordinal(child_run_id: str) -> int:
    matched = RUN_ORDINAL_PATTERN.search(child_run_id)
    if matched is None:
        raise AutomationStateError("child run ID must end with its attempt ordinal")
    return int(matched.group(1))


def _validated_child_session(child_session: str) -> str:
    if SESSION_PATTERN.fullmatch(child_session) is None:
        raise AutomationStateError("child session must be a safe 1-256 character identifier")
    return child_session


def _validated_result(result: str) -> str:
    if result not in ALLOWED_RESULTS:
        raise AutomationStateError(f"result must be one of {', '.join(sorted(ALLOWED_RESULTS))}")
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


def _require_active_owner(
    state: AutomationState,
    owner: str,
    lease_id: str,
    now: datetime,
) -> Lease:
    lease = state.lease
    if lease is None:
        raise AutomationStateError("no automation lease is currently held")
    if lease.owner != owner or lease.lease_id != lease_id or lease.expires_at <= now:
        raise LeaseBusyError(lease.owner, lease.expires_at)
    return lease


def acquire_lease(
    state_dir: Path,
    owner: str,
    ttl_seconds: int,
    *,
    lease_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    requested_lease_id = _validated_lease_id(lease_id) if lease_id is not None else None
    if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise AutomationStateError(f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}")
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        previous = state.lease
        if (
            previous is not None
            and previous.expires_at > current
            and (previous.owner != owner or requested_lease_id != previous.lease_id)
        ):
            raise LeaseBusyError(previous.owner, previous.expires_at)
        if previous is None:
            if requested_lease_id is not None:
                raise AutomationStateError(
                    "lease ID is only valid when renewing an active same-owner lease"
                )
            action = "ACQUIRED"
            acquired_at = current
            next_lease_id = str(uuid.uuid4())
        elif previous.expires_at <= current:
            if requested_lease_id is not None:
                raise AutomationStateError("stale lease takeover must mint a new lease ID")
            action = "STALE_TAKEOVER"
            acquired_at = current
            next_lease_id = str(uuid.uuid4())
        else:
            action = "RENEWED"
            acquired_at = previous.acquired_at
            next_lease_id = previous.lease_id
        state.lease = Lease(
            lease_id=next_lease_id,
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
            "lease_id": next_lease_id,
            "expires_at": _format_time(state.lease.expires_at),
        }


def release_lease(
    state_dir: Path,
    owner: str,
    lease_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        state.lease = None
        state.updated_at = current
        _write_state(state_dir, state)
        return {"action": "RELEASED", "owner": owner, "lease_id": lease_id}


def begin_attempt(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    branch: str,
    base_commit: str,
    child_run_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    attempt_ordinal = _attempt_ordinal(attempt_id)
    branch = _validated_branch(branch)
    base_commit = _validated_commit(base_commit, "base commit")
    child_run_id = _validated_child_run_id(child_run_id)
    child_run_ordinal = _child_run_ordinal(child_run_id)
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = state.work_items.setdefault(work_item, WorkItemState())
        if item.attempt_id == attempt_id:
            if (
                item.branch != branch
                or item.base_commit != base_commit
                or item.child_run_id != child_run_id
            ):
                raise AutomationStateError(
                    "attempt ID is already bound to different recovery metadata"
                )
            return {
                "action": "ATTEMPT_ALREADY_BEGUN",
                "owner": owner,
                "lease_id": lease_id,
                "work_item": work_item,
                "attempt_id": attempt_id,
                "attempt": item.attempts,
                "phase": item.phase,
            }
        next_ordinal = item.attempts + 1
        if attempt_ordinal != next_ordinal or child_run_ordinal != next_ordinal:
            raise AutomationStateError(
                "attempt and child run IDs must match the next attempt ordinal"
            )
        for item_id, candidate in state.work_items.items():
            if candidate.attempt_id == attempt_id:
                raise AutomationStateError(f"attempt ID is already assigned to work item {item_id}")
            if candidate.child_run_id == child_run_id:
                raise AutomationStateError(
                    f"child run ID is already assigned to work item {item_id}"
                )
        active_items = [
            item_id
            for item_id, candidate in state.work_items.items()
            if candidate.phase in NONTERMINAL_PHASES
        ]
        if active_items:
            raise AutomationStateError(
                f"cannot begin an attempt while {active_items[0]} is non-terminal"
            )
        if item.attempts >= MAX_ATTEMPTS:
            raise AttemptLimitError(work_item, item.attempts)
        item.attempts += 1
        item.attempt_id = attempt_id
        item.last_attempt_at = current
        item.last_result = None
        item.last_summary = None
        item.last_result_at = None
        item.phase = "PREPARED"
        item.branch = branch
        item.base_commit = base_commit
        item.child_run_id = child_run_id
        item.child_session = None
        item.task_commit = None
        item.delivery_commit = None
        item.legacy_migrated = False
        state.updated_at = current
        _write_state(state_dir, state)
        return {
            "action": "ATTEMPT_BEGUN",
            "owner": owner,
            "lease_id": lease_id,
            "work_item": work_item,
            "attempt_id": attempt_id,
            "attempt": item.attempts,
            "phase": item.phase,
        }


def _require_current_attempt(
    state: AutomationState,
    work_item: str,
    attempt_id: str,
    operation: str,
) -> WorkItemState:
    item = state.work_items.get(work_item)
    if item is None or item.attempts == 0:
        raise AutomationStateError(f"{operation} requires a prior begin-attempt")
    if item.attempt_id != attempt_id:
        raise AutomationStateError(f"{operation} attempt ID is not the current attempt")
    return item


def attach_child(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    child_session: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    child_session = _validated_child_session(child_session)
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = _require_current_attempt(state, work_item, attempt_id, "attach-child")
        if item.phase == "CHILD_RUNNING":
            if item.child_session != child_session:
                raise AutomationStateError(
                    "current attempt already has a different immutable child session"
                )
            return _transition_output("CHILD_ALREADY_ATTACHED", owner, lease_id, work_item, item)
        if item.phase != "PREPARED":
            raise AutomationStateError("attach-child requires PREPARED phase")
        if item.child_session is not None:
            raise AutomationStateError("PREPARED attempt unexpectedly has a child session")
        item.child_session = child_session
        item.phase = "CHILD_RUNNING"
        state.updated_at = current
        _write_state(state_dir, state)
        return _transition_output("CHILD_ATTACHED", owner, lease_id, work_item, item)


def _transition_output(
    action: str,
    owner: str,
    lease_id: str,
    work_item: str,
    item: WorkItemState,
) -> dict[str, object]:
    return {
        "action": action,
        "owner": owner,
        "lease_id": lease_id,
        "work_item": work_item,
        "attempt_id": item.attempt_id,
        "attempt": item.attempts,
        "phase": item.phase,
    }


def begin_verification(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    return _advance_phase(
        state_dir,
        owner,
        lease_id,
        work_item,
        attempt_id,
        operation="begin-verification",
        source_phase="CHILD_RUNNING",
        target_phase="VERIFYING",
        action="VERIFICATION_BEGUN",
        idempotent_action="VERIFICATION_ALREADY_BEGUN",
        now=now,
    )


def _advance_phase(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    *,
    operation: str,
    source_phase: str,
    target_phase: str,
    action: str,
    idempotent_action: str,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = _require_current_attempt(state, work_item, attempt_id, operation)
        if item.phase == target_phase:
            return _transition_output(idempotent_action, owner, lease_id, work_item, item)
        if item.phase != source_phase:
            raise AutomationStateError(f"{operation} requires {source_phase} phase")
        item.phase = target_phase
        state.updated_at = current
        _write_state(state_dir, state)
        return _transition_output(action, owner, lease_id, work_item, item)


def record_task_commit(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    task_commit: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    task_commit = _validated_commit(task_commit, "task commit")
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = _require_current_attempt(state, work_item, attempt_id, "record-task-commit")
        if item.phase == "TASK_COMMITTED":
            if item.task_commit != task_commit:
                raise AutomationStateError(
                    "current attempt already has a different immutable task commit"
                )
            return _transition_output(
                "TASK_COMMIT_ALREADY_RECORDED", owner, lease_id, work_item, item
            )
        if item.phase != "VERIFYING":
            raise AutomationStateError("record-task-commit requires VERIFYING phase")
        if item.task_commit is not None:
            raise AutomationStateError("VERIFYING attempt unexpectedly has a task commit")
        item.task_commit = task_commit
        item.phase = "TASK_COMMITTED"
        state.updated_at = current
        _write_state(state_dir, state)
        return _transition_output("TASK_COMMIT_RECORDED", owner, lease_id, work_item, item)


def record_merged(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    return _advance_phase(
        state_dir,
        owner,
        lease_id,
        work_item,
        attempt_id,
        operation="record-merged",
        source_phase="TASK_COMMITTED",
        target_phase="MERGED",
        action="MERGE_RECORDED",
        idempotent_action="MERGE_ALREADY_RECORDED",
        now=now,
    )


def record_delivery_commit(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    delivery_commit: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    delivery_commit = _validated_commit(delivery_commit, "delivery commit")
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = _require_current_attempt(
            state,
            work_item,
            attempt_id,
            "record-delivery-commit",
        )
        if item.phase == "DELIVERY_COMMITTED":
            if item.delivery_commit != delivery_commit:
                raise AutomationStateError(
                    "current attempt already has a different immutable delivery commit"
                )
            return _transition_output(
                "DELIVERY_COMMIT_ALREADY_RECORDED",
                owner,
                lease_id,
                work_item,
                item,
            )
        if item.phase != "MERGED":
            raise AutomationStateError("record-delivery-commit requires MERGED phase")
        if item.delivery_commit is not None:
            raise AutomationStateError("MERGED attempt unexpectedly has a delivery commit")
        item.delivery_commit = delivery_commit
        item.phase = "DELIVERY_COMMITTED"
        state.updated_at = current
        _write_state(state_dir, state)
        return _transition_output(
            "DELIVERY_COMMIT_RECORDED",
            owner,
            lease_id,
            work_item,
            item,
        )


def record_block_commit(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    delivery_commit: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    delivery_commit = _validated_commit(delivery_commit, "block commit")
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = _require_current_attempt(
            state,
            work_item,
            attempt_id,
            "record-block-commit",
        )
        if item.phase == "BLOCK_COMMITTED":
            if item.delivery_commit != delivery_commit:
                raise AutomationStateError(
                    "current attempt already has a different immutable block commit"
                )
            return _transition_output(
                "BLOCK_COMMIT_ALREADY_RECORDED",
                owner,
                lease_id,
                work_item,
                item,
            )
        if item.phase != "RECOVERY_REQUIRED":
            raise AutomationStateError("record-block-commit requires RECOVERY_REQUIRED phase")
        if item.delivery_commit is not None:
            raise AutomationStateError("RECOVERY_REQUIRED attempt unexpectedly has a block commit")
        item.delivery_commit = delivery_commit
        item.phase = "BLOCK_COMMITTED"
        state.updated_at = current
        _write_state(state_dir, state)
        return _transition_output(
            "BLOCK_COMMIT_RECORDED",
            owner,
            lease_id,
            work_item,
            item,
        )


def record_recovery_required(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = _require_current_attempt(state, work_item, attempt_id, "record-recovery-required")
        if item.phase == "RECOVERY_REQUIRED":
            return _transition_output("RECOVERY_ALREADY_REQUIRED", owner, lease_id, work_item, item)
        if item.phase not in NONTERMINAL_PHASES:
            raise AutomationStateError("terminal attempts cannot require recovery")
        if item.phase in {"MERGED", "DELIVERY_COMMITTED", "BLOCK_COMMITTED"}:
            raise AutomationStateError("post-merge attempts cannot regress to RECOVERY_REQUIRED")
        item.phase = "RECOVERY_REQUIRED"
        state.updated_at = current
        _write_state(state_dir, state)
        return _transition_output("RECOVERY_REQUIRED", owner, lease_id, work_item, item)


def record_result(
    state_dir: Path,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    result: str,
    summary: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    owner = _validated_owner(owner)
    lease_id = _validated_lease_id(lease_id)
    work_item = _validated_work_item(work_item)
    attempt_id = _validated_attempt_id(attempt_id)
    result = _validated_result(result)
    summary = _validated_summary(summary)
    with _state_mutex(state_dir):
        current = _normalized_now(now)
        state = _load_state(state_dir, current)
        _require_active_owner(state, owner, lease_id, current)
        item = _require_current_attempt(state, work_item, attempt_id, "record-result")
        if item.phase == "TERMINAL":
            if item.last_result == result and item.last_summary == summary:
                return {
                    "action": "RESULT_ALREADY_RECORDED",
                    "owner": owner,
                    "lease_id": lease_id,
                    "work_item": work_item,
                    "attempt_id": attempt_id,
                    "attempt": item.attempts,
                    "result": result,
                }
            raise AutomationStateError(
                "current attempt already has a different immutable result or summary"
            )
        if result == "PASSED" and item.phase != "DELIVERY_COMMITTED":
            raise AutomationStateError("PASSED result requires DELIVERY_COMMITTED phase")
        if result == "BLOCKED" and item.phase != "BLOCK_COMMITTED":
            raise AutomationStateError("BLOCKED result requires BLOCK_COMMITTED phase")
        if result in RETRYABLE_RESULTS and item.attempts >= MAX_ATTEMPTS:
            raise AutomationStateError(
                "final retry budget must end through the BLOCK_COMMITTED path"
            )
        if result in RETRYABLE_RESULTS and item.phase not in {
            "PREPARED",
            "CHILD_RUNNING",
            "VERIFYING",
            "TASK_COMMITTED",
        }:
            raise AutomationStateError("retryable result requires a pre-merge execution phase")
        item.last_result = result
        item.last_summary = summary
        item.last_result_at = current
        item.phase = "TERMINAL"
        state.updated_at = current
        _write_state(state_dir, state)
        return {
            "action": "RESULT_RECORDED",
            "owner": owner,
            "lease_id": lease_id,
            "work_item": work_item,
            "attempt_id": attempt_id,
            "attempt": item.attempts,
            "result": result,
            "phase": item.phase,
        }


def status(
    state_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    with _state_mutex(state_dir):
        current = _normalized_now(now)
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
    acquire.add_argument(
        "--lease-id",
        help="Required fencing ID when renewing an active same-owner lease.",
    )
    acquire.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)

    release = commands.add_parser("release", help="Release the current lease as its owner.")
    release.add_argument("--owner", required=True)
    release.add_argument("--lease-id", required=True)

    attempt = commands.add_parser("begin-attempt", help="Increment a work item's attempt counter.")
    attempt.add_argument("--owner", required=True)
    attempt.add_argument("--lease-id", required=True)
    attempt.add_argument("--work-item", required=True)
    attempt.add_argument("--attempt-id", required=True)
    attempt.add_argument("--branch", required=True)
    attempt.add_argument("--base-commit", required=True)
    attempt.add_argument("--child-run-id", required=True)

    attach = commands.add_parser(
        "attach-child", help="Attach the uniquely recovered child session."
    )
    attach.add_argument("--owner", required=True)
    attach.add_argument("--lease-id", required=True)
    attach.add_argument("--work-item", required=True)
    attach.add_argument("--attempt-id", required=True)
    attach.add_argument("--child-session", required=True)

    verification = commands.add_parser(
        "begin-verification", help="Move a child-complete attempt into verification."
    )
    verification.add_argument("--owner", required=True)
    verification.add_argument("--lease-id", required=True)
    verification.add_argument("--work-item", required=True)
    verification.add_argument("--attempt-id", required=True)

    task_commit = commands.add_parser(
        "record-task-commit", help="Record the verified task-branch commit."
    )
    task_commit.add_argument("--owner", required=True)
    task_commit.add_argument("--lease-id", required=True)
    task_commit.add_argument("--work-item", required=True)
    task_commit.add_argument("--attempt-id", required=True)
    task_commit.add_argument("--task-commit", required=True)

    merged = commands.add_parser(
        "record-merged", help="Record that the task commit was fast-forwarded to main."
    )
    merged.add_argument("--owner", required=True)
    merged.add_argument("--lease-id", required=True)
    merged.add_argument("--work-item", required=True)
    merged.add_argument("--attempt-id", required=True)

    delivery_commit = commands.add_parser(
        "record-delivery-commit",
        help="Record the control-state commit after delivery completion.",
    )
    delivery_commit.add_argument("--owner", required=True)
    delivery_commit.add_argument("--lease-id", required=True)
    delivery_commit.add_argument("--work-item", required=True)
    delivery_commit.add_argument("--attempt-id", required=True)
    delivery_commit.add_argument("--delivery-commit", required=True)

    block_commit = commands.add_parser(
        "record-block-commit",
        help="Record the control-state commit after blocking delivery work.",
    )
    block_commit.add_argument("--owner", required=True)
    block_commit.add_argument("--lease-id", required=True)
    block_commit.add_argument("--work-item", required=True)
    block_commit.add_argument("--attempt-id", required=True)
    block_commit.add_argument("--delivery-commit", required=True)

    recovery = commands.add_parser(
        "record-recovery-required",
        help="Fail closed when deterministic recovery cannot continue.",
    )
    recovery.add_argument("--owner", required=True)
    recovery.add_argument("--lease-id", required=True)
    recovery.add_argument("--work-item", required=True)
    recovery.add_argument("--attempt-id", required=True)

    result = commands.add_parser("record-result", help="Record the latest work-item result.")
    result.add_argument("--owner", required=True)
    result.add_argument("--lease-id", required=True)
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
                lease_id=cast(str | None, arguments.lease_id),
            )
        elif arguments.command == "release":
            output = release_lease(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
            )
        elif arguments.command == "begin-attempt":
            output = begin_attempt(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
                cast(str, arguments.branch),
                cast(str, arguments.base_commit),
                cast(str, arguments.child_run_id),
            )
        elif arguments.command == "attach-child":
            output = attach_child(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
                cast(str, arguments.child_session),
            )
        elif arguments.command == "begin-verification":
            output = begin_verification(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
            )
        elif arguments.command == "record-task-commit":
            output = record_task_commit(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
                cast(str, arguments.task_commit),
            )
        elif arguments.command == "record-merged":
            output = record_merged(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
            )
        elif arguments.command == "record-delivery-commit":
            output = record_delivery_commit(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
                cast(str, arguments.delivery_commit),
            )
        elif arguments.command == "record-block-commit":
            output = record_block_commit(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
                cast(str, arguments.delivery_commit),
            )
        elif arguments.command == "record-recovery-required":
            output = record_recovery_required(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
                cast(str, arguments.work_item),
                cast(str, arguments.attempt_id),
            )
        elif arguments.command == "record-result":
            output = record_result(
                state_dir,
                cast(str, arguments.owner),
                cast(str, arguments.lease_id),
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
    except AttemptLimitError as error:
        print(
            json.dumps(
                {
                    "error": "ATTEMPT_LIMIT_REACHED",
                    "work_item": error.work_item,
                    "attempts": error.attempts,
                    "max_attempts": MAX_ATTEMPTS,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return EXIT_STATE_ERROR
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
