"""`OPS-BOARD-001`: the board a person works from, proved to be the board the assistant counts.

The SLA engine, the board query and the day counts all existed before this item. What this module
holds are the properties that had no test because nothing had ever surfaced them:

* every figure's query version is pinned, so changing the rule fails here until somebody publishes
  a new identifier;
* the board pages by keyset without repeating or losing an order;
* an order that crosses its mark between two reads reads as breached on the second, and nothing was
  written to make that happen;
* a year of orders stays inside a stated bound, measured against a populated table rather than
  asserted, with the index the planner actually chose read out of `EXPLAIN`.

The "one query, not two" property is proved where it can be proved end to end — against the real
API and the real assistant, in `apps/api/tests/test_ops_board_postgres.py`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.assistant import TODAY_STATUS_COUNTS_QUERY, today_status_counts
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.query_version import query_version, rule_source
from nha_trang_laundry_db.settlement import COLLECTED_TODAY_QUERY
from nha_trang_laundry_db.shadow_console import (
    SLA_BOARD_MAX_LIMIT,
    ShadowConsoleRepository,
    ShadowStateError,
    sla_board_query_version,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain import sla as sla_engine
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    CommitmentAuthority,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from nha_trang_laundry_domain.sla import (
    OTHER_SPECIAL_ITEM_SLA,
    SPECIAL_ITEM_GUIDANCE_SLA,
    STANDARD_WASH_SLA,
    ProductionSlaPolicy,
    SlaPolicyType,
    evaluate_production_sla,
)
from quote_test_data import accepted_quote

NOW = datetime(2026, 9, 18, 3, tzinfo=UTC)
#: Every intake blocker cleared. Intake acceptance is gated on six separate facts; this module is
#: about the board, so they are satisfied rather than exercised.
READY = IntakeReadiness(True, True, True, True, True, True)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, *, roles: frozenset[StaffRole]) -> StaffPrincipal:
    staff_user_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên thử nghiệm', 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", NOW),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_user_id, role.value, NOW),
            )
    return StaffPrincipal(
        staff_user_id=staff_user_id,
        oidc_subject=f"oidc-{staff_user_id}",
        roles=roles,
        mfa_verified=True,
        session_id=uuid4(),
    )


def _member(
    connection: Any, store_id: UUID, *, roles: frozenset[StaffRole] | None = None
) -> StaffPrincipal:
    """A staff member of a real store, granted by a real owner. `STORE-REGISTRY-001`."""
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=owner.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    principal = _staff(connection, roles=roles or frozenset({StaffRole.OPERATOR}))
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=principal.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    return principal


def _advance(
    connection: Any, order_id: UUID, staff: StaffPrincipal, version: int, **target: Any
) -> Any:
    occurred_at = target.pop("occurred_at", NOW)
    return OrderRepository().transition(
        connection,
        OrderTransitionCommand(
            order_id,
            version,
            staff,
            f"step-{uuid4().hex}",
            uuid4(),
            occurred_at=occurred_at,
            **target,
        ),
    )


def _order_in_production(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    *,
    accepted_at: datetime,
    ready_at: datetime | None = None,
) -> UUID:
    """One order the board will select, through the transitions production really makes.

    Only two steps are needed to reach the board's population — an order is on it from the moment
    intake accepts it. The rest of the chain runs only when the test asked for a `ready_at`, because
    reaching `READY_AT_STORE` requires the commercial order to be ACTIVE first, and a fixture that
    stamped the column directly would be testing a state no shop can produce.
    """
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff
    )
    stored = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            staff,
            f"order-{uuid4().hex}",
            uuid4(),
            accepted_at,
            AcquisitionSource.WALK_IN,
        ),
    )
    order_id = stored.order_id
    version = stored.row_version
    steps: list[dict[str, Any]] = [
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION, "occurred_at": accepted_at},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": accepted_at,
            "intake_readiness": READY,
            "occurred_at": accepted_at,
        },
    ]
    if ready_at is not None:
        steps += [
            {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
            {"commercial_target": CommercialOrderStatus.CONFIRMED},
            {"commercial_target": CommercialOrderStatus.ACTIVE},
            {"production_target": ProductionStatus.QUEUED},
            {"production_target": ProductionStatus.IN_PROCESS},
            {"production_target": ProductionStatus.QUALITY_CHECK},
            {"production_target": ProductionStatus.READY_AT_STORE, "occurred_at": ready_at},
        ]
    for step in steps:
        stored_step = _advance(connection, order_id, staff, version, **step)
        version = stored_step.row_version
    return order_id


# --- the versions every figure carries ----------------------------------------------------------


def test_the_board_query_version_is_pinned_to_the_rule_it_names() -> None:
    """Invariant 18's teeth. Editing the board's rule fails this until a new version is published.

    Both halves are pinned on purpose. The identifier alone would let somebody change the query and
    leave `v1` printed beside numbers it no longer describes; the digest alone would leave a failure
    nobody could interpret. Together, a rule change produces a red test whose only honest fix is to
    read the diff and publish `v2`.

    The digest moved from `4740bd7e51cb2542` to `e56e50ea7beb4021` when `OPS-BOARD-FIX-001` widened
    what "the rule" means. The old value hashed `_SLA_BOARD_SQL` alone, so the SLA policy and the
    domain engine -- which decide where the clock stops, where the mark falls and which reason
    codes travel with a row -- could both change while `v1:4740bd…` stayed printed beside figures
    they had already moved. The identifier stays `v1`: no board figure changed on the day this
    pinned value did, and republishing as `v2` would say one had.
    """
    version = sla_board_query_version(STANDARD_WASH_SLA)
    assert version.identifier == "sla-risk-board-v1"
    assert version.digest == "e56e50ea7beb4021"
    assert version.label == "sla-risk-board-v1:e56e50ea7beb4021"


def test_the_board_version_moves_when_the_policy_behind_the_figure_moves() -> None:
    """A figure's version has to name the policy that produced it, not only the SQL that fetched it.

    `sla_risk_board` takes its policy as an argument because choosing one per order is an open
    business decision, so two pages of the same SQL can carry entirely different breaches. Three
    changes are asserted, each of which a caller could make while leaving the statement untouched:
    a different published policy, the same policy identifier with a different mark, and a different
    policy type. A version that survived any of them would be the bare `v1` this pin exists to stop.
    """
    standard = sla_board_query_version(STANDARD_WASH_SLA)
    # `SLA_STANDARD_CLOTHES` under a twelve-hour mark rather than eight: same name on the printout,
    # four more hours before an order reads BREACHED.
    widened = sla_board_query_version(
        ProductionSlaPolicy(
            policy_id=STANDARD_WASH_SLA.policy_id,
            policy_type=SlaPolicyType.COMMITMENT,
            target_min_hours=None,
            target_max_hours=12,
            commitment_authority=CommitmentAuthority.HUMAN_CONFIRM,
        )
    )

    assert sla_board_query_version(SPECIAL_ITEM_GUIDANCE_SLA).digest != standard.digest
    assert sla_board_query_version(OTHER_SPECIAL_ITEM_SLA).digest != standard.digest
    assert widened.digest != standard.digest
    # Still the same published identifier -- what moved is the digest under it, which is the half
    # that says "this is no longer the rule you read last time".
    assert widened.identifier == standard.identifier == "sla-risk-board-v1"


def test_the_board_version_moves_when_the_engine_behind_the_figure_moves() -> None:
    """The other half of the same property: the engine is a rule, and rules are versioned.

    `evaluate_production_sla` is where `0037`'s clock stop lives. Changing `comparison_at =
    ready_at_store or evaluated_at` back to `evaluated_at` would re-break every figure on the board
    and touch no SQL at all, so the engine's structure is hashed into the version alongside the
    statement. Comments and docstrings are excluded deliberately -- a version that moved when
    somebody improved an explanation would train people to bump the pinned digest unread, which is
    the habit `query_version` exists to break.
    """
    engine = rule_source(sla_engine)
    assert "ready_at_store" in engine, "the engine's rule text was not read; this pin is vacuous"

    # The clock-stop rule, removed from a copy of the engine's own structure the way `0037` found
    # it removed from the query. The digest under the same identifier must not survive it.
    without_the_clock_stop = engine.replace("'ready_at_store'", "'evaluated_at'")
    assert (
        query_version("probe-v1", engine).digest
        != query_version("probe-v1", without_the_clock_stop).digest
    )

    # Rewriting an explanation is not a rule change. `rule_source` strips docstrings before the
    # digest is taken, so improving one must not move a published version -- a bump nobody had to
    # think about is exactly the habit these identifiers exist to break.
    assert rule_source(_documented_probe()) == rule_source(_reworded_probe())
    assert rule_source(_documented_probe()) != rule_source(_changed_probe())


# Three copies of one rule, each wrapped so that all three inner functions carry the same name --
# the name is part of the structure, and two probes called different things would differ for a
# reason that has nothing to do with the property being asserted.


def _documented_probe() -> Any:
    def rule(hours: int) -> int:
        """A rule, with one explanation of it."""
        return hours * 3_600

    return rule


def _reworded_probe() -> Any:
    def rule(hours: int) -> int:
        """The same rule, explained completely differently.

        Several more paragraphs of it, in fact, and a blank line after them.
        """

        return hours * 3_600

    return rule


def _changed_probe() -> Any:
    def rule(hours: int) -> int:
        """A rule, with one explanation of it."""
        return hours * 7_200

    return rule


def test_the_day_count_and_takings_versions_are_pinned_too() -> None:
    """The other two figures the day surfaces publish, held to the same rule."""
    assert TODAY_STATUS_COUNTS_QUERY.label == "today-status-counts-v1:cd422085b053d921"
    # v1 (`391bd9369153e5ae`) summed settlements alone, so a paid order cancelled with the cash
    # handed back under DEC-024 left the card above the drawer. v2 keeps that gross sum as
    # `collected_vnd` and adds today's `order_refunds` and the drawer's net movement as a
    # non-negative magnitude plus IN/OUT, each leg on its own business day, with the day a
    # parameter instead of `now()` -- a changed rule, so a new identifier, not a bumped digest.
    # v3 (`PAYMENT-001`, `DEC-035`): money in is the payment ledger, split by method, so a deposit
    # counts on the day it was taken; `0056` backfilled every settlement as its one payment, so no
    # past day moves. v2 was `c266d2f11377a64c`.
    assert COLLECTED_TODAY_QUERY.label == "collected-today-v3:3e7eb2f9fcaade13"


def test_a_changed_rule_produces_a_different_digest_under_the_same_identifier() -> None:
    """The mechanism the three pins above depend on, asserted rather than assumed.

    Reformatting is not a rule change: a version that moved every time somebody re-indented a SQL
    string would teach people to bump the pinned value without reading it, which is the habit these
    versions exist to prevent. Changing a predicate is a rule change and must move the digest.
    """
    original = query_version("probe-v1", "SELECT id FROM orders WHERE store_id = %s")
    reindented = query_version(
        "probe-v1",
        """
            SELECT id   FROM orders
            WHERE  store_id = %s
        """,
    )
    narrowed = query_version(
        "probe-v1", "SELECT id FROM orders WHERE store_id = %s AND closed_at IS NULL"
    )

    assert reindented.digest == original.digest
    assert narrowed.digest != original.digest


# --- the board itself ---------------------------------------------------------------------------


def test_the_board_reports_exactly_what_the_domain_engine_computed(
    connection: psycopg.Connection[Any],
) -> None:
    """Delegation, against real PostgreSQL rather than a stub cursor.

    Every field the board added for the screen is compared to the engine's own answer for the same
    order at the same instant. If a future change starts deriving any of them in SQL, the two stop
    agreeing here.
    """
    store_id = uuid4()
    principal = _member(connection, store_id)
    accepted_at = NOW - timedelta(hours=6)
    order_id = _order_in_production(connection, store_id, principal, accepted_at=accepted_at)

    board = ShadowConsoleRepository().sla_risk_board(
        connection, store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA, now=NOW
    )

    expected = evaluate_production_sla(
        STANDARD_WASH_SLA, evaluated_at=NOW, production_accepted_at=accepted_at
    )
    assert len(board) == 1
    row = board[0]
    assert row.order_id == order_id
    assert row.internal_risk_due_at == expected.internal_risk_due_at
    assert row.sla_outcome == expected.outcome.value
    assert row.overall_outcome == expected.overall_outcome.value
    assert row.reason_codes == tuple(code.value for code in expected.reason_codes)
    assert row.elapsed_microseconds == expected.trace.actual_elapsed_microseconds
    assert row.breach_microseconds == expected.trace.breach_microseconds
    assert row.evaluated_at == NOW
    # Six hours in on an eight-hour mark: two hours left, no breach, and both figures non-negative.
    assert row.remaining_microseconds == 2 * 3_600 * 1_000_000
    assert row.breach_microseconds == 0


def test_a_finished_order_keeps_the_time_it_finished_with_rather_than_counting_down(
    connection: psycopg.Connection[Any],
) -> None:
    """`0037`'s fix, seen from the new field. A stopped clock must stay stopped.

    The order was accepted six hours before the evaluation instant and reported finished two hours
    after acceptance, so six hours of the eight-hour mark are left and none of them elapse while the
    bag waits on the shelf for its owner. Deriving "remaining" from the due timestamp and `now`
    would have said two hours, and an overnight wait would eventually have said `SLA_BREACHED` --
    which is the exact bug migration `0037` was written for.
    """
    store_id = uuid4()
    principal = _member(connection, store_id)
    accepted_at = NOW - timedelta(hours=6)
    _order_in_production(
        connection,
        store_id,
        principal,
        accepted_at=accepted_at,
        ready_at=accepted_at + timedelta(hours=2),
    )

    board = ShadowConsoleRepository().sla_risk_board(
        connection, store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA, now=NOW
    )

    assert len(board) == 1
    assert board[0].sla_outcome == "MET"
    assert board[0].remaining_microseconds == 6 * 3_600 * 1_000_000
    assert board[0].breach_microseconds == 0


def test_an_order_crossing_its_mark_between_two_reads_shows_breached_on_the_second(
    connection: psycopg.Connection[Any],
) -> None:
    """The SLA flag is computed on read, so crossing the mark needs no writer — and must have none.

    An order accepted just inside the eight-hour mark reads PENDING, and the same row read a minute
    later on the other side of it reads BREACHED. The order's `row_version` is captured before and
    after: nothing about the order changed, which is what makes this a read model rather than a
    state machine somebody has to remember to advance.
    """
    store_id = uuid4()
    principal = _member(connection, store_id)
    repository = ShadowConsoleRepository()
    accepted_at = NOW - timedelta(hours=8) + timedelta(minutes=1)
    order_id = _order_in_production(connection, store_id, principal, accepted_at=accepted_at)

    def row_version() -> int:
        with connection.cursor() as cursor:
            cursor.execute("SELECT row_version FROM orders WHERE id = %s", (order_id,))
            found = cursor.fetchone()
            assert found is not None
            return int(found[0])

    before_version = row_version()
    first = repository.sla_risk_board(
        connection, store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA, now=NOW
    )
    second = repository.sla_risk_board(
        connection,
        store_id=store_id,
        principal=principal,
        policy=STANDARD_WASH_SLA,
        now=NOW + timedelta(minutes=2),
    )

    assert first[0].sla_outcome == "PENDING"
    assert "SLA_BREACHED" not in first[0].reason_codes
    assert second[0].sla_outcome == "BREACHED"
    assert "SLA_BREACHED" in second[0].reason_codes
    assert second[0].breach_microseconds == 60 * 1_000_000
    # The complement is zero rather than negative: the magnitude never carries the direction.
    assert second[0].remaining_microseconds == 0
    assert row_version() == before_version


def test_the_board_orders_by_acceptance_and_says_so_rather_than_claiming_urgency(
    connection: psycopg.Connection[Any],
) -> None:
    """The order the screen renders, and the exact population in which it is not urgency order.

    The first version of this test seeded three orders whose clocks were all still running, and on
    that population oldest-accepted-first and least-time-remaining-first are the same list. It
    therefore asserted a coincidence: it excluded `READY_AT_STORE`, which is the one state
    migration `0037` exists for, and `0037` is precisely what breaks the equivalence. A finished
    order's clock stopped at `production_ready_at`, so its remaining time is frozen while the order
    accepted beside it keeps spending.

    So the fourth order here is finished. It was accepted eleven hours ago -- earlier than every
    other row, which puts it at the top of the page -- and it was reported ready one hour later, so
    seven of its eight hours are still unspent. It outranks an order with two hours left and an
    order already two hours past the mark. That is the board's real behaviour, and the docstrings,
    the route summary and the screen's lede all say "oldest accepted first" because of it.

    What makes the page still workable is on the rows rather than in their order: every one carries
    its own `sla_outcome`, `remaining_microseconds` and `breach_microseconds`, so a shift ranks by
    the figure. Asserted here so that a future change to the ORDER BY fails against the requirement
    rather than against a fixture that could not see it.
    """
    store_id = uuid4()
    principal = _member(connection, store_id)
    ids = {
        hours: _order_in_production(
            connection, store_id, principal, accepted_at=NOW - timedelta(hours=hours)
        )
        for hours in (2, 10, 6)
    }
    accepted_at = NOW - timedelta(hours=11)
    finished = _order_in_production(
        connection,
        store_id,
        principal,
        accepted_at=accepted_at,
        ready_at=accepted_at + timedelta(hours=1),
    )

    board = ShadowConsoleRepository().sla_risk_board(
        connection, store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA, now=NOW
    )

    assert [row.order_id for row in board] == [finished, ids[10], ids[6], ids[2]]
    assert [row.sla_outcome for row in board] == ["MET", "BREACHED", "PENDING", "PENDING"]
    # The row at the top of the page has the *most* time left of anything on it, and the row two
    # places below it is two hours past the mark. Ordering by acceptance cannot express that, which
    # is why nothing above this query claims it does.
    assert board[0].remaining_microseconds == 7 * 3_600 * 1_000_000
    assert board[1].breach_microseconds == 2 * 3_600 * 1_000_000
    assert board[2].remaining_microseconds == 2 * 3_600 * 1_000_000
    assert board[3].remaining_microseconds == 6 * 3_600 * 1_000_000
    # And the figure a person would rank by really is on every row, non-negative on all of them.
    assert all(
        row.remaining_microseconds is not None and row.remaining_microseconds >= 0 for row in board
    )
    assert all(row.breach_microseconds >= 0 for row in board)


def test_the_board_pages_by_keyset_without_repeating_or_losing_an_order(
    connection: psycopg.Connection[Any],
) -> None:
    """Pagination is required, not optional — and a keyset rather than an offset.

    Five orders read two at a time must reassemble into exactly the five, in the same order a single
    unpaged read returns. An offset would satisfy this test and still skip a row in production, so
    the cursor is the `(production_accepted_at, id)` pair the ORDER BY is built on.
    """
    store_id = uuid4()
    principal = _member(connection, store_id)
    repository = ShadowConsoleRepository()
    for hours in (2, 3, 4, 5, 6):
        _order_in_production(
            connection, store_id, principal, accepted_at=NOW - timedelta(hours=hours)
        )

    whole = repository.sla_risk_board(
        connection, store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA, now=NOW
    )
    paged: list[UUID] = []
    after: tuple[datetime, UUID] | None = None
    while True:
        page = repository.sla_risk_board(
            connection,
            store_id=store_id,
            principal=principal,
            policy=STANDARD_WASH_SLA,
            now=NOW,
            limit=2,
            after=after,
        )
        if not page:
            break
        paged.extend(row.order_id for row in page)
        after = (page[-1].production_accepted_at, page[-1].order_id)

    assert len(whole) == 5
    assert paged == [row.order_id for row in whole]


def test_a_limit_outside_the_published_bounds_is_refused_rather_than_clamped(
    connection: psycopg.Connection[Any],
) -> None:
    """A caller who asks for a thousand rows gets a refusal, not a silently different answer.

    Clamping would hand back 200 rows under a request for 1000 and let a console believe it had the
    whole board. "Unknown means stop" applies to a page size as much as to a price.
    """
    store_id = uuid4()
    principal = _member(connection, store_id)

    with pytest.raises(ShadowStateError):
        ShadowConsoleRepository().sla_risk_board(
            connection,
            store_id=store_id,
            principal=principal,
            policy=STANDARD_WASH_SLA,
            limit=SLA_BOARD_MAX_LIMIT + 1,
        )


# --- the day counts -----------------------------------------------------------------------------


def test_the_day_counts_are_the_store_s_own_day_grouped_by_status(
    connection: psycopg.Connection[Any],
) -> None:
    """The query `#/today` now renders, unchanged by this item beyond gaining a version."""
    store_id = uuid4()
    principal = _member(connection, store_id)
    for hours in (1, 2):
        _order_in_production(
            connection, store_id, principal, accepted_at=datetime.now(UTC) - timedelta(hours=hours)
        )

    with connection.cursor() as cursor:
        counts = today_status_counts(cursor, store_id=store_id, principal=principal)

    assert sum(total for _, total in counts) == 2
    assert all(isinstance(total, int) and total >= 0 for _, total in counts)


# --- a year of orders ---------------------------------------------------------------------------

#: A year for this shop, at a generous twenty orders a day and none of them ever released. Every one
#: is on the board, which is the worst case rather than a realistic one: in a real year most would
#: have left the population long before.
SYNTHETIC_YEAR = 7_300

#: The bound. One page of a board is a screen a person is waiting in front of, and a quarter of a
#: second is the point at which a list stops feeling like it is already there. Deliberately far
#: above what the index delivers (single-digit milliseconds when this was written) so that the test
#: fails on a lost index rather than on a busy machine.
PAGE_BUDGET_SECONDS = 0.25


def _bulk_orders(connection: Any, store_id: UUID, principal: StaffPrincipal, count: int) -> None:
    """Populate the board with `count` orders, reusing one real quote revision.

    Driving every one through the transition chain would take minutes and would measure the write
    path rather than the read. The rows are still real rows: the same foreign keys, the same CHECK
    constraints -- including the one tying `intake_status = 'ACCEPTED'` to a non-null
    `production_accepted_at` -- and the same index. What is skipped is the history, which this query
    does not read.
    """
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=principal
    )
    base = NOW - timedelta(days=365)
    with connection.transaction(), connection.cursor() as cursor:
        with cursor.copy(
            """
            COPY orders (
                id, store_id, bound_contact_id, current_quote_id, current_quote_revision,
                current_quote_snapshot_hash, commercial_status, intake_status, production_status,
                fulfillment_mode, balance_status, customer_final_quote_accepted_at,
                production_accepted_at, row_version, created_at, acquisition_source
            ) FROM STDIN
            """
        ) as copy:
            for index in range(count):
                accepted_at = base + timedelta(minutes=index * 72)
                copy.write_row(
                    (
                        uuid4(),
                        store_id,
                        contact_id,
                        quote_id,
                        revision,
                        quote.document.snapshot_hash,
                        "ACTIVE",
                        "ACCEPTED",
                        "IN_PROCESS",
                        "SELF_DROP_SELF_COLLECT",
                        "UNPAID",
                        accepted_at,
                        accepted_at,
                        1,
                        accepted_at,
                        "WALK_IN",
                    )
                )
        # Without fresh statistics the planner is choosing for a table it believes is empty, and the
        # plan this test reads would be an artefact of the fixture rather than of the index.
        cursor.execute("ANALYZE orders")


def test_a_year_of_orders_does_not_degrade_the_board(
    connection: psycopg.Connection[Any],
) -> None:
    """Measured against a populated table, with the plan read out rather than assumed.

    Two separate claims, and the second is the one that decays silently. The elapsed time says the
    page is fast today. The plan says *why*: `orders_sla_board_idx` is what served the ordering and
    the limit, so an index dropped or a predicate edited out of alignment with the query fails here
    while the timing would still pass on a small machine for months.
    """
    store_id = uuid4()
    principal = _member(connection, store_id)
    _bulk_orders(connection, store_id, principal, SYNTHETIC_YEAR)

    started = time.perf_counter()
    page = ShadowConsoleRepository().sla_risk_board(
        connection, store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA, now=NOW
    )
    elapsed = time.perf_counter() - started

    assert len(page) == 50
    assert elapsed < PAGE_BUDGET_SECONDS, (
        f"one page of a {SYNTHETIC_YEAR}-order board took {elapsed:.3f}s, over the "
        f"{PAGE_BUDGET_SECONDS}s bound"
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "EXPLAIN (FORMAT JSON) " + _board_sql(),
            {"store": store_id, "after_accepted": None, "after_id": None, "limit": 50},
        )
        found = cursor.fetchone()
        assert found is not None
        plan = str(found[0])

    assert "orders_sla_board_idx" in plan, (
        "the board's page is no longer served by its own index; the partial predicate in migration "
        f"0044 has drifted from the query. Plan was: {plan}"
    )
    assert "Seq Scan" not in plan, f"the board fell back to a sequential scan. Plan was: {plan}"


def _board_sql() -> str:
    """The board's own statement, read from the module rather than copied into this test.

    Copying it would make this test pass against a query the repository no longer runs, which is
    precisely the failure `EXPLAIN` is being used to catch.
    """
    from nha_trang_laundry_db import shadow_console

    return shadow_console._SLA_BOARD_SQL
