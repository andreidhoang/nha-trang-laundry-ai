# Evidence store

Store immutable, reviewable proof here or in an approved external evidence system with a stable URI and
recorded SHA-256 hash. Evidence is append-only: corrections create a new artifact and link to the old
one.

Suggested layout:

```text
evidence/
  <release-or-task-id>/
    test-report.txt
    eval-result.json
    security-scan.json
    restore-drill.md
    runbook-drill.md
    artifact-sha256.txt
```

Never store secrets, raw customer PII, chain-of-thought, unredacted production logs, or private source
documents here. A signed release manifest references evidence by immutable path/URI and content hash.

