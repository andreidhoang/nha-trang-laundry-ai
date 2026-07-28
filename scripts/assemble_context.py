"""Assemble a deterministic context packet for a bounded engineering task."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_MAP_PATH = ROOT / "context/CONTEXT_MAP.yaml"


def unique_paths(paths: Iterable[str]) -> list[str]:
    """Keep source order while removing duplicate paths."""
    return list(dict.fromkeys(paths))


def load_context_map() -> dict[str, object]:
    with CONTEXT_MAP_PATH.open(encoding="utf-8") as context_file:
        loaded = yaml.safe_load(context_file)
    if not isinstance(loaded, dict):
        raise ValueError("CONTEXT_MAP.yaml must contain a mapping")
    return loaded


def assemble_packet(task_id: str, domains: list[str]) -> str:
    """Render the minimum authoritative packet for selected named domains."""
    context_map = load_context_map()
    domain_map = context_map.get("domains")
    global_sources = context_map.get("global_sources")
    if not isinstance(domain_map, dict) or not isinstance(global_sources, list):
        raise ValueError("CONTEXT_MAP.yaml has invalid domains or global_sources")

    paths: list[str] = []
    for source in global_sources:
        if isinstance(source, dict) and isinstance(source.get("path"), str):
            paths.append(source["path"])

    prohibitions: list[str] = []
    for domain in domains:
        selected = domain_map.get(domain)
        if not isinstance(selected, dict):
            raise ValueError(f"Unknown context domain: {domain}")
        for section in ("sources", "contracts"):
            entries = selected.get(section, [])
            if not isinstance(entries, list) or not all(
                isinstance(entry, str) for entry in entries
            ):
                raise ValueError(f"Invalid {section} for domain: {domain}")
            paths.extend(entries)
        domain_prohibitions = selected.get("prohibitions", [])
        if not isinstance(domain_prohibitions, list) or not all(
            isinstance(entry, str) for entry in domain_prohibitions
        ):
            raise ValueError(f"Invalid prohibitions for domain: {domain}")
        prohibitions.extend(domain_prohibitions)

    rendered_sources = "\n".join(f"- `{path}`" for path in unique_paths(paths))
    rendered_prohibitions = "\n".join(f"- {item}" for item in dict.fromkeys(prohibitions))
    return (
        f"# Context packet: {task_id}\n\n"
        f"**Domains:** {', '.join(domains)}\n\n"
        "## Authoritative sources\n\n"
        f"{rendered_sources}\n\n"
        "## Domain prohibitions\n\n"
        f"{rendered_prohibitions}\n\n"
        "## Required task constraints\n\n"
        "- Read `context/INVARIANTS.md` and the named sources before editing.\n"
        "- Use `context/DECISION_REGISTRY.yaml`; unresolved decisions must fail closed.\n"
        "- Link tests, rollback impact, and evidence in the task handoff.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--domain", action="append", required=True, dest="domains")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    packet = assemble_packet(arguments.task_id, arguments.domains)
    if arguments.output is None:
        print(packet, end="")
    else:
        output_path = arguments.output.resolve()
        if ROOT not in output_path.parents:
            raise ValueError("Context packet output must remain inside the repository")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(packet, encoding="utf-8")
        print(f"Wrote context packet: {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
