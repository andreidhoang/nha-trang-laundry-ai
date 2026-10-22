"""Synthetic-only provider contracts. All transports and credentials below are fake."""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from nha_trang_laundry_api import assistant_gemini as gemini
from nha_trang_laundry_api.assistant import AssistantContextReads, DeterministicAssistantBrain
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from pydantic import SecretStr


def _reads() -> AssistantContextReads:
    return AssistantContextReads(
        today_counts=(("ACTIVE", 7),),
        sla_in_production=8,
        sla_breached=2,
        pending_approvals=3,
        pending_approvals_truncated=False,
        pending_approval_previews=("private approved business data",),
        lookup_order=lambda order_id: None,
    )


def _response(intent: str = "TODAY_OVERVIEW", **overrides: Any) -> bytes:
    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"role": "model", "parts": [{"text": json.dumps({"intent": intent})}]},
            }
        ],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 12},
        **overrides,
    }
    return json.dumps(response).encode()


class FakeTransport:
    def __init__(self, response: bytes | Exception | None = None) -> None:
        self.response = _response() if response is None else response
        self.questions: list[str] = []

    def generate(self, question: str, api_key: SecretStr) -> bytes:
        assert api_key.get_secret_value() == "fake-local-test-key"
        self.questions.append(question)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _settings(tmp_path: Path, **overrides: Any) -> gemini.LocalGeminiSettings:
    tmp_path.chmod(0o700)
    return gemini.LocalGeminiSettings(
        **{
            "enabled": True,
            "AGENT_PROVIDER_API_KEY": "fake-local-test-key",
            "budget_path": tmp_path / "evaluation.sqlite3",
            "max_calls": 5,
            **overrides,
        }
    )


def test_default_off_and_existing_intents_never_spend_a_call(tmp_path: Path) -> None:
    transport = FakeTransport()
    brain = gemini.GeminiAssistantBrain(_settings(tmp_path), transport)
    for question in (
        "Xin chào",
        "Hôm nay thế nào?",
        "Doanh thu hôm nay bao nhiêu?",
        "Đơn nào trễ hẹn?",
        "Có gì chờ duyệt?",
        "Đơn 123e4567-e89b-42d3-a456-426614174000 tới đâu?",
    ):
        assert brain.answer(question, _reads()) == DeterministicAssistantBrain().answer(
            question, _reads()
        )
    disabled = gemini.GeminiAssistantBrain(_settings(tmp_path, enabled=False), transport)
    assert disabled.answer("Khối lượng công việc ra sao?", _reads()).intent == "UNSUPPORTED"
    assert transport.questions == []
    assert not (tmp_path / "evaluation.sqlite3").exists()


@pytest.mark.parametrize("intent", list(gemini._INTENT_QUESTIONS))
def test_provider_selects_only_an_existing_deterministic_renderer(
    tmp_path: Path, intent: gemini.Intent
) -> None:
    transport = FakeTransport(_response(intent))
    brain = gemini.GeminiAssistantBrain(_settings(tmp_path), transport)
    answer = brain.answer("Khối lượng công việc ra sao?", _reads())
    assert answer.intent == intent
    assert (
        answer.answer
        == DeterministicAssistantBrain()
        .answer(
            gemini._INTENT_QUESTIONS[intent],
            _reads(),
        )
        .answer
    )
    assert "ASSISTANT_GEMINI_CLASSIFIED" in answer.reason_codes
    assert transport.questions == ["Khối lượng công việc ra sao?"]
    evidence = gemini.LocalCallBudget(tmp_path / "evaluation.sqlite3", 5).snapshot()
    assert evidence["attempts"] == 1
    assert evidence["outcomes"] == [
        {
            "reason": "ASSISTANT_GEMINI_CLASSIFIED",
            "attempts": 1,
            "input_tokens": 120,
            "output_tokens": 12,
        }
    ]
    assert "Khối lượng" not in json.dumps(evidence)
    assert "fake-local-test-key" not in repr(brain._settings)


def test_opaque_signature_is_discarded_without_changing_the_answer(tmp_path: Path) -> None:
    response = json.loads(_response())
    signature = base64.b64encode(b"synthetic opaque provider metadata").decode()
    response["candidates"][0]["content"]["parts"][0]["thoughtSignature"] = signature
    settings = _settings(tmp_path)
    brain = gemini.GeminiAssistantBrain(settings, FakeTransport(json.dumps(response).encode()))
    answer = brain.answer("Khối lượng công việc ra sao?", _reads())
    assert answer.intent == "TODAY_OVERVIEW"
    assert "ASSISTANT_GEMINI_CLASSIFIED" in answer.reason_codes
    assert signature not in repr(answer)
    assert signature.encode() not in (tmp_path / "evaluation.sqlite3").read_bytes()


@pytest.mark.parametrize("signature", [None, 123, "", "not valid base64!", "é"])
def test_malformed_opaque_signature_fails_closed(signature: object) -> None:
    response = json.loads(_response())
    response["candidates"][0]["content"]["parts"][0]["thoughtSignature"] = signature
    with pytest.raises(gemini.GeminiFailure, match="ASSISTANT_GEMINI_INVALID_RESPONSE"):
        gemini._parse_response(json.dumps(response).encode())


@pytest.mark.parametrize("field", ["functionCall", "inlineData", "thought", "unknownMetadata"])
def test_signature_support_does_not_admit_other_part_fields(field: str) -> None:
    response = json.loads(_response())
    part = response["candidates"][0]["content"]["parts"][0]
    part["thoughtSignature"] = base64.b64encode(b"synthetic opaque metadata").decode()
    part[field] = False if field == "thought" else {}
    with pytest.raises(gemini.GeminiFailure, match="ASSISTANT_GEMINI_INVALID_RESPONSE"):
        gemini._parse_response(json.dumps(response).encode())


def test_network_input_contains_no_governed_reads_history_or_recognized_identifiers(
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    brain = gemini.GeminiAssistantBrain(_settings(tmp_path), transport)
    key = "AIza" + "X" * 35
    question = f"Khối lượng công việc ra sao? demo@example.invalid 0901234567 {key}"
    brain.answer(question, _reads())
    assert len(transport.questions) == 1
    sent = transport.questions[0]
    for forbidden in ("demo@example.invalid", "0901234567", key, "private", "ACTIVE"):
        assert forbidden not in sent
    assert "[REDACTED_EMAIL]" in sent
    assert "123e4567-e89b-42d3-a456-426614174000" not in gemini.redact_provider_question(
        "reference 123e4567-e89b-42d3-a456-426614174000"
    )
    # UUID-bearing questions take the existing deterministic path before outbound redaction.
    brain.answer("123e4567-e89b-42d3-a456-426614174000", _reads())
    assert len(transport.questions) == 1


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("123456", "123456"),
        ("1234567", "[REDACTED_NUMBER]"),
        ("+1234567", "[REDACTED_NUMBER]"),
        ("x+1234567", "x+[REDACTED_NUMBER]"),
        ("1234567x", "1234567x"),
        ("x1234567", "x1234567"),
        ("_1234567_", "_1234567_"),
        ("1(23) 4-5.67", "[REDACTED_NUMBER]"),
        ("1234567 89x", "[REDACTED_NUMBER] 89x"),
        ("12 34 567x", "12 34 567x"),
        ("١٢٣٤٥٦٧", "[REDACTED_NUMBER]"),
        ("¹1234567", "¹1234567"),
    ],
)
def test_phone_redaction_preserves_word_boundaries_and_decimal_digit_semantics(
    question: str, expected: str
) -> None:
    assert gemini.redact_provider_question(question) == expected


def test_long_numeric_identifiers_cannot_exhaust_redaction_cpu() -> None:
    """A killable child makes a regression fail instead of hanging the entire test runner."""
    code = textwrap.dedent(
        """
        import time
        from nha_trang_laundry_api.assistant_gemini import redact_provider_question

        started = time.monotonic()
        assert redact_provider_question("1" * 3999 + "x") == "1" * 3999 + "x"
        assert redact_provider_question("1 " * 1999 + "1x") == "[REDACTED_NUMBER] 1x"
        assert time.monotonic() - started < 2
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_raw_input_limit_precedes_sanitization_budget_and_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport()
    brain = gemini.GeminiAssistantBrain(_settings(tmp_path), transport)

    def must_not_sanitize(question: str) -> str:
        raise AssertionError("oversized raw input must be refused before sanitization")

    monkeypatch.setattr(gemini, "redact_provider_question", must_not_sanitize)
    answer = brain.answer("1" * 3999 + "x", _reads())
    assert "ASSISTANT_GEMINI_INPUT_LIMIT" in answer.reason_codes
    assert transport.questions == []
    assert not (tmp_path / "evaluation.sqlite3").exists()


def test_numeric_identifier_does_not_bypass_adjacent_secret_redaction(tmp_path: Path) -> None:
    transport = FakeTransport()
    brain = gemini.GeminiAssistantBrain(_settings(tmp_path), transport)
    secret = "AIza" + "X" * 35
    question = f"{'1' * 30}x {secret} demo@example.invalid +1234567"
    answer = brain.answer(question, _reads())
    assert "ASSISTANT_GEMINI_CLASSIFIED" in answer.reason_codes
    assert transport.questions == [f"{'1' * 30}x [REDACTED] [REDACTED_EMAIL] [REDACTED_NUMBER]"]


@pytest.mark.parametrize(
    "body",
    [
        b"not JSON with a secret that must never be shown",
        b"x" * (gemini.MAX_RESPONSE_BYTES + 1),
        b"[]",
        _response("TRANSFER_MONEY"),
        _response("ORDER_LOOKUP"),
        _response(candidates=[]),
        _response(candidates=[{}, {}]),
        _response(promptFeedback={"blockReason": "SAFETY"}),
        _response(usageMetadata={"promptTokenCount": True}),
        _response(usageMetadata={"candidatesTokenCount": 129}),
        _response(usageMetadata=[]),
        _response(promptFeedback=[]),
        _response().replace(b'"STOP"', b'"MAX_TOKENS"'),
        _response().replace(b'"role": "model"', b'"role": "user"'),
        _response().replace(
            b'\\"TODAY_OVERVIEW\\"', b'\\"TODAY_OVERVIEW\\", \\"answer\\": \\"invented\\"'
        ),
        _response().replace(b'{\\"intent\\":', b'{\\"intent\\": \\"GREETING\\", \\"intent\\":'),
    ],
)
def test_invalid_output_is_spent_once_and_returns_the_deterministic_refusal(
    tmp_path: Path, body: bytes
) -> None:
    transport = FakeTransport(body)
    answer = gemini.GeminiAssistantBrain(_settings(tmp_path), transport).answer("?", _reads())
    assert answer.intent == "UNSUPPORTED"
    assert answer.answer == DeterministicAssistantBrain().answer("?", _reads()).answer
    assert "ASSISTANT_GEMINI_INVALID_RESPONSE" in answer.reason_codes
    assert len(transport.questions) == 1
    assert gemini.LocalCallBudget(tmp_path / "evaluation.sqlite3", 5).snapshot()["attempts"] == 1


@pytest.mark.parametrize(
    "error",
    [TimeoutError("secret body"), OSError("secret body"), gemini.GeminiFailure("secret body")],
)
def test_provider_failure_never_retries_or_exposes_raw_errors(
    tmp_path: Path, error: Exception
) -> None:
    transport = FakeTransport(error)
    answer = gemini.GeminiAssistantBrain(_settings(tmp_path), transport).answer("?", _reads())
    assert "ASSISTANT_GEMINI_UNAVAILABLE" in answer.reason_codes
    assert "secret body" not in str(answer)
    assert len(transport.questions) == 1


def test_input_limit_and_unconfigured_key_precede_budget_or_network(tmp_path: Path) -> None:
    transport = FakeTransport()
    brain = gemini.GeminiAssistantBrain(_settings(tmp_path), transport)
    assert "ASSISTANT_GEMINI_INPUT_LIMIT" in brain.answer("?" * 601, _reads()).reason_codes
    missing = gemini.GeminiAssistantBrain(
        _settings(tmp_path, AGENT_PROVIDER_API_KEY=None), transport
    )
    assert "ASSISTANT_GEMINI_NOT_CONFIGURED" in missing.answer("?", _reads()).reason_codes
    assert transport.questions == []
    assert not (tmp_path / "evaluation.sqlite3").exists()


def test_durable_budget_is_shared_across_concurrent_instances_and_restarts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    transport = FakeTransport()

    def invoke(_: int) -> tuple[str, ...]:
        return gemini.GeminiAssistantBrain(settings, transport).answer("?", _reads()).reason_codes

    with ThreadPoolExecutor(max_workers=12) as pool:
        reasons = list(pool.map(invoke, range(20)))
    assert sum("ASSISTANT_GEMINI_CLASSIFIED" in reason for reason in reasons) == 5
    assert sum("ASSISTANT_GEMINI_BUDGET_EXHAUSTED" in reason for reason in reasons) == 15
    assert len(transport.questions) == 5
    # A fresh object/process or larger configuration cannot raise this ledger's original ceiling.
    restarted = gemini.GeminiAssistantBrain(_settings(tmp_path, max_calls=20), transport)
    assert "ASSISTANT_GEMINI_BUDGET_EXHAUSTED" in restarted.answer("?", _reads()).reason_codes
    assert gemini.LocalCallBudget(tmp_path / "evaluation.sqlite3", 20).snapshot()["ceiling"] == 5


def test_unknown_attempt_consumes_budget_and_private_path_is_required(tmp_path: Path) -> None:
    _settings(tmp_path)
    budget = gemini.LocalCallBudget(tmp_path / "unknown.sqlite3", 1)
    assert budget.reserve() == 1  # Simulate a process death after provider acceptance.
    assert gemini.LocalCallBudget(budget.path, 20).reserve() is None
    assert budget.snapshot()["attempts"] == 1
    transport = FakeTransport()
    tmp_path.chmod(0o755)
    answer = gemini.GeminiAssistantBrain(
        gemini.LocalGeminiSettings.model_validate(
            {
                "enabled": True,
                "AGENT_PROVIDER_API_KEY": "fake-local-test-key",
                "budget_path": tmp_path / "new.sqlite3",
            }
        ),
        transport,
    ).answer("?", _reads())
    assert "ASSISTANT_GEMINI_NOT_CONFIGURED" in answer.reason_codes
    assert transport.questions == []


def test_only_exact_demo_environment_store_subject_and_staff_role_select_provider() -> None:
    settings = AuthSettings(
        oidc_issuer="https://demo-idp.local",
        oidc_audience="nha-trang-laundry-staff-demo",
        oidc_jwks_url="http://demo-idp:9000/.well-known/jwks.json",
    )
    principal = StaffPrincipal(
        staff_user_id=uuid4(),
        oidc_subject="demo-owner",
        roles=frozenset({StaffRole.OWNER_ADMIN}),
        mfa_verified=True,
        session_id=uuid4(),
    )
    selected: list[bool] = []

    def provider() -> DeterministicAssistantBrain:
        selected.append(True)
        return DeterministicAssistantBrain()

    factory = gemini.local_demo_brain_factory(settings, provider_brain=provider)
    factory(principal, uuid4())
    factory(replace(principal, oidc_subject="real-owner"), gemini.DEMO_STORE_ID)
    factory(replace(principal, mfa_verified=False), gemini.DEMO_STORE_ID)
    factory(replace(principal, roles=frozenset({StaffRole.AUDITOR})), gemini.DEMO_STORE_ID)
    for field in ("oidc_issuer", "oidc_audience", "oidc_jwks_url"):
        changed = settings.model_copy(update={field: "https://production.invalid"})
        gemini.local_demo_brain_factory(changed, provider_brain=provider)(
            principal, gemini.DEMO_STORE_ID
        )
    assert selected == []
    factory(principal, gemini.DEMO_STORE_ID)
    assert selected == [True]


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def getheader(self, name: str, default: str) -> str:
        return default

    def read1(self, length: int) -> bytes:
        chunk, self.body = self.body[:length], self.body[length:]
        return chunk


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[Any, ...]] = []
        self.closed = False
        self.sock = None

    def connect(self) -> None:
        pass

    def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_http_boundary_is_fixed_and_does_not_follow_redirects_or_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(b"do not follow or display this", status=302)
    connection = FakeConnection(response)

    def connect(host: str, *, timeout: float, context: Any) -> FakeConnection:
        assert host == "generativelanguage.googleapis.com"
        assert timeout == 15
        assert context.check_hostname
        return connection

    monkeypatch.setattr(http.client, "HTTPSConnection", connect)
    monkeypatch.setattr(gemini, "_connect_bounded", lambda *args: None)
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-use.invalid")
    monkeypatch.setenv("ASSISTANT_GEMINI_MODEL", "unapproved-model")
    with pytest.raises(gemini.GeminiFailure, match="ASSISTANT_GEMINI_UNAVAILABLE"):
        gemini.HttpsGeminiTransport().generate(
            "sanitized synthetic question", SecretStr("test-key")
        )
    assert connection.closed
    assert len(connection.requests) == 1
    method, path, body, headers = connection.requests[0]
    assert method == "POST"
    assert path == "/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert "test-key" not in path
    assert headers["x-goog-api-key"] == "test-key"
    request = json.loads(body)
    assert set(request) == {"contents", "systemInstruction", "generationConfig"}
    assert request["contents"] == [
        {"role": "user", "parts": [{"text": "sanitized synthetic question"}]}
    ]
    assert request["generationConfig"]["maxOutputTokens"] == 128
    assert request["generationConfig"]["candidateCount"] == 1
    assert request["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "minimal",
        "includeThoughts": False,
    }
    assert request["generationConfig"]["responseMimeType"] == "application/json"
    assert request["generationConfig"]["responseJsonSchema"]["additionalProperties"] is False


def test_http_body_cap_and_elapsed_deadline_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(FakeResponse(b"x" * (gemini.MAX_RESPONSE_BYTES + 1)))
    monkeypatch.setattr(http.client, "HTTPSConnection", lambda *a, **kw: connection)
    monkeypatch.setattr(gemini, "_connect_bounded", lambda *args: None)
    with pytest.raises(gemini.GeminiFailure, match="ASSISTANT_GEMINI_INVALID_RESPONSE"):
        gemini.HttpsGeminiTransport().generate("?", SecretStr("test-key"))
    assert connection.closed
    connection = FakeConnection(FakeResponse(_response()))
    times = iter([0.0, 16.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    with pytest.raises(gemini.GeminiFailure, match="ASSISTANT_GEMINI_UNAVAILABLE"):
        gemini.HttpsGeminiTransport().generate("?", SecretStr("test-key"))
    assert connection.closed
    assert connection.requests == []


def test_valid_slow_provider_response_within_deadline_is_not_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Observed Free-tier responses can exceed the former five-second cutoff.
    # Exercise the whole transport with a clock, without sleeping or contacting Google.
    body = _response()
    connection = FakeConnection(FakeResponse(body))
    monkeypatch.setattr(http.client, "HTTPSConnection", lambda *a, **kw: connection)
    monkeypatch.setattr(gemini, "_connect_bounded", lambda *args: None)
    times = iter([0.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    assert gemini.HttpsGeminiTransport().generate("?", SecretStr("test-key")) == body
    assert connection.closed
    assert len(connection.requests) == 1


def test_blocked_dns_returns_by_deadline_and_never_sends_a_late_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    started = threading.Event()
    resolved = threading.Event()
    connection = FakeConnection(FakeResponse(_response()))

    def blocked_resolver(*args: Any, **kwargs: Any) -> list[Any]:
        started.set()
        release.wait(timeout=2)
        resolved.set()
        return []

    monkeypatch.setattr(http.client, "HTTPSConnection", lambda *a, **kw: connection)
    monkeypatch.setattr(socket, "getaddrinfo", blocked_resolver)
    monkeypatch.setattr(gemini, "TIMEOUT_SECONDS", 0.05)
    before = time.monotonic()
    try:
        with pytest.raises(gemini.GeminiFailure, match="ASSISTANT_GEMINI_UNAVAILABLE"):
            gemini.HttpsGeminiTransport().generate("?", SecretStr("test-key"))
        assert time.monotonic() - before < 0.5
        assert started.is_set()
        assert connection.closed
        assert connection.requests == []
    finally:
        release.set()
        assert resolved.wait(timeout=1)
    assert connection.requests == []


def test_malformed_enabled_configuration_is_visible_and_does_not_read_or_log_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini._configured_brain.cache_clear()
    monkeypatch.setenv("ASSISTANT_GEMINI_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_GEMINI_MAX_CALLS", "1000")
    monkeypatch.setenv("AGENT_PROVIDER_API_KEY", "fake-local-test-key")
    try:
        answer = gemini._configured_brain().answer("?", _reads())
        assert "ASSISTANT_GEMINI_NOT_CONFIGURED" in answer.reason_codes
        assert "fake-local-test-key" not in repr(answer)
    finally:
        gemini._configured_brain.cache_clear()


def test_connect_uses_reachable_address_with_fixed_tls_host_and_original_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockets: list[Any] = []

    class FakeSocket:
        closed = False

        def __init__(self, *args: Any) -> None:
            self.timeouts: list[float] = []
            sockets.append(self)

        def settimeout(self, value: float) -> None:
            self.timeouts.append(value)

        def connect(self, address: Any) -> None:
            if address == "unreachable-v6":
                raise OSError("no route")
            assert address == "reachable-v4"

        def close(self) -> None:
            self.closed = True

    class FakeTlsContext:
        def wrap_socket(self, raw: Any, *, server_hostname: str) -> Any:
            assert server_hostname == gemini.PROVIDER_HOST
            return raw

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(0, 0, 0, "", "unreachable-v6"), (0, 0, 0, "", "reachable-v4")],
    )
    monkeypatch.setattr(socket, "socket", FakeSocket)
    connection: Any = FakeConnection(FakeResponse(_response()))
    context: Any = FakeTlsContext()
    deadline = time.monotonic() + 5
    secured = gemini._connect_bounded(connection, context, deadline)
    assert sockets[0].closed
    assert all(0 < timeout <= 5 for sock in sockets for timeout in sock.timeouts)
    assert sockets[1].timeouts[-1] <= sockets[0].timeouts[0]
    assert secured is sockets[1] and connection.sock is secured
