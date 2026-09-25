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
between them is an approval envelope nobody ever decides, which expires on its own at the end of the
next business day (the DEC-031 addendum's window for `APPROVE_REMEDY`) and is visible in the audit
trail -- an inert record, not an authorisation.

**Which garment (`REMEDY-GARMENT-001`).** A damage or loss claim on a line priced per piece names
its garment, and the staff limit and the ceiling are cumulative per (line, garment). The committed
totals are read grouped by the garment each proposal named, and handed to the domain, which owns
the rule for a proposal written before migration `0053` named any: it counts against every garment.
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
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    redeem_remedy_credit,
    reserved_remedy_credits,
)
from nha_trang_laundry_domain.quotes import (
    ConfigurationSnapshotReference,
    ImmutableQuoteSnapshot,
    parse_quote_revision,
)
from nha_trang_laundry_domain.remedies import (
    REMEDY_POLICY_CONFIG_TYPE,
    REMEDY_POLICY_VERSION,
    ItemCompensationTerms,
    RemedyAuthorized,
    RemedyCommitments,
    RemedyCredit,
    RemedyKind,
    RemedyOrderFacts,
    RemedyPolicy,
    RemedyPolicyError,
    RemedyRefusal,
    RemedyRefused,
    RemedyRequest,
    RemedyStatus,
    committed_against_any_garment,
    committed_against_garment,
    evaluate_remedy,
    item_compensation_terms,
    parse_remedy_policy,
    remedy_line_facts,
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
        committed_vnd: int | None = None,
        garments: int | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.authority = authority
        self.ceiling_vnd = ceiling_vnd
        self.window_closes_at = window_closes_at
        self.threshold_minutes = threshold_minutes
        self.committed_vnd = committed_vnd
        #: How many garments the line holds, when the claim named none or one it does not have.
        self.garments = garments
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
    #: `REMEDY-GARMENT-001`: the garment's 1-based position within the line's quantity. Required on
    #: a line of several garments priced per piece; refused where no garment has a fee of its own.
    garment_index: int | None = None


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
    #: Was `LOSS_POLICY_UNRESOLVED` for a loss before `DEC-031`. No proposal is recorded in that
    #: shape any more, so this is `None`; the field stays because the response contract carries it.
    reason_code: str | None = None
    #: `OwnerReason` values, in order, when the owner is needed; empty when staff may authorise.
    #: What lets the counter say *why* a 20.000 d loss waits for the owner.
    owner_reasons: tuple[str, ...] = ()
    #: The garment the claim was recorded against (`REMEDY-GARMENT-001`); `None` for a line with no
    #: garment identity and for every kind that is not about one item.
    garment_index: int | None = None


@dataclass(frozen=True, slots=True)
class RemedyLineOption:
    """One line a damage or loss claim may name, with the terms `propose` will decide it on.

    `DEC-031` and the staging review: the counter picked "line-0" from a list of bare identifiers
    and could not see what the item already carried, so the form said "staff can approve" and the
    server answered "needs the owner". Every figure here comes from
    `domain.remedies.item_compensation_terms` and the same committed-total read `propose` uses.
    """

    line_id: str
    service_code: str
    #: The display name in the pricebook version the order was priced under, or `None` when that
    #: version cannot be read or does not carry the code. Never today's catalogue.
    service_name: str | None
    unit: str
    quantity: str
    #: `ItemFeeBasis`: `UNIT` (one piece), `BAG` (weight), or `NOT_RECORDED` (owner decides).
    item_fee_basis: str
    item_fee_vnd: int
    #: One item's ceiling: the most a single proposal may ask for.
    ceiling_vnd: int
    #: How many items the line counts, and what they may carry together (per-item ceiling x pieces,
    #: never above 5x the line's charge). Equal to `ceiling_vnd` for a bag or a single piece.
    pieces: int
    line_ceiling_vnd: int
    #: What live or paid damage and loss proposals already committed against this line.
    committed_vnd: int
    #: `OwnerReason` values that send *every* damage amount on this line to the owner. A loss adds
    #: `LOSS_CLAIM` on top, whatever this says.
    owner_always: tuple[str, ...]
    #: `REMEDY-GARMENT-001`: how many garments on the line have a fee of their own, or `None` when
    #: none does (a bag, or a fee nobody recorded) and the line is one claimable whole.
    garments: int | None = None
    #: Per garment, 1..`garments` in order: what already counts against it -- proposals naming it
    #: plus every line-level one. The number the staff limit and the item ceiling compare against.
    #: Empty when `garments` is `None`.
    garment_committed_vnd: tuple[int, ...] = ()
    #: What proposals recorded before garments could be named (migration `0053`) hold on this line.
    #: Already inside every figure of `garment_committed_vnd`; shown so staff can see why.
    line_level_committed_vnd: int = 0


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
    #: The 5x cap per priced line of the order's own revision, keyed by `line_id`. Kept for callers
    #: that read it; `damage_lines` carries the same ceilings with what the counter needs besides.
    damage_line_ceilings_vnd: Mapping[str, int] | None = None
    #: Per damageable line: service, fee basis, ceiling, what it already carries. `DEC-031`.
    damage_lines: tuple[RemedyLineOption, ...] | None = None
    #: The 10% the server computed, or `None` when the order records no settled total, no
    #: succeeded return leg, or already has a late-delivery credit proposed or paid -- in which case
    #: the credit is not available and the form must say so.
    late_delivery_credit_vnd: int | None = None
    late_delivery_threshold_minutes: int | None = None
    #: `DEC-031` rule 2, stated by the server rather than assumed by the form: a loss always needs
    #: the owner. Replaces `loss_reason_code`, which told the form to render loss as unsupported.
    loss_requires_owner: bool = True
    #: `DEC-031` rule 3: the order's money was refunded, so every compensation needs the owner.
    order_refunded: bool = False


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
    #: The pricebook version the order's revision was priced under, for display names only.
    pricebook: ConfigurationSnapshotReference | None
    #: The incident's status when it was read, unlocked. The pre-check refuses on it so a closed
    #: incident is answered before an approval envelope is raised; the binding check is repeated
    #: under the row lock in the proposal's own transaction. Empty when read by order.
    incident_status: str


#: The incident states a remedy may still be proposed against. `0014`'s ladder is
#: `OPEN -> UNDER_REVIEW -> CLOSED`, and `execute` writes `CLOSED` when a remedy reaches its
#: outcome: an incident that has an outcome is not a place to hang another one.
_PROPOSABLE_INCIDENT_STATUSES = frozenset({"OPEN", "UNDER_REVIEW"})

#: The `approval_request_states` a proposal waiting on the owner can never leave, other than
#: approval. `0008`'s transition guard admits no way out of any of them, and `execute` requires an
#: `APPROVED` envelope, so a proposal whose envelope is in one of these can never pay. These are the
#: "terminal non-paying" states: `RemedyStatus` itself has no such member.
_DEAD_ENVELOPE_STATUSES = ("REJECTED", "EXPIRED", "CANCELLED")


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
                incident_id=incident_id,
                order_id=record.order_id,
                policy_published=False,
                order_refunded=record.facts.refunded,
            )
        # Only the order-level count matters to the probe below: a late-delivery credit already
        # proposed or paid for this order means there is no second one to offer.
        prior = _read_commitments(cursor, order_id=record.order_id, order_line_id=None)
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
                committed=prior,
            )
            credit = probe.amount_vnd if isinstance(probe, RemedyAuthorized) else None
        # Per line, the terms `evaluate_remedy` will apply -- the same domain function, the same
        # committed-total filter -- so the form's "staff can approve" is the server's answer.
        committed_by_line = _read_line_commitments(cursor, order_id=record.order_id)
        names = _service_names(cursor, record.pricebook)
        lines: list[RemedyLineOption] = []
        for line_id in sorted(facts.lines):
            terms = item_compensation_terms(policy, facts, line_id)
            assert terms is not None
            line = facts.lines[line_id]
            by_garment = committed_by_line.get(line_id, {})
            lines.append(
                RemedyLineOption(
                    line_id=line_id,
                    service_code=line.service_code,
                    service_name=names.get(line.service_code),
                    unit=line.unit.value,
                    quantity=line.quantity,
                    item_fee_basis=terms.basis.value,
                    item_fee_vnd=terms.item_fee_vnd,
                    ceiling_vnd=terms.ceiling_vnd,
                    pieces=terms.pieces,
                    line_ceiling_vnd=terms.line_ceiling_vnd,
                    committed_vnd=sum(by_garment.values()),
                    owner_always=tuple(reason.value for reason in terms.owner_always),
                    garments=terms.garments,
                    garment_committed_vnd=_per_garment(terms, by_garment),
                    line_level_committed_vnd=by_garment.get(None, 0),
                )
            )
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
            damage_line_ceilings_vnd={line.line_id: line.ceiling_vnd for line in lines},
            damage_lines=tuple(lines),
            late_delivery_credit_vnd=credit,
            late_delivery_threshold_minutes=policy.late_delivery_threshold_minutes,
            order_refunded=facts.refunded,
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
            committed = _read_commitments(
                cursor,
                order_id=record.order_id,
                order_line_id=command.order_line_id,
                garment_index=command.garment_index,
            )

        # Before the policy and before any envelope: an incident that already reached its outcome
        # is answered first, whatever the figures would have said. Re-checked under the lock below.
        _require_proposable_incident(record.incident_status)
        if published is None:
            # Invariant 11, and it applies to every kind including the ones that move no money: the
            # 7-day rewash window is itself one of the owner's published figures.
            raise RemedyStateError(
                "no remedy policy is published, so no remedy may be authorised",
                reason_code=RemedyRefusal.REMEDY_POLICY_UNPUBLISHED.value,
                authority="INVARIANT-11",
            )

        request = RemedyRequest(
            kind=command.kind,
            store_fault_attested=command.store_fault_attested,
            order_line_id=command.order_line_id,
            amount_vnd=command.amount_vnd,
            attested_late_by_minutes=command.attested_late_by_minutes,
            garment_index=command.garment_index,
        )

        policy = published.policy

        def decide(prior: RemedyCommitments) -> Any:
            return evaluate_remedy(
                policy=policy,
                facts=record.facts,
                request=request,
                requested_at=proposed_at,
                committed=prior,
            )

        outcome = decide(committed)
        if isinstance(outcome, RemedyRefused):
            raise _refusal_error(outcome)

        proposal_id = uuid4()
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
            # Rule 4: the committed total that binds is the one read under the order-row lock, in
            # this transaction. The pre-check above could not see a proposal another counter was
            # writing at the same moment; this read can, because that writer holds the same lock
            # until it commits, and this one waits for it.
            locked_status = _lock_proposable_incident(
                cursor, order_id=record.order_id, incident_id=command.incident_id
            )
            locked = _read_commitments(
                cursor,
                order_id=record.order_id,
                order_line_id=command.order_line_id,
                garment_index=command.garment_index,
            )
            if locked != committed:
                recheck = decide(locked)
                if isinstance(recheck, RemedyRefused):
                    raise _refusal_error(recheck)
                if recheck != outcome:
                    # Still allowable, but no longer on the terms the pre-check decided -- most
                    # often a staff authorisation that now needs the owner, whose envelope was not
                    # raised. Refused rather than upgraded here: the proposal is recorded exactly as
                    # it was decided or not at all, and proposing again decides it afresh.
                    raise RemedyStateError(
                        "another remedy was recorded against this order while yours was being "
                        "checked; propose it again",
                        reason_code="STALE_VERSION",
                    )
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
                # The garment the domain resolved, not the one typed: on a line of one garment an
                # unnamed claim is garment 1, and the row says so.
                garment_index=outcome.garment_index,
            )
            _open_incident_review(
                cursor,
                command.incident_id,
                fault=command.store_fault_attested,
                locked_status=locked_status,
            )

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
                    "item_fee_basis": (
                        None if outcome.item_fee_basis is None else outcome.item_fee_basis.value
                    ),
                    "owner_reasons": [reason.value for reason in outcome.owner_reasons],
                    "order_line_id": command.order_line_id,
                    "garment_index": outcome.garment_index,
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
            owner_reasons=tuple(reason.value for reason in outcome.owner_reasons),
            garment_index=outcome.garment_index,
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
        """Raise the `APPROVE_REMEDY` envelope a proposal with any `OwnerReason` needs.

        `DEC-004` requires it above the staff ceiling; `DEC-031` adds every loss, every
        compensation on a refunded order, and every claim on an item whose fee was never recorded.

        Who may decide it and for how long is `APPROVAL_POLICIES`, not this module: the owner, MFA
        and separation of duty as `_OWNER_FINANCIAL`, open until the end of the next business day
        in Asia/Ho_Chi_Minh since the DEC-031 addendum (`_OWNER_REMEDY`). The resource type
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
                       o.bound_contact_id, p.order_line_id, p.ceiling_vnd, p.garment_index
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
        if status is RemedyStatus.POLICY_UNRESOLVED:
            # The wall, for the loss records written before `DEC-031`. They carry no amount and no
            # envelope, and nothing may pay them; a new loss proposal goes to the owner instead.
            raise RemedyStateError(
                "loss policy is not resolved, so no remedy may be paid against this record",
                reason_code=RemedyRefusal.LOSS_POLICY_UNRESOLVED.value,
                authority="DEC-004",
            )
        if kind is RemedyKind.LOST_ITEM and row[6] is None:
            # `DEC-031` rule 2, in code as well as in migration 0051: a loss without the owner's
            # envelope is not a loss anybody may pay.
            raise RemedyStateError(
                "a loss is paid only with the owner's approval",
                reason_code="REMEDY_APPROVAL_REQUIRED",
                authority="DEC-031",
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
            _recheck_before_paying(
                cursor,
                proposal_id=command.proposal_id,
                order_id=order_id,
                kind=kind,
                amount_vnd=amount_vnd,
                order_line_id=None if row[11] is None else str(row[11]),
                ceiling_vnd=None if row[12] is None else int(row[12]),
                garment_index=None if row[13] is None else int(row[13]),
                staff_authorized=row[6] is None,
                policy_version_id=_uuid(row[8]),
            )
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
    """Reserve one issued credit on an open quote; it is spent when that quote becomes an order."""

    def redeem(
        self, connection: Any, command: RemedyCreditRedemptionCommand
    ) -> StoredCreditRedemption:
        """Derive a credited revision that **reserves** the credit. Nothing about the credit moves.

        Corrected by the credit-lifecycle fix. This used to burn the credit -- set `redeemed_at` --
        in the transaction that wrote the credited revision, so everything that happened to the
        quote afterwards lost it: re-pricing composed a revision without the credit, an expired
        quote could not be accepted, an abandoned one was never converted, and `0042` rightly
        forbids handing a spent credit back. Now the revision's `REMEDY_CREDIT` adjustment is the
        reservation, a reprice carries it forward (`compose_quote_revision`), and
        `spend_reserved_remedy_credits` spends it inside `OrderRepository.create`. A credit reserved
        on two quotes is spent by whichever becomes an order first; the other conversion is refused.

        What is still checked here, under the credit's row lock and again inside the revision's own
        transaction: the credit exists in this store and has not been spent. Reserving a spent
        credit would put a discount on a bill that no order could ever honour.

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
                # What was left to discount, for the one refusal where that is the answer.
                ceiling_vnd=(
                    priced.data.totals.net_service_subtotal_min_vnd
                    if reason == RemedyRefusal.REMEDY_CREDIT_UNALLOCATABLE.value
                    else None
                ),
            )
        snapshot = composition.snapshot

        def still_unspent(cursor: Any) -> None:
            # Re-read under the row lock inside the revision's own transaction. The read above may
            # have run in autocommit, where its lock ended with the statement; an order that spent
            # the credit in between must not be followed by a reservation of it.
            cursor.execute(
                "SELECT redeemed_at FROM remedy_credits WHERE id = %s FOR UPDATE",
                (command.credit_id,),
            )
            row = cursor.fetchone()
            if row is None or row[0] is not None:
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
            also_mutate=still_unspent,
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


@dataclass(frozen=True, slots=True)
class ReservedRemedyCredits:
    """What a quote revision reserves, and which of those another order has already spent.

    The two inputs `compose_quote_revision` needs to re-price a credited quote honestly: carry the
    first, release the second.
    """

    credits: tuple[RemedyCredit, ...] = ()
    spent_ids: frozenset[UUID] = frozenset()


def read_reserved_remedy_credits(
    cursor: Any, *, store_id: UUID, quote_id: UUID, revision: int
) -> ReservedRemedyCredits:
    """The credits one stored revision reserves, for the caller about to re-price it.

    Both composing callers -- the counter's `create_quote` and the agent estimate tool -- read the
    revision they are replacing through this, so there is one answer to "what does this bill
    carry". A quote that is not this store's, or a revision that does not exist, reserves nothing
    here; the write that follows refuses it for its own reasons. A credit row that is missing or not
    this store's is treated as spent: it could not be spent by an order in this store, so it must
    not ride on one of its bills.
    """

    container = QuoteRepository.find_container_by_id(cursor, store_id, quote_id)
    if container is None:
        return ReservedRemedyCredits()
    stored = QuoteRepository.get_revision(cursor, quote_id, revision)
    if stored is None:
        return ReservedRemedyCredits()
    credits = reserved_remedy_credits(
        parse_quote_revision(json.loads(stored.document.canonical_json))
    )
    if not credits:
        return ReservedRemedyCredits()
    cursor.execute(
        """
        SELECT id FROM remedy_credits
        WHERE id = ANY(%s) AND store_id = %s AND redeemed_at IS NULL
        """,
        ([credit.credit_id for credit in credits], store_id),
    )
    unspent = {_uuid(row[0]) for row in cursor.fetchall()}
    return ReservedRemedyCredits(
        credits=credits,
        spent_ids=frozenset(c.credit_id for c in credits if c.credit_id not in unspent),
    )


def spend_reserved_remedy_credits(
    connection: Any,
    *,
    store_id: UUID,
    quote_id: UUID,
    revision: int,
    order_id: UUID,
    actor_id: UUID,
    correlation_id: UUID,
    occurred_at: datetime,
) -> tuple[UUID, ...]:
    """Spend every credit the order's accepted revision reserves. Called inside order creation.

    This is the moment the credit-lifecycle fix moved the spend to: the credit leaves the
    customer's hands when the bill it discounts becomes an order, and not before. It runs inside
    `OrderRepository.create`'s transaction, so the order, the spend, and each spend's own domain
    event, audit row and outbox row commit together or not at all (invariant 5); a nested
    `commit_material_change` is a savepoint inside that transaction.

    Each credit is locked `FOR UPDATE` in id order -- a fixed order, so two orders spending
    overlapping credits cannot deadlock -- and spent by the compare-and-swap `0042`'s trigger
    already expects: `redeemed_at` from NULL to set, once, naming this quote revision. A credit that
    is already spent, belongs to another store, or no longer matches the amount the revision took
    off is refused with `RemedyStateError`, which rolls the whole order back. The first order to
    spend a credit reserved on two quotes wins; the second is told why.
    """

    with connection.cursor() as cursor:
        stored = QuoteRepository.get_revision(cursor, quote_id, revision)
    if stored is None:
        return ()
    credits = reserved_remedy_credits(
        parse_quote_revision(json.loads(stored.document.canonical_json))
    )
    spent: list[UUID] = []
    for credit in sorted(credits, key=lambda item: item.credit_id):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT store_id, amount_vnd, policy_version_id, redeemed_at, row_version
                FROM remedy_credits WHERE id = %s
                FOR UPDATE
                """,
                (credit.credit_id,),
            )
            row = cursor.fetchone()
        if row is None or _uuid(row[0]) != store_id:
            raise RemedyStateError(
                "the quote carries a remedy credit this store never issued",
                reason_code="REMEDY_CREDIT_NOT_FOUND",
            )
        if row[3] is not None:
            raise RemedyStateError(
                "this quote carries a remedy credit that another order has already spent; price "
                "the bag again and the credit will be released from it",
                reason_code=RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED.value,
                authority="DEC-015",
            )
        if int(row[1]) != credit.amount_vnd or _uuid(row[2]) != credit.policy_version_id:
            # Unreachable while credits land whole: the adjustment is always the full face value
            # under the credit's own policy version. Refused rather than spent if it ever is not.
            raise RemedyStateError(
                "the quote's credit does not match the credit that was issued",
                reason_code="REMEDY_CREDIT_NOT_FOUND",
            )
        version = int(row[4])

        def spend(cursor: Any, credit_id: UUID = credit.credit_id, version: int = version) -> None:
            cursor.execute(
                """
                UPDATE remedy_credits
                SET redeemed_at = %s, redeemed_quote_id = %s, redeemed_quote_revision = %s,
                    row_version = row_version + 1
                WHERE id = %s AND row_version = %s AND redeemed_at IS NULL
                RETURNING id
                """,
                (occurred_at, quote_id, revision, credit_id, version),
            )
            if cursor.fetchone() is None:
                raise RemedyStateError(
                    "this credit has already been redeemed",
                    reason_code=RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED.value,
                    authority="DEC-015",
                )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="REMEDY_CREDIT",
                aggregate_id=credit.credit_id,
                aggregate_version=version + 1,
                event_type=REMEDY_CREDIT_REDEEMED,
                event_payload={
                    "order_id": str(order_id),
                    "quote_id": str(quote_id),
                    "quote_revision": revision,
                    "amount_vnd": credit.amount_vnd,
                },
                audit_action="REMEDY_CREDIT_SPEND",
                actor_type="STAFF",
                actor_id=actor_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "remedy.credit_redeemed.v1",
                        {"credit_id": str(credit.credit_id), "order_id": str(order_id)},
                        f"remedy-credit:{credit.credit_id}:redeemed",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            spend,
        )
        spent.append(credit.credit_id)
    return tuple(spent)


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
        SELECT i.order_id, i.status FROM customer_incidents i
        WHERE i.id = %s AND i.store_id = %s AND i.order_id IS NOT NULL
        """,
        (incident_id, store_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise RemedyStateError(
            "the incident is missing, not this store's, or names no order",
            reason_code="REMEDY_INCIDENT_NOT_FOUND",
        )
    return _read_order_record(cursor, order_id=_uuid(row[0]), incident_status=str(row[1]))


def _read_order_record(cursor: Any, *, order_id: UUID, incident_status: str = "") -> _OrderRecord:
    """One order's stored facts, by order. `propose`, the options read and `execute` all use it.

    `execute` re-reads through this rather than trusting what `propose` recorded, because two facts
    here can change after a proposal is written: the order can be refunded, and a proposal written
    before `DEC-031` was checked against a line-wide ceiling the ruling has since lowered.
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
               ) AS returned_at,
               -- `DEC-031` rule 3. Either fact is enough: the balance `CANCEL-REFUND-001` writes,
               -- or a refund row. Reading both fails closed if one is ever written without the
               -- other.
               o.balance_status = 'REFUNDED'
                   OR EXISTS (SELECT 1 FROM order_refunds rf WHERE rf.order_id = o.id)
                   AS refunded
        FROM orders o
        JOIN quote_revisions r
          ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
        -- A settlement that has been refunded is not a charge. `order_refunds` keeps the
        -- settlement row it references, so reading `paid_amount_vnd` alone let a delivered order
        -- cancelled SHOP_FAULT_NO_CHARGE and refunded in full also earn a late-delivery credit on
        -- the money already handed back. Excluding it reads as "not settled", which refuses.
        LEFT JOIN order_settlements s
          ON s.order_id = o.id
         AND NOT EXISTS (SELECT 1 FROM order_refunds rf WHERE rf.settlement_id = s.id)
        WHERE o.id = %s
        """,
        (order_id,),
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
    revision = _stored_revision(row[6])
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
            lines=remedy_line_facts(revision.data.lines),
            expects_return_leg=expects_return,
            return_leg_succeeded=delivered_at is not None,
            refunded=bool(row[9]),
        ),
        pricebook=next(
            (
                reference
                for reference in revision.data.configuration_snapshots
                if reference.config_type == "PRICEBOOK"
            ),
            None,
        ),
        incident_status=incident_status,
    )


#: What "committed" excludes, shared by both committed-total reads so they cannot disagree: a
#: proposal waiting on an owner envelope that can never be approved any more.
_LIVE_PROPOSAL_FILTER = """
    NOT (
        p.status = 'OWNER_APPROVAL_REQUIRED'
        AND coalesce(s.status, 'REQUESTED') = ANY(%s)
    )
"""


def _read_commitments(
    cursor: Any, *, order_id: UUID, order_line_id: str | None, garment_index: int | None = None
) -> RemedyCommitments:
    """What earlier proposals already committed against this order, for `evaluate_remedy`.

    Every proposal counts except one that can never pay: a proposal waiting on the owner whose
    envelope reached a terminal non-approval state (`_DEAD_ENVELOPE_STATUSES`). Nothing else is
    excluded -- a staff-authorised proposal nobody has executed yet is still money the counter can
    hand over, so it is committed. Fail-closed at every edge: a missing envelope state row reads as
    live, and an envelope that dies concurrently is still counted by the reader that raced it.

    Damage and loss on one line share the sum since `DEC-031`: both pay against the same item.

    `REMEDY-GARMENT-001`: the line's sum is read grouped by the garment each proposal named, and
    when the request names one, `domain.remedies.committed_against_garment` says what counts
    against it -- including every proposal that named none. With no garment named the domain gets
    the line alone and treats it as the garment's, which is the pre-addendum rule.

    Called twice by `propose`: once unlocked to decide whether the owner is needed before anything
    is written, and once under the order-row lock inside the transaction that inserts, which is the
    read that binds. The second is what stops two counters both reading "nothing committed yet".
    """

    cursor.execute(
        """
        SELECT count(*) FILTER (WHERE p.kind = 'LATE_DELIVERY_CREDIT')
        FROM remedy_proposals p
        LEFT JOIN approval_request_states s ON s.approval_request_id = p.approval_id
        WHERE p.order_id = %s AND
        """
        + _LIVE_PROPOSAL_FILTER,
        (order_id, list(_DEAD_ENVELOPE_STATUSES)),
    )
    row = cursor.fetchone()
    assert row is not None
    by_garment = (
        {}
        if order_line_id is None
        else _read_line_commitments(cursor, order_id=order_id).get(order_line_id, {})
    )
    return RemedyCommitments(
        line_committed_vnd=sum(by_garment.values()),
        late_delivery_credits=int(row[0]),
        garment_committed_vnd=(
            None if garment_index is None else committed_against_garment(by_garment, garment_index)
        ),
    )


def _read_line_commitments(cursor: Any, *, order_id: UUID) -> dict[str, dict[int | None, int]]:
    """Every line's live-or-paid damage and loss, by the garment each proposal named.

    One read for `propose`, for the options form and -- with its own filter -- nowhere else, so the
    form's "đã ghi" and the figure `evaluate_remedy` decides from are the same rows summed the same
    way. `None` is a proposal that named no garment (written before migration `0053`, or on a line
    with no garment identity).
    """

    cursor.execute(
        """
        SELECT p.order_line_id, p.garment_index, coalesce(sum(p.amount_vnd), 0)
        FROM remedy_proposals p
        LEFT JOIN approval_request_states s ON s.approval_request_id = p.approval_id
        WHERE p.order_id = %s
          AND p.kind IN ('DAMAGE_COMPENSATION', 'LOST_ITEM')
          AND p.order_line_id IS NOT NULL AND
        """
        + _LIVE_PROPOSAL_FILTER
        + " GROUP BY p.order_line_id, p.garment_index",
        (order_id, list(_DEAD_ENVELOPE_STATUSES)),
    )
    lines: dict[str, dict[int | None, int]] = {}
    for row in cursor.fetchall():
        garment = None if row[1] is None else int(row[1])
        lines.setdefault(str(row[0]), {})[garment] = int(row[2])
    return lines


def _per_garment(
    terms: ItemCompensationTerms, by_garment: Mapping[int | None, int]
) -> tuple[int, ...]:
    """What counts against each garment 1..N, for the form, by the domain's own rule."""

    if terms.garments is None:
        return ()
    return tuple(
        committed_against_garment(by_garment, garment) for garment in range(1, terms.garments + 1)
    )


def _service_names(cursor: Any, reference: ConfigurationSnapshotReference | None) -> dict[str, str]:
    """Display names from the pricebook version the order was priced under. Display only.

    The version the snapshot cites, not the one in force: a service renamed since would otherwise
    label an old order with a name its customer never saw. Digest-checked like every configuration
    read; a version that cannot be read, or does not match the digest the snapshot recorded, gives
    no names at all and the console shows the service code. Nothing here reaches a money decision.
    """

    if reference is None:
        return {}
    cursor.execute(
        """
        SELECT payload, snapshot_hash FROM configuration_versions
        WHERE id = %s AND config_type = 'PRICEBOOK' AND lifecycle IN ('PUBLISHED', 'RETIRED')
        """,
        (reference.version_id,),
    )
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], Mapping):
        return {}
    stored = str(row[1]).removeprefix(_DIGEST_PREFIX)
    cited = reference.snapshot_hash.removeprefix(_DIGEST_PREFIX)
    if not (
        hmac.compare_digest(snapshot_hash(row[0]).removeprefix(_DIGEST_PREFIX), stored)
        and hmac.compare_digest(cited, stored)
    ):
        return {}
    services = row[0].get("services")
    if not isinstance(services, list):
        return {}
    return {
        str(item["code"]): str(item["display_name"])
        for item in services
        if isinstance(item, Mapping) and "code" in item and "display_name" in item
    }


_DIGEST_PREFIX = "JCS-SHA256-V1:"


def _stored_revision(snapshot: object) -> ImmutableQuoteSnapshot:
    """The order's stored revision, through the domain's own parser.

    A revision this system cannot parse produces no ceilings rather than a plausible wrong one.
    """

    if not isinstance(snapshot, Mapping):
        raise RemedyStateError(
            "the order's stored quote revision is unreadable",
            reason_code="REMEDY_ORDER_REVISION_UNREADABLE",
        )
    return parse_quote_revision(snapshot)


def _lock_proposable_incident(cursor: Any, *, order_id: UUID, incident_id: UUID) -> str:
    """Take the locks a proposal is decided under, and re-check the incident while holding them.

    The order row first, then the incident row, always in that order -- `execute` takes the
    proposal, then the order, then writes the incident, so no path waits on these two the other way
    round. The order row is the one that matters for money: `DEC-004`'s ceilings are about an item
    and an order, and two incidents on one order must serialise even though they share no incident
    row. `FOR UPDATE` rather than a weaker mode so that two proposers conflict with each other, and
    it is held until the proposal's transaction ends.
    """

    cursor.execute("SELECT id FROM orders WHERE id = %s FOR UPDATE", (order_id,))
    if cursor.fetchone() is None:
        raise RemedyStateError(
            "the incident is missing, not this store's, or names no order",
            reason_code="REMEDY_INCIDENT_NOT_FOUND",
        )
    cursor.execute("SELECT status FROM customer_incidents WHERE id = %s FOR UPDATE", (incident_id,))
    row = cursor.fetchone()
    if row is None:
        raise RemedyStateError(
            "the incident is missing, not this store's, or names no order",
            reason_code="REMEDY_INCIDENT_NOT_FOUND",
        )
    status = str(row[0])
    _require_proposable_incident(status)
    return status


def _require_proposable_incident(status: str) -> None:
    if status not in _PROPOSABLE_INCIDENT_STATUSES:
        raise RemedyStateError(
            "this incident has already reached its outcome; open a new incident for a new "
            "complaint",
            reason_code="REMEDY_INCIDENT_NOT_OPEN",
            authority="DEC-004",
        )


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
    garment_index: int | None,
) -> None:
    cursor.execute(
        """
        INSERT INTO remedy_proposals (
            id, store_id, incident_id, order_id, kind, status, amount_vnd, direction, ceiling_vnd,
            order_line_id, policy_version_id, policy_version, store_fault_attested,
            attested_late_by_minutes, window_opened_at, window_closes_at, proposal_hash,
            approval_id, proposed_by, proposed_at, correlation_id, garment_index
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s
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
            garment_index,
        ),
    )


def _open_incident_review(
    cursor: Any, incident_id: UUID, *, fault: bool, locked_status: str
) -> None:
    """Move the incident onto the ladder `0014` drew and set the flag `0039` left writable.

    `fault_decided` records that a named staff member made the store-fault determination `DEC-004`
    asks for -- whichever way it went. It is not "the store was at fault"; that is
    `store_fault_attested` on the proposal. The distinction matters for the same reason
    `AcquisitionSource.UNKNOWN` exists: a decision recorded as not-taken is different from one
    recorded as no.

    `locked_status` is what `_lock_proposable_incident` read while holding the row, so it is the
    status this transaction is actually acting on. Only `OPEN` moves; an incident already
    `UNDER_REVIEW` keeps its first fault finding. The UPDATE used to run blind -- no status check
    beforehand and no row count afterwards -- which is how a proposal could be written against an
    incident a remedy had already closed. Now a missed row is an error rather than a shrug: under
    the lock it cannot happen, and if it ever does the proposal must not be recorded.
    """

    _require_proposable_incident(locked_status)
    if locked_status != "OPEN":
        return
    cursor.execute(
        """
        UPDATE customer_incidents
        SET status = 'UNDER_REVIEW', fault_decided = %s
        WHERE id = %s AND status = 'OPEN'
        RETURNING id
        """,
        (fault, incident_id),
    )
    if cursor.fetchone() is None:
        raise RemedyStateError(
            "the incident changed while the proposal was being recorded",
            reason_code="STALE_VERSION",
        )


def _recheck_before_paying(
    cursor: Any,
    *,
    proposal_id: UUID,
    order_id: UUID,
    kind: RemedyKind,
    amount_vnd: int | None,
    order_line_id: str | None,
    ceiling_vnd: int | None,
    staff_authorized: bool,
    policy_version_id: UUID,
    garment_index: int | None = None,
) -> None:
    """Refuse to pay a proposal that would break a limit `propose` enforces, whoever wrote it.

    `propose` is the gate, and a proposal it wrote can never fail here. This is the second line, for
    rows it did not write: the ones a deployed database already holds from before the limits became
    cumulative -- three 80.000 d staff authorisations on one item, say -- and any future writer that
    forgets the rule. It re-checks, over what has actually been **paid** (`EXECUTED`), the two
    invariants the cumulative rule guarantees regardless of the order proposals were made in:

    * everything paid on one item, this included, is within the item's ceiling;
    * everything paid on one item *without the owner* -- proposals carrying no envelope -- is within
      the staff limit of this proposal's own policy version. The last staff authorisation on an
      item had every earlier one in its committed total, so under the rule this sum can never
      exceed the limit; a row set where it does was not written under the rule.

    `DEC-031` adds, for damage and loss alike, terms re-derived from the order's stored snapshot
    rather than trusted from the row: one claim is paid within the lower of the ceiling it recorded
    and one item's ceiling now (a row written under the line-wide rule is not paid past the
    per-piece cap); everything paid on the line stays within its items' ceilings together (the
    founder's per-item clarification); and a staff-authorised row is refused if the line now needs
    the owner whatever the amount -- the order was refunded after it was proposed, or its fee was
    never recorded.

    `REMEDY-GARMENT-001` makes the item a garment where the line has garments with a fee each:
    the item ceiling and the staff limit are re-checked over what was paid on *that* garment plus
    every line-level payment on the line. A line-level row itself -- one written before migration
    `0053`, naming no garment -- is re-checked against the garment that leaves it least room,
    because nothing says which it was about. The line's total still binds everything paid on it.

    And one late-delivery credit per order. The order row is locked first, as `propose` locks it,
    so a proposal and a payment on the same order serialise rather than each checking a sum the
    other is changing.
    """

    cursor.execute("SELECT id FROM orders WHERE id = %s FOR UPDATE", (order_id,))
    if kind is RemedyKind.LATE_DELIVERY_CREDIT:
        cursor.execute(
            """
            SELECT count(*) FROM remedy_proposals
            WHERE order_id = %s AND kind = 'LATE_DELIVERY_CREDIT' AND status = 'EXECUTED'
              AND id <> %s
            """,
            (order_id, proposal_id),
        )
        paid = cursor.fetchone()
        if paid is not None and int(paid[0]) > 0:
            raise RemedyStateError(
                "a late-delivery credit has already been paid for this order",
                reason_code=RemedyRefusal.REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED.value,
                authority="DEC-004",
            )
        return
    if kind not in (RemedyKind.DAMAGE_COMPENSATION, RemedyKind.LOST_ITEM):
        return
    assert amount_vnd is not None and ceiling_vnd is not None and order_line_id is not None
    cursor.execute(
        """
        SELECT garment_index, coalesce(sum(amount_vnd), 0),
               coalesce(sum(amount_vnd) FILTER (WHERE approval_id IS NULL), 0)
        FROM remedy_proposals
        WHERE order_id = %s AND kind IN ('DAMAGE_COMPENSATION', 'LOST_ITEM')
          AND order_line_id = %s AND status = 'EXECUTED' AND id <> %s
        GROUP BY garment_index
        """,
        (order_id, order_line_id, proposal_id),
    )
    paid_by_garment: dict[int | None, int] = {}
    paid_staff: dict[int | None, int] = {}
    for garment_row in cursor.fetchall():
        key = None if garment_row[0] is None else int(garment_row[0])
        paid_by_garment[key], paid_staff[key] = int(garment_row[1]), int(garment_row[2])
    paid_total = sum(paid_by_garment.values())
    policy = _policy_by_version(cursor, policy_version_id)
    if policy is None:
        # The figures this proposal was checked against cannot be read back, so neither the item's
        # ceiling nor whether staff alone may pay it can be answered. Invariant 11: that is a stop.
        raise RemedyStateError(
            "the remedy policy this proposal cites cannot be read",
            reason_code=RemedyRefusal.REMEDY_POLICY_UNPUBLISHED.value,
            authority="INVARIANT-11",
        )
    # `DEC-031`, applied to rows written before it: the item's terms are re-derived from the
    # order's own stored snapshot, and the lower of the recorded and the re-derived ceiling binds.
    # The ruling only ever lowers exposure, so a proposal checked against a line-wide 750.000 d is
    # not paid past the per-piece 75.000 d, and one whose order was refunded since is the owner's.
    terms = item_compensation_terms(
        policy, _read_order_record(cursor, order_id=order_id).facts, order_line_id
    )
    if terms is None:
        raise RemedyStateError(
            "the order's revision no longer prices this line",
            reason_code=RemedyRefusal.REMEDY_LINE_NOT_PRICED.value,
            authority="INVARIANT-3",
        )
    # One claim, one item: the lower of the ceiling the row recorded and one item's ceiling now.
    # A row written under the line-wide rule recorded the whole line's cap, and is not paid past
    # one item's.
    item_ceiling = min(ceiling_vnd, terms.ceiling_vnd)
    if amount_vnd > item_ceiling:
        raise RemedyStateError(
            "paying this would give one item more than its compensation ceiling",
            reason_code=RemedyRefusal.REMEDY_CEILING_EXCEEDED.value,
            authority="DEC-004",
            ceiling_vnd=item_ceiling,
        )
    # What was paid on this item: the garment where the line has several with a fee each, the line
    # otherwise. The domain's own rule decides how line-level rows count.
    per_garment = terms.garments is not None and terms.garments > 1
    if not per_garment:
        paid_on_item, paid_on_item_by_staff = paid_total, sum(paid_staff.values())
    elif garment_index is not None:
        paid_on_item = committed_against_garment(paid_by_garment, garment_index)
        paid_on_item_by_staff = committed_against_garment(paid_staff, garment_index)
    else:
        # A line-level row, proposed before migration `0053` under the line rule, which bounded
        # line-level rows among themselves only by the line together -- so another line-level row
        # is not "this item" for the ceiling, and refusing on their sum would newly refuse a claim
        # decided correctly at the time. It does count against every garment, so no garment's
        # payments plus this one may pass one item's ceiling; and staff alone never pay more than
        # the limit on any one garment, line-level payments included, which is what every
        # proposal since the addendum was decided against.
        paid_on_item = committed_against_any_garment(
            {garment: amount for garment, amount in paid_by_garment.items() if garment is not None}
        )
        paid_on_item_by_staff = committed_against_any_garment(paid_staff)
    if paid_on_item + amount_vnd > terms.ceiling_vnd:
        raise RemedyStateError(
            "paying this would give one item more than its compensation ceiling",
            reason_code=RemedyRefusal.REMEDY_CEILING_EXCEEDED.value,
            authority="DEC-004",
            ceiling_vnd=terms.ceiling_vnd,
            committed_vnd=paid_on_item,
        )
    # Everything paid on the line, this included, within every item's ceiling together.
    if paid_total + amount_vnd > terms.line_ceiling_vnd:
        raise RemedyStateError(
            "paying this would take the line past its compensation ceiling",
            reason_code=RemedyRefusal.REMEDY_CEILING_EXCEEDED.value,
            authority="DEC-004",
            ceiling_vnd=terms.line_ceiling_vnd,
            committed_vnd=paid_total,
        )
    if not staff_authorized:
        return
    if terms.owner_always:
        raise RemedyStateError(
            "this item's compensation now needs the owner: "
            + ", ".join(reason.value for reason in terms.owner_always),
            reason_code="REMEDY_APPROVAL_REQUIRED",
            authority="DEC-031",
        )
    if paid_on_item_by_staff + amount_vnd > policy.staff_approval_ceiling_vnd:
        raise RemedyStateError(
            "staff alone have already paid up to their limit on this item; the owner must approve",
            reason_code="REMEDY_APPROVAL_REQUIRED",
            authority="DEC-004",
        )


def _policy_by_version(cursor: Any, version_id: UUID) -> RemedyPolicy | None:
    """The `REMEDY_POLICY` version a proposal cites, digest-checked and parsed, or `None`.

    The version the proposal was *checked against*, not the one in force now: invariant 4 keeps a
    published document immutable, so a proposal made under version 1 is judged by version 1's staff
    limit even after version 2 is published. `RETIRED` is read for the same reason -- retiring a
    document ends its use for new decisions, not the meaning of the ones already made under it.
    """

    cursor.execute(
        """
        SELECT payload, snapshot_hash FROM configuration_versions
        WHERE id = %s AND config_type = %s AND lifecycle IN ('PUBLISHED', 'RETIRED')
        """,
        (version_id, REMEDY_POLICY_CONFIG_TYPE),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    payload = row[0]
    if not isinstance(payload, Mapping) or not hmac.compare_digest(
        snapshot_hash(payload), str(row[1])
    ):
        return None
    try:
        return parse_remedy_policy(payload)
    except RemedyPolicyError:
        return None


def _refusal_error(refused: RemedyRefused) -> RemedyStateError:
    """The domain's refusal, carried to the caller with every figure that explains it."""

    return RemedyStateError(
        "this remedy is not authorised",
        reason_code=refused.reason_code,
        authority=refused.authority,
        ceiling_vnd=refused.ceiling_vnd,
        window_closes_at=refused.window_closes_at,
        threshold_minutes=refused.threshold_minutes,
        committed_vnd=refused.committed_vnd,
        garments=refused.garments,
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
    "ReservedRemedyCredits",
    "StoredCreditRedemption",
    "StoredRemedyExecution",
    "StoredRemedyProposal",
    "publish_remedy_policy",
    "read_published_remedy_policy",
    "read_reserved_remedy_credits",
    "spend_reserved_remedy_credits",
    "validate_remedy_policy",
]
