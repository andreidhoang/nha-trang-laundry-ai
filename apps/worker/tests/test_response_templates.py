from nha_trang_laundry_worker.response_templates import (
    render_generic_order_status_unavailable,
    render_published_list_price,
)


def test_order_status_unavailable_shape_is_neutral_and_requires_human() -> None:
    draft = render_generic_order_status_unavailable()

    assert draft == {
        "response_shape": "GENERIC_UNAVAILABLE",
        "message_kind": "ORDER_STATUS",
        "disposition": "REQUIRE_HUMAN",
    }
    assert not any(key in draft for key in ("exists", "owner", "public_code", "order_id"))


def test_published_list_price_has_disclosure_and_no_personalized_total() -> None:
    draft = render_published_list_price(
        service_code="STANDARD_WASH_DRY",
        unit="KG",
        published_price_rules=(
            {
                "minimum_quantity": "1",
                "maximum_quantity_exclusive": "6",
                "unit_price_vnd": 25_000,
            },
        ),
    )

    assert draft["estimate_disclosure"] == "LIST_PRICE_ONLY_NOT_PERSONALIZED_TOTAL"
    assert "personalized_subtotal_vnd" not in draft
    assert "personalized_total_vnd" not in draft
