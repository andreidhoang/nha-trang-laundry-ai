"""Going back to an earlier configuration puts it back in force. Invariants 4 and 11.

Reviewer repro, 2026-09: the owner publishes remedy policy v1, raises the staff approval ceiling for
a week as v2, then republishes the v1 document to end it. `publish_remedy_policy` answered "already
published" -- because *some* version with that digest was PUBLISHED -- and did nothing, while
`latest_published` kept serving v2 as the policy in force. The pricebook and the promotion programme
shared the same check and the same defect: an owner could not revert a price change or restore an
earlier programme at all, and the script told them they had.

The decided repair keeps every published version exactly as it is (invariant 4) and makes a revert
what it is: a new version, next number, same content, with its own event and audit trail. So the
history reads v1 -> v2 -> v3(=v1), and "which version was in force when this quote was priced"
still has one answer per version. Publishing the document that is *already in force* remains a
no-op, for the reason the originals give: a duplicate version would make every outstanding quote
look out of date for no reason.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.configurations import ConfigurationRepository, snapshot_hash
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.promotions import (
    publish_promotion_policy,
    read_published_promotion_program,
)
from nha_trang_laundry_db.remedies import publish_remedy_policy, read_published_remedy_policy
from nha_trang_laundry_domain.promotion_policy import PROMOTION_POLICY_CONFIG_TYPE

ROOT = Path(__file__).resolve().parents[3]
PRICEBOOK_SOURCE = (ROOT / "templates" / "services-pricebook.csv").read_text(encoding="utf-8")
PROMOTION: dict[str, Any] = json.loads(
    (ROOT / "templates" / "promotion-policy-dec-002.json").read_text(encoding="utf-8")
)
REMEDY: dict[str, Any] = json.loads(
    (ROOT / "templates" / "remedy-policy-dec-004.json").read_text(encoding="utf-8")
)
NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _actor(connection: Any) -> UUID:
    actor_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Chủ tiệm', 'ACTIVE', %s)
            """,
            (actor_id, f"oidc-{actor_id}", NOW),
        )
    return actor_id


def _history(connection: Any, config_type: str) -> list[tuple[int, str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT version, lifecycle, snapshot_hash FROM configuration_versions
            WHERE config_type = %s ORDER BY version
            """,
            (config_type,),
        )
        return [(int(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()]


def _published_events(connection: Any, config_type: str) -> list[tuple[int, str]]:
    """The CONFIGURATION_PUBLISHED events, oldest first, with the audit row each must have."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT (e.payload->>'version')::int, e.payload->>'snapshot_hash'
            FROM domain_events e
            JOIN configuration_versions v ON v.id = e.aggregate_id
            WHERE e.event_type = 'CONFIGURATION_PUBLISHED' AND v.config_type = %s
              AND EXISTS (
                  SELECT 1 FROM audit_events a
                  WHERE a.aggregate_id = e.aggregate_id AND a.action = 'CONFIGURATION_PUBLISH'
              )
              AND EXISTS (
                  SELECT 1 FROM outbox_events o
                  WHERE o.aggregate_id = e.aggregate_id
                    AND o.event_type = 'configuration.published.v1'
              )
            ORDER BY e.occurred_at, (e.payload->>'version')::int
            """,
            (config_type,),
        )
        return [(int(row[0]), str(row[1])) for row in cursor.fetchall()]


def _in_force(connection: Any, config_type: str) -> tuple[int, str]:
    with connection.cursor() as cursor:
        published = ConfigurationRepository.latest_published(cursor, config_type)
    assert published is not None
    return published.version, published.snapshot_hash


def _assert_revert_is_a_new_version(
    connection: Any,
    config_type: str,
    publish: Callable[[Any], tuple[str, bool]],
    first: Any,
    second: Any,
) -> None:
    """The shared claim, run against each of the three publication paths."""
    v1_digest, created = publish(first)
    assert created
    v2_digest, created = publish(second)
    assert created and v2_digest != v1_digest
    assert _in_force(connection, config_type) == (2, v2_digest)

    # The revert. Before the fix this returned (v1_digest, False) and v2 stayed in force.
    reverted_digest, created = publish(first)
    assert (reverted_digest, created) == (v1_digest, True)
    assert _in_force(connection, config_type) == (3, v1_digest)

    # Invariant 4: nothing was un-published or edited; the revert is its own row with v1's content.
    assert _history(connection, config_type) == [
        (1, "PUBLISHED", v1_digest),
        (2, "PUBLISHED", v2_digest),
        (3, "PUBLISHED", v1_digest),
    ]
    assert _published_events(connection, config_type) == [
        (1, v1_digest),
        (2, v2_digest),
        (3, v1_digest),
    ]

    # Positive control: the version now in force, published again, is still a no-op.
    assert publish(first) == (v1_digest, False)
    assert len(_history(connection, config_type)) == 3


def test_reverting_the_remedy_policy_puts_the_earlier_figures_back_in_force(
    connection: psycopg.Connection[Any],
) -> None:
    """The reviewer's own case: a week-long raise of the staff ceiling, then the original back."""
    actor_id = _actor(connection)
    raised = dict(REMEDY, staff_approval_ceiling_vnd=500_000)
    assert raised["staff_approval_ceiling_vnd"] != REMEDY["staff_approval_ceiling_vnd"]

    _assert_revert_is_a_new_version(
        connection,
        "REMEDY_POLICY",
        lambda payload: publish_remedy_policy(connection, actor_id=actor_id, payload=payload),
        REMEDY,
        raised,
    )
    with connection.cursor() as cursor:
        policy = read_published_remedy_policy(cursor)
    assert policy is not None
    assert policy.version == 3
    assert policy.policy.staff_approval_ceiling_vnd == REMEDY["staff_approval_ceiling_vnd"]


def test_reverting_the_promotion_programme_puts_the_earlier_programme_back_in_force(
    connection: psycopg.Connection[Any],
) -> None:
    actor_id = _actor(connection)
    changed = deepcopy(PROMOTION)
    changed["services"] = [
        {**rule, "rate_bps": 1_500} if rule["service_code"] == "STANDARD_WASH_DRY" else rule
        for rule in PROMOTION["services"]
    ]
    assert changed != PROMOTION

    _assert_revert_is_a_new_version(
        connection,
        PROMOTION_POLICY_CONFIG_TYPE,
        lambda payload: publish_promotion_policy(connection, actor_id=actor_id, payload=payload),
        PROMOTION,
        changed,
    )
    with connection.cursor() as cursor:
        programme = read_published_promotion_program(cursor)
    assert programme is not None
    assert programme.version == 3
    assert programme.snapshot_hash == f"JCS-SHA256-V1:{snapshot_hash(PROMOTION)}"
    assert programme.program.rule_for("STANDARD_WASH_DRY").rate_bps == 3_000


def test_reverting_the_pricebook_puts_the_earlier_prices_back_in_force(
    connection: psycopg.Connection[Any],
) -> None:
    actor_id = _actor(connection)
    row = 'DC_AO_DAI_PLAIN,dry_cleaning,"Áo dài trơn",cái,65000,65000,'
    raised = PRICEBOOK_SOURCE.replace(row, row.replace("65000,65000", "70000,70000"))
    assert raised != PRICEBOOK_SOURCE, "the pricebook row this test edits has changed shape"

    _assert_revert_is_a_new_version(
        connection,
        "PRICEBOOK",
        lambda source: publish_pricebook(connection, actor_id=actor_id, source=source),
        PRICEBOOK_SOURCE.encode("utf-8"),
        raised.encode("utf-8"),
    )


def test_publishing_the_in_force_document_twice_is_still_one_version(
    connection: psycopg.Connection[Any],
) -> None:
    """The idempotency the originals were built for survives, for all three config types."""
    actor_id = _actor(connection)

    def remedy() -> tuple[str, bool]:
        return publish_remedy_policy(connection, actor_id=actor_id, payload=REMEDY)

    def promotion() -> tuple[str, bool]:
        return publish_promotion_policy(connection, actor_id=actor_id, payload=PROMOTION)

    def pricebook() -> tuple[str, bool]:
        return publish_pricebook(
            connection, actor_id=actor_id, source=PRICEBOOK_SOURCE.encode("utf-8")
        )

    publications: tuple[tuple[str, Callable[[], tuple[str, bool]]], ...] = (
        ("REMEDY_POLICY", remedy),
        (PROMOTION_POLICY_CONFIG_TYPE, promotion),
        ("PRICEBOOK", pricebook),
    )
    for config_type, publish in publications:
        digest, created = publish()
        assert created
        assert publish() == (digest, False)
        assert publish() == (digest, False)
        assert [version for version, _, _ in _history(connection, config_type)] == [1]
