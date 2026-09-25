"""`PROMISE-001` (`DEC-037`): the promised-ready time, in the shop's opening hours.

The policy under test is built from the real owner-confirmed sheets in `templates/`, with Tết 2027
published (5-10 February 2027) and no other year's -- so every case below is the rule the owner
will publish, not a fixture that could drift from it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv
from nha_trang_laundry_domain.promise import (
    SHOP_TIMEZONE,
    PromiseChoice,
    Promised,
    PromiseError,
    PromiseNeedsHuman,
    PromiseReason,
    PromiseRefusal,
    PromiseRequirement,
    PromiseState,
    TurnaroundPolicy,
    TurnaroundPolicyError,
    TurnaroundScope,
    add_calendar_hours,
    add_opening_hours,
    compute_promise,
    met_first_promise,
    parse_turnaround_policy,
    promise_figures,
    promise_options,
    promise_state,
    scope_for_service,
)
from nha_trang_laundry_domain.turnaround_source import build_turnaround_policy

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "templates"
TET_2027 = [date(2027, 2, day) for day in range(5, 11)]
VN = ZoneInfo("Asia/Ho_Chi_Minh")

STANDARD = "STANDARD_WASH_DRY"
BLANKET = "BED_BLANKET"
SHOES = "SHOE_SPORTS"
CURTAIN = "OTHER_CURTAIN"
PLUSH = "OTHER_PLUSH"
PILLOW = "BED_PILLOW"


def _document(
    *,
    sla_csv: str | None = None,
    calendar_csv: str | None = None,
    tet: list[date] | None = None,
    adhoc: list[date] | None = None,
) -> dict[str, object]:
    pricebook = (TEMPLATES / "services-pricebook.csv").read_bytes()
    book = import_pricebook_csv(pricebook)
    return build_turnaround_policy(
        sla_csv=sla_csv or (TEMPLATES / "service-sla.csv").read_text(encoding="utf-8"),
        calendar_csv=calendar_csv
        or (TEMPLATES / "business-calendar-rules.csv").read_text(encoding="utf-8"),
        services=[(service.code, service.category) for service in book.services],
        tet_dates=TET_2027 if tet is None else tet,
        adhoc_dates=adhoc or [],
        pricebook_sha256=hashlib.sha256(pricebook).hexdigest(),
    )


POLICY: TurnaroundPolicy = parse_turnaround_policy(_document())


def at(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=VN)


def promised(accepted: datetime, codes: list[str], **kwargs: object) -> datetime:
    result = compute_promise(POLICY, accepted_at=accepted, service_codes=codes, **kwargs)  # type: ignore[arg-type]
    assert isinstance(result, Promised), result
    return result.promised_at.astimezone(VN)


# --- opening-hour boundaries (standard clothing, 8 opening hours) -----------------------------


@pytest.mark.parametrize(
    ("accepted", "expected"),
    [
        # DEC-037's own example: accepted at 17:00, promised 13:00 the next open day (thứ Sáu 25/9
        # 2026 -> thứ Bảy 26/9), not 01:00.
        (at(2026, 9, 25, 17), at(2026, 9, 26, 13)),
        (at(2026, 9, 25, 8), at(2026, 9, 25, 16)),
        # Ends exactly at closing: 20:00 is the promise, not tomorrow 08:00.
        (at(2026, 9, 25, 12), at(2026, 9, 25, 20)),
        (at(2026, 9, 25, 12, 1), at(2026, 9, 26, 8, 1)),
        # Accepted at or after closing: the clock starts at the next opening.
        (at(2026, 9, 25, 20), at(2026, 9, 26, 16)),
        (at(2026, 9, 25, 21, 30), at(2026, 9, 26, 16)),
        # Before opening: the clock waits for 08:00.
        (at(2026, 9, 25, 6), at(2026, 9, 25, 16)),
        # Floored to the minute, never later than the rule.
        (at(2026, 9, 25, 17, 0, 45), at(2026, 9, 26, 13)),
        # Across midnight at the year's end: the shop is open on 1 January.
        (at(2026, 12, 31, 19), at(2027, 1, 1, 15)),
    ],
)
def test_standard_clothing_is_eight_opening_hours(accepted: datetime, expected: datetime) -> None:
    result = compute_promise(POLICY, accepted_at=accepted, service_codes=[STANDARD])
    assert isinstance(result, Promised)
    assert result.promised_at.astimezone(VN) == expected
    assert result.basis == "RULE"
    assert result.rule_id == "SLA_STANDARD_CLOTHES"


# --- closed days ------------------------------------------------------------------------------


def test_30_april_and_1_may_are_skipped() -> None:
    # 29/4 15:00: five hours to closing, three left; 30/4 and 1/5 are closed; 2/5 11:00.
    assert promised(at(2027, 4, 29, 15), [STANDARD]) == at(2027, 5, 2, 11)


def test_accepted_on_a_closed_day_starts_at_the_next_opening() -> None:
    assert promised(at(2027, 4, 30, 10), [STANDARD]) == at(2027, 5, 2, 16)


def test_published_tet_days_are_skipped() -> None:
    # 4/2/2027 16:00: four hours today, four left; 5-10/2 is Tết; 11/2 12:00.
    assert promised(at(2027, 2, 4, 16), [STANDARD]) == at(2027, 2, 11, 12)


def test_an_owner_ad_hoc_closed_day_is_skipped() -> None:
    policy = parse_turnaround_policy(_document(adhoc=[date(2026, 9, 26)]))
    result = compute_promise(policy, accepted_at=at(2026, 9, 25, 17), service_codes=[STANDARD])
    assert isinstance(result, Promised)
    assert result.promised_at.astimezone(VN) == at(2026, 9, 27, 13)


# --- a year whose Tết is not published --------------------------------------------------------


def test_a_promise_crossing_an_unpublished_tet_needs_a_person() -> None:
    result = compute_promise(POLICY, accepted_at=at(2028, 1, 20, 10), service_codes=[STANDARD])
    assert result == PromiseNeedsHuman((PromiseReason.TET_DATES_UNPUBLISHED,), ())


def test_running_into_the_window_is_enough_to_stop() -> None:
    # 14/1/2028 17:00 would finish on 15/1, the first day Tết 2028 could touch.
    result = compute_promise(POLICY, accepted_at=at(2028, 1, 14, 17), service_codes=[STANDARD])
    assert isinstance(result, PromiseNeedsHuman)
    assert result.reason_codes == (PromiseReason.TET_DATES_UNPUBLISHED,)


def test_the_same_day_before_the_window_is_still_promised() -> None:
    assert promised(at(2028, 1, 14, 10), [STANDARD]) == at(2028, 1, 14, 18)


def test_after_the_window_the_rule_applies_again() -> None:
    assert promised(at(2028, 3, 1, 17), [STANDARD]) == at(2028, 3, 2, 13)


def test_a_published_year_promises_through_january() -> None:
    assert promised(at(2027, 1, 20, 17), [STANDARD]) == at(2027, 1, 21, 13)


def test_a_person_may_set_a_time_across_an_unpublished_tet() -> None:
    chosen = at(2028, 1, 22, 10)
    result = compute_promise(
        POLICY,
        accepted_at=at(2028, 1, 20, 10),
        service_codes=[STANDARD],
        choice=PromiseChoice.CUSTOM,
        custom_at=chosen,
    )
    assert isinstance(result, Promised)
    assert result.promised_at == chosen.astimezone(UTC)
    assert (result.basis, result.rule_id) == ("CUSTOM", "STAFF_SET")


# --- 24/48 h lines, special items, mixed orders -----------------------------------------------


# Founder ruling on DEC-037 (2026-09-25): "24 giờ / 48 giờ" is one or two calendar days, as a
# Vietnamese customer hears it -- clock hours, then rolled into opening hours. Only the 8 h and the
# 2 h are opening hours.


@pytest.mark.parametrize("code", [BLANKET, SHOES, CURTAIN, "OTHER_CURTAIN_INSTALL"])
def test_a_range_line_defaults_to_48_calendar_hours(code: str) -> None:
    result = compute_promise(POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=[code])
    assert isinstance(result, Promised)
    # Friday 17:00 + 48 h is Sunday 17:00 (it was Tuesday 17:00 in opening hours).
    assert result.promised_at.astimezone(VN) == at(2026, 9, 27, 17)
    assert result.basis == "H48"
    assert result.lines[0].counting == "CALENDAR_HOURS"


def test_a_range_line_takes_24_hours_by_choice() -> None:
    assert promised(at(2026, 9, 25, 17), [BLANKET], choice=PromiseChoice.H24) == at(2026, 9, 26, 17)


@pytest.mark.parametrize(
    ("accepted", "choice", "expected"),
    [
        # 19:30 + 24 h is 19:30 the next day: inside opening hours, untouched.
        (at(2026, 9, 25, 19, 30), PromiseChoice.H24, at(2026, 9, 26, 19, 30)),
        # Landing at 07:30 moves to 08:00 the same day.
        (at(2026, 9, 25, 7, 30), PromiseChoice.H24, at(2026, 9, 26, 8)),
        # Landing exactly at closing is a promise; a minute after closing is tomorrow 08:00.
        (at(2026, 9, 25, 20), PromiseChoice.H24, at(2026, 9, 26, 20)),
        (at(2026, 9, 25, 20, 1), PromiseChoice.H24, at(2026, 9, 27, 8)),
        # Landing on 30/4 (closed), 1/5 closed too: 08:00 on 2/5.
        (at(2027, 4, 29, 10), PromiseChoice.H24, at(2027, 5, 2, 8)),
        (at(2027, 4, 28, 15), PromiseChoice.H48, at(2027, 5, 2, 8)),
        # Landing inside a published Tết: 08:00 on the first day after it.
        (at(2027, 2, 4, 10), PromiseChoice.H24, at(2027, 2, 11, 8)),
        # Floored to the minute.
        (at(2026, 9, 25, 15, 0, 59), PromiseChoice.H48, at(2026, 9, 27, 15)),
    ],
)
def test_a_range_line_lands_in_opening_hours(
    accepted: datetime, choice: PromiseChoice, expected: datetime
) -> None:
    assert promised(accepted, [BLANKET], choice=choice) == expected


def test_a_range_line_across_an_unpublished_tet_needs_a_person() -> None:
    # 13/1/2028 + 48 h lands on 15/1/2028, inside the window of a year without published Tết days.
    result = compute_promise(
        POLICY, accepted_at=at(2028, 1, 13, 10), service_codes=[BLANKET], choice=PromiseChoice.H48
    )
    assert result == PromiseNeedsHuman((PromiseReason.TET_DATES_UNPUBLISHED,), ())
    # The day before the window it is promised as usual.
    assert promised(at(2028, 1, 12, 10), [BLANKET], choice=PromiseChoice.H48) == at(2028, 1, 14, 10)


def test_the_latest_line_wins() -> None:
    mixed = compute_promise(
        POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=[STANDARD, BLANKET]
    )
    assert isinstance(mixed, Promised)
    # The blanket's Sunday 17:00 is later than the shirts' Saturday 13:00.
    assert mixed.promised_at.astimezone(VN) == at(2026, 9, 27, 17)
    assert mixed.rule_id == "SLA_BLANKETS_SHEETS"
    assert [line.hours for line in mixed.lines] == [8, 48]
    assert mixed.trace()["lines"] == [
        {
            "service_code": STANDARD,
            "scope": "STANDARD_CLOTHES",
            "sla_id": "SLA_STANDARD_CLOTHES",
            "hours": 8,
            "counting": "OPENING_HOURS",
        },
        {
            "service_code": BLANKET,
            "scope": "BLANKETS_AND_SHEETS",
            "sla_id": "SLA_BLANKETS_SHEETS",
            "hours": 48,
            "counting": "CALENDAR_HOURS",
        },
    ]


def test_the_latest_line_wins_when_it_is_the_opening_hours_one() -> None:
    # Accepted 19:30: the shirts' 8 opening hours end 15:30 the next day, the blanket's 24 calendar
    # hours at 19:30 the next day -- and 2 h express shirts beside a blanket are refused elsewhere.
    mixed = compute_promise(
        POLICY,
        accepted_at=at(2026, 9, 25, 19, 30),
        service_codes=[STANDARD, BLANKET],
        choice=PromiseChoice.H24,
    )
    assert isinstance(mixed, Promised)
    assert mixed.promised_at.astimezone(VN) == at(2026, 9, 26, 19, 30)
    # Accepted 07:00 on a day before two closed days: the shirts end at 16:00 the same day, the
    # blanket's 24 h lands on closed 30/4 and moves to 2/5 08:00, which wins.
    later = compute_promise(
        POLICY,
        accepted_at=at(2027, 4, 29, 7),
        service_codes=[STANDARD, BLANKET],
        choice=PromiseChoice.H24,
    )
    assert isinstance(later, Promised)
    assert later.promised_at.astimezone(VN) == at(2027, 5, 2, 8)


@pytest.mark.parametrize("codes", [[PLUSH], [STANDARD, PILLOW], [BLANKET, "BED_TOPPER"]])
def test_a_special_item_needs_a_person(codes: list[str]) -> None:
    result = compute_promise(POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=codes)
    assert isinstance(result, PromiseNeedsHuman)
    assert result.reason_codes == (PromiseReason.HUMAN_ETA_REQUIRED,)
    assert result.service_codes == (codes[-1],)


def test_a_special_item_with_a_24_48_choice_still_needs_a_person() -> None:
    result = compute_promise(
        POLICY,
        accepted_at=at(2026, 9, 25, 17),
        service_codes=[BLANKET, PLUSH],
        choice=PromiseChoice.H24,
    )
    assert isinstance(result, PromiseNeedsHuman)


def test_a_service_the_policy_does_not_name_needs_a_person() -> None:
    result = compute_promise(POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=["NEW_THING"])
    assert result == PromiseNeedsHuman((PromiseReason.SERVICE_NOT_IN_POLICY,), ("NEW_THING",))


def test_an_order_without_lines_is_not_promised() -> None:
    result = compute_promise(POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=[])
    assert result == PromiseNeedsHuman((PromiseReason.NO_LINES,), ())


# --- express and custom -----------------------------------------------------------------------


def test_express_is_two_opening_hours_for_standard_clothing() -> None:
    result = compute_promise(
        POLICY,
        accepted_at=at(2026, 9, 25, 19),
        service_codes=[STANDARD, STANDARD],
        choice=PromiseChoice.EXPRESS_2H,
    )
    assert isinstance(result, Promised)
    assert result.promised_at.astimezone(VN) == at(2026, 9, 26, 9)
    assert (result.basis, result.rule_id) == ("EXPRESS_2H", "EXPRESS_2H")


@pytest.mark.parametrize(
    ("codes", "choice"),
    [
        ([STANDARD, BLANKET], PromiseChoice.EXPRESS_2H),
        ([PLUSH], PromiseChoice.EXPRESS_2H),
        ([STANDARD], PromiseChoice.H24),
        ([STANDARD], PromiseChoice.H48),
    ],
)
def test_a_choice_that_does_not_fit_the_lines_is_refused(
    codes: list[str], choice: PromiseChoice
) -> None:
    with pytest.raises(PromiseError) as refused:
        compute_promise(POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=codes, choice=choice)
    assert refused.value.code is PromiseRefusal.PROMISE_CHOICE_NOT_APPLICABLE


@pytest.mark.parametrize(
    ("custom_at", "code"),
    [
        (at(2026, 9, 26, 21), PromiseRefusal.PROMISE_OUTSIDE_OPENING_HOURS),
        (at(2026, 9, 26, 7, 59), PromiseRefusal.PROMISE_OUTSIDE_OPENING_HOURS),
        (at(2027, 4, 30, 10), PromiseRefusal.PROMISE_ON_CLOSED_DAY),
        (at(2027, 2, 7, 10), PromiseRefusal.PROMISE_ON_CLOSED_DAY),
        (at(2026, 9, 25, 16), PromiseRefusal.PROMISE_NOT_AFTER_ACCEPTANCE),
        (at(2026, 9, 25, 17), PromiseRefusal.PROMISE_NOT_AFTER_ACCEPTANCE),
    ],
)
def test_a_custom_time_must_be_later_open_and_in_hours(
    custom_at: datetime, code: PromiseRefusal
) -> None:
    with pytest.raises(PromiseError) as refused:
        compute_promise(
            POLICY,
            accepted_at=at(2026, 9, 25, 17),
            service_codes=[PLUSH],
            choice=PromiseChoice.CUSTOM,
            custom_at=custom_at,
        )
    assert refused.value.code is code


def test_a_custom_time_at_opening_and_at_closing_is_taken() -> None:
    for chosen in (at(2026, 9, 26, 8), at(2026, 9, 26, 20)):
        assert (
            promised(at(2026, 9, 25, 17), [PLUSH], choice=PromiseChoice.CUSTOM, custom_at=chosen)
            == chosen
        )


def test_custom_needs_its_time_and_a_time_needs_custom() -> None:
    with pytest.raises(PromiseError) as missing:
        compute_promise(
            POLICY,
            accepted_at=at(2026, 9, 25, 17),
            service_codes=[PLUSH],
            choice=PromiseChoice.CUSTOM,
        )
    assert missing.value.code is PromiseRefusal.PROMISE_CUSTOM_AT_REQUIRED
    with pytest.raises(PromiseError) as stray:
        compute_promise(
            POLICY,
            accepted_at=at(2026, 9, 25, 17),
            service_codes=[STANDARD],
            custom_at=at(2026, 9, 26, 10),
        )
    assert stray.value.code is PromiseRefusal.PROMISE_CUSTOM_AT_NOT_TAKEN


# --- what the counter is offered before Nhận đồ -----------------------------------------------


def test_options_for_standard_clothing_offer_express_and_custom() -> None:
    options = promise_options(POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=[STANDARD])
    assert options.requirement is PromiseRequirement.NONE
    assert options.default_promised_at == at(2026, 9, 26, 13)
    assert [(item.choice, item.promised_at) for item in options.choices] == [
        (PromiseChoice.EXPRESS_2H, at(2026, 9, 25, 19)),
        (PromiseChoice.CUSTOM, None),
    ]


def test_options_for_a_blanket_offer_24_and_48_with_48_by_default() -> None:
    options = promise_options(
        POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=[STANDARD, BLANKET]
    )
    assert options.requirement is PromiseRequirement.RANGE_CHOICE
    assert options.default_choice is PromiseChoice.H48
    assert options.default_promised_at == at(2026, 9, 27, 17)
    assert [(item.choice, item.promised_at) for item in options.choices] == [
        (PromiseChoice.H24, at(2026, 9, 26, 17)),
        (PromiseChoice.H48, at(2026, 9, 27, 17)),
        (PromiseChoice.CUSTOM, None),
    ]


def test_options_for_a_special_item_ask_for_a_time() -> None:
    options = promise_options(
        POLICY, accepted_at=at(2026, 9, 25, 17), service_codes=[STANDARD, PLUSH]
    )
    assert options.requirement is PromiseRequirement.CUSTOM
    assert options.default_promised_at is None
    assert options.human_service_codes == (PLUSH,)
    assert options.reason_codes == (PromiseReason.HUMAN_ETA_REQUIRED,)


# --- state, on-time, board figures ------------------------------------------------------------


PROMISE = at(2026, 9, 26, 13)


@pytest.mark.parametrize(
    ("ready_at", "now", "state"),
    [
        (None, at(2026, 9, 26, 10), PromiseState.ON_TRACK),
        (None, at(2026, 9, 26, 11), PromiseState.DUE_SOON),
        (None, at(2026, 9, 26, 13), PromiseState.DUE_SOON),
        (None, at(2026, 9, 26, 13, 1), PromiseState.LATE),
        (at(2026, 9, 26, 13), at(2026, 9, 27, 9), PromiseState.MET),
        (at(2026, 9, 26, 13, 1), at(2026, 9, 26, 14), PromiseState.MISSED),
    ],
)
def test_promise_state(ready_at: datetime | None, now: datetime, state: PromiseState) -> None:
    assert promise_state(PROMISE, ready_at=ready_at, now=now) is state


def test_no_promise_has_no_state() -> None:
    assert promise_state(None, ready_at=None, now=PROMISE) is None


def test_on_time_counts_against_the_first_promise() -> None:
    assert met_first_promise(PROMISE, PROMISE)
    assert not met_first_promise(PROMISE, PROMISE + timedelta(minutes=1))


def test_board_figures_carry_the_side_of_the_mark_in_which_figure_is_non_zero() -> None:
    accepted = at(2026, 9, 25, 17)
    running = promise_figures(PROMISE, accepted_at=accepted, ready_at=None, now=at(2026, 9, 26, 12))
    assert (running.outcome, running.remaining_microseconds, running.breach_microseconds) == (
        "PENDING",
        3_600_000_000,
        0,
    )
    late = promise_figures(PROMISE, accepted_at=accepted, ready_at=None, now=at(2026, 9, 26, 15))
    assert (late.outcome, late.remaining_microseconds, late.breach_microseconds) == (
        "BREACHED",
        0,
        7_200_000_000,
    )
    finished = promise_figures(
        PROMISE, accepted_at=accepted, ready_at=at(2026, 9, 26, 9), now=at(2026, 9, 27, 9)
    )
    assert (finished.outcome, finished.remaining_microseconds) == ("MET", 4 * 3_600_000_000)


# --- the policy document and its sources ------------------------------------------------------


def test_the_price_list_maps_to_the_owner_sla_scopes() -> None:
    assert scope_for_service("STANDARD_WASH_DRY", "standard_weight") is (
        TurnaroundScope.STANDARD_CLOTHES
    )
    assert scope_for_service("SHOE_SUEDE", "shoes") is TurnaroundScope.SHOES
    assert scope_for_service("OTHER_CURTAIN_INSTALL", "other") is TurnaroundScope.CURTAINS
    assert scope_for_service("BED_BLANKET", "bedding") is TurnaroundScope.BLANKETS_AND_SHEETS
    # service-sla.csv SLA_OTHER_SPECIAL names toppers and pillows as special.
    assert scope_for_service("BED_TOPPER", "bedding") is TurnaroundScope.OTHER_SPECIAL
    assert scope_for_service("BED_PILLOW", "bedding") is TurnaroundScope.OTHER_SPECIAL
    assert scope_for_service("DRY_BEDDING", "drying") is TurnaroundScope.OTHER_SPECIAL
    assert scope_for_service("DC_SUIT", "dry_cleaning") is TurnaroundScope.OTHER_SPECIAL


def test_the_document_says_what_the_sheets_say() -> None:
    document = _document()
    assert document["opening_hours"] == {"opens": "08:00", "closes": "20:00"}
    assert document["recurring_closed"] == ["04-30", "05-01"]
    assert document["tet"] == {"2027": [day.isoformat() for day in TET_2027]}
    assert document["express_hours"] == 2
    scopes = document["scopes"]
    assert isinstance(scopes, dict)
    assert scopes["STANDARD_CLOTHES"] == {
        "sla_id": "SLA_STANDARD_CLOTHES",
        "kind": "FIXED",
        "hours": 8,
    }
    assert scopes["BLANKETS_AND_SHEETS"]["choices_hours"] == [24, 48]
    assert scopes["BLANKETS_AND_SHEETS"]["default_hours"] == 48
    assert scopes["OTHER_SPECIAL"] == {"sla_id": "SLA_OTHER_SPECIAL", "kind": "HUMAN"}


@pytest.mark.parametrize(
    "tet",
    [
        TET_2027[:5],
        [*TET_2027[:5], TET_2027[0]],
        [*TET_2027[:5], date(2028, 2, 1)],
        [*TET_2027[:5], date(2027, 3, 1)],
    ],
)
def test_tet_is_six_days_of_one_year_in_its_window(tet: list[date]) -> None:
    with pytest.raises(TurnaroundPolicyError):
        _document(tet=tet)


def test_more_ad_hoc_days_than_the_sheet_allows_are_refused() -> None:
    with pytest.raises(TurnaroundPolicyError):
        _document(adhoc=[date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)])


def test_a_sheet_that_no_longer_says_it_refuses_the_build() -> None:
    sla = (TEMPLATES / "service-sla.csv").read_text(encoding="utf-8")
    with pytest.raises(TurnaroundPolicyError):
        _document(sla_csv=sla.replace("fastest 2h", "fastest two hours"))
    with pytest.raises(TurnaroundPolicyError):
        _document(
            sla_csv=sla.replace(
                "SLA_SHOES,shoes,24,48,accepted_at", "SLA_SHOES,shoes,24,48,created_at"
            )
        )
    calendar = (TEMPLATES / "business-calendar-rules.csv").read_text(encoding="utf-8")
    with pytest.raises(TurnaroundPolicyError):
        _document(calendar_csv=calendar.replace("TET_CLOSE,CLOSE,6", "TET_CLOSE,CLOSE,5"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("policy_version", "turnaround-policy-v0"),
        ("decision_ref", "DEC-036"),
        ("timezone", "UTC"),
        ("opening_hours", {"opens": "20:00", "closes": "08:00"}),
        ("express_hours", 0),
        ("recurring_closed", ["02-30"]),
    ],
)
def test_a_malformed_document_is_not_a_policy(key: str, value: object) -> None:
    document = _document()
    document[key] = value
    with pytest.raises(TurnaroundPolicyError):
        parse_turnaround_policy(document)


def test_a_document_missing_a_scope_is_not_a_policy() -> None:
    document = _document()
    scopes = dict(document["scopes"])  # type: ignore[call-overload]
    scopes.pop("SHOES")
    document["scopes"] = scopes
    with pytest.raises(TurnaroundPolicyError):
        parse_turnaround_policy(document)


# --- properties -------------------------------------------------------------------------------


def _open_minutes_between(start: datetime, end: datetime) -> int:
    """An independent oracle: walk minute by minute and count the ones the shop is open."""

    cursor = start.astimezone(VN).replace(second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(minutes=1)
    count = 0
    while cursor < end:
        day = cursor.date()
        opened = datetime.combine(day, POLICY.opens_at, VN)
        closed = datetime.combine(day, POLICY.closes_at, VN)
        if not POLICY.is_closed(day) and opened <= cursor < closed:
            count += 1
        cursor += timedelta(minutes=1)
    return count


@settings(max_examples=60, deadline=None)
@given(
    offset=st.integers(min_value=0, max_value=200 * 24 * 60),
    hours=st.sampled_from([2, 8, 24, 48]),
)
def test_every_promise_is_exactly_the_hours_in_opening_time(offset: int, hours: int) -> None:
    accepted = at(2026, 3, 1, 0) + timedelta(minutes=offset)
    result = add_opening_hours(POLICY, accepted, hours)
    # March to September 2026 is outside every Tết window, so the walk always ends.
    assert result is not None
    local = result.astimezone(VN)
    assert local > accepted
    assert not POLICY.is_closed(local.date())
    assert POLICY.opens_at < local.time() <= POLICY.closes_at
    assert _open_minutes_between(accepted, local) == hours * 60


@settings(max_examples=60, deadline=None)
@given(
    first=st.integers(min_value=0, max_value=60 * 24 * 60),
    gap=st.integers(min_value=0, max_value=3 * 24 * 60),
)
def test_a_later_acceptance_never_gets_an_earlier_promise(first: int, gap: int) -> None:
    early = at(2026, 9, 1, 0) + timedelta(minutes=first)
    late = early + timedelta(minutes=gap)
    assert promised(early, [STANDARD, BLANKET]) <= promised(late, [STANDARD, BLANKET])


@settings(max_examples=80, deadline=None)
@given(
    offset=st.integers(min_value=0, max_value=200 * 24 * 60 * 60),
    hours=st.sampled_from([24, 48]),
)
def test_every_calendar_promise_is_the_first_opening_minute_at_or_after_the_clock(
    offset: int, hours: int
) -> None:
    accepted = at(2026, 3, 1, 0) + timedelta(seconds=offset)
    result = add_calendar_hours(POLICY, accepted, hours)
    assert result is not None
    local = result.astimezone(VN)
    clock = (accepted + timedelta(hours=hours)).astimezone(VN).replace(second=0, microsecond=0)
    assert local >= clock
    assert not POLICY.is_closed(local.date())
    assert POLICY.opens_at <= local.time() <= POLICY.closes_at
    # Nothing in between is an open minute the promise skipped: it is the clock itself, or the
    # opening of the first open day after it.
    assert local == clock or local.time() == POLICY.opens_at


def test_the_fixed_offset_is_asia_ho_chi_minh() -> None:
    for moment in (datetime(1990, 1, 1, tzinfo=UTC), datetime(2030, 7, 1, 12, tzinfo=UTC)):
        assert moment.astimezone(SHOP_TIMEZONE).utcoffset() == moment.astimezone(VN).utcoffset()
