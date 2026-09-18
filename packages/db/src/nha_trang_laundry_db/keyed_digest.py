"""Keyed commitments for the two hashes that outlive the data they describe.

`HASH-KEYING-001`, scheduled by `DEC-018` as an accepted residual with a named fix.

Two values in this system are SHA-256 commitments over customer content, and both are designed to
survive the disposal of the content itself:

* `webhook_events.payload_hash` commits to a raw inbound message. `RETENTION-STORE-001` disposes of
  the ciphertext at 30 days and keeps this hash forever, because it is what lets an auditor prove
  which bytes arrived. Unsalted, and a short Vietnamese message has a small enough preimage space to
  enumerate -- `DỪNG` above all, which is exactly the message whose evidence matters most. So the
  purge removed the payload and left a recoverable commitment to it.
* `command_idempotency_records.request_hash` commits to a request document that, for the assistant,
  contains the question a person typed. `protect_idempotency_record` lets nobody delete that row, so
  it outlives the 180-day `ASSISTANT_TRANSCRIPT` purge.

Neither was a defect in the code that wrote it. SHA-256 is the right primitive for "prove these
bytes did not change"; it is the wrong primitive for "prove these bytes did not change, to someone
who must not be able to recover them". Nobody asked the second question until a purge existed.

**HMAC-SHA256 under a per-deployment key.** Equality comparison is preserved exactly --
`HMAC(k, a) == HMAC(k, b)` if and only if `a == b` for a fixed `k` -- so deduplication of provider
events and idempotent replay detection behave identically, while the value stops being enumerable by
anyone without the key. That is the whole change.

**Fail closed.** A deployment with no key refuses the write. There is no unkeyed fallback, because a
fallback is how a deployment quietly spends a year writing reversible commitments and nobody notices
until the first purge.

**History is never re-keyed.** Existing rows keep their `V1` prefix and their residual. Re-keying
them would mean reading the plaintext they commit to -- which is either already disposed of, or is
exactly the material this change exists to protect. The prefix makes the two generations
distinguishable in the data rather than by a date, and the residual stops growing.

**Rotation is an operational procedure, not a code path.** A new key makes previously-seen inputs
hash differently, so deduplication treats them as new. For the inbox that is safe -- a provider
event id is unique per provider and the unique constraint still refuses a true duplicate -- and for
idempotency it means a retry spanning a rotation is treated as a fresh command. Rotate between
deployments, not during one.
"""

from __future__ import annotations

import hmac
import os
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

#: Where a deployment puts the key, in the order it is looked for. The `_FILE` form and the
#: `/run/secrets` mount are the conventions every other secret in this repository already uses; the
#: bare environment variable is for a developer machine and a test run.
KEY_FILE_VARIABLE = "NTL_HASH_KEY_FILE"
KEY_SECRET_PATH = Path("/run/secrets/hash_key")
KEY_VARIABLE = "NTL_HASH_KEY"

#: 32 bytes. Shorter than the HMAC block size and long enough that guessing the key is not the
#: cheapest attack on the thing it protects.
MINIMUM_KEY_BYTES = 32

RAW_PAYLOAD_PREFIX = "RAW-HMAC-V2"
REQUEST_PREFIX = "JCS-HMAC-V2"


class HashKeyUnavailable(RuntimeError):
    """Raised when no deployment key is configured. The caller must refuse, never degrade."""


@lru_cache(maxsize=1)
def load_hash_key() -> bytes:
    """Return the deployment key, cached for the life of the process.

    Cached because this is read on the inbound path of every provider event, and a per-deployment
    secret does not change under a running process by design -- rotation is a restart. `reset()`
    exists for tests, which need to change it without a new interpreter.
    """

    configured = os.environ.get(KEY_FILE_VARIABLE, "").strip()
    if configured:
        material = Path(configured).read_bytes()
    elif KEY_SECRET_PATH.is_file():
        material = KEY_SECRET_PATH.read_bytes()
    else:
        material = os.environ.get(KEY_VARIABLE, "").encode()
    key = material.strip()
    if len(key) < MINIMUM_KEY_BYTES:
        raise HashKeyUnavailable(
            "no deployment hash key is configured. Set "
            f"{KEY_FILE_VARIABLE}, mount {KEY_SECRET_PATH}, or set {KEY_VARIABLE} to at least "
            f"{MINIMUM_KEY_BYTES} bytes. `scripts/bootstrap_shop_local.py` and "
            "`scripts/generate_demo_material.py` both generate one; this is never typed by hand "
            "and never committed."
        )
    return key


def reset() -> None:
    """Forget the cached key. For tests and for a process that has just been given a new one."""

    load_hash_key.cache_clear()


def raw_payload_digest(authenticated_plaintext: bytes) -> str:
    """Commit to an inbound provider payload without leaving it recoverable."""

    signature = hmac.new(load_hash_key(), authenticated_plaintext, sha256).hexdigest()
    return f"{RAW_PAYLOAD_PREFIX}:{signature}"


def request_digest(canonical_json: bytes) -> str:
    """Commit to a canonical command document, for idempotent replay detection."""

    signature = hmac.new(load_hash_key(), canonical_json, sha256).hexdigest()
    return f"{REQUEST_PREFIX}:{signature}"


__all__ = [
    "KEY_FILE_VARIABLE",
    "KEY_SECRET_PATH",
    "KEY_VARIABLE",
    "MINIMUM_KEY_BYTES",
    "RAW_PAYLOAD_PREFIX",
    "REQUEST_PREFIX",
    "HashKeyUnavailable",
    "load_hash_key",
    "raw_payload_digest",
    "request_digest",
    "reset",
]
