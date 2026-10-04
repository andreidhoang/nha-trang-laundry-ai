"""The figures that count waiting days in Python carry the clock's rule in their version.

Round 9 review, round 2, P2. `waiting_clock` (a hold takes out the time it lasted, an exception of
finished laundry pauses the shelf clock) and `order_storage_fee` live in the domain; the count, the
exports' `owed_vnd` and the evening summary read them. Each version hashed its SQL and constants
and not that rule, so the days and the fee changed under labels that stayed as they were. A label
now names the clock rule too: editing it moves all four, and these tests are the proof that the
rule is among the hashed parts rather than a copy of today's digest.
"""

from __future__ import annotations

import nha_trang_laundry_domain.unclaimed as clock_rules
import pytest
from nha_trang_laundry_db import exports, unclaimed
from nha_trang_laundry_db.daily_summary import daily_summary_template_version
from nha_trang_laundry_db.query_version import query_version, rule_source


def test_every_figure_that_counts_waiting_days_names_the_clock_rule() -> None:
    rule = rule_source(clock_rules)
    # The rule is the structure of the domain module: a comment or docstring is not a change.
    assert rule and "waiting_clock" in rule and "order_storage_fee" in rule

    for version, before_rule in (
        (unclaimed.WAITING_COUNT_QUERY, unclaimed._WAITING_COUNT_SQL),
        (exports.EXPORT_QUERY, exports._EXPORT_SQL),
        (exports.EXPORT_WINDOW_QUERY, exports._EXPORT_WINDOW_SQL),
    ):
        assert rule in _hashed_parts(version.identifier)
        # Without the rule the digest is the one these labels carried before this fix.
        assert before_rule


def _hashed_parts(identifier: str) -> list[str]:
    """The rule parts a version was built from, found by rebuilding it with and without the rule."""

    rule = rule_source(clock_rules)
    built = {
        "awaiting-pickup-count-v2": unclaimed.WAITING_COUNT_QUERY,
        "store-day-orders-export-v5": exports.EXPORT_QUERY,
        "store-window-orders-export-v4": exports.EXPORT_WINDOW_QUERY,
    }[identifier]
    parts = _PARTS[identifier]()
    assert query_version(identifier, *parts, rule).digest == built.digest
    assert query_version(identifier, *parts).digest != built.digest
    return [*parts, rule]


_PARTS = {
    "awaiting-pickup-count-v2": lambda: [
        unclaimed._WAITING_COUNT_SQL,
        ",".join(str(days) for days in unclaimed.WAITING_SUMMARY_THRESHOLDS),
        str(unclaimed.WAITING_COUNT_READ_LIMIT),
    ],
    "store-day-orders-export-v5": lambda: [
        exports._EXPORT_SQL,
        exports.BUSINESS_TIMEZONE,
        exports.EXPORT_DAY_BOUNDARY,
        ",".join(exports.EXPORT_COLUMNS),
        ",".join(exports.EXPORT_EXCLUSIONS),
        exports._money_sources_text(exports.EXPORT_MONEY_SOURCES),
    ],
    "store-window-orders-export-v4": lambda: [
        exports._EXPORT_WINDOW_SQL,
        exports.BUSINESS_TIMEZONE,
        exports.EXPORT_DAY_BOUNDARY,
        ",".join(exports.EXPORT_COLUMNS),
        ",".join(exports.EXPORT_EXCLUSIONS),
        exports._money_sources_text(exports.EXPORT_MONEY_SOURCES),
    ],
}


def test_the_count_is_pinned() -> None:
    assert unclaimed.WAITING_COUNT_QUERY.label == "awaiting-pickup-count-v2:ca8759495a5ee8b1"


def test_the_evening_summary_names_the_clock_rule_it_counts_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = rule_source
    seen: list[object] = []

    def spy(subject: object) -> str:
        seen.append(subject)
        return original(subject)

    monkeypatch.setattr("nha_trang_laundry_db.daily_summary.rule_source", spy)
    daily_summary_template_version()
    assert clock_rules in seen
