"""A customer's phone number at rest: sealed, digested for exact search, never stored in clear.

`CUSTOMER-001`, `DEC-034` ("the phone number is stored encrypted, with a keyed digest for exact
search -- the `NTL_HASH_KEY` pattern the inbox already uses").

**What the repository already had, measured.** The inbox and the other disposable payload stores
hold *ciphertext they were handed*: `webhook_event_payloads.encrypted_payload` is a column, and the
bytes arrive sealed by the channel edge. No module here encrypted anything, and nothing decrypted.
The one server-held secret is the deployment hash key `keyed_digest.load_hash_key` reads
(`NTL_HASH_KEY_FILE`, `/run/secrets/hash_key`, `NTL_HASH_KEY`), fail-closed when absent.

So this module reuses that key and derives two sub-keys from it with HKDF-SHA256 under distinct
`info` labels, rather than asking a deployment for a second secret it does not have:

* the **digest key** keys an HMAC-SHA256 over the normalised `+84…` number: equality search and a
  per-store unique index, not enumerable without the key (a Vietnamese mobile number has ~10^9
  values, so an unkeyed hash of one is a lookup table);
* the **sealing key** is an AES-256-GCM key. The associated data is the record's own id and store,
  so a ciphertext copied onto another customer's row does not open.

HKDF with separate labels keeps the two uses independent of each other and of the existing
`RAW-HMAC-V2` / `JCS-HMAC-V2` digests made with the raw key.

**Fail closed.** No key, no write and no read: `HashKeyUnavailable` propagates.

**Rotation** is the operational procedure `keyed_digest` describes, with one addition: a sealed
phone opens only under the key it was sealed with, so a rotation must re-seal the customer rows
(read under the old key, write under the new) in the same maintenance window. There is no code path
that does it silently.
"""

from __future__ import annotations

import hmac
import os
from functools import lru_cache
from hashlib import sha256
from typing import Final
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .keyed_digest import load_hash_key

PHONE_DIGEST_PREFIX: Final = "PHONE-HMAC-V1"
#: The sealed form's first byte: the format version, so a later scheme is distinguishable in data.
SEAL_VERSION: Final = b"\x01"
_NONCE_BYTES: Final = 12
_DIGEST_INFO: Final = b"ntl/customer-phone/digest/v1"
_SEAL_INFO: Final = b"ntl/customer-phone/aes-256-gcm/v1"


class PersonalDataError(ValueError):
    """A sealed value that does not open: wrong key, wrong row, or corrupted bytes."""


@lru_cache(maxsize=4)
def _derive(key: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(key)


def _subkey(info: bytes) -> bytes:
    return _derive(load_hash_key(), info)


def phone_digest(e164: str) -> str:
    """Keyed digest of a normalised number. Equal numbers, equal digests; nothing else learnable."""

    if not e164.startswith("+84"):
        raise ValueError("a phone digest is taken over the normalised +84 form only")
    signature = hmac.new(_subkey(_DIGEST_INFO), e164.encode("ascii"), sha256).hexdigest()
    return f"{PHONE_DIGEST_PREFIX}:{signature}"


def _associated_data(customer_id: UUID, store_id: UUID) -> bytes:
    return f"customer:{customer_id}:store:{store_id}".encode("ascii")


def seal_phone(e164: str, *, customer_id: UUID, store_id: UUID) -> bytes:
    """AES-256-GCM of the normalised number, bound to its row. Version byte + nonce + ciphertext."""

    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(_subkey(_SEAL_INFO)).encrypt(
        nonce, e164.encode("ascii"), _associated_data(customer_id, store_id)
    )
    return SEAL_VERSION + nonce + sealed


def open_phone(sealed: bytes, *, customer_id: UUID, store_id: UUID) -> str:
    """The normalised number back, or `PersonalDataError`. Never returns a partial value."""

    data = bytes(sealed)
    if len(data) <= 1 + _NONCE_BYTES or data[:1] != SEAL_VERSION:
        raise PersonalDataError("sealed phone has an unknown format")
    nonce, body = data[1 : 1 + _NONCE_BYTES], data[1 + _NONCE_BYTES :]
    try:
        plain = AESGCM(_subkey(_SEAL_INFO)).decrypt(
            nonce, body, _associated_data(customer_id, store_id)
        )
    except Exception as error:  # InvalidTag, and nothing about why is worth keeping
        raise PersonalDataError("sealed phone does not open under this key and row") from error
    return plain.decode("ascii")


def reset() -> None:
    """Forget derived keys. For tests that change the deployment key."""

    _derive.cache_clear()


__all__ = [
    "PHONE_DIGEST_PREFIX",
    "SEAL_VERSION",
    "PersonalDataError",
    "open_phone",
    "phone_digest",
    "reset",
    "seal_phone",
]
