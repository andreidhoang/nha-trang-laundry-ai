"""The owner's turnaround policy, and the promise *Nhận đồ* writes under it (`PROMISE-001`).

`DEC-037`. The policy is a configuration version like the messaging, remedy and promotion
policies: an immutable, hashed document in `configuration_versions`, published by
`scripts/publish_turnaround_policy.py` -- a script the owner runs, never a seed a process applies
on boot. Only an active `OWNER_ADMIN` may publish it, because it is the shop's promise to its
customers.

**Before publication nothing refuses.** Orders take no promise and the counter works exactly as it
did: the reversal `DEC-037` names ("unpublish the turnaround policy; orders carry no promise") is
the state of a fresh deployment. Publishing `withdrawn: true` returns a published shop to it.

**After publication**, `RECEIVE` computes the promise with `promise.compute_promise` from the
order's priced lines and the staff member's choice, and writes it in the same transaction as the
step -- its own row version, `ORDER_PROMISE_SET` event with the full calculation trace, audit row
and outbox row. When the rule says a person must set it and nobody did, the whole step is refused
(`PROMISE_REQUIRED`) and nothing is written.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.promise import (
    TURNAROUND_DECISION_REF,
    TURNAROUND_POLICY_CONFIG_TYPE,
    TURNAROUND_POLICY_VERSION,
    PromiseChoice,
    Promised,
    PromiseError,
    PromiseNeedsHuman,
    TurnaroundPolicy,
    TurnaroundPolicyError,
    compute_promise,
    parse_turnaround_policy,
)

from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    JsonObject,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: The refusal a promise request gets while no turnaround policy is in force.
TURNAROUND_POLICY_UNPUBLISHED: Final = "TURNAROUND_POLICY_UNPUBLISHED"
#: The refusal `RECEIVE` gets when the rule says a person must set the time and nobody did.
PROMISE_REQUIRED: Final = "PROMISE_REQUIRED"


class TurnaroundPolicyAuthorizationError(PermissionError):
    """Only an active owner may publish the shop's promise to its customers."""


class PromiseRequiredError(ValueError):
    """`RECEIVE` needs a person's promise time. Nothing was written."""

    def __init__(self, reason_codes: tuple[str, ...], service_codes: tuple[str, ...]) -> None:
        self.reason_codes = reason_codes
        self.service_codes = service_codes
        super().__init__(f"{PROMISE_REQUIRED}: " + ", ".join(reason_codes))


class PromiseRefusedError(ValueError):
    """A promise request the rules refuse, by one code. Nothing was written."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class PublishedTurnaroundPolicy:
    policy: TurnaroundPolicy
    version_id: UUID
    version: int
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class PlannedPromise:
    """What `RECEIVE` will store: the domain's answer and the policy version it came from."""

    promised: Promised
    policy_version_id: UUID


def validate_turnaround_document(payload: JsonObject) -> None:
    """The typed validator: a full policy, or the owner's withdrawal of it."""

    if payload.get("withdrawn") is True:
        if set(payload) != {"policy_version", "decision_ref", "withdrawn"} or (
            payload.get("policy_version") != TURNAROUND_POLICY_VERSION
            or payload.get("decision_ref") != TURNAROUND_DECISION_REF
        ):
            raise TurnaroundPolicyError("a withdrawal names only the policy version and DEC-037")
        return
    parse_turnaround_policy(payload)


def withdrawal_document() -> dict[str, Any]:
    """`DEC-037`'s reversal: once in force, orders carry no promise, as before publication."""

    return {
        "policy_version": TURNAROUND_POLICY_VERSION,
        "decision_ref": TURNAROUND_DECISION_REF,
        "withdrawn": True,
    }


def publish_turnaround_policy(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish one turnaround document; return its digest and whether this call created it.

    Idempotent on the version in force, as the messaging policy is: the document already in force
    changes nothing; a different one (or an earlier one again) is a new version.
    """

    validate_turnaround_document(payload)
    digest = snapshot_hash(payload)
    repository = ConfigurationRepository(
        {TURNAROUND_POLICY_CONFIG_TYPE: validate_turnaround_document}
    )
    with connection.transaction(), connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        in_force = ConfigurationRepository.latest_published(cursor, TURNAROUND_POLICY_CONFIG_TYPE)
        if in_force is not None and in_force.snapshot_hash == digest:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (TURNAROUND_POLICY_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1
    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=TURNAROUND_POLICY_CONFIG_TYPE,
            version=next_version,
            payload=payload,
            created_by=actor_id,
        ),
        correlation_id=uuid4(),
    )
    repository.publish(
        connection,
        config_id=config_id,
        version=next_version,
        snapshot_hash_value=digest,
        published_by=actor_id,
        correlation_id=uuid4(),
    )
    return digest, True


def read_published_turnaround_policy(cursor: Any) -> PublishedTurnaroundPolicy | None:
    """The policy in force, or None -- which means orders carry no promise.

    Re-hashed against the digest recorded at publication and re-parsed before use: a payload that
    no longer matches or no longer parses is not a published policy. A withdrawal in force is None.
    """

    published = ConfigurationRepository.latest_published(cursor, TURNAROUND_POLICY_CONFIG_TYPE)
    if published is None:
        return None
    payload = ConfigurationRepository.get_published(cursor, published.version_id)
    if payload is None or not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
        return None
    if payload.get("withdrawn") is True:
        return None
    try:
        policy = parse_turnaround_policy(payload)
    except TurnaroundPolicyError:
        return None
    return PublishedTurnaroundPolicy(
        policy=policy,
        version_id=published.version_id,
        version=published.version,
        snapshot_hash=published.snapshot_hash,
    )


#: The service codes of the order's bound quote revision, in line order.
_ORDER_SERVICE_CODES_SQL: Final = """
    SELECT line.value ->> 'service_code'
    FROM orders o
    JOIN quote_revisions r
      ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(r.snapshot -> 'lines') = 'array'
             THEN r.snapshot -> 'lines' ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS line(value, position)
    WHERE o.id = %s
    ORDER BY line.position
"""


def order_service_codes(cursor: Any, order_id: UUID) -> tuple[str, ...]:
    cursor.execute(_ORDER_SERVICE_CODES_SQL, (order_id,))
    return tuple(str(row[0]) for row in cursor.fetchall() if row[0])


def plan_receive_promise(
    cursor: Any,
    *,
    order_id: UUID,
    accepted_at: datetime,
    choice: PromiseChoice | None,
    custom_at: datetime | None,
) -> PlannedPromise | None:
    """The promise `RECEIVE` will write, None when no policy is in force, or a refusal.

    With no policy in force a choice is refused rather than dropped: the person said something the
    shop cannot record, and silently ignoring it is how a ledger stops being believed.
    """

    published = read_published_turnaround_policy(cursor)
    if published is None:
        if choice is not None or custom_at is not None:
            raise PromiseRefusedError(
                TURNAROUND_POLICY_UNPUBLISHED, "the owner has not published the turnaround policy"
            )
        return None
    try:
        decided = compute_promise(
            published.policy,
            accepted_at=accepted_at,
            service_codes=order_service_codes(cursor, order_id),
            choice=choice,
            custom_at=custom_at,
        )
    except PromiseError as error:
        raise PromiseRefusedError(error.code.value, str(error)) from error
    if isinstance(decided, PromiseNeedsHuman):
        raise PromiseRequiredError(
            tuple(reason.value for reason in decided.reason_codes), decided.service_codes
        )
    return PlannedPromise(promised=decided, policy_version_id=published.version_id)


def write_receive_promise(
    connection: Any,
    *,
    order_id: UUID,
    expected_row_version: int,
    planned: PlannedPromise,
    principal: StaffPrincipal,
    correlation_id: UUID,
    occurred_at: datetime,
) -> int:
    """Store the first promise on the order, with its event, audit and outbox rows. Returns the
    new row version. Must run inside the step's transaction, after its transitions."""

    promised = planned.promised
    next_version = expected_row_version + 1

    def mutation(cursor: Any) -> None:
        cursor.execute(
            """
            UPDATE orders
            SET promised_ready_at = %(at)s, current_promise_at = %(at)s,
                promise_basis = %(basis)s, promise_rule_id = %(rule)s,
                promise_policy_version_id = %(policy)s, row_version = row_version + 1
            WHERE id = %(order)s AND row_version = %(version)s AND promised_ready_at IS NULL
            RETURNING id
            """,
            {
                "at": promised.promised_at,
                "basis": promised.basis,
                "rule": promised.rule_id,
                "policy": planned.policy_version_id,
                "order": order_id,
                "version": expected_row_version,
            },
        )
        if cursor.fetchone() is None:
            raise PromiseRefusedError("STALE_VERSION", "order changed while its promise was set")

    promised_text = promised.promised_at.isoformat()
    commit_material_change(
        connection,
        MaterialChange(
            aggregate_type="ORDER",
            aggregate_id=order_id,
            aggregate_version=next_version,
            event_type="ORDER_PROMISE_SET",
            event_payload={
                "promised_ready_at": promised_text,
                "basis": promised.basis,
                "rule_id": promised.rule_id,
                "policy_version_id": str(planned.policy_version_id),
                "trace": promised.trace(),
            },
            audit_action="ORDER_PROMISE_SET",
            actor_type="STAFF",
            actor_id=principal.staff_user_id,
            correlation_id=correlation_id,
            audit_details={"basis": promised.basis, "rule_id": promised.rule_id},
            outbox_events=(
                OutboxEvent(
                    "order.promise_set.v1",
                    {
                        "order_id": str(order_id),
                        "promised_ready_at": promised_text,
                        "row_version": next_version,
                    },
                    f"order:{order_id}:promise-set",
                ),
            ),
            occurred_at=occurred_at,
        ),
        mutation,
    )
    return next_version


def _require_active_owner(cursor: Any, actor_id: UUID) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id = %s AND u.status = 'ACTIVE' AND r.role = %s AND r.revoked_at IS NULL
        """,
        (actor_id, StaffRole.OWNER_ADMIN.value),
    )
    if cursor.fetchone() is None:
        raise TurnaroundPolicyAuthorizationError(
            "only an active OWNER_ADMIN may publish the turnaround policy"
        )


__all__ = [
    "PROMISE_REQUIRED",
    "TURNAROUND_POLICY_UNPUBLISHED",
    "PlannedPromise",
    "PromiseRefusedError",
    "PromiseRequiredError",
    "PublishedTurnaroundPolicy",
    "TurnaroundPolicyAuthorizationError",
    "order_service_codes",
    "plan_receive_promise",
    "publish_turnaround_policy",
    "read_published_turnaround_policy",
    "validate_turnaround_document",
    "withdrawal_document",
    "write_receive_promise",
]
