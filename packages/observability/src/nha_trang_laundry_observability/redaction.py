"""Recursive allow-safe serialization for logs and traces."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

REDACTED = "[REDACTED]"
_MAX_DEPTH = 8
_MAX_ITEMS = 50
_MAX_TEXT = 512
_SAFE_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_API_KEY = re.compile(r"(?i)\b(?:sk|api)[-_][A-Za-z0-9_-]{8,}")
_VIETNAMESE_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)[\s.-]?[0-9]{2,3}(?:[\s.-]?[0-9]){6,8}(?!\d)")

_SENSITIVE_FIELDS = {
    "address",
    "api_key",
    "authorization",
    "bearer",
    "chain_of_thought",
    "cookie",
    "cookies",
    "full_address",
    "password",
    "phone",
    "phone_number",
    "prompt",
    "prompt_body",
    "provider_payload",
    "raw_provider_payload",
    "reasoning",
    "reasoning_content",
    "refresh_token",
    "secret",
    "set_cookie",
    "tool_payload",
    "access_token",
}
_SENSITIVE_MARKERS = tuple(item.replace("_", "") for item in _SENSITIVE_FIELDS)


def _normalized_field(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _safe_field_name(value: object) -> tuple[str, bool]:
    raw = value if isinstance(value, str) else type(value).__name__
    normalized = _normalized_field(raw)
    compact = normalized.replace("_", "")
    sensitive = any(marker in compact for marker in _SENSITIVE_MARKERS)
    if sensitive and _SAFE_FIELD.fullmatch(raw):
        return raw, True
    if not _SAFE_FIELD.fullmatch(raw) or _contains_sensitive_text(raw):
        digest = sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"unsafe_field_{digest}", sensitive
    return raw, sensitive


def _contains_sensitive_text(value: str) -> bool:
    return bool(_BEARER.search(value) or _API_KEY.search(value) or _VIETNAMESE_PHONE.search(value))


def _safe_text(value: str) -> str:
    redacted = _BEARER.sub(REDACTED, value)
    redacted = _API_KEY.sub(REDACTED, redacted)
    redacted = _VIETNAMESE_PHONE.sub(REDACTED, redacted)
    if len(redacted) > _MAX_TEXT:
        return f"{redacted[:_MAX_TEXT]}[TRUNCATED]"
    return redacted


def sanitize(value: Any, *, _depth: int = 0) -> Any:
    """Return a bounded JSON-compatible value without using arbitrary object reprs."""
    if _depth >= _MAX_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, BaseException):
        return {"exception_type": type(value).__name__, "message": REDACTED}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["truncated_items"] = len(value) - _MAX_ITEMS
                break
            safe_key, sensitive = _safe_field_name(key)
            if safe_key in result:
                safe_key = f"{safe_key}_{index}"
            result[safe_key] = REDACTED if sensitive else sanitize(item, _depth=_depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        items = [sanitize(item, _depth=_depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"[TRUNCATED_{len(value) - _MAX_ITEMS}_ITEMS]")
        return items
    return {"object_type": type(value).__name__}
