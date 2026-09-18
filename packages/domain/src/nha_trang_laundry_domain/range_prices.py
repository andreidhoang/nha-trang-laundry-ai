"""Decide whether a staff-chosen amount is inside a band the owner already published.

Twenty of the forty-four published services carry `min_price_vnd != max_price_vnd` and
`agent_permission = RANGE_ONLY_HUMAN_FINAL`: áo dài truyền thống 80.000-240.000 ₫, áo lông thú
200.000-400.000 ₫, vệ sinh sofa 300.000-500.000 ₫. They are priced by inspection, so no rule can
produce the number — a person has to look at the garment.

**The server owns the bound; the human owns the number.** That division is the whole of this
module. Publishing a band under an immutable pricebook version (invariant 4) is the owner stating
that every integer in that interval is an acceptable price for that service; a staff member
choosing inside it exercises an authority already granted rather than creating one. What this code
decides is only whether a submitted amount lies inside the interval the *published* pricebook drew,
and it decides that deterministically (invariant 3). It never proposes an amount: there is no
midpoint, no minimum, no maximum and no default anywhere below. A range line nobody has priced
stays unpriced.

**The band is read from the revision's own pricebook version, never the current one.** A quote
priced against version 3 keeps version 3's band even after version 4 is published, because the
customer was read a price from version 3 and an amount validated against a band they never saw
would be a number nobody agreed to. `RANGE_PRICE_PRICEBOOK_MISMATCH` is that check failing, and it
is a refusal rather than a silent re-read.

The refusal registry follows `settlement.REFUSAL_DECISIONS`: every refusal names the invariant or
decision that causes it, so a caller is told what is blocking them rather than only that something
is. `test_range_prices.py` pins the mapping's completeness against the enum, as
`test_settlement_policy.py:103` does for settlement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from nha_trang_laundry_domain.canonical import CanonicalDocument, canonical_document

#: The same ceiling `quotes._money` enforces. An amount above it cannot survive canonicalization,
#: so it is refused as out of band rather than allowed to raise deeper in the composition.
MAX_JCS_INTEGER: Final = 9_007_199_254_740_991

#: The rule this module applies, versioned so an approval envelope names the rule in force when it
#: was signed. It moves when what counts as an in-band amount changes.
RANGE_PRICE_POLICY_VERSION: Final = "range-price-published-band-v1"

#: The canonical document type an approval's `rendered_hash` is taken over. Named in the document
#: itself so a digest cannot be mistaken for the digest of some other kind of content.
RANGE_PRICE_DOCUMENT_SCHEMA: Final = "range-price-attestation-v1"


class RangePriceRefusal(StrEnum):
    """Why a submitted amount is not a price this system will record."""

    #: Below `min_price_vnd` or above `max_price_vnd` of the line's published band. The owner
    #: authorised the interval, not the number, so an amount outside it has no authority at all.
    RANGE_PRICE_OUT_OF_BAND = "RANGE_PRICE_OUT_OF_BAND"
    #: An amount was supplied for a service the published pricebook prices exactly. Accepting it
    #: would let a staff member overwrite a published price, which is a different act entirely.
    RANGE_PRICE_NOT_APPLICABLE = "RANGE_PRICE_NOT_APPLICABLE"
    #: The band was read from a pricebook version other than the one the revision was priced
    #: against. The interval may have moved between them, so the check would be against the wrong
    #: authorisation.
    RANGE_PRICE_PRICEBOOK_MISMATCH = "RANGE_PRICE_PRICEBOOK_MISMATCH"


#: Which invariant or decision owns each refusal. Same shape and same purpose as
#: `settlement.REFUSAL_DECISIONS`: the caller is told what would have to change, not merely that
#: something did not pass.
RANGE_PRICE_REFUSAL_AUTHORITIES: Final = {
    # Invariant 3: deterministic code, not a human and not a model, decides what money is
    # permissible. The human supplies a fact inside a bound this code owns.
    RangePriceRefusal.RANGE_PRICE_OUT_OF_BAND: "INVARIANT-3",
    # Invariant 4: the published pricebook is immutable and is the only authority on what a service
    # costs. A service priced exactly there has no band for anyone to choose inside.
    RangePriceRefusal.RANGE_PRICE_NOT_APPLICABLE: "INVARIANT-4",
    # Invariant 4 again, from the other side: a historical snapshot is immutable, so the band that
    # authorises an amount is the one the revision was priced against, not whichever is newest.
    RangePriceRefusal.RANGE_PRICE_PRICEBOOK_MISMATCH: "INVARIANT-4",
}


@dataclass(frozen=True, slots=True)
class PriceBand:
    """One line's published interval, in integer VND, inclusive at both ends.

    Both ends are inclusive because the owner published both: 80.000 ₫ and 240.000 ₫ are prices the
    shop charges for an áo dài, not open bounds approaching prices it charges.
    """

    minimum_vnd: int
    maximum_vnd: int


@dataclass(frozen=True, slots=True)
class RangePriceChoice:
    """One exact amount a named staff member chose for one range-priced line."""

    service_code: str
    amount_vnd: int


@dataclass(frozen=True, slots=True)
class RangePriceAttestation:
    """The complete set of amounts chosen for one revision, and what they were chosen against.

    `quote_id` and `revision` are inside the attestation rather than beside it because the rendered
    document is what an approval envelope binds (invariant 8). An amount approved for one garment on
    one revision must not be reusable on another, and the only way to make that structurally true is
    to put the identity of the thing being priced inside the content that was approved.

    `approval_id` is deliberately *not* part of the rendered document: the document is hashed first
    and the approval is created from that hash, so the approval cannot be inside what it binds. It
    is `None` for exactly as long as that ordering requires -- while a proposal is being validated
    and hashed, before any envelope exists. A composition given amounts with no approval refuses
    with `HUMAN_APPROVAL_REQUIRED` rather than pricing them, so the gap cannot be walked through.
    """

    quote_id: UUID
    revision: int
    pricebook_version_id: UUID
    pricebook_version: int
    choices: tuple[RangePriceChoice, ...]
    approval_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ResolvedRangePrices:
    """Every range line's chosen amount, proven in band. Keyed by service code."""

    amounts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class RangePriceRefused:
    """Refused, with the reason and the authority that would have to change to allow it."""

    refusal: RangePriceRefusal
    service_code: str | None = None

    @property
    def reason_code(self) -> str:
        return self.refusal.value

    @property
    def authority(self) -> str:
        return RANGE_PRICE_REFUSAL_AUTHORITIES[self.refusal]


RangePriceOutcome = ResolvedRangePrices | RangePriceRefused


def range_price_rendered_document(attestation: RangePriceAttestation) -> CanonicalDocument:
    """The exact content an approval binds, canonicalised.

    Invariant 8: an approval binds a rendered-content hash and a revision, and editing invalidates
    it. This is that rendered content. The choices are sorted by service code so that two callers
    submitting the same amounts in a different order produce the same digest — an approval must be
    about what was priced, not about the order a form serialised it in.
    """

    return canonical_document(
        {
            "schema": RANGE_PRICE_DOCUMENT_SCHEMA,
            "quote_id": str(attestation.quote_id),
            "revision": attestation.revision,
            "pricebook_version_id": str(attestation.pricebook_version_id),
            "pricebook_version": attestation.pricebook_version,
            "choices": [
                {"service_code": choice.service_code, "amount_vnd": choice.amount_vnd}
                for choice in sorted(attestation.choices, key=lambda item: item.service_code)
            ],
        }
    )


def resolve_range_prices(
    *,
    bands: Mapping[str, PriceBand],
    pricebook_version_id: UUID,
    pricebook_version: int,
    attestation: RangePriceAttestation,
) -> RangePriceOutcome:
    """Check every chosen amount against the band the published pricebook drew for its line.

    `bands` holds one entry per range-priced line of the revision, derived by the caller from the
    deterministic engine or from the stored revision — never from anything a client sent. A choice
    naming a service that is not in it is `RANGE_PRICE_NOT_APPLICABLE`, which is the same refusal
    whether the service is priced exactly or is not on the quote at all: in both cases there is no
    band to be inside.

    A range line with no choice is *not* refused here. That is the caller's composition decision --
    `RANGE_PRICE_REQUIRES_HUMAN` for a revision that must be exact, or a band presented to the
    customer -- and this function answers only about the amounts it was given.
    """

    if (
        attestation.pricebook_version_id != pricebook_version_id
        or attestation.pricebook_version != pricebook_version
    ):
        return RangePriceRefused(RangePriceRefusal.RANGE_PRICE_PRICEBOOK_MISMATCH)
    amounts: dict[str, int] = {}
    for choice in attestation.choices:
        band = bands.get(choice.service_code)
        if band is None or choice.service_code in amounts:
            # Two amounts for one line is the same problem as an amount for a line with no band:
            # there is no single authorised interval this amount sits in. Refusing both the same
            # way keeps the caller from having to guess which of two numbers a person meant.
            return RangePriceRefused(
                RangePriceRefusal.RANGE_PRICE_NOT_APPLICABLE, choice.service_code
            )
        if not _in_band(choice.amount_vnd, band):
            return RangePriceRefused(RangePriceRefusal.RANGE_PRICE_OUT_OF_BAND, choice.service_code)
        amounts[choice.service_code] = choice.amount_vnd
    return ResolvedRangePrices(amounts)


def _in_band(amount: int, band: PriceBand) -> bool:
    """Integer VND, non-negative, inside the published interval. Invariant 2.

    `bool` is excluded explicitly because `True` is an `int` in Python and `isinstance(True, int)`
    is true: without this, `amount_vnd=True` would be read as 1 ₫ and compared as a price.
    """

    return (
        isinstance(amount, int)
        and not isinstance(amount, bool)
        and 0 <= amount <= MAX_JCS_INTEGER
        and band.minimum_vnd <= amount <= band.maximum_vnd
    )


__all__ = [
    "RANGE_PRICE_DOCUMENT_SCHEMA",
    "RANGE_PRICE_POLICY_VERSION",
    "RANGE_PRICE_REFUSAL_AUTHORITIES",
    "PriceBand",
    "RangePriceAttestation",
    "RangePriceChoice",
    "RangePriceOutcome",
    "RangePriceRefusal",
    "RangePriceRefused",
    "ResolvedRangePrices",
    "range_price_rendered_document",
    "resolve_range_prices",
]
