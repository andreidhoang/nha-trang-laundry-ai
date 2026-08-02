"""Fail-closed validation for versioned metric, SLO, and alert contracts."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from .telemetry import METRIC_CONTRACTS, SAFE_ATTRIBUTE_VALUES

_METRIC_FIELDS = frozenset(
    {
        "metric_id",
        "version",
        "business_question",
        "numerator",
        "denominator",
        "inclusion_rules",
        "exclusion_rules",
        "event_source",
        "required_fields",
        "window",
        "timezone",
        "late_event_policy",
        "dimensions",
        "owner",
        "target",
        "alert",
        "rollback_threshold",
    }
)
_COMPARISONS = frozenset({"gt", "gte", "lt", "lte", "eq"})
_SEVERITIES = frozenset({"warning", "urgent", "high"})


class TelemetryContractError(ValueError):
    """Raised when a metric or alert definition is incomplete or unsafe."""


def load_telemetry_contracts() -> dict[str, Any]:
    resource = files("nha_trang_laundry_observability.contracts").joinpath("telemetry-v1.json")
    document = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TelemetryContractError("telemetry contract root must be an object")
    return document


def validate_telemetry_contracts(document: dict[str, Any]) -> None:
    if document.get("schema_version") != 1:
        raise TelemetryContractError("unsupported telemetry contract schema")
    metrics = _objects(document, "metrics")
    slos = _objects(document, "slos")
    alerts = _objects(document, "alerts")
    runtime_ids = {contract.metric_id for contract in METRIC_CONTRACTS}
    declared_ids: set[str] = set()
    for metric in metrics:
        missing_fields = _METRIC_FIELDS - metric.keys()
        if missing_fields:
            raise TelemetryContractError(f"metric is missing fields: {sorted(missing_fields)}")
        metric_id = _identifier(metric.get("metric_id"), "metric_id")
        if metric_id in declared_ids:
            raise TelemetryContractError(f"duplicate metric contract: {metric_id}")
        declared_ids.add(metric_id)
        if metric.get("version") != 1:
            raise TelemetryContractError(f"unsupported metric version: {metric_id}")
        dimensions = metric.get("dimensions")
        if not isinstance(dimensions, list) or not all(
            isinstance(item, str) and item in SAFE_ATTRIBUTE_VALUES for item in dimensions
        ):
            raise TelemetryContractError(f"unsafe metric dimension: {metric_id}")
        for field in _METRIC_FIELDS - {"version", "dimensions"}:
            value = metric.get(field)
            if value in (None, "", []):
                raise TelemetryContractError(f"empty metric field {field}: {metric_id}")
    if declared_ids != runtime_ids:
        registry_missing = sorted(runtime_ids - declared_ids)
        extra = sorted(declared_ids - runtime_ids)
        raise TelemetryContractError(
            f"runtime/contract metric mismatch missing={registry_missing} extra={extra}"
        )
    _validate_rules(slos, declared_ids, kind="slo")
    _validate_rules(alerts, declared_ids, kind="alert")


def _objects(document: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = document.get(field)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, dict) for item in value)
    ):
        raise TelemetryContractError(f"{field} must be a non-empty object list")
    return value


def _validate_rules(rules: list[dict[str, Any]], metric_ids: set[str], *, kind: str) -> None:
    seen: set[str] = set()
    for rule in rules:
        rule_id = _identifier(rule.get("id"), f"{kind} id")
        if rule_id in seen:
            raise TelemetryContractError(f"duplicate {kind}: {rule_id}")
        seen.add(rule_id)
        if rule.get("metric_id") not in metric_ids:
            raise TelemetryContractError(f"unknown metric in {kind}: {rule_id}")
        if rule.get("comparison") not in _COMPARISONS:
            raise TelemetryContractError(f"invalid comparison in {kind}: {rule_id}")
        if not isinstance(rule.get("threshold"), int | float):
            raise TelemetryContractError(f"invalid threshold in {kind}: {rule_id}")
        if not isinstance(rule.get("window_seconds"), int) or rule["window_seconds"] <= 0:
            raise TelemetryContractError(f"invalid window in {kind}: {rule_id}")
        for field in ("owner", "schedule", "runbook"):
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                raise TelemetryContractError(f"missing {field} in {kind}: {rule_id}")
        if kind == "alert":
            if rule.get("severity") not in _SEVERITIES:
                raise TelemetryContractError(f"invalid alert severity: {rule_id}")
            if not isinstance(rule.get("for_seconds"), int) or rule["for_seconds"] < 0:
                raise TelemetryContractError(f"invalid alert duration: {rule_id}")


def _identifier(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in value)
    ):
        raise TelemetryContractError(f"invalid {field}")
    return value
