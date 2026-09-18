"""`PROMO-WIRING-001`: the owner starts a promotion by publishing, not by shipping code.

The item's claim is that `CURRENT_PROMOTION` stops being where a programme lives. A module-level
Python constant is neither immutable-and-versioned (invariant 4) nor replaceable by the person whose
money it is: running a new programme was a deploy, and retiring the expired one was a deploy too.

So these tests are about the publication vehicle rather than about the discount arithmetic, which
`packages/domain/tests/test_promotion_wiring.py` holds down. What is asserted here is the part only
a database can show: a malformed document is refused while somebody can still fix it, an identical
document does not become a second version, a payload whose bytes no longer match its digest is not a
published programme, and -- the one the packet asks for by name -- publishing a *different* document
starts a new programme with no code change at all.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.configurations import (
    ConfigurationRepository,
    ConfigurationValidationError,
    snapshot_hash,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.promotions import (
    publish_promotion_policy,
    read_published_promotion_program,
    validate_promotion_policy_document,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    PromotionEligibilityEvent,
    PromotionResolution,
)
from nha_trang_laundry_domain.promotion_policy import PROMOTION_POLICY_CONFIG_TYPE

ROOT = Path(__file__).resolve().parents[3]
DOCUMENT: dict[str, Any] = json.loads(
    (ROOT / "templates" / "promotion-policy-dec-002.json").read_text(encoding="utf-8")
)
NOW = datetime.now(UTC).replace(microsecond=0)
STANDARD = "STANDARD_WASH_DRY"


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _actor(connection: Any) -> UUID:
    """One staff user who may publish. Attribution is not optional on a configuration version."""

    store_id, actor_id, assigner = uuid4(), uuid4(), uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (actor_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Chủ tiệm', 'ACTIVE', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (identifier, f"oidc-{identifier}", NOW),
            )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            ON CONFLICT DO NOTHING
            """,
            (actor_id, store_id, assigner, NOW),
        )
    return actor_id


def _document(**overrides: Any) -> dict[str, Any]:
    payload = deepcopy(DOCUMENT)
    payload.update(overrides)
    return payload


# --- fail closed, then publish ------------------------------------------------------------------


def test_with_nothing_published_there_is_no_programme_and_none_is_invented(
    connection: psycopg.Connection[Any],
) -> None:
    """Invariant 11, and the whole distance from `CURRENT_PROMOTION`.

    `None` is a legitimate answer, not an error and not a cue to fall back on the constant still
    sitting in `promotion.py`. A fresh deployment runs no promotion, and every quote it composes
    says `PROMOTION_NOT_PUBLISHED` rather than quietly applying a programme nobody ratified.
    """

    with connection.cursor() as cursor:
        assert read_published_promotion_program(cursor) is None


def test_the_shipped_programme_publishes_once_and_reads_back_with_its_provenance(
    connection: psycopg.Connection[Any],
) -> None:
    """The owner's one confirmed programme, through the real publication path.

    Publishing the identical document a second time is a no-op rather than version 2. That is not
    tidiness: `accept_quote_revision` compares programme *identity* before it compares dong, so a
    duplicate version would make every outstanding quote refuse its own acceptance with
    `PROMOTION_CHANGED_SINCE_QUOTE` for no reason anybody could explain to a customer.
    """

    actor_id = _actor(connection)
    digest, created = publish_promotion_policy(connection, actor_id=actor_id, payload=DOCUMENT)
    assert created is True
    assert digest == snapshot_hash(DOCUMENT)

    again_digest, again_created = publish_promotion_policy(
        connection, actor_id=actor_id, payload=DOCUMENT
    )
    assert (again_digest, again_created) == (digest, False)

    with connection.cursor() as cursor:
        published = read_published_promotion_program(cursor)
    assert published is not None
    assert published.version == 1
    # The prefix every hash on an immutable quote revision carries; the column stores the bare
    # digest, exactly as it does for the pricebook.
    assert published.snapshot_hash == f"JCS-SHA256-V1:{digest}"

    policy = published.program.policy
    assert policy.code == "PROMO_WET30_DRY40_20260717_20260831"
    assert policy.eligibility_event is PromotionEligibilityEvent.STORE_COMMERCIAL_ACCEPTED
    assert policy.stacking_allowed is False
    # 17/07/2026 to 31/08/2026 inclusive, in the shop's own timezone. The end is exclusive, so the
    # last day the programme covered is the day before it.
    assert policy.start_at.isoformat() == "2026-07-17T00:00:00+07:00"
    assert policy.end_at_exclusive.isoformat() == "2026-09-01T00:00:00+07:00"

    wash = published.program.rule_for(STANDARD)
    assert (wash.resolution, wash.rate_bps) == (PromotionResolution.AUTO_IF_TARGETED, 3_000)
    dry = published.program.rule_for("DC_AO_DAI_TRADITIONAL")
    assert (dry.resolution, dry.rate_bps) == (PromotionResolution.AUTO_IF_TARGETED, 4_000)
    # Silence is not a discount: a service the document never names is outside the programme.
    unnamed = published.program.rule_for("STANDARD_WASH_DRY_NOT_A_SERVICE")
    assert (unnamed.resolution, unnamed.rate_bps) == (PromotionResolution.NOT_ELIGIBLE, None)


def test_the_owner_starts_a_new_programme_by_publishing_and_the_old_one_stops(
    connection: psycopg.Connection[Any],
) -> None:
    """The item's "done when", stated as a database transaction rather than as a deploy.

    A *different* document is a new version, and the one in force is the newest published one. The
    second programme here moves the window to September and halves the wash rate to 15%, and both
    changes reach the reader with no code change, no restart and no engineer.
    """

    actor_id = _actor(connection)
    publish_promotion_policy(connection, actor_id=actor_id, payload=DOCUMENT)

    september = _document(
        start_at="2026-09-01T00:00:00+07:00",
        end_at_exclusive="2026-10-01T00:00:00+07:00",
        services=[
            {**rule, "rate_bps": 1_500} if rule["service_code"] == STANDARD else rule
            for rule in DOCUMENT["services"]
        ],
    )
    digest, created = publish_promotion_policy(connection, actor_id=actor_id, payload=september)
    assert created is True

    with connection.cursor() as cursor:
        published = read_published_promotion_program(cursor)
    assert published is not None
    assert published.version == 2
    assert published.snapshot_hash == f"JCS-SHA256-V1:{digest}"
    assert published.program.policy.start_at.isoformat() == "2026-09-01T00:00:00+07:00"
    assert published.program.rule_for(STANDARD).rate_bps == 1_500
    # And the first version is still on the record, unedited. Invariant 4: a published version is
    # immutable, so a quote priced under version 1 can still be read back years later.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT version, lifecycle FROM configuration_versions
            WHERE config_type = %s ORDER BY version
            """,
            (PROMOTION_POLICY_CONFIG_TYPE,),
        )
        assert [(int(row[0]), str(row[1])) for row in cursor.fetchall()] == [
            (1, "PUBLISHED"),
            (2, "PUBLISHED"),
        ]


# --- refused at publication, not at the counter --------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # No event means the engine can never resolve eligibility, which is exactly the defect
        # `CURRENT_PROMOTION` carried for weeks under a comment claiming DEC-002 was still open.
        ("eligibility_event", None),
        # An interval that ends before it starts is not a window.
        ("end_at_exclusive", "2026-07-01T00:00:00+07:00"),
        # A boundary with no offset is a midnight in no particular place.
        ("start_at", "2026-07-17T00:00:00"),
        # The engine accepts no other zone, so refusing here names the field instead.
        ("timezone", "Asia/Bangkok"),
        # A programme whose name cannot be written onto the money line it produces.
        ("code", "promo wet30"),
        # Stacking is a decision, and an absent decision is not a `False`.
        ("stacking_allowed", "no"),
        # A document that targets nothing is not a programme.
        ("services", []),
        # A payload that is some other kind of document entirely.
        ("schema", "remedy-policy-v1"),
    ],
)
def test_a_document_that_could_not_price_is_refused_at_publication(
    connection: psycopg.Connection[Any], field: str, value: Any
) -> None:
    """Every one of these would otherwise surface with a customer standing at the counter.

    The registered validator runs the real parse rather than a shape check, so a document that
    satisfies a schema and then fails to produce a rate is refused while somebody can still fix it.
    """

    actor_id = _actor(connection)
    payload = _document(**{field: value})
    with pytest.raises(ConfigurationValidationError):
        validate_promotion_policy_document(payload)
    with pytest.raises(ConfigurationValidationError):
        publish_promotion_policy(connection, actor_id=actor_id, payload=payload)
    with connection.cursor() as cursor:
        assert read_published_promotion_program(cursor) is None


def test_a_rate_of_true_is_not_a_discount_of_one_basis_point(
    connection: psycopg.Connection[Any],
) -> None:
    """`True` is an `int` in Python, so `"rate_bps": true` would publish a 0,01% discount nobody
    decided. It is money, so it is refused rather than coerced."""

    actor_id = _actor(connection)
    payload = _document(
        services=[
            {**rule, "rate_bps": True} if rule["service_code"] == STANDARD else rule
            for rule in DOCUMENT["services"]
        ]
    )
    with pytest.raises(ConfigurationValidationError):
        publish_promotion_policy(connection, actor_id=actor_id, payload=payload)


def test_a_published_programme_cannot_be_edited_in_place_at_any_layer(
    connection: psycopg.Connection[Any],
) -> None:
    """Invariant 4, checked from the two places it can be broken.

    The first line of defence is the database: `reject_published_configuration_mutation` refuses an
    UPDATE against a published row outright, so a rate cannot be rewritten under its own digest
    even by someone holding a psql prompt. That is why this test asserts a raised exception rather
    than a changed rate -- the edit never lands.

    The second is `read_published_promotion_program`, which re-hashes the stored payload against the
    digest recorded at publication before it parses it. It is not redundant: it is what would catch
    a payload restored from a backup, moved between deployments, or written by a future path that
    does not go through the trigger, and it fails towards list price rather than towards a discount.
    """

    actor_id = _actor(connection)
    publish_promotion_policy(connection, actor_id=actor_id, payload=DOCUMENT)
    with connection.cursor() as cursor:
        published = ConfigurationRepository.latest_published(cursor, PROMOTION_POLICY_CONFIG_TYPE)
    assert published is not None
    tripled = _document(
        services=[
            {**rule, "rate_bps": 9_000} if rule["service_code"] == STANDARD else rule
            for rule in DOCUMENT["services"]
        ]
    )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE configuration_versions SET payload = %s WHERE id = %s",
            (json.dumps(tripled), published.version_id),
        )

    # And the programme in force is still the one that was published, at 30% rather than 90%.
    with connection.cursor() as cursor:
        intact = read_published_promotion_program(cursor)
    assert intact is not None
    assert intact.version == 1
    assert intact.program.rule_for(STANDARD).rate_bps == 3_000
    assert intact.snapshot_hash == f"JCS-SHA256-V1:{snapshot_hash(DOCUMENT)}"
