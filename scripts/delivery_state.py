"""Serialize and recover mutations of the three delivery-control YAML files.

Callers must hold :func:`delivery_state_mutex` for the complete
``recover -> read -> validate -> mutate -> commit`` operation. The published
journal is the transaction commit point. If a process stops after that point,
the next lock holder rolls all three files forward to the journaled generation.
"""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
DELIVERY_DIRECTORY = Path("delivery")
LOCK_NAME = ".delivery-state.lock"
JOURNAL_NAME = ".delivery-state.transaction.json"
TARGET_RELATIVE_PATHS = (
    "delivery/WORK_QUEUE.yaml",
    "delivery/LOOP_STATE.yaml",
    "delivery/PROGRAM_PLAN.yaml",
)
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
JOURNAL_SCHEMA_VERSION = 1
ALLOWED_DELIVERY_STATUSES = frozenset({"PENDING", "READY", "IN_PROGRESS", "COMPLETE", "BLOCKED"})
ALLOWED_PHASE_STATUSES = frozenset({"PENDING", "IN_PROGRESS", "COMPLETE", "BLOCKED"})


class DeliveryStateError(RuntimeError):
    """Base class for delivery state coordination failures."""


class DeliveryStateLockTimeout(DeliveryStateError):
    """Raised when the shared delivery mutex cannot be acquired in time."""


class DeliveryTransactionError(DeliveryStateError):
    """Raised when a delivery transaction journal is invalid or inconsistent."""


def _ensure_lock_byte(handle: IO[bytes]) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())


def _try_lock(handle: IO[bytes]) -> bool:
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


def _unlock(handle: IO[bytes]) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt_attributes = vars(importlib.import_module("msvcrt"))
        locking = cast(Callable[[int, int, int], None], msvcrt_attributes["locking"])
        lock_un = cast(int, msvcrt_attributes["LK_UNLCK"])
        locking(handle.fileno(), lock_un, 1)
        return

    fcntl_attributes = vars(importlib.import_module("fcntl"))
    flock = cast(Callable[[int, int], None], fcntl_attributes["flock"])
    lock_un = cast(int, fcntl_attributes["LOCK_UN"])
    flock(handle.fileno(), lock_un)


@contextmanager
def delivery_state_mutex(
    *,
    root: Path = ROOT,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Acquire the shared cross-platform delivery-state mutex.

    The acquisition is bounded so a dead or unexpectedly long-running
    controller fails closed instead of silently waiting forever.
    """

    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    delivery_directory = root / DELIVERY_DIRECTORY
    delivery_directory.mkdir(parents=True, exist_ok=True)
    lock_path = delivery_directory / LOCK_NAME
    with lock_path.open("a+b") as lock_file:
        _ensure_lock_byte(lock_file)
        deadline = time.monotonic() + timeout_seconds
        while not _try_lock(lock_file):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeliveryStateLockTimeout(
                    f"Timed out acquiring delivery state mutex after {timeout_seconds:.3f}s"
                )
            time.sleep(min(LOCK_POLL_INTERVAL_SECONDS, remaining))
        try:
            yield
        finally:
            _unlock(lock_file)


def _serialize_mapping(content: dict[str, Any]) -> str:
    serialized = yaml.safe_dump(
        content,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return serialized if serialized.endswith("\n") else f"{serialized}\n"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError as error:
        if os.name == "nt" and error.errno in {errno.EACCES, errno.EPERM, errno.EINVAL}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if not (
                os.name == "nt"
                and error.errno in {errno.EACCES, errno.EPERM, errno.EINVAL, errno.EBADF}
            ):
                raise
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        if existing_mode is not None:
            os.chmod(temporary_path, existing_mode)
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def _publish_journal(journal_path: Path, content: bytes) -> None:
    _atomic_replace(journal_path, content)
    # Windows requires a write-capable descriptor for fsync/commit.
    with journal_path.open("r+b") as journal_file:
        os.fsync(journal_file.fileno())
    _fsync_directory(journal_path.parent)


def _replace_target(path: Path, content: bytes) -> None:
    _atomic_replace(path, content)


def _build_journal(
    queue: dict[str, Any],
    state: dict[str, Any],
    program: dict[str, Any],
) -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    for relative_path, mapping in zip(
        TARGET_RELATIVE_PATHS,
        (queue, state, program),
        strict=True,
    ):
        content = _serialize_mapping(mapping).encode("utf-8")
        entries.append(
            {
                "path": relative_path,
                "sha256": _sha256(content),
                "yaml": content.decode("utf-8"),
            }
        )
    return {"schema_version": JOURNAL_SCHEMA_VERSION, "targets": entries}


def delivery_generation_digest(
    queue: dict[str, Any],
    state: dict[str, Any],
    program: dict[str, Any],
) -> str:
    """Return a stable digest for one logical delivery generation."""

    digest = hashlib.sha256()
    for relative_path, mapping in zip(
        TARGET_RELATIVE_PATHS,
        (queue, state, program),
        strict=True,
    ):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_serialize_mapping(mapping).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def validate_delivery_generation(
    queue: dict[str, Any],
    state: dict[str, Any],
    program: dict[str, Any],
) -> None:
    """Validate cross-file queue, loop-state, and program invariants."""

    items = queue.get("items")
    if queue.get("schema_version") != 1 or not isinstance(items, list):
        raise DeliveryTransactionError(
            "Delivery queue must have schema_version 1 and an items list"
        )
    item_ids: set[str] = set()
    in_progress: list[str] = []
    statuses_by_phase: dict[str, set[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise DeliveryTransactionError("Delivery queue item must be a mapping")
        item_id = item.get("id")
        item_status = item.get("status")
        phase_id = item.get("phase")
        if not isinstance(item_id, str) or not item_id or item_id in item_ids:
            raise DeliveryTransactionError("Delivery queue item IDs must be unique strings")
        if item_status not in ALLOWED_DELIVERY_STATUSES:
            raise DeliveryTransactionError(f"Delivery queue item {item_id} has an invalid status")
        if not isinstance(phase_id, str) or not phase_id:
            raise DeliveryTransactionError(f"Delivery queue item {item_id} has an invalid phase")
        item_ids.add(item_id)
        if item_status == "IN_PROGRESS":
            in_progress.append(item_id)
        statuses_by_phase.setdefault(phase_id, set()).add(str(item_status))
    if len(in_progress) > 1:
        raise DeliveryTransactionError(
            "Delivery generation cannot contain multiple IN_PROGRESS items"
        )

    if state.get("schema_version") != 1:
        raise DeliveryTransactionError("Delivery loop state must have schema_version 1")
    current_work_item = state.get("current_work_item")
    expected_current = in_progress[0] if in_progress else None
    if current_work_item != expected_current:
        raise DeliveryTransactionError(
            "Delivery loop current_work_item does not match the queue generation"
        )

    phases = program.get("phases")
    if program.get("schema_version") != 1 or not isinstance(phases, list):
        raise DeliveryTransactionError(
            "Delivery program must have schema_version 1 and a phases list"
        )
    program_statuses: dict[str, str] = {}
    for phase in phases:
        if not isinstance(phase, dict):
            raise DeliveryTransactionError("Delivery program phase must be a mapping")
        phase_id = phase.get("id")
        phase_status = phase.get("status")
        if (
            not isinstance(phase_id, str)
            or not phase_id
            or phase_id in program_statuses
            or phase_status not in ALLOWED_PHASE_STATUSES
        ):
            raise DeliveryTransactionError("Delivery program phase is invalid")
        program_statuses[phase_id] = str(phase_status)
    if set(statuses_by_phase) != set(program_statuses):
        raise DeliveryTransactionError("Delivery queue and program contain different phase IDs")
    for phase_id, phase_statuses in statuses_by_phase.items():
        if phase_statuses == {"COMPLETE"}:
            expected_status = "COMPLETE"
        elif "IN_PROGRESS" in phase_statuses or "COMPLETE" in phase_statuses:
            expected_status = "IN_PROGRESS"
        elif phase_statuses == {"BLOCKED"}:
            expected_status = "BLOCKED"
        else:
            expected_status = "PENDING"
        if program_statuses[phase_id] != expected_status:
            raise DeliveryTransactionError(
                f"Delivery program phase {phase_id} is inconsistent with its queue items"
            )


def _serialize_journal(journal: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            journal,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _validated_entries(journal: object) -> list[tuple[str, bytes, str]]:
    if not isinstance(journal, dict) or journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise DeliveryTransactionError("Delivery transaction journal has an invalid schema")
    targets = journal.get("targets")
    if not isinstance(targets, list) or len(targets) != len(TARGET_RELATIVE_PATHS):
        raise DeliveryTransactionError("Delivery transaction journal must contain three targets")

    entries: list[tuple[str, bytes, str]] = []
    for expected_path, target in zip(TARGET_RELATIVE_PATHS, targets, strict=True):
        if not isinstance(target, dict) or set(target) != {"path", "sha256", "yaml"}:
            raise DeliveryTransactionError("Delivery transaction target has invalid fields")
        relative_path = target.get("path")
        expected_hash = target.get("sha256")
        yaml_content = target.get("yaml")
        if relative_path != expected_path:
            raise DeliveryTransactionError("Delivery transaction contains an unexpected target")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise DeliveryTransactionError("Delivery transaction target has an invalid SHA-256")
        if not isinstance(yaml_content, str):
            raise DeliveryTransactionError("Delivery transaction target has invalid YAML content")
        content = yaml_content.encode("utf-8")
        if _sha256(content) != expected_hash:
            raise DeliveryTransactionError("Delivery transaction target SHA-256 does not match")
        parsed = yaml.safe_load(yaml_content)
        if not isinstance(parsed, dict) or _serialize_mapping(parsed) != yaml_content:
            raise DeliveryTransactionError("Delivery transaction target YAML is not canonical")
        entries.append((relative_path, content, expected_hash))
    return entries


def _read_journal(journal_path: Path) -> object:
    try:
        return json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeliveryTransactionError("Delivery transaction journal cannot be read") from error


def _roll_forward(
    *,
    root: Path,
    journal_path: Path,
    journal: object,
) -> None:
    entries = _validated_entries(journal)
    generation = [
        cast(dict[str, Any], yaml.safe_load(content.decode("utf-8")))
        for _relative_path, content, _expected_hash in entries
    ]
    validate_delivery_generation(*generation)

    for relative_path, content, expected_hash in entries:
        target_path = root / relative_path
        if target_path.is_file() and _sha256(target_path.read_bytes()) == expected_hash:
            continue
        _replace_target(target_path, content)

    mismatched = [
        relative_path
        for relative_path, _content, expected_hash in entries
        if not (root / relative_path).is_file()
        or _sha256((root / relative_path).read_bytes()) != expected_hash
    ]
    if mismatched:
        raise DeliveryTransactionError(
            f"Delivery transaction verification failed for {len(mismatched)} target(s)"
        )
    journal_path.unlink()
    _fsync_directory(journal_path.parent)


def recover_delivery_state(*, root: Path = ROOT) -> bool:
    """Roll forward a previously committed delivery transaction, if present."""

    journal_path = root / DELIVERY_DIRECTORY / JOURNAL_NAME
    if not journal_path.exists():
        return False
    _roll_forward(
        root=root,
        journal_path=journal_path,
        journal=_read_journal(journal_path),
    )
    return True


def commit_delivery_state(
    queue: dict[str, Any],
    state: dict[str, Any],
    program: dict[str, Any],
    *,
    root: Path = ROOT,
) -> None:
    """Atomically commit one logical generation of the delivery YAML files."""

    validate_delivery_generation(queue, state, program)
    journal_path = root / DELIVERY_DIRECTORY / JOURNAL_NAME
    if journal_path.exists():
        raise DeliveryTransactionError(
            "An existing delivery transaction must be recovered before committing"
        )
    journal = _build_journal(queue, state, program)
    _publish_journal(journal_path, _serialize_journal(journal))
    _roll_forward(root=root, journal_path=journal_path, journal=journal)
