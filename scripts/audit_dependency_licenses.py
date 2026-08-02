"""Fail closed on unknown or disallowed Python/Node dependency licenses."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
from pathlib import Path

ALLOWED_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BlueOak-1.0.0",
    "ISC",
    "LGPL-3.0-only",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
}
FIRST_PARTY_PREFIX = "nha-trang-laundry-"


def parser() -> argparse.ArgumentParser:
    candidate = argparse.ArgumentParser(description=__doc__)
    candidate.add_argument("--package-lock", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    return candidate


def _python_license(distribution: importlib.metadata.Distribution) -> str:
    expression = distribution.metadata.get("License-Expression")
    if expression:
        return expression
    classifiers = distribution.metadata.get_all("Classifier", [])
    for classifier in classifiers:
        if "License :: OSI Approved :: MIT License" in classifier:
            return "MIT"
        if "Apache Software License" in classifier:
            return "Apache-2.0"
        if "BSD License" in classifier:
            return "BSD-3-Clause"
        if "Mozilla Public License 2.0" in classifier:
            return "MPL-2.0"
        if "Python Software Foundation License" in classifier:
            return "PSF-2.0"
    license_value = distribution.metadata.get("License")
    if (
        license_value
        and len(license_value) < 100
        and license_value.upper() not in {"UNKNOWN", "NONE"}
    ):
        aliases = {"Apache 2.0": "Apache-2.0"}
        return aliases.get(license_value, license_value)
    return "UNKNOWN"


def _permitted(expression: str) -> bool:
    alternatives = re.split(r"\s+OR\s+", expression.strip("() "), flags=re.IGNORECASE)
    for alternative in alternatives:
        terms = re.split(r"\s+AND\s+", alternative.strip("() "), flags=re.IGNORECASE)
        if terms and all(term.strip("() ") in ALLOWED_LICENSES for term in terms):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    rejected: list[dict[str, str]] = []
    reviewed = 0
    for distribution in importlib.metadata.distributions():
        name = (distribution.metadata.get("Name") or "UNKNOWN").lower()
        if name.startswith(FIRST_PARTY_PREFIX):
            continue
        license_value = _python_license(distribution)
        reviewed += 1
        if not _permitted(license_value):
            rejected.append({"ecosystem": "python", "name": name, "license": license_value})

    lock = json.loads(args.package_lock.read_text(encoding="utf-8"))
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("package-lock packages must be an object")
    for package_path, package in packages.items():
        if not package_path or not isinstance(package, dict):
            continue
        reviewed += 1
        node_license = package.get("license")
        node_name = package.get("name") or package_path.removeprefix("node_modules/")
        if not isinstance(node_license, str) or not _permitted(node_license):
            rejected.append(
                {
                    "ecosystem": "node",
                    "name": str(node_name),
                    "license": str(node_license or "UNKNOWN"),
                }
            )
    result = {
        "schema_version": 1,
        "status": "PASSED" if not rejected else "FAILED",
        "reviewed_dependencies": reviewed,
        "forbidden_or_unknown": rejected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if rejected:
        names = ", ".join(f"{item['ecosystem']}:{item['name']}" for item in rejected)
        raise ValueError(f"forbidden or unknown dependency licenses: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
