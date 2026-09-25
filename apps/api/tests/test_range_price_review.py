"""DEC-029 moved the choice of a price inside a band to the counter. Its control is review.

The ruling (docs/DECISION_RECORD_FOUNDER_2026-09-25.md) rests on three things: the server refuses
any figure outside the band, the choosing staff member is on the immutable record, and **the owner
reviews the set afterwards**. RANGE-COUNTER-ATTEST-001 built the first two. The third existed only
as a read keyed by approval id, which nothing in the console could discover -- so a control the
ruling depends on could not be exercised. A review the owner cannot find is not a review.

This is the list the owner reads: every price chosen inside a band on one business day in one
store, with who chose it, the band it was checked against, and the envelope's state. Each entry goes
through `RangePriceProposalRepository.read`, so a stored amount that does not match its envelope is
withheld here exactly as it is on the single read.
"""

# ruff: noqa: F811  (pytest fixtures re-exported from test_range_counter_attestation)

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import OperationsService, RangePriceProposalResult
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.store_access import StoreAccessError
from test_range_counter_attestation import (  # noqa: F401  (fixtures re-exported)
    AO_DAI,
    CHOSEN,
    _band_quote,
    _member,
    _propose,
    _publish,
    _shop,
    connection,
    service,
)

BUSINESS = ZoneInfo("Asia/Ho_Chi_Minh")


def _today() -> date:
    return datetime.now(BUSINESS).date()


def _priced(connection: Any, service: OperationsService, amount: int = CHOSEN) -> tuple[Any, ...]:
    store_id = _shop(connection)
    _publish(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    owner = _member(connection, store_id, StaffRole.OWNER_ADMIN)
    quote = _band_quote(service, store_id=store_id, staff=operator)
    proposed = _propose(service, store_id=store_id, staff=operator, quote=quote, amount=amount)
    assert isinstance(proposed, RangePriceProposalResult)
    return store_id, operator, owner, quote, proposed


def test_the_owner_sees_who_chose_which_price_inside_which_band_today(
    connection: Any, service: OperationsService
) -> None:
    store_id, operator, owner, quote, proposed = _priced(connection, service)

    reviews = service.list_range_price_reviews(
        store_id=store_id, business_date=_today(), principal=owner
    )

    assert len(reviews) == 1
    entry = reviews[0]
    assert entry.approval_id == proposed.approval.approval_request_id
    assert entry.proposed_by == operator.staff_user_id
    assert entry.proposed_by_name == "Nhân viên"
    assert entry.withheld is False
    assert entry.record is not None and entry.record.quote_id == quote.quote_id
    assert [(line.service_code, line.proposed_amount_vnd) for line in entry.record.lines] == [
        (AO_DAI, CHOSEN)
    ]
    line = entry.record.lines[0]
    assert line.band_minimum_vnd <= CHOSEN <= line.band_maximum_vnd


def test_the_list_is_one_business_day_in_the_shops_timezone(
    connection: Any, service: OperationsService
) -> None:
    store_id, _, owner, _, _ = _priced(connection, service)

    assert (
        service.list_range_price_reviews(
            store_id=store_id, business_date=_today() - timedelta(days=1), principal=owner
        )
        == ()
    )
    assert (
        len(
            service.list_range_price_reviews(
                store_id=store_id, business_date=_today(), principal=owner
            )
        )
        == 1
    )


def test_the_staff_member_who_chose_cannot_review_their_own_choices(
    connection: Any, service: OperationsService
) -> None:
    store_id, operator, _, _, _ = _priced(connection, service)

    with pytest.raises(StoreAccessError):
        service.list_range_price_reviews(
            store_id=store_id, business_date=_today(), principal=operator
        )


def test_another_shops_owner_reads_nothing_of_this_one(
    connection: Any, service: OperationsService
) -> None:
    store_id, _, _, _, _ = _priced(connection, service)
    elsewhere = _shop(connection)
    stranger = _member(connection, elsewhere, StaffRole.OWNER_ADMIN)

    with pytest.raises(StoreAccessError):
        service.list_range_price_reviews(
            store_id=store_id, business_date=_today(), principal=stranger
        )


def test_the_review_list_is_served_to_an_approver_and_refused_to_the_counter(
    connection: Any, service: OperationsService
) -> None:
    store_id, operator, owner, _, proposed = _priced(connection, service)
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        app.dependency_overrides[current_principal] = lambda: owner
        client = TestClient(app, base_url="https://localhost")
        served = client.get(f"/internal/v1/stores/{store_id}/range-price-reviews")
        dated = client.get(
            f"/internal/v1/stores/{store_id}/range-price-reviews",
            params={"date": (_today() - timedelta(days=1)).isoformat()},
        )
        app.dependency_overrides[current_principal] = lambda: operator
        refused = client.get(f"/internal/v1/stores/{store_id}/range-price-reviews")
    finally:
        app.dependency_overrides.clear()

    assert served.status_code == 200, served.text
    body = served.json()
    assert body["business_date"] == _today().isoformat()
    assert [item["approval_request_id"] for item in body["items"]] == [
        str(proposed.approval.approval_request_id)
    ]
    assert body["items"][0]["proposed_by_name"] == "Nhân viên"
    assert body["items"][0]["lines"][0]["proposed_amount_vnd"] == CHOSEN
    assert dated.status_code == 200 and dated.json()["items"] == []
    assert refused.status_code == 403


def test_an_unknown_store_id_is_not_distinguishable_from_one_you_do_not_work_in(
    connection: Any, service: OperationsService
) -> None:
    _, _, owner, _, _ = _priced(connection, service)

    with pytest.raises(StoreAccessError):
        service.list_range_price_reviews(store_id=uuid4(), business_date=_today(), principal=owner)


def test_a_tampered_amount_is_listed_as_withheld_rather_than_shown_or_dropped(
    connection: Any, service: OperationsService
) -> None:
    """The owner's list must not show a number the envelope never bound, and must not hide the row.

    Showing it would have the owner review a figure nobody chose; dropping it would hide the one
    row a review exists to catch. The stored amount is rewritten with the immutability trigger
    forced aside, the same simulation `test_range_price_visibility.py` uses.
    """

    from test_range_price_visibility import _force_stored_amount

    store_id, _, owner, _, proposed = _priced(connection, service)
    _force_stored_amount(connection, proposed.approval.approval_request_id, CHOSEN + 10_000)

    reviews = service.list_range_price_reviews(
        store_id=store_id, business_date=_today(), principal=owner
    )

    assert [(entry.withheld, entry.record) for entry in reviews] == [(True, None)]
    assert reviews[0].approval_id == proposed.approval.approval_request_id
