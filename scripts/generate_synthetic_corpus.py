"""Regenerate the frozen synthetic combinatorial corpus from the deterministic domain engines.

Deterministic by construction: the same domain produces the same corpus, so a diff in the output
means the domain moved. That is the signal the corpus exists to give, and resolving it is a human
decision about whether the engine or the expectation was wrong.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_evals.synthetic_combinatorial import (
    corpus_document,
    generate_cases,
    wrong_monetary_value_count,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "specs/evals/synthetic-combinatorial-v1.json"


def main(argv: list[str]) -> int:
    document = corpus_document(generate_cases())
    wrong = wrong_monetary_value_count(document)
    if wrong:
        print(f"refusing to write: {wrong} cases disagree with the domain engines")
        return 1
    serialized = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if "--check" in argv:
        current = CORPUS_PATH.read_text(encoding="utf-8") if CORPUS_PATH.is_file() else ""
        if current != serialized:
            print("synthetic combinatorial corpus is stale; regenerate it")
            return 1
        print(f"corpus is current: {document['case_count']} cases")
        return 0
    CORPUS_PATH.write_text(serialized, encoding="utf-8")
    print(f"wrote {document['case_count']} cases to {CORPUS_PATH.relative_to(ROOT)}")
    print(f"content hash {document['content_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
