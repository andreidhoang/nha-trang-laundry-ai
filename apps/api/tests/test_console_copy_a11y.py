"""CONSOLE-COPY-A11Y-009: what the counter reads, and when (review findings C8 and C9).

- **C8.** A FastAPI 422 put its wire path and its English on the screen ("lines.0.quantity -- Input
  should be greater than 0"), and a screen that crashed showed the JavaScript message as its only
  sentence. `core/errors.js` now names refused fields in Vietnamese from one map, says "Một ô nhập
  chưa hợp lệ" for a field it has no name for, and keeps the path and the English for "Chi tiết kỹ
  thuật". The map is held to the API's own field names here, so a renamed field cannot leave a
  stale label behind.
- **C9.** The same moment read four ways on four screens. `core/format.js` now owns one convention
  (documented at its top) and every date and time on the console goes through it. The formats are
  pinned under Node here, in more than one device time zone, and a scan refuses a date formatted
  anywhere else.

The rendered halves -- the notice, the crash screen, the accessible names of the inputs on Nhận đồ
and Thu tiền (C10) -- are section 28 of `scripts/verify_console_interaction.py`, because they need
a real browser.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any

import pytest
from nha_trang_laundry_api.main import app

ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run(script: str, *, tz: str = "UTC") -> Any:
    """Execute an ES module against a copy of the client tree; return what it prints as JSON."""
    with tempfile.TemporaryDirectory() as directory:
        target = pathlib.Path(directory)
        shutil.copytree(WEB / "src", target / "src")
        (target / "package.json").write_text(json.dumps({"type": "module"}))
        entry = target / "check.mjs"
        entry.write_text(script)
        result = subprocess.run(
            ["node", str(entry)],
            capture_output=True,
            text=True,
            cwd=target,
            timeout=60,
            env={**os.environ, "TZ": tz},
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)


# --- C9: one convention, pinned -------------------------------------------------------------------

#: The shop's "now" for every pin: Wednesday 30/09/2026, 10:00 in Nha Trang.
_NOW = "2026-09-30T03:00:00Z"

#: (call, expected). Instants are UTC on the wire; the shop is UTC+7 all year.
_PINS: list[tuple[str, str]] = [
    # a time: hours and minutes, never seconds
    ("f.clock('2026-09-26T08:05:33Z')", "15:05"),
    ("f.clock('2026-09-26T17:00:00Z')", "00:00"),
    ("f.clock(null)", "—"),
    # a moment: time first, DD/MM, the year only when not this one
    ("f.dateTime('2026-09-26T08:05:33Z', {now})", "15:05 26/09"),
    ("f.dateTime('2026-09-26T08:05:33+00:00', {now})", "15:05 26/09"),
    ("f.dateTime('2026-01-05T01:02:00Z', {now})", "08:02 05/01"),
    ("f.dateTime('2025-12-31T16:59:00Z', {now})", "23:59 31/12/2025"),
    # 17:30 UTC on 31/12/2025 is already 1 January in the shop: the shop's year decides
    ("f.dateTime('2025-12-31T17:30:00Z', {now})", "00:30 01/01"),
    ("f.dateTime('2027-01-01T01:00:00Z', {now})", "08:00 01/01/2027"),
    # paper and records always print the year
    ("f.dateTime('2026-09-26T08:05:33Z', {now, year: true})", "15:05 26/09/2026"),
    ("f.dateTime('garbage', {now})", "—"),
    # the day of a moment
    ("f.dateOnly('2026-09-26T08:05:33Z', {now})", "26/09"),
    ("f.dateOnly('2026-09-26T17:05:33Z', {now})", "27/09"),
    ("f.dateOnly('2025-09-26T08:05:33Z', {now})", "26/09/2025"),
    ("f.dateOnly('2026-09-26T08:05:33Z', {now, year: true})", "26/09/2026"),
    # a calendar day the server named
    ("f.calendarDay('2026-09-26', {now})", "Thứ Bảy 26/09"),
    ("f.calendarDay('2026-09-27', {now})", "Chủ nhật 27/09"),
    ("f.calendarDay('2026-09-28', {now})", "Thứ Hai 28/09"),
    ("f.calendarDay('2026-09-26', {now, weekday: false})", "26/09"),
    ("f.calendarDay('2026-09-26', {now, weekday: false, year: true})", "26/09/2026"),
    ("f.calendarDay('2025-09-26', {now, weekday: false})", "26/09/2025"),
    ("f.calendarDay('2026-9-26', {now})", "—"),
    # the promised-ready time (DEC-037), and its list-row form
    ("f.promiseTime('2026-09-26T06:00:00Z', {now})", "13:00 thứ Bảy 26/09"),
    ("f.promiseTime('2026-09-27T10:00:00Z', {now})", "17:00 Chủ nhật 27/09"),
    ("f.promiseTime('2026-09-26T06:00:00Z', {now, short: true})", "13:00 26/09"),
    ("f.promiseTime('2027-01-02T06:00:00Z', {now})", "13:00 thứ Bảy 02/01/2027"),
    ("f.promiseTime(undefined, {now})", "—"),
    # when a list was read
    ("f.updatedAt(new Date('2026-09-26T08:05:33Z'))", "Cập nhật lúc 15:05"),
    # a month
    ("f.monthLabel('2026-09')", "Tháng 9/2026"),
    ("f.monthLabel('2026-9')", "—"),
    ("f.shiftMonth('2026-01', -1)", "2025-12"),
    ("f.shiftMonth('2026-12', 1)", "2027-01"),
    # the datetime-local round trip, on the shop's clock
    ("f.shopLocalInput('2026-09-26T17:00:05Z')", "2026-09-27T00:00"),
    ("f.shopInstantFromInput('2026-09-27T00:00')", "2026-09-27T00:00:00+07:00"),
    ("f.shopInstantFromInput('2026-09-27T00:00:05')", "2026-09-27T00:00:05+07:00"),
    ("f.shopInstantFromInput('2026-09-27')", ""),
    ("String(f.shopHour(new Date('2026-09-26T17:00:05Z')))", "0"),
    ("String(f.shopHour(new Date('2026-09-26T10:59:00Z')))", "17"),
]


def _pins(tz: str) -> dict[str, str]:
    calls = ",\n".join(f"  [{json.dumps(call)}, () => {call}]" for call, _ in _PINS)
    return dict(
        _run(
            f"""
const f = await import("./src/core/format.js");
const now = new Date("{_NOW}");
const out = {{}};
for (const [name, run] of [
{calls}
]) {{
  try {{ out[name] = run(); }} catch (error) {{ out[name] = "ERROR " + error.message; }}
}}
console.log(JSON.stringify(out));
""",
            tz=tz,
        )
    )


@needs_node
@pytest.mark.parametrize("tz", ["UTC", "Asia/Ho_Chi_Minh", "America/Los_Angeles", "Asia/Tokyo"])
def test_every_date_and_time_has_one_spelling_whatever_the_phone_zone(tz: str) -> None:
    got = _pins(tz)
    wrong = {call: (got.get(call), want) for call, want in _PINS if got.get(call) != want}
    assert wrong == {}, wrong


@needs_node
def test_format_js_documents_its_convention_at_the_top() -> None:
    head = (WEB / "src" / "core" / "format.js").read_text(encoding="utf-8").split("@module")[0]
    for example in (
        "15:05 26/09",
        "15:05 26/09/2025",
        "15:05 thứ Bảy 26/09",
        "Cập nhật lúc 15:05",
        "Asia/Ho_Chi_Minh",
    ):
        assert example in head, example


#: A date formatted outside `format.js`: the engine's own spellings, the device's getters, and
#: hand-padded parts. Each was a second convention on some screen before this item.
_FORMATTING = re.compile(
    r"toLocale(?:Date|Time)?String|Intl\.DateTimeFormat|\.get(?:UTC)?(?:FullYear|Month|Date|Day|"
    r"Hours|Minutes|Seconds)\(|padStart\("
)


def test_no_screen_formats_a_date_outside_format_js() -> None:
    offenders = []
    for path in sorted([*(WEB / "src").rglob("*.js"), WEB / "app.js"]):
        if path == WEB / "src" / "core" / "format.js":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _FORMATTING.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


# --- C8: a 422 in Vietnamese ----------------------------------------------------------------------


def _classify(status: int, detail: Any) -> dict[str, Any]:
    return dict(
        _run(
            f"""
const errors = await import("./src/core/errors.js");
const error = errors.classify({status}, {json.dumps(detail)}, {{}});
console.log(JSON.stringify({{
  kind: error.kind,
  message: error.message,
  fields: error.fieldErrors,
  detail: error.detail,
  visible: errors.visibleMessage(error),
}}));
"""
        )
    )


def _item(kind: str, loc: list[Any], msg: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": kind, "loc": loc, "msg": msg, "input": None}
    if ctx is not None:
        body["ctx"] = ctx
    return body


#: (FastAPI item, the line a person reads). The English and the path never appear in the line.
_VALIDATION: list[tuple[dict[str, Any], str]] = [
    (
        _item(
            "greater_than",
            ["body", "lines", 0, "quantity"],
            "Input should be greater than 0",
            {"gt": "0"},
        ),
        "Khối lượng / số lượng (dòng 1): phải lớn hơn 0",
    ),
    (
        _item(
            "greater_than",
            ["body", "lines", 2, "quantity"],
            "Input should be greater than 0",
            {"gt": "0"},
        ),
        "Khối lượng / số lượng (dòng 3): phải lớn hơn 0",
    ),
    (
        _item(
            "less_than_equal",
            ["body", "amount_vnd"],
            "Input should be less than or equal to 110000",
            {"le": 110000},
        ),
        "Số tiền: không được lớn hơn 110.000\u00a0₫",
    ),
    (_item("missing", ["body", "phone"], "Field required"), "Số điện thoại: chưa nhập"),
    (
        _item(
            "string_too_long",
            ["body", "note"],
            "String should have at most 500 characters",
            {"max_length": 500},
        ),
        "Ghi chú: dài quá 500 ký tự",
    ),
    (
        _item(
            "less_than_equal",
            ["query", "limit"],
            "Input should be less than or equal to 200",
            {"le": 200},
        ),
        "Số dòng mỗi lần đọc: không được lớn hơn 200",
    ),
    (
        _item("literal_error", ["body", "method"], "Input should be 'TIEN_MAT' or 'CHUYEN_KHOAN'"),
        "Cách trả: không phải một lựa chọn có sẵn",
    ),
    (
        _item("uuid_parsing", ["path", "order_id"], "Input should be a valid UUID"),
        "Một ô nhập chưa hợp lệ",
    ),
    (_item("json_invalid", ["body", 12], "JSON decode error"), "Một ô nhập chưa hợp lệ"),
    (
        _item("extra_forbidden", ["body", "frobnicate"], "Extra inputs are not permitted"),
        "Một ô nhập chưa hợp lệ",
    ),
    (_item("value_error", [], "Value error, something"), "Một ô nhập chưa hợp lệ"),
    (_item("some_future_type", ["body", "phone"], "Something new"), "Số điện thoại: chưa hợp lệ"),
]


def _case_id(item: dict[str, Any]) -> str:
    """ "greater_than-lines.0.quantity": the pydantic type and the wire path, in ASCII."""
    return f"{item['type']}-{'.'.join(str(part) for part in item['loc']) or 'nowhere'}"


@needs_node
@pytest.mark.parametrize(
    ("item", "line"),
    _VALIDATION,
    ids=[_case_id(item) for item, _ in _VALIDATION],
)
def test_a_fastapi_422_names_the_field_in_vietnamese_and_keeps_the_english_aside(
    item: dict[str, Any], line: str
) -> None:
    result = _classify(422, [item])

    assert result["kind"] == "INVALID"
    assert result["message"] == "Dữ liệu nhập không hợp lệ."
    [field] = result["fields"]
    assert field["text"] == line
    # What the drawer keeps: the path and the server's own words, verbatim.
    assert field["raw"] == item["msg"]
    path = ".".join(str(part) for part in item["loc"][1:]) if item["loc"] else ""
    assert field["field"] == (path or "—")
    # Nothing of the English or the path reaches the line a person reads.
    assert item["msg"] not in field["text"]
    for part in item["loc"]:
        if isinstance(part, str) and part not in {"body", "query", "path"}:
            assert part not in field["text"], part


@pytest.mark.parametrize(
    ("status", "detail", "message"),
    [
        (422, "phone must be E.164", "Dữ liệu nhập không hợp lệ."),
        (
            409,
            "this order is in a state nobody expected",
            "Máy chủ từ chối vì trạng thái hiện tại.",
        ),
        (400, "Bad Request", "Dữ liệu nhập không hợp lệ."),
        (500, "Internal Server Error", None),
    ],
)
@needs_node
def test_a_server_sentence_in_english_is_never_the_visible_message(
    status: int, detail: str, message: str | None
) -> None:
    result = _classify(status, detail)

    assert detail not in result["visible"]
    assert result["detail"] == detail, "kept verbatim for the drawer"
    if message is not None:
        assert result["visible"] == message


@needs_node
def test_an_error_that_is_not_the_servers_reads_as_the_consoles_fault() -> None:
    result = _run(
        """
const errors = await import("./src/core/errors.js");
const cases = {
  type: new TypeError("Cannot read properties of undefined (reading 'items')"),
  plain: new Error("the draft's binding read did not name a customer of this store"),
  thrown: "boom",
  own: new errors.ConsoleNotice("Máy chủ chưa trả thời điểm khách đồng ý của bản này."),
  api: errors.apiError("OFFLINE"),
};
const out = {};
for (const [name, error] of Object.entries(cases)) {
  out[name] = { visible: errors.visibleMessage(error), tech: errors.technicalText(error) };
}
console.log(JSON.stringify(out));
"""
    )
    unexpected = "Bảng vận hành gặp lỗi ngoài dự kiến. Nếu lỗi lặp lại, báo chủ tiệm."
    assert result["type"] == {
        "visible": unexpected,
        "tech": "TypeError: Cannot read properties of undefined (reading 'items')",
    }
    assert result["plain"]["visible"] == unexpected
    assert "binding read" in result["plain"]["tech"]
    assert result["thrown"] == {"visible": unexpected, "tech": "boom"}
    # The console's own Vietnamese sentence, and the server taxonomy's, are shown as written.
    assert result["own"] == {
        "visible": "Máy chủ chưa trả thời điểm khách đồng ý của bản này.",
        "tech": "",
    }
    assert result["api"]["visible"].startswith("Mất kết nối mạng.")
    assert result["api"]["tech"] == ""


def _api_field_names() -> set[str]:
    """Every request-body property and query parameter name the API declares."""
    spec = app.openapi()
    components = spec["components"]["schemas"]
    names: set[str] = set()

    def walk(schema: dict[str, Any], seen: frozenset[str]) -> None:
        if "$ref" in schema:
            name = schema["$ref"].split("/")[-1]
            if name not in seen:
                walk(components[name], seen | {name})
            return
        for key in ("anyOf", "allOf", "oneOf"):
            for inner in schema.get(key, []):
                walk(inner, seen)
        if "items" in schema:
            walk(schema["items"], seen)
        for prop, inner in schema.get("properties", {}).items():
            names.add(prop)
            walk(inner, seen)

    for operations in spec["paths"].values():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                if parameter.get("in") == "query":
                    names.add(parameter["name"])
            if method == "get":
                continue
            content = operation.get("requestBody", {}).get("content", {})
            schema = content.get("application/json", {}).get("schema")
            if schema:
                walk(schema, frozenset())
    return names


@needs_node
def test_every_field_label_is_a_field_the_api_really_has() -> None:
    labels = _run(
        """
const errors = await import("./src/core/errors.js");
console.log(JSON.stringify(errors.FIELD_LABELS));
"""
    )
    unknown = sorted(set(labels) - _api_field_names())
    assert unknown == [], f"labels for fields the API does not have: {unknown}"
    # Labels are Vietnamese a person reads, never a token or a path.
    for field, label in labels.items():
        assert label and label != field and "_" not in label and "." not in label, (field, label)


def test_no_visible_text_is_built_from_an_errors_raw_message() -> None:
    """`error.message` of an arbitrary error is English; only `errors.js` decides what is shown.

    Allowed: `errors.js` itself, `visibleMessage`'s callers, and the one `components.js` line that
    compares the server's detail with the message to decide whether the drawer repeats it.
    """
    offenders = []
    pattern = re.compile(r"\b(?:error|err|failure|e)\??\.message\b")
    for path in sorted([*(WEB / "src").rglob("*.js"), WEB / "app.js"]):
        if path.name == "errors.js":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")) or not pattern.search(line):
                continue
            if "api.detail !== error.message" in line or "REFUSAL_NOTE[api?.message]" in line:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{number}: {stripped}")
    assert offenders == [], "\n".join(offenders)
