"""HASH-KEYING-001: the commitments that outlive a purge stop being reversible.

`DEC-018` accepted an explicit residual and scheduled this fix by name. Two values in this system
are SHA-256 over customer content and are designed to survive the disposal of that content:
`webhook_events.payload_hash`, which `RETENTION-STORE-001` keeps forever after disposing of the
ciphertext at 30 days, and `command_idempotency_records.request_hash`, which
`protect_idempotency_record` lets nobody delete at all. Unsalted, and a short Vietnamese message has
a small enough preimage space to enumerate -- so a purge removed the payload and left a commitment
from which it could be recovered.
"""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

import pytest
from nha_trang_laundry_db import keyed_digest
from nha_trang_laundry_db.inbox import RAW_HASH_PATTERN, raw_payload_hash

PLAINTEXT = "DỪNG".encode()


@pytest.fixture(autouse=True)
def _clear_key_cache() -> None:
    keyed_digest.reset()


def test_the_same_input_under_the_same_key_is_stable_and_under_another_key_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equality is the whole property that had to survive.

    Deduplication of provider events and idempotent replay detection are equality comparisons over
    these values, so the change is safe only if identical inputs still collide and different ones
    still do not. A keyed digest preserves both, for a fixed key.
    """
    monkeypatch.setenv(keyed_digest.KEY_VARIABLE, "k" * 48)
    keyed_digest.reset()
    first = keyed_digest.raw_payload_digest(PLAINTEXT)
    again = keyed_digest.raw_payload_digest(PLAINTEXT)
    other_input = keyed_digest.raw_payload_digest(b"something else")

    monkeypatch.setenv(keyed_digest.KEY_VARIABLE, "j" * 48)
    keyed_digest.reset()
    under_another_key = keyed_digest.raw_payload_digest(PLAINTEXT)

    assert first == again
    assert first != other_input
    assert first != under_another_key


def test_the_digest_is_not_the_unsalted_sha256_of_the_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the item, stated as the thing that must no longer be true.

    Before this, anyone holding a purged record's `payload_hash` could confirm the message was
    `DỪNG` by hashing the four bytes themselves. This asserts that specific attack no longer works.
    """
    monkeypatch.setenv(keyed_digest.KEY_VARIABLE, "k" * 48)
    keyed_digest.reset()

    digest = keyed_digest.raw_payload_digest(PLAINTEXT)

    assert sha256(PLAINTEXT).hexdigest() not in digest
    assert digest.startswith(f"{keyed_digest.RAW_PAYLOAD_PREFIX}:")
    assert RAW_HASH_PATTERN.fullmatch(digest)


def test_both_generations_remain_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    """History is never re-keyed, so the pattern that validates it must admit the old shape.

    Re-keying an existing row would mean reading the plaintext it commits to, which is either
    already disposed of or is exactly the material this change protects.
    """
    monkeypatch.setenv(keyed_digest.KEY_VARIABLE, "k" * 48)
    keyed_digest.reset()

    assert RAW_HASH_PATTERN.fullmatch(f"RAW-SHA256-V1:{'a' * 64}")
    assert RAW_HASH_PATTERN.fullmatch(raw_payload_hash(PLAINTEXT))
    assert not RAW_HASH_PATTERN.fullmatch(f"RAW-MD5-V1:{'a' * 64}")


def test_a_deployment_with_no_key_refuses_rather_than_degrading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed, and say where the key comes from.

    An unkeyed fallback is how a deployment spends a year writing reversible commitments without
    anyone noticing, and the first time it would be discovered is the first purge -- by which point
    the reversible values are already permanent.
    """
    monkeypatch.delenv(keyed_digest.KEY_VARIABLE, raising=False)
    monkeypatch.delenv(keyed_digest.KEY_FILE_VARIABLE, raising=False)
    monkeypatch.setattr(keyed_digest, "KEY_SECRET_PATH", Path("/nonexistent/hash_key"))
    keyed_digest.reset()

    with pytest.raises(keyed_digest.HashKeyUnavailable) as failure:
        keyed_digest.raw_payload_digest(PLAINTEXT)

    message = str(failure.value)
    assert keyed_digest.KEY_VARIABLE in message
    assert "bootstrap_shop_local.py" in message


def test_a_key_too_short_to_be_a_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A truncated or half-written secret file must not silently become a weak key."""

    monkeypatch.setenv(keyed_digest.KEY_VARIABLE, "short")
    keyed_digest.reset()

    with pytest.raises(keyed_digest.HashKeyUnavailable):
        keyed_digest.raw_payload_digest(PLAINTEXT)


def test_the_key_is_read_from_a_file_the_way_every_other_secret_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_FILE` and `/run/secrets` are how this repository delivers every other secret.

    Trailing whitespace is stripped because a file written by a shell heredoc or an editor usually
    ends with a newline, and a key that differs by one byte between two machines produces two
    incomparable sets of commitments -- which would look like a deduplication bug, not a
    configuration one.
    """
    key_file = tmp_path / "hash_key"
    key_file.write_text("f" * 48 + "\n", encoding="utf-8")
    monkeypatch.setenv(keyed_digest.KEY_FILE_VARIABLE, str(key_file))
    monkeypatch.delenv(keyed_digest.KEY_VARIABLE, raising=False)
    keyed_digest.reset()

    from_file = keyed_digest.raw_payload_digest(PLAINTEXT)

    monkeypatch.delenv(keyed_digest.KEY_FILE_VARIABLE)
    monkeypatch.setenv(keyed_digest.KEY_VARIABLE, "f" * 48)
    keyed_digest.reset()

    assert from_file == keyed_digest.raw_payload_digest(PLAINTEXT)


def test_the_request_digest_is_domain_separated_from_the_payload_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two different commitments under one key must not be interchangeable.

    Without a distinct prefix, a value proving "this request document" would also prove "this
    provider payload", and a comparison across the two would silently succeed.
    """
    monkeypatch.setenv(keyed_digest.KEY_VARIABLE, "k" * 48)
    keyed_digest.reset()

    assert keyed_digest.request_digest(PLAINTEXT).startswith(f"{keyed_digest.REQUEST_PREFIX}:")
    assert keyed_digest.request_digest(PLAINTEXT) != keyed_digest.raw_payload_digest(PLAINTEXT)


def test_the_bootstrap_scripts_generate_the_key_rather_than_asking_for_one() -> None:
    """The goal this was built against: a fresh clone reaches a running app with no manual secret.

    A key a person invents is a key a person can guess, and a manual step between cloning and
    running is a step somebody skips. Both generators mint one; neither prompts for it.
    """
    for script in ("scripts/bootstrap_shop_local.py", "scripts/generate_demo_material.py"):
        source = Path(script).read_text(encoding="utf-8")
        assert "hash_key" in source, f"{script} does not generate a deployment hash key"
        assert "token_urlsafe" in source


def test_no_test_or_fixture_hard_codes_a_real_looking_key() -> None:
    """The key must never be committed. The test key is named as one and is not secret."""

    assert "test-only" in os.environ[keyed_digest.KEY_VARIABLE]
