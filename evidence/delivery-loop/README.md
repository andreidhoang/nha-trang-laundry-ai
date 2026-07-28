# Delivery-loop evidence

Each completed work item requires one YAML evidence record. The record must identify the work item,
state the requirement or contract touched, describe rollback impact and unresolved assumptions, and
list every queue-declared acceptance command with `status: PASSED`.

Example:

```yaml
work_item: DOMAIN-001
requirement_contract: specs/contracts/canonical-enums-v1.json
rollback_impact: Remove the enum registry module; no database migration or release capability changes.
unresolved_assumptions: None.
checks:
  - command: uv run pytest packages/domain/tests
    status: PASSED
```
