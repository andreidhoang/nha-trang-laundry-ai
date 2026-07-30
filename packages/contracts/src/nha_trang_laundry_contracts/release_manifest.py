"""Fail-closed verification for capability-specific release gate manifests."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    SECP256R1,
    EllipticCurvePublicKey,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from jsonschema import Draft202012Validator, FormatChecker

from .runtime_registry import ReleaseCapability


class ReleaseManifestError(ValueError):
    """A release manifest is malformed, untrusted, stale, or artifact-incomplete."""


class ReleaseSignerFunction(StrEnum):
    OWNER = "OWNER"
    SECURITY = "SECURITY"
    OPERATIONS = "OPERATIONS"


class ReleaseSignatureAlgorithm(StrEnum):
    ED25519 = "ED25519"
    ECDSA_P256_SHA256 = "ECDSA_P256_SHA256"


ReleasePublicKey = Ed25519PublicKey | EllipticCurvePublicKey
ArtifactResolver = Callable[[str], bytes]


@dataclass(frozen=True, slots=True)
class TrustedReleaseSigner:
    """Out-of-band signer identity and key policy; manifests cannot define trust."""

    key_id: str
    function: ReleaseSignerFunction
    actor_id: str
    algorithm: ReleaseSignatureAlgorithm
    public_key: ReleasePublicKey

    def __post_init__(self) -> None:
        if not self.key_id or not self.actor_id:
            raise ValueError("trusted release signer identity cannot be empty")
        if self.algorithm is ReleaseSignatureAlgorithm.ED25519:
            if not isinstance(self.public_key, Ed25519PublicKey):
                raise ValueError("ED25519 signer requires an Ed25519 public key")
        elif not (
            isinstance(self.public_key, EllipticCurvePublicKey)
            and isinstance(self.public_key.curve, SECP256R1)
        ):
            raise ValueError("ECDSA_P256_SHA256 signer requires a P-256 public key")


@dataclass(frozen=True, slots=True)
class VerifiedReleaseAuthorization:
    """Result of complete verification, scoped to one exact deployment envelope."""

    release_id: str
    commit_sha: str
    stage: str
    capability: ReleaseCapability
    starts_at: datetime
    expires_at: datetime
    payload_hash: str
    verified_uris: tuple[str, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", self.commit_sha):
            raise ValueError("verified release commit SHA is invalid")
        if self.stage not in {"SHADOW", "ASSISTED", "BOUNDED"}:
            raise ValueError("verified release stage is invalid")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.payload_hash):
            raise ValueError("verified release payload hash is invalid")
        starts_at = _aware_utc(self.starts_at, "authorization starts_at")
        expires_at = _aware_utc(self.expires_at, "authorization expires_at")
        if starts_at >= expires_at:
            raise ValueError("verified release authorization window is invalid")
        if not self.release_id or not self.verified_uris:
            raise ValueError("verified release authorization evidence is incomplete")

    def authorizes(
        self,
        *,
        commit_sha: str,
        stage: str,
        capability: ReleaseCapability,
        now: datetime,
    ) -> bool:
        current = _aware_utc(now, "authorization check time")
        return (
            self.commit_sha == commit_sha
            and self.stage == stage
            and self.capability is capability
            and self.starts_at <= current < self.expires_at
        )


class RepositoryArtifactResolver:
    """Resolve only repository-relative evidence; external stores need an explicit resolver."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def __call__(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        if parsed.scheme == "repo":
            if parsed.netloc or parsed.query or parsed.fragment:
                raise ReleaseManifestError(f"invalid repository artifact URI: {uri}")
            relative = parsed.path.lstrip("/")
        elif not parsed.scheme:
            relative = uri
        else:
            raise ReleaseManifestError(f"artifact URI requires an approved resolver: {uri}")
        path = (self._root / relative).resolve()
        if path == self._root or self._root not in path.parents or not path.is_file():
            raise ReleaseManifestError(f"repository artifact is missing or escapes root: {uri}")
        return path.read_bytes()


def verify_release_manifest(
    *,
    root: Path,
    manifest: Mapping[str, object],
    trusted_signers: Mapping[str, TrustedReleaseSigner],
    expected_commit_sha: str,
    expected_stage: str,
    expected_capability: ReleaseCapability,
    now: datetime,
    artifact_resolver: ArtifactResolver | None = None,
) -> VerifiedReleaseAuthorization:
    """Verify schema, envelope, chronology, artifacts, and all detached signatures."""

    _validate_schema(root, manifest)
    current = _aware_utc(now, "verification time")
    if manifest["commit_sha"] != expected_commit_sha:
        raise ReleaseManifestError("release manifest commit does not match deployed commit")
    if manifest["stage"] != expected_stage:
        raise ReleaseManifestError("release manifest stage does not match deployment stage")
    if manifest["capability"] != expected_capability.value:
        raise ReleaseManifestError(
            "release manifest capability does not match requested capability"
        )

    observation = _mapping(manifest["observation_window"], "observation_window")
    start_at = _timestamp(observation["start_at"], "observation_window.start_at")
    end_at = _timestamp(observation["end_at"], "observation_window.end_at")
    created_at = _timestamp(manifest["created_at"], "created_at")
    expires_at = _timestamp(manifest["expires_at"], "expires_at")
    activation = _mapping(manifest["activation"], "activation")
    activation_at = _timestamp(activation["starts_at"], "activation.starts_at")
    if not start_at < end_at <= created_at <= activation_at < expires_at:
        raise ReleaseManifestError("release manifest timestamps are not chronological")
    if current < created_at:
        raise ReleaseManifestError("release manifest is not yet valid")
    if current >= expires_at:
        raise ReleaseManifestError("release manifest is expired")

    gate_evidence = manifest["gate_evidence"]
    if not isinstance(gate_evidence, list):
        raise ReleaseManifestError("gate_evidence must be an array")
    for index, gate in enumerate(gate_evidence):
        gate_record = _mapping(gate, f"gate_evidence[{index}]")
        checked_at = _timestamp(gate_record["checked_at"], f"gate_evidence[{index}].checked_at")
        if not end_at <= checked_at <= created_at:
            raise ReleaseManifestError("gate evidence check is outside the closed evidence window")

    signoffs = manifest["signoffs"]
    if not isinstance(signoffs, list):
        raise ReleaseManifestError("signoffs must be an array")
    payload = dict(manifest)
    del payload["signoffs"]
    payload_hash = f"sha256:{sha256(rfc8785.dumps(cast(Any, payload))).hexdigest()}"
    _verify_signoffs(signoffs, trusted_signers, payload_hash, end_at, created_at)

    resolver = artifact_resolver or RepositoryArtifactResolver(root)
    verified_uris = _verify_artifacts(manifest, resolver)
    return VerifiedReleaseAuthorization(
        release_id=str(manifest["release_id"]),
        commit_sha=expected_commit_sha,
        stage=expected_stage,
        capability=expected_capability,
        starts_at=activation_at,
        expires_at=expires_at,
        payload_hash=payload_hash,
        verified_uris=verified_uris,
    )


def load_and_verify_release_manifest(
    *,
    root: Path,
    path: Path,
    trusted_signers: Mapping[str, TrustedReleaseSigner],
    expected_commit_sha: str,
    expected_stage: str,
    expected_capability: ReleaseCapability,
    now: datetime,
    artifact_resolver: ArtifactResolver | None = None,
) -> VerifiedReleaseAuthorization:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseManifestError("release manifest cannot be read as JSON") from error
    if not isinstance(value, Mapping):
        raise ReleaseManifestError("release manifest root must be an object")
    return verify_release_manifest(
        root=root,
        manifest=value,
        trusted_signers=trusted_signers,
        expected_commit_sha=expected_commit_sha,
        expected_stage=expected_stage,
        expected_capability=expected_capability,
        now=now,
        artifact_resolver=artifact_resolver,
    )


def load_trusted_release_signers(
    *,
    root: Path,
    path: Path,
    expected_sha256: str,
) -> Mapping[str, TrustedReleaseSigner]:
    """Load a public-key-only trust registry bound to an out-of-band content hash."""

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256):
        raise ReleaseManifestError("trusted signer registry hash pin is invalid")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ReleaseManifestError("trusted signer registry cannot be read") from error
    actual_sha256 = f"sha256:{sha256(content).hexdigest()}"
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise ReleaseManifestError("trusted signer registry hash mismatch")
    try:
        registry = json.loads(content)
    except json.JSONDecodeError as error:
        raise ReleaseManifestError("trusted signer registry is not valid JSON") from error
    if not isinstance(registry, Mapping):
        raise ReleaseManifestError("trusted signer registry root must be an object")
    schema_path = root / "specs/contracts/trusted-release-signers-v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseManifestError("trusted signer registry schema cannot be loaded") from error
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(registry), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise ReleaseManifestError(f"trusted signer registry violates schema: {details}")

    entries = registry["signers"]
    assert isinstance(entries, list)
    signers: dict[str, TrustedReleaseSigner] = {}
    actors: set[str] = set()
    functions: set[ReleaseSignerFunction] = set()
    for entry in entries:
        record = _mapping(entry, "trusted signer")
        key_id = str(record["key_id"])
        actor_id = str(record["actor_id"])
        function = ReleaseSignerFunction(str(record["function"]))
        algorithm = ReleaseSignatureAlgorithm(str(record["algorithm"]))
        if key_id in signers or actor_id in actors or function in functions:
            raise ReleaseManifestError(
                "trusted signer registry requires distinct keys, actors, and functions"
            )
        try:
            public_key = load_pem_public_key(str(record["public_key_pem"]).encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as error:
            raise ReleaseManifestError("trusted signer public key is invalid") from error
        if not isinstance(public_key, (Ed25519PublicKey, EllipticCurvePublicKey)):
            raise ReleaseManifestError("trusted signer public key type is unsupported")
        try:
            signer = TrustedReleaseSigner(
                key_id=key_id,
                function=function,
                actor_id=actor_id,
                algorithm=algorithm,
                public_key=public_key,
            )
        except ValueError as error:
            raise ReleaseManifestError(str(error)) from error
        signers[key_id] = signer
        actors.add(actor_id)
        functions.add(function)
    if functions != set(ReleaseSignerFunction):
        raise ReleaseManifestError("trusted signer registry does not cover all required functions")
    return signers


def _validate_schema(root: Path, manifest: Mapping[str, object]) -> None:
    schema_path = root / "specs/contracts/release-gate-manifest-v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseManifestError("release manifest schema cannot be loaded") from error
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise ReleaseManifestError(f"release manifest violates schema: {details}")


def _verify_signoffs(
    signoffs: list[object],
    trusted_signers: Mapping[str, TrustedReleaseSigner],
    payload_hash: str,
    evidence_end_at: datetime,
    created_at: datetime,
) -> None:
    actor_ids: set[str] = set()
    key_ids: set[str] = set()
    functions: set[ReleaseSignerFunction] = set()
    for index, value in enumerate(signoffs):
        signoff = _mapping(value, f"signoffs[{index}]")
        key_id = str(signoff["key_id"])
        actor_id = str(signoff["actor_id"])
        try:
            function = ReleaseSignerFunction(str(signoff["function"]))
            algorithm = ReleaseSignatureAlgorithm(str(signoff["signature_algorithm"]))
        except ValueError as error:
            raise ReleaseManifestError(
                "release signoff uses an unsupported policy value"
            ) from error
        if actor_id in actor_ids or key_id in key_ids or function in functions:
            raise ReleaseManifestError(
                "release signoffs must use distinct actors, keys, and functions"
            )
        actor_ids.add(actor_id)
        key_ids.add(key_id)
        functions.add(function)
        signer = trusted_signers.get(key_id)
        if signer is None:
            raise ReleaseManifestError(f"release signer key is not trusted: {key_id}")
        if (
            signer.actor_id != actor_id
            or signer.function is not function
            or signer.algorithm is not algorithm
        ):
            raise ReleaseManifestError("release signoff identity does not match trusted key policy")
        if signoff["signed_payload_hash"] != payload_hash:
            raise ReleaseManifestError(
                "release signoff payload hash does not match canonical manifest"
            )
        signed_at = _timestamp(signoff["signed_at"], f"signoffs[{index}].signed_at")
        if not evidence_end_at <= signed_at <= created_at:
            raise ReleaseManifestError("release signoff is outside the approval window")
        signature = _decode_signature(str(signoff["signature"]))
        try:
            if algorithm is ReleaseSignatureAlgorithm.ED25519:
                assert isinstance(signer.public_key, Ed25519PublicKey)
                signer.public_key.verify(signature, payload_hash.encode("ascii"))
            else:
                assert isinstance(signer.public_key, EllipticCurvePublicKey)
                signer.public_key.verify(signature, payload_hash.encode("ascii"), ECDSA(SHA256()))
        except InvalidSignature as error:
            raise ReleaseManifestError("release signoff signature is invalid") from error
    if functions != set(ReleaseSignerFunction):
        raise ReleaseManifestError("release signoffs do not cover all required functions")


def _verify_artifacts(
    manifest: Mapping[str, object], resolver: ArtifactResolver
) -> tuple[str, ...]:
    references: list[Mapping[str, object]] = []
    artifacts = _mapping(manifest["artifacts"], "artifacts")
    references.extend(_mapping(value, f"artifacts.{name}") for name, value in artifacts.items())
    activation = _mapping(manifest["activation"], "activation")
    if "prior_canary_artifact" in activation:
        references.append(_mapping(activation["prior_canary_artifact"], "prior_canary_artifact"))
    gates = manifest["gate_evidence"]
    assert isinstance(gates, list)
    for gate_index, gate in enumerate(gates):
        gate_record = _mapping(gate, f"gate_evidence[{gate_index}]")
        evidence_refs = gate_record["evidence_refs"]
        assert isinstance(evidence_refs, list)
        references.extend(
            _mapping(value, f"gate_evidence[{gate_index}].evidence_refs[{ref_index}]")
            for ref_index, value in enumerate(evidence_refs)
        )

    verified: list[str] = []
    observed_hashes: dict[str, str] = {}
    for reference in references:
        uri = str(reference["uri"])
        expected_hash = str(reference["sha256"])
        previous_hash = observed_hashes.setdefault(uri, expected_hash)
        if previous_hash != expected_hash:
            raise ReleaseManifestError("one artifact URI is bound to conflicting hashes")
        try:
            content = resolver(uri)
        except ReleaseManifestError:
            raise
        except Exception as error:
            raise ReleaseManifestError(f"artifact resolver failed for {uri}") from error
        actual_hash = f"sha256:{sha256(content).hexdigest()}"
        if actual_hash != expected_hash:
            raise ReleaseManifestError(f"release artifact hash mismatch: {uri}")
        verified.append(uri)
    return tuple(dict.fromkeys(verified))


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseManifestError(f"{field} must be an object")
    return value


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseManifestError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseManifestError(f"{field} must be an RFC 3339 timestamp") from error
    return _aware_utc(parsed, field)


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReleaseManifestError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _decode_signature(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise ReleaseManifestError("release signoff signature is not valid base64url") from error
