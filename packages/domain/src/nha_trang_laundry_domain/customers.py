"""The shop's customer list: phone normalisation, search classification, the notice, retention.

`CUSTOMER-001`, `DEC-034` (reopens `DEC-015`). A customer is recognised at the counter by phone
number, so the number has one canonical form, and everything that decides about one -- whether a
typed string is a phone at all, which search it asks for, whether a record has gone 24 months
without an order -- is decided here, purely. Nothing in this module reads a clock, a database or the
environment: the instant being judged is always passed in.

**Phone numbers** (Vietnamese numbering plan since the 2018 mobile renumbering):

* a **mobile** number is `0` + 9 digits whose first digit is 3, 5, 7, 8 or 9
  (`03x`, `05x`, `07x`, `08x`, `09x`);
* a **landline** is `0` + 10 digits whose first digit is 2 -- the area code is part of it
  (`0258 xxx xxxx` in Khánh Hòa, `024 xxxx xxxx` in Hà Nội);
* each may be written `0…`, `+84…` or `84…`, with spaces, dots or hyphens between groups.

Anything else is refused `PHONE_INVALID` rather than repaired. In particular `+84 0905…` (the trunk
zero kept after the country code) and the retired 11-digit `01x` mobile numbers are refused: a
number the software "fixed" is a number nobody checked with the customer.

**Search** (`classify_query`): four digits are the last four of a phone; a string that normalises is
a full phone; digits that do not are reported as incomplete or invalid rather than searched as a
name; anything else is a name, folded case- and diacritic-insensitively (`fold_text`: "Chị Lan",
"chi lan" and "CHỊ LAN" are one search, and `đ` folds to `d`).

**Retention** (`DEC-034`): a record with no order for 24 months is erased the way a customer's own
request erases it. "24 months" is calendar months, clamped to the last day of a shorter month.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

#: The decision every refusal and the published notice cite.
CUSTOMER_DECISION: Final = "DEC-034"
#: The `configuration_versions.config_type` the privacy notice is published under.
PRIVACY_NOTICE_CONFIG_TYPE: Final = "CUSTOMER_PRIVACY_NOTICE"
PRIVACY_NOTICE_SCHEMA: Final = "customer-privacy-notice-v1"
#: `DEC-034`: a record with no order for this many months is erased by the retention job.
CUSTOMER_RETENTION_MONTHS: Final = 24

DISPLAY_NAME_MAX: Final = 80
NOTE_MAX: Final = 200
ADDRESS_MAX: Final = 300
#: The spec's bound on one search answer (`SHOP_OPERATIONS_SPEC_V1.md` §1).
SEARCH_LIMIT: Final = 20
#: The shortest folded name a search runs on. One letter matches half the list.
NAME_QUERY_MIN: Final = 2
NOTICE_TEXT_MAX: Final = 6000

_SEPARATORS: Final = re.compile(r"[\s.\-]+")
_MOBILE_FIRST_DIGITS: Final = frozenset("35789")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")


class CustomerRefusal(StrEnum):
    """Why a customer command was refused. Each is a named code the console renders."""

    PHONE_INVALID = "PHONE_INVALID"
    #: The owner has not published the privacy notice: no record may be created (`DEC-034`).
    PRIVACY_NOTICE_UNPUBLISHED = "PRIVACY_NOTICE_UNPUBLISHED"
    #: The staff member did not attest that the customer heard and accepted the notice.
    SERVICE_CONSENT_REQUIRED = "SERVICE_CONSENT_REQUIRED"
    #: A record with this phone already exists in this store; the refusal names it.
    CUSTOMER_PHONE_EXISTS = "CUSTOMER_PHONE_EXISTS"
    #: The record's personal data was erased; it can be read, never edited or linked again.
    CUSTOMER_ERASED = "CUSTOMER_ERASED"
    DISPLAY_NAME_INVALID = "DISPLAY_NAME_INVALID"
    NOTE_TOO_LONG = "NOTE_TOO_LONG"
    ADDRESS_TOO_LONG = "ADDRESS_TOO_LONG"
    #: A link names a ticket or binding this store has no record of.
    LINK_REFERENCE_UNKNOWN = "LINK_REFERENCE_UNKNOWN"
    #: The ticket or binding is already linked to a customer.
    LINK_EXISTS = "LINK_EXISTS"
    NOTHING_TO_CHANGE = "NOTHING_TO_CHANGE"


class CustomerRuleError(ValueError):
    """A refusal by name. `str()` is the code, so a caller never paraphrases it."""

    def __init__(self, code: CustomerRefusal) -> None:
        super().__init__(code.value)
        self.code = code


class PhoneKind(StrEnum):
    MOBILE = "MOBILE"
    LANDLINE = "LANDLINE"


class CustomerKind(StrEnum):
    RETAIL = "RETAIL"
    BUSINESS = "BUSINESS"


class ErasureReason(StrEnum):
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"
    RETENTION = "RETENTION"


class LinkKind(StrEnum):
    COUNTER_TICKET = "COUNTER_TICKET"
    CHANNEL_BINDING = "CHANNEL_BINDING"


class QueryMode(StrEnum):
    #: No query: the newest-activity page of the list.
    EMPTY = "EMPTY"
    PHONE = "PHONE"
    LAST4 = "LAST4"
    NAME = "NAME"
    #: Digits, but fewer than a phone has: nothing is searched yet.
    PHONE_INCOMPLETE = "PHONE_INCOMPLETE"
    #: Digits of a phone's length that are not a Vietnamese number: nothing is searched.
    PHONE_INVALID = "PHONE_INVALID"
    #: Letters, but fewer than `NAME_QUERY_MIN` once folded: nothing is searched.
    TOO_SHORT = "TOO_SHORT"


class MarketingChange(StrEnum):
    GIVEN = "GIVEN"
    WITHDRAWN = "WITHDRAWN"


@dataclass(frozen=True, slots=True)
class Phone:
    """One number in its three forms. `e164` is what is encrypted and digested."""

    e164: str
    national: str
    last4: str
    kind: PhoneKind


@dataclass(frozen=True, slots=True)
class CustomerQuery:
    mode: QueryMode
    #: The e164 number, the four digits, or the folded name; empty for the non-searching modes.
    value: str = ""

    @property
    def searches(self) -> bool:
        return self.mode in (QueryMode.EMPTY, QueryMode.PHONE, QueryMode.LAST4, QueryMode.NAME)


def normalize_phone(raw: str) -> Phone:
    """The canonical form of a Vietnamese phone number, or `PHONE_INVALID`.

    >>> normalize_phone("0905.123.456").e164
    '+84905123456'
    """

    if not isinstance(raw, str):
        raise CustomerRuleError(CustomerRefusal.PHONE_INVALID)
    compact = _SEPARATORS.sub("", raw.strip())
    if compact.startswith("+"):
        if not compact.startswith("+84"):
            raise CustomerRuleError(CustomerRefusal.PHONE_INVALID)
        subscriber = compact[3:]
    elif compact.startswith("84"):
        subscriber = compact[2:]
    elif compact.startswith("0"):
        subscriber = compact[1:]
    else:
        raise CustomerRuleError(CustomerRefusal.PHONE_INVALID)
    if not subscriber.isascii() or not subscriber.isdigit():
        raise CustomerRuleError(CustomerRefusal.PHONE_INVALID)
    if len(subscriber) == 9 and subscriber[0] in _MOBILE_FIRST_DIGITS:
        kind = PhoneKind.MOBILE
    elif len(subscriber) == 10 and subscriber[0] == "2":
        kind = PhoneKind.LANDLINE
    else:
        raise CustomerRuleError(CustomerRefusal.PHONE_INVALID)
    national = f"0{subscriber}"
    return Phone(e164=f"+84{subscriber}", national=national, last4=national[-4:], kind=kind)


def national_from_e164(e164: str) -> str:
    """`+84905123456` -> `0905123456`; refuses anything `normalize_phone` would not produce."""

    return normalize_phone(e164).national


def fold_text(text: str) -> str:
    """Case- and diacritic-insensitive form of a name, for search only (never displayed).

    Decompose, drop the combining marks, fold `đ` to `d` (it is a letter, not a mark, so the
    decomposition keeps it), case-fold, and collapse whitespace.
    """

    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    stripped = stripped.replace("đ", "d").replace("Đ", "D")
    return " ".join(stripped.casefold().split())


def classify_query(raw: str) -> CustomerQuery:
    """Which search a typed string asks for. Digits are never searched as a name."""

    text = (raw or "").strip()
    if not text:
        return CustomerQuery(QueryMode.EMPTY)
    compact = _SEPARATORS.sub("", text)
    digits = compact[1:] if compact.startswith("+") else compact
    if digits and digits.isascii() and digits.isdigit():
        if len(digits) == 4 and not compact.startswith("+"):
            return CustomerQuery(QueryMode.LAST4, digits)
        try:
            return CustomerQuery(QueryMode.PHONE, normalize_phone(text).e164)
        except CustomerRuleError:
            # A national number is 10 or 11 digits with its leading zero; fewer is still being
            # typed, more (or the right length but not Vietnamese) is wrong.
            complete = len(digits) >= (11 if digits.startswith("84") else 10)
            return CustomerQuery(
                QueryMode.PHONE_INVALID if complete else QueryMode.PHONE_INCOMPLETE
            )
    folded = fold_text(text)
    if len(folded) < NAME_QUERY_MIN:
        return CustomerQuery(QueryMode.TOO_SHORT)
    return CustomerQuery(QueryMode.NAME, folded)


def clean_display_name(raw: str | None) -> str | None:
    """The name as the customer gave it, trimmed; `None` when blank."""

    if raw is None:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    if len(text) > DISPLAY_NAME_MAX or _CONTROL.search(text):
        raise CustomerRuleError(CustomerRefusal.DISPLAY_NAME_INVALID)
    return text


def clean_note(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > NOTE_MAX or _CONTROL.search(text.replace("\n", " ")):
        raise CustomerRuleError(CustomerRefusal.NOTE_TOO_LONG)
    return text


def clean_address(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    if len(text) > ADDRESS_MAX or _CONTROL.search(text):
        raise CustomerRuleError(CustomerRefusal.ADDRESS_TOO_LONG)
    return text


def marketing_change(*, active: bool, requested: bool | None) -> MarketingChange | None:
    """What a requested marketing-consent value changes, if anything."""

    if requested is None or requested == active:
        return None
    return MarketingChange.GIVEN if requested else MarketingChange.WITHDRAWN


def _months_back(moment: datetime, months: int) -> datetime:
    total = moment.year * 12 + (moment.month - 1) - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    # The last day of the target month: day 1 of the next month, minus one day, by arithmetic.
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    last_day = (datetime(next_year, next_month, 1) - datetime(year, month, 1)).days
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def retention_cutoff(as_of: datetime) -> datetime:
    """The instant 24 calendar months before `as_of`. Activity at or before it is stale."""

    if as_of.tzinfo is None:
        raise ValueError("retention is judged at a timezone-aware instant")
    return _months_back(as_of, CUSTOMER_RETENTION_MONTHS)


def retention_due(*, last_activity_at: datetime, as_of: datetime) -> bool:
    """Whether a record whose newest activity is `last_activity_at` is erased at `as_of`.

    Due when there has been no activity for the whole 24 months: activity exactly at the cutoff
    instant is 24 months old, so it is due; one microsecond later is not.
    """

    if last_activity_at.tzinfo is None:
        raise ValueError("activity must be timezone-aware")
    return last_activity_at <= retention_cutoff(as_of)


# --- the privacy notice the owner publishes ------------------------------------------------------


class PrivacyNoticeError(ValueError):
    """A notice document that cannot be published as it stands."""


@dataclass(frozen=True, slots=True)
class PrivacyNotice:
    notice_version: str
    legal_entity: str
    hotline: Phone
    retention_months: int
    #: The sentence the staff member reads aloud before ticking service consent.
    consent_sentence_vi: str
    service_consent_label_vi: str
    marketing_consent_label_vi: str
    title_vi: str
    text_vi: str


_NOTICE_KEYS: Final = frozenset(
    {
        "schema",
        "decision_ref",
        "notice_version",
        "legal_entity",
        "hotline_e164",
        "retention_months",
        "consent_sentence_vi",
        "service_consent_label_vi",
        "marketing_consent_label_vi",
        "title_vi",
        "text_vi",
        "source",
    }
)


def parse_privacy_notice(payload: Mapping[str, Any]) -> PrivacyNotice:
    """Validate a notice document; every refusal says which rule it broke."""

    if set(payload) != _NOTICE_KEYS:
        missing = sorted(_NOTICE_KEYS - set(payload))
        extra = sorted(set(payload) - _NOTICE_KEYS)
        raise PrivacyNoticeError(f"notice keys differ: missing {missing}, unexpected {extra}")
    if payload["schema"] != PRIVACY_NOTICE_SCHEMA:
        raise PrivacyNoticeError(f"schema must be {PRIVACY_NOTICE_SCHEMA}")
    if payload["decision_ref"] != CUSTOMER_DECISION:
        raise PrivacyNoticeError(f"decision_ref must be {CUSTOMER_DECISION}")
    if payload["retention_months"] != CUSTOMER_RETENTION_MONTHS or isinstance(
        payload["retention_months"], bool
    ):
        raise PrivacyNoticeError(
            f"retention_months must be {CUSTOMER_RETENTION_MONTHS}, the period DEC-034 decided"
        )
    texts = {}
    for key in (
        "notice_version",
        "legal_entity",
        "consent_sentence_vi",
        "service_consent_label_vi",
        "marketing_consent_label_vi",
        "title_vi",
        "text_vi",
        "source",
    ):
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            raise PrivacyNoticeError(f"{key} must be non-empty text")
        texts[key] = value.strip()
    if len(texts["text_vi"]) > NOTICE_TEXT_MAX:
        raise PrivacyNoticeError(f"text_vi is longer than {NOTICE_TEXT_MAX} characters")
    try:
        hotline = normalize_phone(str(payload["hotline_e164"]))
    except CustomerRuleError as error:
        raise PrivacyNoticeError("hotline_e164 is not a Vietnamese phone number") from error
    if hotline.e164 != payload["hotline_e164"]:
        raise PrivacyNoticeError("hotline_e164 must be written in +84 form")
    # The notice must say who keeps the data, how to reach them, and for how long -- the three facts
    # a customer needs to exercise the right to deletion. Checked in the text itself.
    body = texts["text_vi"]
    if texts["legal_entity"] not in body:
        raise PrivacyNoticeError("the notice text must name the legal entity")
    if hotline.national[-4:] not in _SEPARATORS.sub("", body):
        raise PrivacyNoticeError("the notice text must give the hotline")
    if f"{CUSTOMER_RETENTION_MONTHS} tháng" not in body:
        raise PrivacyNoticeError("the notice text must state the retention period")
    return PrivacyNotice(
        notice_version=texts["notice_version"],
        legal_entity=texts["legal_entity"],
        hotline=hotline,
        retention_months=CUSTOMER_RETENTION_MONTHS,
        consent_sentence_vi=texts["consent_sentence_vi"],
        service_consent_label_vi=texts["service_consent_label_vi"],
        marketing_consent_label_vi=texts["marketing_consent_label_vi"],
        title_vi=texts["title_vi"],
        text_vi=body,
    )


def validate_privacy_notice(payload: Mapping[str, Any]) -> None:
    """The `ConfigurationRepository` validator shape: raise or return nothing."""

    parse_privacy_notice(payload)


_LEGAL_ENTITY_LINE: Final = re.compile(
    r"Pháp nhân vận hành/xuất hóa đơn:\s*\*\*(?P<entity>[^*—]+?)\s*—\s*ĐÃ XÁC NHẬN"
)
_HOTLINE_LINE: Final = re.compile(r"E\.164:\s*\*\*(?P<e164>\+84\d{9,10})\s*—\s*ĐÃ XÁC NHẬN")


def _between(document: str, name: str, *, close: str | None = None) -> str:
    opening = f"<!-- notice:{name} -->"
    closing = close or f"<!-- /notice:{name} -->"
    start = document.find(opening)
    end = document.find(closing, start + len(opening)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise PrivacyNoticeError(f"the notice document has no {name} section")
    return document[start + len(opening) : end].strip()


def notice_payload_from_documents(
    *, notice_markdown: str, business_truth: str, source: str
) -> dict[str, object]:
    """Build the publishable notice from its markdown and the owner-confirmed business facts.

    The legal entity and the hotline are read from `BUSINESS_TRUTH_INTAKE.md`, and only from lines
    marked `ĐÃ XÁC NHẬN`: a notice that named an unconfirmed entity would be the software asserting
    who the data controller is. The texts are the marked sections of the notice document, verbatim.
    """

    entity = _LEGAL_ENTITY_LINE.search(business_truth)
    hotline = _HOTLINE_LINE.search(business_truth)
    if entity is None or hotline is None:
        raise PrivacyNoticeError(
            "BUSINESS_TRUTH_INTAKE.md does not confirm the legal entity and the hotline"
        )
    payload: dict[str, object] = {
        "schema": PRIVACY_NOTICE_SCHEMA,
        "decision_ref": CUSTOMER_DECISION,
        "notice_version": "V1",
        "legal_entity": entity.group("entity").strip(),
        "hotline_e164": hotline.group("e164"),
        "retention_months": CUSTOMER_RETENTION_MONTHS,
        "consent_sentence_vi": _between(notice_markdown, "consent-sentence"),
        "service_consent_label_vi": _between(notice_markdown, "service-consent-label"),
        "marketing_consent_label_vi": _between(notice_markdown, "marketing-consent-label"),
        "title_vi": _between(notice_markdown, "title"),
        "text_vi": _between(notice_markdown, "start", close="<!-- notice:end -->"),
        "source": source,
    }
    parse_privacy_notice(payload)
    return payload


__all__ = [
    "ADDRESS_MAX",
    "CUSTOMER_DECISION",
    "CUSTOMER_RETENTION_MONTHS",
    "DISPLAY_NAME_MAX",
    "NAME_QUERY_MIN",
    "NOTE_MAX",
    "PRIVACY_NOTICE_CONFIG_TYPE",
    "PRIVACY_NOTICE_SCHEMA",
    "SEARCH_LIMIT",
    "CustomerKind",
    "CustomerQuery",
    "CustomerRefusal",
    "CustomerRuleError",
    "ErasureReason",
    "LinkKind",
    "MarketingChange",
    "Phone",
    "PhoneKind",
    "PrivacyNotice",
    "PrivacyNoticeError",
    "QueryMode",
    "classify_query",
    "clean_address",
    "clean_display_name",
    "clean_note",
    "fold_text",
    "marketing_change",
    "national_from_e164",
    "normalize_phone",
    "notice_payload_from_documents",
    "parse_privacy_notice",
    "retention_cutoff",
    "retention_due",
    "validate_privacy_notice",
]
