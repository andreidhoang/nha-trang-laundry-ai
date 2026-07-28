import pytest
from nha_trang_laundry_domain.money import require_non_negative_vnd


def test_accepts_whole_non_negative_vnd() -> None:
    assert require_non_negative_vnd(25_000) == 25_000


@pytest.mark.parametrize("invalid_amount", [True, 25_000.0, -1])
def test_rejects_non_integer_or_negative_vnd(invalid_amount: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        require_non_negative_vnd(invalid_amount)  # type: ignore[arg-type]
