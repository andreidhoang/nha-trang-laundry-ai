"""Money primitives: whole Vietnamese đồng only, never floating point."""

from __future__ import annotations


def require_non_negative_vnd(amount_vnd: int) -> int:
    """Validate a whole non-negative VND amount without accepting bool or float values."""
    if isinstance(amount_vnd, bool) or not isinstance(amount_vnd, int):
        raise TypeError("VND amount must be an integer, not bool or floating point")
    if amount_vnd < 0:
        raise ValueError("VND amount must not be negative")
    return amount_vnd
