"""Publish `DEC-004`, propose a remedy against it, and spend the credit one produces.

`REMEDY-001`. Three concerns, deliberately in one module because they are one thread: the owner's
figures become a published configuration version, a staff member proposes a remedy the server checks
against those figures and the order's own recorded facts, and an executed money remedy becomes a
credit that lands on the customer's next quote.

**Nothing here decides money.** `nha_trang_laundry_domain.remedies` does. This module reads stored
state, hands it to `evaluate_remedy`, and persists what comes back -- the same division
`SettlementRepository` keeps, and for the same reason: a repository that can compute a ceiling is a
repository that can get one wrong somewhere a test is not looking.

**What this does not do, and why it is not a gap.** The packet expects `FREE_REWASH` to trigger the
`EXCEPTION -> earlier production state` transition `DEC-024` built. It does not, because the two
cannot meet: the rewash window runs from pickup, pickup is `RELEASED`, and `RELEASED` is terminal on
the production dimension -- `transition_production` refuses to leave it and widening that guard is
not this item's business. The existing mechanic serves the rewash found *before* the laundry is
handed over (a stain at quality check), which needs no remedy proposal because nothing has reached
the customer. A rewash of laundry the customer took home and brought back is a new intake, which is
how the counter already handles it. So `FREE_REWASH` records the authority, the fault finding and
the window, and emits `REWASH_COMMANDED`; it moves no order.

**Ordering of the two writes in `propose`.** When the owner is required, the approval envelope is
created first and the proposal row second, carrying the envelope's id. It cannot be the other way
around: `remedy_proposals.approval_id` is a foreign key, and a proposal that exists without its
envelope for even one transaction is a proposal somebody could act on. The residue of a failure
between them is an approval envelope nobody ever decides, which expires on its own ten-minute TTL
and is visible in the audit trail -- an inert record, not an authorisation.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.catalog import MODES_EXPECTING_RETURN as _MODES_EXPECTING_RETURN
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    ApprovalAction,
    FulfillmentMode,
    PolicyOutcome,
)
from nha_trang_laundry_domain.quote_composition import ComposedQuote, redeem_remedy_credit
from nha_trang_laundry_domain.quotes import ExactLineAmounts, parse_quote_revision
from nha_trang_laundry_domain.remedies import (
    REMEDY_POLICY_CONFIG_TYPE,
    REMEDY_POLICY_VERSION,
    RemedyAuthorized,
    RemedyCredit,
    RemedyKind,
    RemedyOrderFacts,
    RemedyPolicy,
    RemedyPolicyError,
    RemedyRefusal,
    RemedyRefused,
    RemedyRequest,
    RemedyStatus,
    RemedyUnresolved,
    evaluate_remedy,
    parse_remedy_policy,
    remedy_proposal_document,
)

from nha_trang_laundry_db.approvals import (
    ApprovalRepository,
    ApprovalRequestCommand,
    read_approval_binding,
)
from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    ConfigurationValidationError,
    JsonObject,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand, QuoteStateError
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Recording a complaint's outcome is counter work, like recording a settlement. Whether the outcome
#: needs the *owner* is not a role question -- it is the published 100.000 d ceiling, checked by the
#: domain -- so this set is the same one settlement uses rather than a narrower one invented here.
REMEDY_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR})

#: The event names the incident eval already queries `domain_events` for
#: (`synthetic_incidents.py:135`), used verbatim so the eval measures this code rather than a
#: vocabulary invented beside it. `REFUND_EXECUTED` is the third name in that list and is
#: deliberately never emitted: nothing in `DEC-004` refunds money, and `DEC-010` keeps the
#: settlement path append-only, so a refund would be a policy nobody has decided.
REWASH_COMMANDED = "REWASH_COMMANDED"
CREDIT_EXECUTED = "CREDIT_EXECUTED"
REMEDY_PROPOSAL_RECORDED = "REMEDY_PROPOSAL_RECORDED"
REMEDY_CREDIT_REDEEMED = "REMEDY_CREDIT_REDEEMED"


class RemedyAuthorizationError(PermissionError):
    """Raised when a principal may not act on remedies for this store."""


class RemedyStateError(ValueError):
    """Raised when a remedy request is refused, with the figure that would have allowed it.

    `ceiling_vnd`, `window_closes_at` and `threshold_minutes` are carried rather than formatted into
    the message because the console has to render them in Vietnamese: staff telling a customer "quá
    7 ngày kể từ khi nhận đồ, hạn là 14/09" is a different conversation from "không được".
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        authority: str | None = None,
        ceiling_vnd: int | None = None,
        window_closes_at: datetime | None = None,
        threshold_minutes: int | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.authority = authority
        self.ceiling_vnd = ceiling_vnd
        self.window_closes_at = window_closes_at
        self.threshold_minutes = threshold_minutes
        super().__init__(message)


# --- the published policy ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublishedRemedyPolicy:
    """One published `REMEDY_POLICY` version, parsed, with the provenance a proposal records."""

    policy: RemedyPolicy
    version_id: UUID
    version: int
    snapshot_hash: str


def validate_remedy_policy(payload: JsonObject) -> None:
    """The registered validator for `REMEDY_POLICY`. A malformed policy is refused at publication.

    It runs the real parse rather than a shape check, exactly as `validate_pricebook` does: a
    document that satisfies a schema and then fails to produce figures at the counter has moved the
    failure from the moment somebody could fix it to the moment somebody needed it.
    """

    try:
        parse_remedy_policy(payload)
    except RemedyPolicyError as error:
        raise ConfigurationValidationError(f"remedy policy is not publishable: {error}") from error


def publish_remedy_policy(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish one remedy policy document, returning its digest and whether this call created it.

    Idempotent on the digest *of the version in force*, for the reason `publish_pricebook` is:
    republishing the document already in force would create a second version of the same figures,
    and every proposal citing it would start looking out of date for no reason. An earlier
    document is not in force, so publishing it again is a new version that is.
    """

    validate_remedy_policy(payload)
    digest = snapshot_hash(payload)
    repository = ConfigurationRepository({REMEDY_POLICY_CONFIG_TYPE: validate_remedy_policy})
    with connection.cursor() as cursor:
        # "Is this the policy in force", not "was it ever published". The second made reverting a
        # temporary change impossible -- raise the staff ceiling as v2, republish v1's figures to
        # end it, and v2 stayed in force behind an "already published". A revert is a new version
        # with the earlier content, exactly as `publish_pricebook` does it.
        in_force = ConfigurationRepository.latest_published(cursor, REMEDY_POLICY_CONFIG_TYPE)
        if in_force is not None and in_force.snapshot_hash == digest:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (REMEDY_POLICY_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1

    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=REMEDY_POLICY_CONFIG_TYPE,
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


def read_published_remedy_policy(cursor: Any) -> PublishedRemedyPolicy | None:
    """The remedy figures in force, or `None`, which means every remedy request fails closed.

    Invariant 11. `None` is a legitimate and expected answer for a deployment whose owner has not
    published the policy yet, and the correct behaviour is to refuse rather than to fall back on a
    constant nobody ratified -- which is exactly the mistake `CURRENT_PROMOTION` records.

    The stored payload is re-hashed against the digest recorded at publication before it is parsed,
    for the same reason the pricebook read is: a payload that no longer matches its digest is not a
    published policy, whatever the lifecycle column says.
    """

    published = ConfigurationRepository.latest_published(cursor, REMEDY_POLICY_CONFIG_TYPE)
    if published is None:
        return None
    payload = ConfigurationRepository.get_published(cursor, published.version_id)
    if payload is None or not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
        return None
    try:
        policy = parse_remedy_policy(payload)
    except RemedyPolicyError:
        # A stored payload that no longer parses is not a policy to apply. It fails closed rather
        # than raising, so the caller reports `REMEDY_POLICY_UNPUBLISHED` -- which is what an
        # unusable published document means at the counter.
        return None
    return PublishedRemedyPolicy(
        policy=policy,
        version_id=published.version_id,
        version=published.version,
        snapshot_hash=published.snapshot_hash,
    )


# --- proposals --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RemedyProposalCommand:
    """What a named staff member proposed. Only a kind and attested facts."""

    store_id: UUID
    incident_id: UUID
    kind: RemedyKind
    store_fault_attested: bool
    principal: StaffPrincipal
    correlation_id: UUID
    order_line_id: str | None = None
    amount_vnd: int | None = None
    attested_late_by_minutes: int | None = None
    proposed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredRemedyProposal:
    """The recorded proposal and what the server decided about it."""

    proposal_id: UUID
    incident_id: UUID
    order_id: UUID
    kind: RemedyKind
    status: RemedyStatus
    outcome: PolicyOutcome
    proposal_hash: str
    policy_version_id: UUID
    policy_version: int
    amount_vnd: int | None = None
    ceiling_vnd: int | None = None
    window_opened_at: datetime | None = None
    window_closes_at: datetime | None = None
    approval_id: UUID | None = None
    #: `LOSS_POLICY_UNRESOLVED` for the one kind that is recorded and stopped; `None` otherwise.
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class RemedyOptions:
    """Everything the form must show before a staff member types anything.

    The packet's requirement in one read: staff must never discover that the owner is required after
    filling the form in, and must see the computed ceiling, the window and whether it is still open
    *first*. Every field here is derived from the published policy and the order's recorded facts;
    none of it is a suggestion, and there is deliberately no proposed amount anywhere on it.
    """

    incident_id: UUID
    order_id: UUID
    policy_published: bool
    #: Above this, `DEC-004` requires the owner. `None` when no policy is published.
    staff_approval_ceiling_vnd: int | None = None
    #: When the customer received their laundry back; every window is measured from it.
    goods_returned_at: datetime | None = None
    rewash_window_closes_at: datetime | None = None
    rewash_window_open: bool = False
    defect_window_closes_at: datetime | None = None
    defect_window_open: bool = False
    #: The 5x cap per priced line of the order's own revision, keyed by `line_id`.
    damage_line_ceilings_vnd: Mapping[str, int] | None = None
    #: The 10% the server computed, or `None` when the order records no settled total or no
    #: succeeded return leg -- in which case the credit is not available and the form must say so.
    late_delivery_credit_vnd: int | None = None
    late_delivery_threshold_minutes: int | None = None
    #: Always `LOSS_POLICY_UNRESOLVED`. Present so the console can render `components.unsupported`
    #: with a reason rather than a broken form.
    loss_reason_code: str = RemedyRefusal.LOSS_POLICY_UNRESOLVED.value


@dataclass(frozen=True, slots=True)
class _OrderRecord:
    """The order's stored facts, before the domain is asked anything."""

    order_id: UUID
    store_id: UUID
    bound_contact_id: UUID
    #: The order's current quote digest. It is the approval envelope's `snapshot_hash`: the priced
    #: evidence a damage ceiling was computed from, so an envelope cannot name a proposal whose
    #: order was repriced underneath it. Kept here rather than on `RemedyOrderFacts` because the
    #: domain decides nothing from it -- it is persistence provenance.
    quote_snapshot_hash: str
    facts: RemedyOrderFacts


class RemedyProposalRepository:
    """Propose and execute remedies, with authorization, ceilings, and atomic ledger records."""

    def __init__(self, approvals: ApprovalRepository | None = None) -> None:
        self._approvals = approvals or ApprovalRepository()

    def options(
        self, cursor: Any, *, store_id: UUID, incident_id: UUID, principal: StaffPrincipal
    ) -> RemedyOptions:
        """What the server would allow, before anybody proposes anything. Reads only."""

        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=RemedyAuthorizationError,
        )
        record = _read_order_for_incident(cursor, store_id=store_id, incident_id=incident_id)
        published = read_published_remedy_policy(cursor)
        if published is None:
            return RemedyOptions(
                incident_id=incident_id, order_id=record.order_id, policy_published=False
            )
        policy, facts = published.policy, record.facts
        at = datetime.now(UTC)
        rewash_closes = _window_close(facts, days=policy.free_rewash_window_days)
        defect_closes = _window_close(facts, hours=policy.defect_report_window_hours)
        credit: int | None = None
        if facts.expects_return_leg and facts.return_leg_succeeded:
            # Priced through the domain rather than multiplied here, so the number the form shows is
            # the number a proposal would produce. No arithmetic on money outside the domain.
            probe = evaluate_remedy(
                policy=policy,
                facts=facts,
                request=RemedyRequest(
                    kind=RemedyKind.LATE_DELIVERY_CREDIT,
                    store_fault_attested=True,
                    attested_late_by_minutes=policy.late_delivery_threshold_minutes + 1,
                ),
                requested_at=at,
            )
            credit = probe.amount_vnd if isinstance(probe, RemedyAuthorized) else None
        return RemedyOptions(
            incident_id=incident_id,
            order_id=record.order_id,
            policy_published=True,
            staff_approval_ceiling_vnd=policy.staff_approval_ceiling_vnd,
            goods_returned_at=facts.goods_returned_at,
            rewash_window_closes_at=rewash_closes,
            rewash_window_open=rewash_closes is not None and at <= rewash_closes,
            defect_window_closes_at=defect_closes,
            defect_window_open=defect_closes is not None and at <= defect_closes,
            damage_line_ceilings_vnd={
                line_id: amount * policy.damage_compensation_multiple
                for line_id, amount in facts.line_amounts_vnd.items()
            },
            late_delivery_credit_vnd=credit,
            late_delivery_threshold_minutes=policy.late_delivery_threshold_minutes,
        )

    def propose(self, connection: Any, command: RemedyProposalCommand) -> StoredRemedyProposal:
        """Check one request against the published figures and record what was decided."""

        if not command.principal.roles & REMEDY_ROLES or not command.principal.mfa_verified:
            raise RemedyAuthorizationError(
                "proposing a remedy requires an operations role with MFA"
            )
        proposed_at = command.proposed_at or datetime.now(UTC)

        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=RemedyAuthorizationError,
            )
            record = _read_order_for_incident(
                cursor, store_id=command.store_id, incident_id=command.incident_id
            )
            published = read_published_remedy_policy(cursor)

        if published is None:
            # Invariant 11, and it applies to every kind including the ones that move no money: the
            # 7-day rewash window is itself one of the owner's published figures.
            raise RemedyStateError(
                "no remedy policy is published, so no remedy may be authorised",
                reason_code=RemedyRefusal.REMEDY_POLICY_UNPUBLISHED.value,
                authority="INVARIANT-11",
            )

        outcome = evaluate_remedy(
            policy=published.policy,
            facts=record.facts,
            request=RemedyRequest(
                kind=command.kind,
                store_fault_attested=command.store_fault_attested,
                order_line_id=command.order_line_id,
                amount_vnd=command.amount_vnd,
                attested_late_by_minutes=command.attested_late_by_minutes,
            ),
            requested_at=proposed_at,
        )
        if isinstance(outcome, RemedyRefused):
            raise RemedyStateError(
                "this remedy is not authorised",
                reason_code=outcome.reason_code,
                authority=outcome.authority,
                ceiling_vnd=outcome.ceiling_vnd,
                window_closes_at=outcome.window_closes_at,
                threshold_minutes=outcome.threshold_minutes,
            )

        proposal_id = uuid4()
        if isinstance(outcome, RemedyUnresolved):
            return self._record_unresolved(
                connection,
                command,
                record=record,
                published=published,
                proposal_id=proposal_id,
                outcome=outcome,
                proposed_at=proposed_at,
            )

        document = remedy_proposal_document(
            proposal_id=proposal_id,
            incident_id=command.incident_id,
            order_id=record.order_id,
            authorized=outcome,
            policy_version_id=published.version_id,
            policy_version=published.version,
            order_line_id=command.order_line_id,
            proposed_at=proposed_at,
        )
        approval_id: UUID | None = None
        if outcome.requires_owner_approval:
            approval_id = self._request_owner_approval(
                connection,
                command,
                record=record,
                proposal_id=proposal_id,
                rendered_hash=document.snapshot_hash,
                requested_at=proposed_at,
            )
        status = (
            RemedyStatus.OWNER_APPROVAL_REQUIRED
            if outcome.requires_owner_approval
            else RemedyStatus.STAFF_AUTHORIZED
        )

        def mutation(cursor: Any) -> None:
            _insert_proposal(
                cursor,
                proposal_id=proposal_id,
                command=command,
                record=record,
                published=published,
                status=status,
                amount_vnd=outcome.amount_vnd,
                direction=outcome.direction,
                ceiling_vnd=outcome.ceiling_vnd,
                window_opened_at=outcome.window_opened_at,
                window_closes_at=outcome.window_closes_at,
                proposal_hash=document.snapshot_hash,
                approval_id=approval_id,
                proposed_at=proposed_at,
            )
            _open_incident_review(cursor, command.incident_id, fault=command.store_fault_attested)

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="REMEDY_PROPOSAL",
                aggregate_id=proposal_id,
                aggregate_version=1,
                event_type=REMEDY_PROPOSAL_RECORDED,
                event_payload={
                    "incident_id": str(command.incident_id),
                    "order_id": str(record.order_id),
                    "kind": command.kind.value,
                    "status": status.value,
                    "amount_vnd": outcome.amount_vnd,
                    "ceiling_vnd": outcome.ceiling_vnd,
                    "policy_version": published.version,
                    "proposal_hash": document.snapshot_hash,
                },
                audit_action="REMEDY_PROPOSE",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "remedy.proposal_recorded.v1",
                        {"proposal_id": str(proposal_id), "kind": command.kind.value},
                        f"remedy:{proposal_id}:proposed",
                    ),
                ),
                occurred_at=proposed_at,
            ),
            mutation,
        )
        return StoredRemedyProposal(
            proposal_id=proposal_id,
            incident_id=command.incident_id,
            order_id=record.order_id,
            kind=command.kind,
            status=status,
            outcome=PolicyOutcome.ALLOW,
            proposal_hash=document.snapshot_hash,
            policy_version_id=published.version_id,
            policy_version=published.version,
            amount_vnd=outcome.amount_vnd,
            ceiling_vnd=outcome.ceiling_vnd,
            window_opened_at=outcome.window_opened_at,
            window_closes_at=outcome.window_closes_at,
            approval_id=approval_id,
        )

    def _record_unresolved(
        self,
        connection: Any,
        command: RemedyProposalCommand,
        *,
        record: _OrderRecord,
        published: PublishedRemedyPolicy,
        proposal_id: UUID,
        outcome: RemedyUnresolved,
        proposed_at: datetime,
    ) -> StoredRemedyProposal:
        """Write down a loss and stop. `DEC-004` carries loss forward as not yet decided.

        The record is the point. The alternative -- refusing the request outright -- would leave the
        shop exactly where it was before this item: a customer says a garment is missing, nothing is
        written down, and the owner has nothing to decide the policy from when they come to decide
        it. So the complaint is stored, `POLICY_UNRESOLVED` is a status no update can leave, and no
        ceiling, window or amount is computed for it anywhere.
        """

        document = remedy_proposal_document(
            proposal_id=proposal_id,
            incident_id=command.incident_id,
            order_id=record.order_id,
            authorized=RemedyAuthorized(
                kind=RemedyKind.LOST_ITEM,
                amount_vnd=None,
                direction=None,
                ceiling_vnd=None,
                window_opened_at=None,
                window_closes_at=None,
                requires_owner_approval=False,
                outcome=PolicyOutcome.REQUIRE_HUMAN,
            ),
            policy_version_id=published.version_id,
            policy_version=published.version,
            order_line_id=None,
            proposed_at=proposed_at,
        )

        def mutation(cursor: Any) -> None:
            _insert_proposal(
                cursor,
                proposal_id=proposal_id,
                command=command,
                record=record,
                published=published,
                status=RemedyStatus.POLICY_UNRESOLVED,
                amount_vnd=None,
                direction=None,
                ceiling_vnd=None,
                window_opened_at=None,
                window_closes_at=None,
                proposal_hash=document.snapshot_hash,
                approval_id=None,
                proposed_at=proposed_at,
            )
            _open_incident_review(cursor, command.incident_id, fault=command.store_fault_attested)

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="REMEDY_PROPOSAL",
                aggregate_id=proposal_id,
                aggregate_version=1,
                event_type=REMEDY_PROPOSAL_RECORDED,
                event_payload={
                    "incident_id": str(command.incident_id),
                    "order_id": str(record.order_id),
                    "kind": RemedyKind.LOST_ITEM.value,
                    "status": RemedyStatus.POLICY_UNRESOLVED.value,
                    "outcome": PolicyOutcome.REQUIRE_HUMAN.value,
                    "reason_code": outcome.reason_code,
                },
                audit_action="REMEDY_PROPOSE",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "remedy.proposal_recorded.v1",
                        {"proposal_id": str(proposal_id), "reason_code": outcome.reason_code},
                        f"remedy:{proposal_id}:proposed",
                    ),
                ),
                occurred_at=proposed_at,
            ),
            mutation,
        )
        return StoredRemedyProposal(
            proposal_id=proposal_id,
            incident_id=command.incident_id,
            order_id=record.order_id,
            kind=RemedyKind.LOST_ITEM,
            status=RemedyStatus.POLICY_UNRESOLVED,
            outcome=PolicyOutcome.REQUIRE_HUMAN,
            proposal_hash=document.snapshot_hash,
            policy_version_id=published.version_id,
            policy_version=published.version,
            reason_code=outcome.reason_code,
        )

    def _request_owner_approval(
        self,
        connection: Any,
        command: RemedyProposalCommand,
        *,
        record: _OrderRecord,
        proposal_id: UUID,
        rendered_hash: str,
        requested_at: datetime,
    ) -> UUID:
        """Raise the `APPROVE_REMEDY` envelope `DEC-004` requires above the staff ceiling.

        `APPROVAL_POLICIES` is not touched: `APPROVE_REMEDY` already maps to `_OWNER_FINANCIAL` with
        `REMEDY_PROPOSAL`, which is owner policy and not this item's to edit. The resource type
        comes from `APPROVAL_RESOURCE_TYPES` and not a literal, because `build_approval_envelope`
        refuses any other value for this action and a literal would be a second place to get it
        wrong.

        `snapshot_hash` is the order's own current quote digest -- the priced evidence the ceiling
        was computed from -- and `rendered_hash` is the proposal document. Invariant 8: editing the
        proposal changes the rendered digest, so the envelope no longer matches and the edit has
        invalidated the approval without anybody writing a rule to say so.
        """

        stored = self._approvals.request(
            connection,
            ApprovalRequestCommand(
                ApprovalAction.APPROVE_REMEDY,
                APPROVAL_RESOURCE_TYPES[ApprovalAction.APPROVE_REMEDY],
                proposal_id,
                1,
                record.quote_snapshot_hash,
                rendered_hash,
                REMEDY_POLICY_VERSION,
                command.principal.staff_user_id,
                f"remedy-proposal:{proposal_id}",
                command.correlation_id,
                requested_at,
                store_id=command.store_id,
            ),
        )
        return stored.approval_request_id

    def execute(self, connection: Any, command: RemedyExecutionCommand) -> StoredRemedyExecution:
        """Carry out an authorised remedy: issue the credit, or command the rewash."""

        if not command.principal.roles & REMEDY_ROLES or not command.principal.mfa_verified:
            raise RemedyAuthorizationError(
                "executing a remedy requires an operations role with MFA"
            )
        executed_at = command.executed_at or datetime.now(UTC)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.store_id, p.incident_id, p.order_id, p.kind, p.status, p.amount_vnd,
                       p.approval_id, p.proposal_hash, p.policy_version_id, p.row_version,
                       o.bound_contact_id
                FROM remedy_proposals p
                JOIN orders o ON o.id = p.order_id
                WHERE p.id = %s
                FOR UPDATE OF p
                """,
                (command.proposal_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RemedyStateError(
                    "remedy proposal is missing", reason_code="REMEDY_PROPOSAL_NOT_FOUND"
                )
            store_id = _uuid(row[0])
            # Membership on the same cursor while the proposal row is locked, exactly as
            # `OrderRepository.transition` and `SettlementRepository.record` do. This route is keyed
            # by `proposal_id`, so a URL-shape enumeration would miss it for the same reason.
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=store_id,
                error=RemedyAuthorizationError,
            )
            binding = read_approval_binding(cursor, _uuid(row[6])) if row[6] is not None else None

        kind = RemedyKind(str(row[3]))
        status = RemedyStatus(str(row[4]))
        if kind is RemedyKind.LOST_ITEM or status is RemedyStatus.POLICY_UNRESOLVED:
            # The wall. A loss record can never be executed, and there is no amount on it to pay
            # even if somebody tried.
            raise RemedyStateError(
                "loss policy is not resolved, so no remedy may be paid against this record",
                reason_code=RemedyRefusal.LOSS_POLICY_UNRESOLVED.value,
                authority="DEC-004",
            )
        if status is RemedyStatus.EXECUTED:
            raise RemedyStateError(
                "this remedy has already been carried out", reason_code="REMEDY_ALREADY_EXECUTED"
            )
        if status is RemedyStatus.OWNER_APPROVAL_REQUIRED:
            _require_remedy_approval(
                binding,
                store_id=store_id,
                proposal_id=command.proposal_id,
                proposal_hash=str(row[7]),
                at=executed_at,
            )

        incident_id, order_id = _uuid(row[1]), _uuid(row[2])
        amount_vnd = None if row[5] is None else int(row[5])
        credit_id = uuid4() if kind is not RemedyKind.FREE_REWASH else None
        event_type = REWASH_COMMANDED if credit_id is None else CREDIT_EXECUTED

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                UPDATE remedy_proposals
                SET status = 'EXECUTED', executed_at = %s, row_version = row_version + 1
                WHERE id = %s AND row_version = %s AND status = %s
                RETURNING id
                """,
                (executed_at, command.proposal_id, int(row[9]), status.value),
            )
            if cursor.fetchone() is None:
                raise RemedyStateError(
                    "the remedy proposal changed while it was being carried out",
                    reason_code="STALE_VERSION",
                )
            if credit_id is not None:
                assert amount_vnd is not None
                cursor.execute(
                    """
                    INSERT INTO remedy_credits (
                        id, remedy_proposal_id, store_id, bearer_contact_id, issued_from_order_id,
                        amount_vnd, direction, policy_version_id, issued_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'CREDIT', %s, %s)
                    """,
                    (
                        credit_id,
                        command.proposal_id,
                        store_id,
                        # The bearer: the counter ticket or channel binding the order already
                        # carries. `DEC-015` refuses a customer record, so this is what a credit can
                        # be issued against, and presenting it is how the credit is redeemed.
                        _uuid(row[10]),
                        order_id,
                        amount_vnd,
                        _uuid(row[8]),
                        executed_at,
                    ),
                )
            # The incident reaches an outcome, which is the whole point of the item. `0014`'s two
            # flags have existed since the table did and nothing ever set them.
            cursor.execute(
                """
                UPDATE customer_incidents
                SET status = 'CLOSED', remedy_decided = TRUE
                WHERE id = %s AND status <> 'CLOSED'
                """,
                (incident_id,),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="REMEDY_PROPOSAL",
                aggregate_id=command.proposal_id,
                aggregate_version=int(row[9]) + 1,
                event_type=event_type,
                event_payload={
                    "incident_id": str(incident_id),
                    "order_id": str(order_id),
                    "kind": kind.value,
                    "amount_vnd": amount_vnd,
                    "credit_id": None if credit_id is None else str(credit_id),
                },
                audit_action="REMEDY_EXECUTE",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "remedy.executed.v1",
                        {"proposal_id": str(command.proposal_id), "event_type": event_type},
                        f"remedy:{command.proposal_id}:executed",
                    ),
                ),
                occurred_at=executed_at,
            ),
            mutation,
        )
        return StoredRemedyExecution(
            proposal_id=command.proposal_id,
            incident_id=incident_id,
            order_id=order_id,
            kind=kind,
            status=RemedyStatus.EXECUTED,
            event_type=event_type,
            credit_id=credit_id,
            amount_vnd=amount_vnd,
        )


@dataclass(frozen=True, slots=True)
class RemedyExecutionCommand:
    proposal_id: UUID
    principal: StaffPrincipal
    correlation_id: UUID
    executed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredRemedyExecution:
    proposal_id: UUID
    incident_id: UUID
    order_id: UUID
    kind: RemedyKind
    status: RemedyStatus
    #: `REWASH_COMMANDED` or `CREDIT_EXECUTED`, the names the incident eval already queries for.
    event_type: str
    credit_id: UUID | None
    amount_vnd: int | None


# --- redeeming a credit -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RemedyCreditRedemptionCommand:
    store_id: UUID
    quote_id: UUID
    credit_id: UUID
    expected_current_revision: int
    expected_snapshot_hash: str
    principal: StaffPrincipal
    correlation_id: UUID
    redeemed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredCreditRedemption:
    credit_id: UUID
    quote_id: UUID
    revision: int
    snapshot_hash: str
    credit_vnd: int
    net_service_subtotal_vnd: int
    display_total_vnd: int | None


class RemedyCreditRepository:
    """Spend one issued credit against one open quote revision, exactly once."""

    def redeem(
        self, connection: Any, command: RemedyCreditRedemptionCommand
    ) -> StoredCreditRedemption:
        """Derive a credited revision and burn the credit in the same transaction.

        Invariant 5, and the reason `QuoteRepository.create_revision` grew an `also_mutate` hook:
        writing the discount without burning the credit lets one voucher be spent twice, and burning
        it without the discount robs the customer. Neither is a state this counter can recover from
        by hand, so they commit together or not at all.

        The credit is a **bearer instrument** and is deliberately not matched against the new
        quote's contact binding. A returning walk-in is issued a fresh counter ticket, so requiring
        the bindings to agree would make every credit unredeemable -- `DEC-015` refuses the customer
        record that would let two visits be linked. What is checked is the store: a credit issued by
        one shop cannot be spent at another.
        """

        if not command.principal.roles & REMEDY_ROLES or not command.principal.mfa_verified:
            raise RemedyAuthorizationError(
                "redeeming a credit requires an operations role with MFA"
            )
        redeemed_at = command.redeemed_at or datetime.now(UTC)

        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=RemedyAuthorizationError,
            )
            cursor.execute(
                """
                SELECT amount_vnd, policy_version_id, redeemed_at, row_version, store_id
                FROM remedy_credits WHERE id = %s AND store_id = %s
                FOR UPDATE
                """,
                (command.credit_id, command.store_id),
            )
            credit_row = cursor.fetchone()
            if credit_row is None:
                # A credit of another store and a credit that does not exist are one answer, the
                # rule `store_access` states: probing identifiers teaches nobody which stores exist.
                raise RemedyStateError(
                    "remedy credit is missing or not this store's",
                    reason_code="REMEDY_CREDIT_NOT_FOUND",
                )
            if credit_row[2] is not None:
                raise RemedyStateError(
                    "this credit has already been redeemed",
                    reason_code=RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED.value,
                    authority="DEC-015",
                )
            container = QuoteRepository.find_container_by_id(
                cursor, command.store_id, command.quote_id
            )
            if container is None or container.lifecycle != "OPEN":
                raise QuoteStateError("quote is missing, closed, or not in this store")
            if container.current_revision != command.expected_current_revision:
                raise QuoteStateError("quote moved since it was read; read it again")
            stored = QuoteRepository.get_revision(
                cursor, command.quote_id, container.current_revision
            )
            if stored is None:
                raise QuoteStateError("quote revision is missing")
            if not hmac.compare_digest(
                stored.document.snapshot_hash, command.expected_snapshot_hash
            ):
                raise QuoteStateError("quote content changed since it was read")

        priced = parse_quote_revision(json.loads(stored.document.canonical_json))
        composition = redeem_remedy_credit(
            priced=priced,
            revision=priced.data.revision + 1,
            credit=RemedyCredit(
                credit_id=command.credit_id,
                amount_vnd=int(credit_row[0]),
                policy_version_id=_uuid(credit_row[1]),
            ),
        )
        if not isinstance(composition, ComposedQuote):
            # Refused before anything is written, exactly as the other quote commands refuse: a
            # credit that cannot be allocated stays owed in full rather than being capped to fit.
            reason = composition.reason_codes[0]
            raise RemedyStateError(
                "this credit cannot be applied to this quote",
                reason_code=reason,
                ceiling_vnd=priced.data.totals.net_service_subtotal_min_vnd,
            )
        snapshot = composition.snapshot
        expected_version = int(credit_row[3])

        def burn(cursor: Any) -> None:
            cursor.execute(
                """
                UPDATE remedy_credits
                SET redeemed_at = %s, redeemed_quote_id = %s, redeemed_quote_revision = %s,
                    row_version = row_version + 1
                WHERE id = %s AND row_version = %s AND redeemed_at IS NULL
                RETURNING id
                """,
                (
                    redeemed_at,
                    snapshot.data.quote_id,
                    snapshot.data.revision,
                    command.credit_id,
                    expected_version,
                ),
            )
            if cursor.fetchone() is None:
                # The compare-and-swap that makes "exactly once" true under concurrency. The row
                # lock above serialises two redemptions; this refuses the loser after it wakes up.
                raise RemedyStateError(
                    "this credit has already been redeemed",
                    reason_code=RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED.value,
                    authority="DEC-015",
                )

        QuoteRepository().create_revision(
            connection,
            QuoteRevisionCommand(
                store_id=command.store_id,
                bound_order_request_id=container.bound_order_request_id,
                snapshot=snapshot,
                expected_current_revision=priced.data.revision,
                expected_row_version=container.row_version,
                created_by=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                occurred_at=redeemed_at,
            ),
            also_mutate=burn,
        )
        return StoredCreditRedemption(
            credit_id=command.credit_id,
            quote_id=snapshot.data.quote_id,
            revision=snapshot.data.revision,
            snapshot_hash=snapshot.document.snapshot_hash,
            credit_vnd=int(credit_row[0]),
            net_service_subtotal_vnd=snapshot.data.totals.net_service_subtotal_min_vnd,
            display_total_vnd=snapshot.data.totals.display_total_min_vnd,
        )


# --- reading the order's recorded facts ----------------------------------------------------------


def _read_order_for_incident(cursor: Any, *, store_id: UUID, incident_id: UUID) -> _OrderRecord:
    """The order an incident names, with every fact a remedy decision needs, read in one place.

    The incident is located by `(id, store_id)` so another store's incident and one that does not
    exist are the same answer. An incident with no order -- the agent correction path opens those --
    has no priced lines, no handover and no settlement, so there is nothing a `DEC-004` figure could
    be computed from and it is refused rather than partially answered.
    """

    cursor.execute(
        """
        SELECT o.id, o.store_id, o.bound_contact_id, o.fulfillment_mode, o.production_released_at,
               o.current_quote_snapshot_hash, r.snapshot, s.paid_amount_vnd,
               (
                   SELECT dl.recorded_at FROM delivery_legs dl
                   WHERE dl.order_id = o.id AND dl.leg_kind = 'RETURN' AND dl.outcome = 'SUCCEEDED'
                   ORDER BY dl.recorded_at
                   LIMIT 1
               ) AS returned_at
        FROM customer_incidents i
        JOIN orders o ON o.id = i.order_id
        JOIN quote_revisions r
          ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
        LEFT JOIN order_settlements s ON s.order_id = o.id
        WHERE i.id = %s AND i.store_id = %s
        """,
        (incident_id, store_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise RemedyStateError(
            "the incident is missing, not this store's, or names no order",
            reason_code="REMEDY_INCIDENT_NOT_FOUND",
        )
    mode = FulfillmentMode(str(row[3]))
    expects_return = mode in _MODES_EXPECTING_RETURN
    delivered_at = _optional_datetime(row[8])
    return _OrderRecord(
        order_id=_uuid(row[0]),
        store_id=_uuid(row[1]),
        bound_contact_id=_uuid(row[2]),
        quote_snapshot_hash=str(row[5]),
        facts=RemedyOrderFacts(
            # One timestamp, two sources, and which one applies is the order's own fulfilment mode.
            # A delivered order's handover is the succeeded `RETURN` leg; a counter collection's is
            # the moment production recorded `RELEASED`. Reading the wrong one would measure the
            # window from an event the customer was not present at.
            goods_returned_at=delivered_at if expects_return else _optional_datetime(row[4]),
            settled_total_vnd=None if row[7] is None else int(row[7]),
            line_amounts_vnd=_line_amounts(row[6]),
            expects_return_leg=expects_return,
            return_leg_succeeded=delivered_at is not None,
        ),
    )


def _line_amounts(snapshot: object) -> dict[str, int]:
    """What the shop charged for each line of the order's current revision.

    The **net** amount, not the list amount: `DEC-004` caps compensation at a multiple of "what the
    store charged", and a line sold with a discount was charged at its net. Read from the stored
    immutable snapshot through the domain's own parser, so a revision this system cannot parse
    produces no ceilings rather than a plausible wrong one.
    """

    if not isinstance(snapshot, Mapping):
        raise RemedyStateError(
            "the order's stored quote revision is unreadable",
            reason_code="REMEDY_ORDER_REVISION_UNREADABLE",
        )
    revision = parse_quote_revision(snapshot)
    return {
        line.line_id: line.amounts.net_amount_vnd
        for line in revision.data.lines
        if isinstance(line.amounts, ExactLineAmounts)
    }


def _window_close(
    facts: RemedyOrderFacts, *, days: int | None = None, hours: int | None = None
) -> datetime | None:
    """When a window measured from the recorded handover closes, or `None` if there is no handover.

    The arithmetic lives in the domain for a proposal; this is the console read, and it must agree
    with it. It does, because both add the same published figure to the same recorded timestamp --
    there is no second rule here, only the same one asked ahead of time.
    """

    if facts.goods_returned_at is None:
        return None
    return facts.goods_returned_at + timedelta(days=days or 0, hours=hours or 0)


def _require_remedy_approval(
    binding: Any,
    *,
    store_id: UUID,
    proposal_id: UUID,
    proposal_hash: str,
    at: datetime,
) -> None:
    """Prove the envelope in hand is an approved, unexpired `APPROVE_REMEDY` for *this* proposal.

    Five separate facts, each named, because a single "not approved" would hide which one failed
    from the person holding the phone. The rendered-hash comparison is invariant 8: the owner
    approved a digest, and the only way to act on that approval is to still hold the content it
    names.
    """

    if binding is None:
        raise RemedyStateError(
            "this remedy needs the owner's approval and has no envelope",
            reason_code="REMEDY_APPROVAL_REQUIRED",
            authority="DEC-004",
        )
    if binding.store_id != store_id or binding.action is not ApprovalAction.APPROVE_REMEDY:
        raise RemedyStateError(
            "the approval does not authorise a remedy in this store",
            reason_code="REMEDY_APPROVAL_NOT_BOUND",
        )
    if binding.resource_id != proposal_id or binding.resource_version != 1:
        raise RemedyStateError(
            "the approval names a different remedy proposal",
            reason_code="REMEDY_APPROVAL_NOT_BOUND",
        )
    if not hmac.compare_digest(binding.rendered_hash, proposal_hash):
        raise RemedyStateError(
            "the approval binds different content than this proposal",
            reason_code="REMEDY_APPROVAL_NOT_BOUND",
        )
    if binding.status != "APPROVED":
        raise RemedyStateError(
            "the owner has not approved this remedy",
            reason_code="REMEDY_APPROVAL_REQUIRED",
            authority="DEC-004",
        )
    if at >= binding.expires_at:
        raise RemedyStateError(
            "the owner's approval of this remedy has expired",
            reason_code="REMEDY_APPROVAL_EXPIRED",
        )


def _insert_proposal(
    cursor: Any,
    *,
    proposal_id: UUID,
    command: RemedyProposalCommand,
    record: _OrderRecord,
    published: PublishedRemedyPolicy,
    status: RemedyStatus,
    amount_vnd: int | None,
    direction: AdjustmentDirection | None,
    ceiling_vnd: int | None,
    window_opened_at: datetime | None,
    window_closes_at: datetime | None,
    proposal_hash: str,
    approval_id: UUID | None,
    proposed_at: datetime,
) -> None:
    cursor.execute(
        """
        INSERT INTO remedy_proposals (
            id, store_id, incident_id, order_id, kind, status, amount_vnd, direction, ceiling_vnd,
            order_line_id, policy_version_id, policy_version, store_fault_attested,
            attested_late_by_minutes, window_opened_at, window_closes_at, proposal_hash,
            approval_id, proposed_by, proposed_at, correlation_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            proposal_id,
            command.store_id,
            command.incident_id,
            record.order_id,
            command.kind.value,
            status.value,
            amount_vnd,
            None if direction is None else direction.value,
            ceiling_vnd,
            command.order_line_id,
            published.version_id,
            published.version,
            command.store_fault_attested,
            command.attested_late_by_minutes,
            window_opened_at,
            window_closes_at,
            proposal_hash,
            approval_id,
            command.principal.staff_user_id,
            proposed_at,
            command.correlation_id,
        ),
    )


def _open_incident_review(cursor: Any, incident_id: UUID, *, fault: bool) -> None:
    """Move the incident onto the ladder `0014` drew and set the flag `0039` left writable.

    `fault_decided` records that a named staff member made the store-fault determination `DEC-004`
    asks for -- whichever way it went. It is not "the store was at fault"; that is
    `store_fault_attested` on the proposal. The distinction matters for the same reason
    `AcquisitionSource.UNKNOWN` exists: a decision recorded as not-taken is different from one
    recorded as no.
    """

    cursor.execute(
        """
        UPDATE customer_incidents
        SET status = 'UNDER_REVIEW', fault_decided = %s
        WHERE id = %s AND status = 'OPEN'
        """,
        (fault, incident_id),
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RemedyStateError(
            "a stored order timestamp is not timezone-aware",
            reason_code="REMEDY_ORDER_TIMESTAMP_INVALID",
        )
    return value


__all__ = [
    "CREDIT_EXECUTED",
    "REMEDY_CREDIT_REDEEMED",
    "REMEDY_PROPOSAL_RECORDED",
    "REMEDY_ROLES",
    "REWASH_COMMANDED",
    "PublishedRemedyPolicy",
    "RemedyAuthorizationError",
    "RemedyCreditRedemptionCommand",
    "RemedyCreditRepository",
    "RemedyExecutionCommand",
    "RemedyOptions",
    "RemedyProposalCommand",
    "RemedyProposalRepository",
    "RemedyStateError",
    "StoredCreditRedemption",
    "StoredRemedyExecution",
    "StoredRemedyProposal",
    "publish_remedy_policy",
    "read_published_remedy_policy",
    "validate_remedy_policy",
]
