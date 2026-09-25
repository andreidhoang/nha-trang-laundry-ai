"""The content an agent-raised approval presents for a stored quote revision, rendered here.

AGENT-SHADOW-DEFECTS-001 F4. An approval binds a `rendered_hash`: the digest of exactly what the
approver authorises to be presented. The agent path let the model choose that digest and bound it
to no content at all, so an approver authorised a hash nothing had rendered, and every later check
compared the hash only with the copy a client echoed back.

The rendering is a pure function of two server facts -- the stored, hash-verified quote revision
and the approval action -- so any verifier can rebuild the same bytes from the immutable store. It
is a projection, not a copy: the figures and lines a customer would be shown, bound to the digest
of the revision they came from. Engine traces and configuration references are not presented, so
they are not what is approved.
"""

from __future__ import annotations

import json
from typing import Any

from .canonical import CanonicalDocument, canonical_document

#: Versioned so a later change of what is presented is a new rendering, never a silent one.
QUOTE_PRESENTATION_RENDERING = "AGENT-QUOTE-PRESENTATION-V1"

#: What a customer is shown of a revision. Every key must be present; a revision missing one is
#: not rendered, because a partial rendering would be approved as if it were whole.
PRESENTED_FIELDS = (
    "quote_id",
    "revision",
    "finality",
    "currency",
    "valid_until",
    "lines",
    "adjustments",
    "totals",
    "required_approvals",
)


class QuotePresentationError(ValueError):
    """The document is not a quote revision this rendering can present."""


def render_quote_presentation(revision: CanonicalDocument, *, action: str) -> CanonicalDocument:
    """Render what an approval of `action` on this stored revision presents, and hash it."""

    if not isinstance(action, str) or not action:
        raise QuotePresentationError("an approval action is required")
    try:
        payload: Any = json.loads(revision.canonical_json)
    except (TypeError, ValueError) as error:
        raise QuotePresentationError("the revision is not a canonical document") from error
    if not isinstance(payload, dict) or any(field not in payload for field in PRESENTED_FIELDS):
        raise QuotePresentationError("the document is not a complete quote revision")
    return canonical_document(
        {
            "rendering": QUOTE_PRESENTATION_RENDERING,
            "action": action,
            "revision_snapshot_hash": revision.snapshot_hash,
            "presented": {field: payload[field] for field in PRESENTED_FIELDS},
        },
        exclude_volatile=False,
    )


__all__ = [
    "PRESENTED_FIELDS",
    "QUOTE_PRESENTATION_RENDERING",
    "QuotePresentationError",
    "render_quote_presentation",
]
