"""`MONEY-LIFECYCLE-009` (review M3, M7): a cancellation never pays a remedy twice or loses a
spent credit. `cancellation_money_refusal`, as a table: a cancellation (which never charges the
customer) is refused for an order a money remedy paid out on, or whose bill spent a remedy credit,
whatever its resolution -- except the owner's disposal, where what was paid is kept.
"""

from __future__ import annotations

import pytest
from nha_trang_laundry_domain.cancellation_money import (
    REMEDY_KIND_VI,
    CancellationMoneyRefusal,
    RemedyCreditFact,
    cancellation_money_refusal,
)
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.remedies import RemedyKind

# --- M3 / M7: a cancellation never pays a remedy twice or loses a spent credit --------------------

LATE = RemedyCreditFact(RemedyKind.LATE_DELIVERY_CREDIT, 11_000)
DAMAGE = RemedyCreditFact(RemedyKind.DAMAGE_COMPENSATION, 80_000)
RESOLUTIONS: tuple[CustodyResolution | None, ...] = (
    None,
    CustodyResolution.NOT_RECEIVED,
    CustodyResolution.RETURNED_UNWASHED_REFUNDED,
    CustodyResolution.SHOP_FAULT_NO_CHARGE,
)


@pytest.mark.parametrize("resolution", RESOLUTIONS)
@pytest.mark.parametrize(
    ("issued", "spent", "codes"),
    [
        ((), (), ()),
        ((LATE,), (), (CancellationMoneyRefusal.CANCEL_AFTER_MONEY_REMEDY,)),
        ((DAMAGE,), (), (CancellationMoneyRefusal.CANCEL_AFTER_MONEY_REMEDY,)),
        ((), (LATE,), (CancellationMoneyRefusal.CANCEL_WOULD_LOSE_SPENT_CREDIT,)),
        (
            (DAMAGE,),
            (LATE,),
            (
                CancellationMoneyRefusal.CANCEL_AFTER_MONEY_REMEDY,
                CancellationMoneyRefusal.CANCEL_WOULD_LOSE_SPENT_CREDIT,
            ),
        ),
    ],
)
def test_a_cancellation_is_refused_for_remedy_money_whatever_its_resolution(
    resolution: CustodyResolution | None,
    issued: tuple[RemedyCreditFact, ...],
    spent: tuple[RemedyCreditFact, ...],
    codes: tuple[CancellationMoneyRefusal, ...],
) -> None:
    refused = cancellation_money_refusal(
        resolution=resolution, issued_from_order=issued, spent_on_order=spent
    )
    if not codes:
        assert refused is None
        return
    assert refused is not None
    assert refused.refusals == codes
    assert refused.reason_codes == tuple(code.value for code in codes)
    # The sentence names every credit by what it was for and its amount, and the way forward.
    for credit in (*issued, *spent):
        assert REMEDY_KIND_VI[credit.kind] in refused.reason_vi
        assert f"{credit.amount_vnd:,}".replace(",", ".") + " ₫" in refused.reason_vi
    assert refused.reason_vi.startswith("Không huỷ được")
    assert "báo chủ tiệm" in refused.reason_vi
    assert "_" not in refused.reason_vi  # no raw code reaches the counter


def test_the_owner_s_disposal_keeps_what_was_paid_so_it_is_never_refused() -> None:
    assert (
        cancellation_money_refusal(
            resolution=CustodyResolution.UNCLAIMED_DISPOSED,
            issued_from_order=(DAMAGE,),
            spent_on_order=(LATE,),
        )
        is None
    )


def test_a_credit_is_a_positive_whole_amount() -> None:
    with pytest.raises(ValueError):
        cancellation_money_refusal(
            resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE,
            issued_from_order=(RemedyCreditFact(RemedyKind.LATE_DELIVERY_CREDIT, 0),),
            spent_on_order=(),
        )
