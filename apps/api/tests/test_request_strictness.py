"""The API edge must not turn `true` into 1 or "120000" into 120000 before the domain sees it.

The domain guards money and distance with `isinstance(x, int) and not isinstance(x, bool)` --
`isinstance(True, int)` is True in Python, so without the second clause `true` prices as 1 ₫. Those
guards never ran on HTTP input: `StrictRequest` only forbade extra keys, and pydantic's lax mode
coerces first. The money reviewer reproduced every one of these against the real models:
`paid_amount_vnd: true` became 1, `verified_distance_m: true` became 1 m (the free-delivery zone),
`"120000"` and `120000.0` became 120000, and `customer_acknowledged_manual_fee: 1` became True.

The console never sends these shapes, which is why nothing noticed. A different client, a script,
or a future integration would, and the domain's own guard is the thing it would silently bypass.
"""

from __future__ import annotations

import inspect
from typing import Annotated, Any, get_args, get_origin

import pytest
from nha_trang_laundry_api import main as api_main
from pydantic import BaseModel, ValidationError


def _request_models() -> list[type[BaseModel]]:
    return [
        value
        for _, value in inspect.getmembers(api_main, inspect.isclass)
        if issubclass(value, api_main.StrictRequest) and value is not api_main.StrictRequest
    ]


def _fields_of(annotation_type: type) -> list[tuple[type[BaseModel], str]]:
    found = []
    for model in _request_models():
        for name, field in model.model_fields.items():
            annotation: Any = field.annotation
            options = get_args(annotation) or (annotation,)
            # `StrictInt | None` holds `Annotated[int, Strict()]`, not `int`: unwrap it, or every
            # optional field silently drops out of the parametrisation and is never tested.
            options = tuple(
                get_args(option)[0] if get_origin(option) is Annotated else option
                for option in options
            )
            if annotation_type in options:
                found.append((model, name))
    return found


@pytest.mark.parametrize(
    ("model", "field"), _fields_of(int), ids=lambda v: getattr(v, "__name__", v)
)
@pytest.mark.parametrize("smuggled", [True, False, "120000", 120000.0])
def test_an_integer_field_refuses_what_is_not_an_integer(
    model: type[BaseModel], field: str, smuggled: object
) -> None:
    with pytest.raises(ValidationError) as caught:
        model.model_validate({field: smuggled})
    assert any(error["loc"][:1] == (field,) for error in caught.value.errors()), (
        f"{model.__name__}.{field} accepted {smuggled!r}"
    )


@pytest.mark.parametrize(
    ("model", "field"), _fields_of(bool), ids=lambda v: getattr(v, "__name__", v)
)
@pytest.mark.parametrize("smuggled", [1, 0, "true", "yes"])
def test_a_boolean_field_refuses_what_is_not_a_boolean(
    model: type[BaseModel], field: str, smuggled: object
) -> None:
    with pytest.raises(ValidationError) as caught:
        model.model_validate({field: smuggled})
    assert any(error["loc"][:1] == (field,) for error in caught.value.errors()), (
        f"{model.__name__}.{field} accepted {smuggled!r}"
    )


def test_the_strictness_covers_the_fields_the_reviewer_named() -> None:
    ints = {(m.__name__, f) for m, f in _fields_of(int)}
    bools = {(m.__name__, f) for m, f in _fields_of(bool)}
    assert {
        ("SettlementRequest", "paid_amount_vnd"),
        ("RemedyProposalRequest", "amount_vnd"),
        ("QuoteCreateRequest", "verified_distance_m"),
    } <= ints
    assert ("QuoteCreateRequest", "customer_acknowledged_manual_fee") in bools
