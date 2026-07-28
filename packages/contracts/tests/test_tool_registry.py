from __future__ import annotations

from pathlib import Path

import pytest
from nha_trang_laundry_contracts import (
    AgentToolOperation,
    AgentToolSideEffect,
    ToolArgumentsInvalid,
    ToolRegistryError,
    load_agent_tool_registry,
)

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = load_agent_tool_registry(ROOT / "specs/contracts/agent-tools-v1.openapi.yaml")


def test_registry_compiles_exact_fixed_openclaw_tools() -> None:
    assert set(REGISTRY.operation_ids) == {operation.value for operation in AgentToolOperation}
    assert len(REGISTRY.tool_names) == 10
    assert len(set(REGISTRY.tool_names)) == 10
    assert all(name.startswith("laundry_") for name in REGISTRY.tool_names)


def test_catalog_arguments_are_strict_and_decimal_is_a_string() -> None:
    contract = REGISTRY.get(AgentToolOperation.CATALOG_RESOLVE)

    accepted = contract.validate_model_arguments(
        {
            "query": "giặt chăn",
            "locale": "vi-VN",
            "known_attributes": {"estimated_quantity": "3.0", "unit": "KG"},
        }
    )

    assert accepted["known_attributes"]["estimated_quantity"] == "3.0"
    with pytest.raises(ToolArgumentsInvalid, match="VALIDATION_ERROR"):
        contract.validate_model_arguments(
            {
                "query": "giặt chăn",
                "locale": "vi-VN",
                "known_attributes": {"estimated_quantity": 3.0, "unit": "KG"},
            }
        )


@pytest.mark.parametrize(
    "injected_field",
    [
        "actor_role",
        "capability",
        "contact_id",
        "customer_id",
        "distance_measurement_id",
        "order_request_id",
        "reason_codes",
        "required_approver_role",
        "stage",
        "store_id",
        "ttl",
    ],
)
def test_server_owned_fields_are_rejected_at_any_depth(injected_field: str) -> None:
    contract = REGISTRY.get(AgentToolOperation.CATALOG_RESOLVE)

    with pytest.raises(ToolArgumentsInvalid, match=f"server-owned field.*{injected_field}"):
        contract.validate_model_arguments(
            {
                "query": "giặt chăn",
                "locale": "vi-VN",
                "known_attributes": {injected_field: "attacker-controlled"},
            }
        )


def test_approval_request_cannot_inject_server_policy_fields() -> None:
    contract = REGISTRY.get(AgentToolOperation.APPROVAL_REQUEST_CREATE)
    base = {
        "action": "PRESENT_QUOTE",
        "resource_type": "QUOTE_REVISION",
        "resource_id": "2de7144d-f09a-4dc2-b44f-4f1109747b05",
        "resource_version": 1,
        "snapshot_hash": f"sha256:{'a' * 64}",
        "rendered_hash": f"sha256:{'b' * 64}",
    }

    assert contract.validate_model_arguments(base) == base
    for field in ("reason_codes", "required_approver_role", "ttl", "obligations"):
        with pytest.raises(ToolArgumentsInvalid):
            contract.validate_model_arguments({**base, field: []})


def test_approval_action_and_resource_pair_must_match() -> None:
    contract = REGISTRY.get(AgentToolOperation.APPROVAL_REQUEST_CREATE)

    with pytest.raises(ToolArgumentsInvalid):
        contract.validate_model_arguments(
            {
                "action": "PRESENT_QUOTE",
                "resource_type": "MESSAGE_DRAFT",
                "resource_id": "2de7144d-f09a-4dc2-b44f-4f1109747b05",
                "resource_version": 1,
                "snapshot_hash": f"sha256:{'a' * 64}",
                "rendered_hash": f"sha256:{'b' * 64}",
            }
        )


def test_bound_path_and_headers_are_not_model_arguments() -> None:
    contract = REGISTRY.get(AgentToolOperation.QUOTE_ESTIMATE)

    assert contract.path_parameters == ("order_request_id",)
    assert set(contract.header_parameters) == {"Idempotency-Key", "If-Match"}
    assert "order_request_id" not in contract.model_argument_schema.get("properties", {})
    with pytest.raises(ToolArgumentsInvalid, match="order_request_id"):
        contract.validate_model_arguments({"order_request_id": "attacker-selected"})


def test_operation_budget_and_side_effect_are_compiled_from_openapi() -> None:
    catalog = REGISTRY.get(AgentToolOperation.CATALOG_RESOLVE)
    create = REGISTRY.get(AgentToolOperation.ORDER_REQUEST_CREATE)

    assert catalog.side_effect is AgentToolSideEffect.NONE
    assert catalog.max_calls_per_run == 3
    assert create.side_effect is AgentToolSideEffect.REVERSIBLE_WRITE
    assert create.max_calls_per_run == 1


@pytest.mark.parametrize(
    "operation",
    ["messageSend", "shellExecute", "browserOpen", "sqlQuery", "genericActionExecute"],
)
def test_generic_and_direct_send_operations_do_not_exist(operation: str) -> None:
    with pytest.raises(ToolRegistryError, match="Unknown public-agent operation"):
        REGISTRY.get(operation)
