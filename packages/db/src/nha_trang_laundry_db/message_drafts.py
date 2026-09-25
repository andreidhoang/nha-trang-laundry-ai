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
from typing import Any
from uuid import UUID

from nha_trang_laundry_domain.canonical import canonical_document

MESSAGE_DRAFT_RESOURCE_TYPE = "MESSAGE_DRAFT"

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


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "AGENT_TEXT_REVISION",
    "EDITED_TEXT_REVISION",
    "MESSAGE_DRAFT_RESOURCE_TYPE",
    "MessageDraftBinding",
    "MessageDraftFacts",
    "MessageDraftRendering",
    "message_draft_binding",
    "read_message_draft_binding",
]
