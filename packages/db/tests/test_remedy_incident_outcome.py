"""`REMEDY-INCIDENT-OUTCOME-001`: a complaint closes when every claim on it has an outcome.

Found by the filmed real-API walk of `DEC-031`, 2026-09-25. A customer brought back three suits and
complained about two: the second faded, the first lost. Staff proposed 60.000 d for the faded
suit, carried it out -- and the complaint read CLOSED. The lost suit could then not be proposed on
it at all, and a claim still waiting for the owner sat under a complaint that said it was over.
Since `DEC-031` made the garment the unit, one complaint about several garments is the normal case.

The rule: carrying out a claim closes the complaint only when no other claim on it is still open --
authorised and not carried out, or waiting on an owner envelope that is still alive. A refused,
expired or cancelled envelope can never pay, so it does not hold the complaint open.
"""

# ruff: noqa: F811  (pytest fixtures re-exported from test_remedy_garment_postgres)

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import psycopg
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
)
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_domain.remedies import RemedyKind, RemedyStatus
from test_remedies import NOW, _approve, _execute, _incident_row, _shop, _staff
from test_remedy_garment_postgres import SHIRTS, _claim, connection  # noqa: F401


def test_while_a_loss_waits_for_the_owner_paying_a_suit_leaves_room_for_the_next_suit(
    connection: psycopg.Connection[Any],
) -> None:
    """The walk's own order of events: the lost suit first, then the faded one paid at once."""

    shop = _shop(connection, lines=(SHIRTS,))
    _, staff, _, incident_id = shop
    lost = _claim(connection, shop, "line-0", 50_000, 1, RemedyKind.LOST_ITEM)
    faded = _claim(connection, shop, "line-0", 60_000, 2)
    _execute(connection, faded.proposal_id, staff)

    assert lost.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert _incident_row(connection, incident_id)[0] == "UNDER_REVIEW"
    third = _claim(connection, shop, "line-0", 40_000, 3)
    assert third.status is RemedyStatus.STAFF_AUTHORIZED


def test_the_only_claim_paid_still_closes_the_complaint(
    connection: psycopg.Connection[Any],
) -> None:
    """Unchanged: a complaint with one claim has its outcome when that claim is carried out."""

    shop = _shop(connection, lines=(SHIRTS,))
    _, staff, _, incident_id = shop
    faded = _claim(connection, shop, "line-0", 60_000, 2)
    _execute(connection, faded.proposal_id, staff)
    assert _incident_row(connection, incident_id)[:3] == ("CLOSED", True, True)


def test_a_claim_waiting_for_the_owner_holds_the_complaint_open_until_it_is_paid(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    store_id, staff, _, incident_id = shop
    lost = _claim(connection, shop, "line-0", 50_000, 1, RemedyKind.LOST_ITEM)
    faded = _claim(connection, shop, "line-0", 60_000, 2)

    _execute(connection, faded.proposal_id, staff)
    assert _incident_row(connection, incident_id)[0] == "UNDER_REVIEW"

    _approve(connection, store_id, lost.approval_id, NOW + timedelta(minutes=5))
    _execute(connection, lost.proposal_id, staff, NOW + timedelta(minutes=6))
    status, _, remedy_decided = _incident_row(connection, incident_id)
    assert (status, remedy_decided) == ("CLOSED", True)


def test_a_claim_nobody_carried_out_yet_holds_it_open_and_the_last_one_closes_it(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    _, staff, _, incident_id = shop
    second = _claim(connection, shop, "line-0", 60_000, 2)
    third = _claim(connection, shop, "line-0", 40_000, 3)

    _execute(connection, second.proposal_id, staff)
    assert _incident_row(connection, incident_id)[0] == "UNDER_REVIEW"
    _execute(connection, third.proposal_id, staff)
    assert _incident_row(connection, incident_id)[0] == "CLOSED"


def test_a_refused_owner_envelope_does_not_hold_the_complaint_open(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    store_id, staff, _, incident_id = shop
    lost = _claim(connection, shop, "line-0", 50_000, 1, RemedyKind.LOST_ITEM)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT resource_version, snapshot_hash, rendered_hash FROM approval_requests "
            "WHERE id = %s",
            (lost.approval_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    ApprovalRepository().decide(
        connection,
        ApprovalDecisionCommand(
            approval_request_id=lost.approval_id,
            decision=ApprovalDecision.REJECTED,
            observed_resource_version=int(row[0]),
            observed_snapshot_hash=str(row[1]),
            observed_rendered_hash=str(row[2]),
            reason_code="OWNER_REFUSED_REMEDY",
            principal=owner,
            correlation_id=uuid4(),
            decided_at=NOW + timedelta(minutes=1),
        ),
    )
    faded = _claim(connection, shop, "line-0", 60_000, 2)

    _execute(connection, faded.proposal_id, staff)
    assert _incident_row(connection, incident_id)[0] == "CLOSED"
