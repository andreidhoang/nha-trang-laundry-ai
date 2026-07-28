"""Deterministic opt-out classification and fail-closed marketing policy."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OptOutDisposition(StrEnum):
    NONE = "NONE"
    WITHDRAW = "WITHDRAW"
    PENDING_REVIEW_BLOCKED = "PENDING_REVIEW_BLOCKED"
    POLICY_UNAVAILABLE_BLOCKED = "POLICY_UNAVAILABLE_BLOCKED"


class SuppressionState(StrEnum):
    CLEAR = "CLEAR"
    SUPPRESSED = "SUPPRESSED"
    PENDING_REVIEW_BLOCKED = "PENDING_REVIEW_BLOCKED"
    UNKNOWN_BLOCKED = "UNKNOWN_BLOCKED"


class ConsentPolicyError(ValueError):
    """Raised when a published opt-out registry is malformed."""


@dataclass(frozen=True)
class OptOutRegistry:
    version: str
    exact_commands: tuple[str, ...]
    ambiguous_patterns: tuple[str, ...]
    published_at: datetime
    expires_at: datetime


def evaluate_opt_out(
    message: str,
    *,
    registry: OptOutRegistry | None,
    now: datetime,
) -> OptOutDisposition:
    """Classify before model dispatch; missing/stale policy blocks marketing."""
    if registry is None or not _registry_is_current(registry, now):
        return OptOutDisposition.POLICY_UNAVAILABLE_BLOCKED
    normalized_message = normalize_provider_command(message)
    exact = frozenset(normalize_provider_command(command) for command in registry.exact_commands)
    if normalized_message in exact:
        return OptOutDisposition.WITHDRAW
    try:
        if any(
            re.fullmatch(pattern, normalized_message) for pattern in registry.ambiguous_patterns
        ):
            return OptOutDisposition.PENDING_REVIEW_BLOCKED
    except re.error as error:
        raise ConsentPolicyError("published ambiguous opt-out pattern is invalid") from error
    return OptOutDisposition.NONE


def normalize_provider_command(value: str) -> str:
    """Normalize Unicode, case, whitespace, and a provider command prefix deterministically."""
    normalized = unicodedata.normalize("NFC", value).casefold().strip()
    if normalized.startswith("/"):
        normalized = normalized[1:].lstrip()
    return " ".join(normalized.split())


def marketing_send_allowed(*, consent_active: bool, suppression: SuppressionState) -> bool:
    """Unknown, pending, and suppressed states all fail closed."""
    return consent_active and suppression is SuppressionState.CLEAR


def _registry_is_current(registry: OptOutRegistry, now: datetime) -> bool:
    if (
        not registry.version.strip()
        or not registry.exact_commands
        or registry.published_at.tzinfo is None
        or registry.expires_at.tzinfo is None
        or now.tzinfo is None
    ):
        return False
    return registry.published_at <= now < registry.expires_at
