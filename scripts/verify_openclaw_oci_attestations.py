"""Verify a hosted OpenClaw OCI image is bound to BuildKit SLSA provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
PROVENANCE = "https://slsa.dev/provenance/v1"
STATEMENT = "https://in-toto.io/Statement/v1"
BUILDKIT_BUILD_TYPE = (
    "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md"
)


def _blob_name(digest: str) -> str:
    if DIGEST.fullmatch(digest) is None:
        raise ValueError("OCI descriptor digest is not a SHA-256")
    return f"blobs/sha256/{digest.removeprefix('sha256:')}"


def _read_json(archive: tarfile.TarFile, name: str) -> dict[str, Any]:
    try:
        member = archive.getmember(name)
    except KeyError as error:
        raise ValueError(f"OCI archive is missing {name}") from error
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValueError(f"OCI archive member is not a file: {name}")
    raw = extracted.read()
    if name.startswith("blobs/sha256/"):
        actual = hashlib.sha256(raw).hexdigest()
        if actual != name.rsplit("/", maxsplit=1)[1]:
            raise ValueError(f"OCI blob digest mismatch: {name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"OCI JSON member is not an object: {name}")
    return value


def _image_descriptor(index: dict[str, Any]) -> dict[str, Any]:
    matches = [
        item
        for item in index.get("manifests", [])
        if isinstance(item, dict)
        and item.get("mediaType") == OCI_MANIFEST
        and item.get("platform") == {"architecture": "amd64", "os": "linux"}
    ]
    if len(matches) != 1:
        raise ValueError("OCI archive must contain exactly one linux/amd64 image")
    return matches[0]


def _platform_index(
    archive: tarfile.TarFile, root_index: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    descriptors = [
        item
        for item in root_index.get("manifests", [])
        if isinstance(item, dict) and item.get("mediaType") == OCI_INDEX
    ]
    if len(descriptors) != 1:
        raise ValueError("OCI archive must contain exactly one platform index")
    digest = str(descriptors[0].get("digest"))
    platform_index = _read_json(archive, _blob_name(digest))
    if platform_index.get("mediaType") != OCI_INDEX:
        raise ValueError("OCI platform index media type is invalid")
    return digest, platform_index


def _attestation_descriptor(index: dict[str, Any], image_digest: str) -> dict[str, Any]:
    matches = [
        item
        for item in index.get("manifests", [])
        if isinstance(item, dict)
        and item.get("mediaType") == OCI_MANIFEST
        and item.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest"
        and item.get("annotations", {}).get("vnd.docker.reference.digest") == image_digest
    ]
    if len(matches) != 1:
        raise ValueError("OCI archive has no unique attestation manifest bound to the image")
    return matches[0]


def verify(archive_path: Path) -> dict[str, str]:
    with tarfile.open(archive_path, mode="r:*") as archive:
        root_index = _read_json(archive, "index.json")
        image_digest, platform_index = _platform_index(archive, root_index)
        image = _image_descriptor(platform_index)
        platform_manifest_digest = str(image.get("digest"))
        image_manifest = _read_json(archive, _blob_name(platform_manifest_digest))
        config = image_manifest.get("config")
        if not isinstance(config, dict):
            raise ValueError("OCI image manifest has no config descriptor")
        config_digest = str(config.get("digest"))
        _read_json(archive, _blob_name(config_digest))
        attestation = _attestation_descriptor(platform_index, platform_manifest_digest)
        attestation_digest = str(attestation.get("digest"))
        manifest = _read_json(archive, _blob_name(attestation_digest))
        layers = manifest.get("layers")
        if not isinstance(layers, list):
            raise ValueError("OCI attestation manifest has no layers")
        provenance_layers = [
            layer
            for layer in layers
            if isinstance(layer, dict)
            and layer.get("annotations", {}).get("in-toto.io/predicate-type") == PROVENANCE
        ]
        if len(provenance_layers) != 1:
            raise ValueError("OCI image has no unique SLSA provenance layer")
        statement = _read_json(archive, _blob_name(str(provenance_layers[0].get("digest"))))
    if statement.get("_type") != STATEMENT:
        raise ValueError("BuildKit provenance is not an in-toto statement")
    if statement.get("predicateType") != PROVENANCE:
        raise ValueError("BuildKit provenance predicate type drifted")
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise ValueError("BuildKit provenance predicate is malformed")
    build_definition = predicate.get("buildDefinition")
    run_details = predicate.get("runDetails")
    if (
        not isinstance(build_definition, dict)
        or build_definition.get("buildType") != BUILDKIT_BUILD_TYPE
        or not isinstance(run_details, dict)
        or not isinstance(run_details.get("builder", {}).get("id"), str)
    ):
        raise ValueError("BuildKit provenance build identity is incomplete")
    dependencies = build_definition.get("resolvedDependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("BuildKit provenance has no source materials")
    return {
        "image_digest": image_digest,
        "platform_manifest_digest": platform_manifest_digest,
        "config_digest": config_digest,
        "attestation_digest": attestation_digest,
        "predicate_type": PROVENANCE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oci-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify(args.oci_archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
