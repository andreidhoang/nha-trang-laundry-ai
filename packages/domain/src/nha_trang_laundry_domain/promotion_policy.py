"""The owner's promotion programme as a published document. `DEC-002`, `PROMO-WIRING-001`.

`promotion.py` is the engine: given a policy, a set of lines and a moment, it decides a discount in
whole dong and shows its working. What it never had was a *source* — the only policy in the
repository was `CURRENT_PROMOTION`, a module-level Python constant. That made the programme a code
deploy: the shop could not start one, could not change a rate, and could not retire an expired one
without an engineer. Invariant 4 asks for published configuration to be immutable and versioned, and
a constant is neither.

So the programme moves to the CONFIG-001 publication vehicle under `config_type = PROMOTION_POLICY`,
exactly as the pricebook and `REMEDY_POLICY` do, and this module is the typed reading of one such
document. `packages/db/.../promotions.py` publishes and reads it; nothing here touches a database.

**Two kinds of fact live in one document.** The *interval* facts — code, window, timezone, which
event eligibility is keyed to, whether the programme may stack — become the engine's
`PromotionPolicy`. The *targeting* facts — which service is discounted, at what rate, and whether a
person has to confirm it — become one `PromotionServiceRule` per service. A service the document
does not name is `NOT_ELIGIBLE`: silence is not a discount.

**Nothing is defaulted.** Every field is required, including `eligibility_event`. A document that
does not name the event eligibility is keyed to is not publishable, because that omission is exactly
what made `CURRENT_PROMOTION` evaluate to PROVISIONAL/REQUIRE_HUMAN forever while claiming an open
decision that had been resolved on 2026-08-18.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from nha_trang_laundry_domain.catalog import (
    PromotionEligibilityEvent,
    PromotionResolution,
)
from nha_trang_laundry_domain.promotion import (
    RATE_DENOMINATOR,
    PromotionError,
    PromotionPolicy,
    validate_promotion_policy,
)
from nha_trang_laundry_domain.quotes import CODE_PATTERN

#: `ConfigurationRepository` config type for the published document. Matches
#: `^[A-Z][A-Z0-9_]{1,62}$`, and `(config_type, version)` is unique since migration `0002`.
#:
#: Deliberately *not* the same string as the `config_type` a quote revision's configuration
#: snapshot uses for its promotion reference, which is `PROMOTION` and predates this item
#: (`quotes._validate_finality` and `quote_composition.accept_quote_revision` both read it). One
#: names a document type in the configuration table; the other names what kind of thing priced the
#: revision. `PROMOTION_SNAPSHOT_CONFIG_TYPE` below is the second, so neither is a loose literal.
PROMOTION_POLICY_CONFIG_TYPE: Final = "PROMOTION_POLICY"

#: The `config_type` of the configuration snapshot reference a priced revision carries when a
#: promotion programme was evaluated against it. The version it names is a `PROMOTION_POLICY`
#: version; the reference says *that a programme priced this revision*, and which one.
PROMOTION_SNAPSHOT_CONFIG_TYPE: Final = "PROMOTION"

#: The schema tag inside the published document, so a payload cannot be mistaken for another type's.
PROMOTION_POLICY_SCHEMA: Final = "promotion-policy-v1"

#: The rule version an evaluation records, so a stored revision says which reading of `DEC-002` was
#: in force. It moves when what counts as an evaluated promotion changes -- not when the owner
#: republishes a rate, which is what the configuration version records.
PROMOTION_POLICY_VERSION: Final = "promotion-dec-002-v1"

#: Component name and engine version of the frozen promotion trace on a quote revision.
#: `quotes.CODE_PATTERN` applies to the component.
PROMOTION_COMPONENT: Final = "PROMOTION"
PROMOTION_COMPONENT_VERSION: Final = "promotion-v1"


class PromotionPolicyError(ValueError):
    """Raised when a payload is not a publishable statement of a promotion programme."""


@dataclass(frozen=True, slots=True)
class PromotionServiceRule:
    """What the programme says about one service.

    `rate_bps` is `None` exactly when `resolution` is `NOT_ELIGIBLE`, which is the shape
    `promotion._validate_line` demands: a rate on a service the programme excludes would be a number
    with no decision behind it, and a missing rate on one it includes would be a decision with no
    number.
    """

    service_code: str
    resolution: PromotionResolution
    rate_bps: int | None


@dataclass(frozen=True, slots=True)
class PromotionProgram:
    """One published programme: the engine's policy plus its per-service targeting."""

    policy: PromotionPolicy
    services: Mapping[str, PromotionServiceRule]

    def rule_for(self, service_code: str) -> PromotionServiceRule:
        """The programme's rule for one service, or `NOT_ELIGIBLE` when it names none.

        Silence is not a discount: a pricebook entry the owner never put in the programme is
        outside it, and inferring a rate from a neighbouring service would be this code deciding a
        price nobody published.
        """

        rule = self.services.get(service_code)
        if rule is None:
            return PromotionServiceRule(service_code, PromotionResolution.NOT_ELIGIBLE, None)
        return rule


@dataclass(frozen=True, slots=True)
class PublishedPromotionProgram:
    """One published `PROMOTION_POLICY` version, parsed, with the provenance a revision records.

    The three provenance fields are what makes a stored discount traceable: a reader years later can
    fetch the exact document that produced it, and `accept_quote_revision` can refuse to finalise a
    price that was quoted under a version no longer in force.
    """

    program: PromotionProgram
    version_id: UUID
    version: int
    #: Already carrying the `JCS-SHA256-V1:` prefix `quotes.HASH_PATTERN` requires, because it goes
    #: straight into a `ConfigurationSnapshotReference`, exactly as `PricebookProvenance` does.
    snapshot_hash: str


def parse_promotion_policy(payload: Mapping[str, Any]) -> PromotionProgram:
    """Read a published payload into a typed programme, or refuse it.

    Run at publication time as the configuration type's registered validator *and* again at read
    time, for the reason `parse_remedy_policy` is: a document that satisfies a shape check and then
    fails to produce a rate at the counter has moved the failure from the moment somebody could fix
    it to the moment somebody needed it.
    """

    if not isinstance(payload, Mapping) or payload.get("schema") != PROMOTION_POLICY_SCHEMA:
        raise PromotionPolicyError("promotion payload is not a promotion-policy-v1 document")
    if payload.get("decision") != "DEC-002":
        raise PromotionPolicyError("a promotion policy must name the decision it expresses")

    code = payload.get("code")
    if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
        # The code is written onto the quote adjustment as its `reason_code`, so it has to satisfy
        # the same pattern every other code on an immutable revision does. A programme whose name
        # cannot be recorded on the money line it produces is not publishable.
        raise PromotionPolicyError("a promotion code must be a canonical code")

    timezone = payload.get("timezone")
    if timezone != "Asia/Ho_Chi_Minh":
        # `promotion._validate_policy` accepts no other zone. Refusing here names the field.
        raise PromotionPolicyError("a promotion programme runs in Asia/Ho_Chi_Minh")

    event = payload.get("eligibility_event")
    try:
        eligibility_event = PromotionEligibilityEvent(str(event))
    except ValueError as error:
        raise PromotionPolicyError(
            "a promotion policy must name the eligibility event it is keyed to"
        ) from error

    stacking = payload.get("stacking_allowed")
    if not isinstance(stacking, bool):
        raise PromotionPolicyError("stacking_allowed must be stated as a boolean")

    policy = PromotionPolicy(
        code=code,
        start_at=_moment(payload, "start_at"),
        end_at_exclusive=_moment(payload, "end_at_exclusive"),
        timezone=timezone,
        eligibility_event=eligibility_event,
        stacking_allowed=stacking,
    )
    try:
        validate_promotion_policy(policy)
    except PromotionError as error:
        raise PromotionPolicyError(f"promotion programme is not evaluable: {error}") from error
    return PromotionProgram(policy=policy, services=_services(payload))


def _services(payload: Mapping[str, Any]) -> Mapping[str, PromotionServiceRule]:
    entries = payload.get("services")
    if not isinstance(entries, list) or not entries:
        raise PromotionPolicyError("a promotion programme must target at least one service")
    rules: dict[str, PromotionServiceRule] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise PromotionPolicyError("each promotion service rule must be an object")
        service_code = entry.get("service_code")
        if not isinstance(service_code, str) or not CODE_PATTERN.fullmatch(service_code):
            raise PromotionPolicyError("a promotion service rule must name a canonical service")
        if service_code in rules:
            # Two rules for one service would give one line two rates, and picking between them
            # here would be this code deciding which discount the owner meant.
            raise PromotionPolicyError(f"service {service_code} is targeted twice")
        try:
            resolution = PromotionResolution(str(entry.get("resolution")))
        except ValueError as error:
            raise PromotionPolicyError(
                f"service {service_code} has no valid promotion resolution"
            ) from error
        rules[service_code] = PromotionServiceRule(
            service_code=service_code,
            resolution=resolution,
            rate_bps=_rate_bps(entry, service_code, resolution),
        )
    return MappingProxyType(rules)


def _rate_bps(
    entry: Mapping[str, Any], service_code: str, resolution: PromotionResolution
) -> int | None:
    rate = entry.get("rate_bps")
    if resolution is PromotionResolution.NOT_ELIGIBLE:
        if rate is not None:
            raise PromotionPolicyError(f"service {service_code} is excluded but carries a rate")
        return None
    # `bool` is excluded because `True` is an `int` in Python: without this, `"rate_bps": true`
    # would publish a one-basis-point discount nobody decided.
    if not isinstance(rate, int) or isinstance(rate, bool) or not 0 < rate <= RATE_DENOMINATOR:
        raise PromotionPolicyError(
            f"service {service_code} must carry a rate in basis points of at most 100%"
        )
    return rate


def _moment(payload: Mapping[str, Any], field: str) -> datetime:
    value = payload.get(field)
    if not isinstance(value, str):
        raise PromotionPolicyError(f"promotion field {field} must be an ISO-8601 moment")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise PromotionPolicyError(f"promotion field {field} is not an ISO-8601 moment") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        # A naive boundary is a boundary in no particular place. The programme's own timezone is
        # published beside it precisely so nobody has to guess which midnight was meant.
        raise PromotionPolicyError(f"promotion field {field} must carry a UTC offset")
    return moment


__all__ = [
    "PROMOTION_COMPONENT",
    "PROMOTION_COMPONENT_VERSION",
    "PROMOTION_POLICY_CONFIG_TYPE",
    "PROMOTION_POLICY_SCHEMA",
    "PROMOTION_POLICY_VERSION",
    "PROMOTION_SNAPSHOT_CONFIG_TYPE",
    "PromotionPolicyError",
    "PromotionProgram",
    "PromotionServiceRule",
    "PublishedPromotionProgram",
    "parse_promotion_policy",
]
