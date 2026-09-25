"""What a `MESSAGE_DRAFT` approval binds, computed by the server from the stored draft.

`API-INTEGRITY-002`. A `SEND_MESSAGE` envelope names a `MESSAGE_DRAFT`, and until this module
nothing resolved one: `_RESOLVABLE_RESOURCES` in `approvals.py` said the content "lives in the
envelope itself", so staff could raise an envelope over any UUID with digests they typed, have it
approved, and spend it on a manual send to a recipient they also typed. The record then said that
approver B authorised a message to recipient X when no message and no X had ever been approved.

The content does exist. `agent_drafts` (migration `0021`) holds the words the agent proposed, the
store and the contact binding they were produced for, and `agent_draft_reviews` holds a reviewer's
replacement text when one was written. Both are append-only, so the binding below is a pure function
of stored rows and is recomputed on every read rather than stored beside them -- a stored digest of
immutable content can only ever disagree with it.

A `MESSAGE_DRAFT`'s identifier is its `agent_run_id`, the draft's own primary key.

Revisions follow content, not decisions:

* revision 1 is the agent's text, and stays revision 1 when a reviewer APPROVEs it unchanged;
* revision 2 is a reviewer's EDIT. Invariant 8 then does the rest: an approval bound to revision 1
  no longer matches, so editing invalidates it;
* a REJECTed draft has no sendable content at all, and `read_message_draft_binding` answers `None`
  exactly as it does for a draft that does not exist. A reviewer's rejection is not something a
  second approval can quietly overrule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_domain.canonical import canonical_document

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership

MESSAGE_DRAFT_RESOURCE_TYPE = "MESSAGE_DRAFT"

#: The `policy_version` a staff-raised `SEND_MESSAGE` envelope names. `MESSAGE-DRAFT-BINDING-001`.
#:
#: The same label the API-INTEGRITY-002 fixtures already raised these envelopes under, and it
#: decides nothing: `SEND_MESSAGE`'s role, TTL and obligations come from `APPROVAL_POLICIES` in
#: `packages/domain`, derived by the server when the envelope is built. It is returned beside the
#: binding for the reason `EXPORT_POLICY_VERSION` is returned beside an export -- so the console
#: raises the envelope from values the server handed it, and never from a string it typed.
SEND_MESSAGE_POLICY_VERSION = "manual-send-policy-v1"

#: Who may read a draft's binding: the three roles that may raise a `SEND_MESSAGE` envelope, decide
#: one, or spend one on a manual send (`require_operations_staff`, `_require_manual_sender`). Each
#: of them has to read the exact words to do their part. `AUDITOR` reads drafts on the Shadow
#: surfaces and has no part in a send, so it is not admitted here.
MESSAGE_DRAFT_BINDING_READ_ROLES = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)

#: The agent's own text, and that same text approved unchanged by a reviewer.
AGENT_TEXT_REVISION = 1
#: A reviewer's replacement text. There is at most one review per draft (`0021`), so at most one.
EDITED_TEXT_REVISION = 2


@dataclass(frozen=True, slots=True)
class MessageDraftFacts:
    """What `snapshot_hash` covers: whose message, to whom, which revision."""

    agent_run_id: UUID
    store_id: UUID
    conversation_binding_id: UUID
    contact_binding_id: UUID
    resource_version: int


@dataclass(frozen=True, slots=True)
class MessageDraftRendering:
    """What `rendered_hash` covers: the exact words, bound to the facts above.

    The facts travel inside the rendering on purpose. The words alone would let one approval of
    "Dạ, đồ của anh đã xong" authorise that sentence to any customer; with the contact binding in
    the digest, the approval names who it is for as well as what it says.
    """

    facts: MessageDraftFacts
    text: str


@dataclass(frozen=True, slots=True)
class MessageDraftBinding:
    """The current sendable content of one draft, and the digests an envelope must name for it."""

    agent_run_id: UUID
    store_id: UUID
    contact_binding_id: UUID
    resource_version: int
    text: str
    snapshot_hash: str
    rendered_hash: str


def message_draft_binding(
    *,
    agent_run_id: UUID,
    store_id: UUID,
    conversation_binding_id: UUID,
    contact_binding_id: UUID,
    resource_version: int,
    text: str,
) -> MessageDraftBinding:
    """The digests for one revision of a draft. Pure: no clock, no database, no environment."""

    facts = MessageDraftFacts(
        agent_run_id=agent_run_id,
        store_id=store_id,
        conversation_binding_id=conversation_binding_id,
        contact_binding_id=contact_binding_id,
        resource_version=resource_version,
    )
    return MessageDraftBinding(
        agent_run_id=agent_run_id,
        store_id=store_id,
        contact_binding_id=contact_binding_id,
        resource_version=resource_version,
        text=text,
        snapshot_hash=canonical_document(facts).snapshot_hash,
        rendered_hash=canonical_document(MessageDraftRendering(facts, text)).snapshot_hash,
    )


def read_message_draft_binding(cursor: Any, agent_run_id: UUID) -> MessageDraftBinding | None:
    """The draft's current sendable binding, or `None` when there is nothing that may be sent.

    `None` covers a draft that does not exist and a draft a reviewer rejected, deliberately in one
    answer: a caller has no business distinguishing them, and neither may be approved or sent.
    """

    cursor.execute(
        """
        SELECT d.store_id, d.conversation_binding_id, d.contact_binding_id, d.draft_text,
               r.decision, r.edited_text
        FROM agent_drafts d
        LEFT JOIN agent_draft_reviews r ON r.agent_run_id = d.agent_run_id
        WHERE d.agent_run_id = %s
        """,
        (agent_run_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    decision = None if row[4] is None else str(row[4])
    if decision == "REJECT":
        return None
    if decision == "EDIT":
        if row[5] is None:  # The table's CHECK forbids this; refuse rather than send the original.
            return None
        revision, text = EDITED_TEXT_REVISION, str(row[5])
    else:
        revision, text = AGENT_TEXT_REVISION, str(row[3])
    return message_draft_binding(
        agent_run_id=agent_run_id,
        store_id=_uuid(row[0]),
        conversation_binding_id=_uuid(row[1]),
        contact_binding_id=_uuid(row[2]),
        resource_version=revision,
        text=text,
    )


def read_message_draft_binding_for_store(
    cursor: Any, *, store_id: UUID, agent_run_id: UUID, principal: StaffPrincipal
) -> MessageDraftBinding | None:
    """One store's draft binding, as a member of that store may read it.

    `MESSAGE-DRAFT-BINDING-001`.

    `API-INTEGRITY-002` made the server compute what a `SEND_MESSAGE` envelope binds and left
    nothing that exposed it, so an envelope could not be raised from the console and an approver
    deciding one could not read the words they were approving. This is that read, and it is the same
    function the request, decision and manual-send paths call -- the digests a caller receives here
    are byte-for-byte the digests those paths compare against, not a second derivation of them.

    Role and MFA first, then membership of the named store, both raising the same
    `StoreAccessError`: a caller cannot tell "your role may not" from "that store is not yours",
    and an unknown store is indistinguishable from somebody else's. Only then is the draft read, and
    a draft of another store answers `None` exactly as a missing or rejected one does, so a member
    of one shop learns nothing about another shop's draft identifiers.

    `None` also covers a draft a reviewer REJECTed: it has no sendable content and there is nothing
    to show an approver or to send.
    """

    if not principal.mfa_verified or not principal.roles & MESSAGE_DRAFT_BINDING_READ_ROLES:
        raise StoreAccessError("store access is not authorized")
    require_store_membership(cursor, staff_user_id=principal.staff_user_id, store_id=store_id)
    binding = read_message_draft_binding(cursor, agent_run_id)
    if binding is None or binding.store_id != store_id:
        return None
    return binding


@dataclass(frozen=True, slots=True)
class MessageDraftSendProgress:
    """How far the latest `SEND_MESSAGE` over one draft has gone, as stored (`MANUAL-SEND-RESUME`).

    Spec V2 principle 2 (zero paste). A person who reopens a draft in a new session -- after the
    approver decided on another device -- used to have to paste the approval id, the bound version
    and two digests to lock the envelope, and the envelope id and row version to attest. Every one
    of those values is a stored column; this carries them back.

    Nothing here is a decision. `status` is the approval's state row verbatim, `past_expiry` is the
    database clock compared with the stored `expires_at` (a `REQUESTED` or `APPROVED` row is not
    rewritten when it lapses), and every write this enables still re-checks all of it -- the
    approval's state, expiry, digests, the draft revision, the envelope's row version and the
    contact's consent -- under its own lock.
    """

    approval_request_id: UUID
    status: str
    expires_at: datetime
    past_expiry: bool
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    requested_by_you: bool
    envelope_id: UUID | None
    envelope_status: str | None
    envelope_row_version: int | None
    prepared_by_you: bool | None


def read_message_draft_send_state_for_store(
    cursor: Any, *, store_id: UUID, agent_run_id: UUID, principal: StaffPrincipal
) -> tuple[MessageDraftBinding, MessageDraftSendProgress | None] | None:
    """One store's draft binding and how far its latest `SEND_MESSAGE` has gone, in one read.

    `MANUAL-SEND-RESUME`. The binding read -- role and MFA, then membership of the named store,
    then the draft of that store -- runs first and unchanged, on this cursor; only a caller it
    admits reaches the progress read, and the progress read is bounded by the same store, so an
    approval another shop raised over the same identifier can never surface here.

    The latest approval is the most recently requested one over this `MESSAGE_DRAFT` in this store.
    `None` progress means nobody has asked for approval of this draft yet.
    """

    binding = read_message_draft_binding_for_store(
        cursor, store_id=store_id, agent_run_id=agent_run_id, principal=principal
    )
    if binding is None:
        return None
    cursor.execute(
        """
        SELECT r.id, s.status, r.expires_at, r.expires_at <= now(), r.resource_version,
               r.snapshot_hash, r.rendered_hash, r.requested_by,
               e.id, e.status, e.row_version, e.prepared_by
        FROM approval_requests r
        JOIN approval_request_states s ON s.approval_request_id = r.id
        LEFT JOIN manual_send_envelopes e ON e.approval_request_id = r.id
        WHERE r.store_id = %s
          AND r.action = 'SEND_MESSAGE'
          AND r.resource_type = %s
          AND r.resource_id = %s
        ORDER BY r.requested_at DESC, r.id DESC
        LIMIT 1
        """,
        (store_id, MESSAGE_DRAFT_RESOURCE_TYPE, agent_run_id),
    )
    row = cursor.fetchone()
    if row is None:
        return binding, None
    you = principal.staff_user_id
    envelope_id = None if row[8] is None else _uuid(row[8])
    return binding, MessageDraftSendProgress(
        approval_request_id=_uuid(row[0]),
        status=str(row[1]),
        expires_at=row[2],
        past_expiry=bool(row[3]),
        resource_version=int(str(row[4])),
        snapshot_hash=str(row[5]),
        rendered_hash=str(row[6]),
        requested_by_you=_uuid(row[7]) == you,
        envelope_id=envelope_id,
        envelope_status=None if envelope_id is None else str(row[9]),
        envelope_row_version=None if envelope_id is None else int(str(row[10])),
        prepared_by_you=None if envelope_id is None else _uuid(row[11]) == you,
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "AGENT_TEXT_REVISION",
    "EDITED_TEXT_REVISION",
    "MESSAGE_DRAFT_BINDING_READ_ROLES",
    "MESSAGE_DRAFT_RESOURCE_TYPE",
    "SEND_MESSAGE_POLICY_VERSION",
    "MessageDraftBinding",
    "MessageDraftFacts",
    "MessageDraftRendering",
    "MessageDraftSendProgress",
    "message_draft_binding",
    "read_message_draft_binding",
    "read_message_draft_binding_for_store",
    "read_message_draft_send_state_for_store",
]
