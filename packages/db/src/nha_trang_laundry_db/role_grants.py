"""What each application database identity is granted, as data. `PLATFORM-SECURITY-009` (P1).

One definition, read by the three places that act on it: `scripts/apply_demo_grants.py` executes
it, `scripts/emit_shop_database_setup.py` prints it for `psql` on the self-managed host, and
`scripts/verify_database_grants.py` proves a live database matches it.

**The API** keeps the grant it has always had: SELECT, INSERT and UPDATE on every table, now and in
future (`ALTER DEFAULT PRIVILEGES`), and nothing destructive. It serves every staff screen, so its
table set is the schema.

**The worker** used to receive the same grant. It is the process that will one day host the model
runtime, so a compromise there is the one the design plans for -- and with the API's grant it could
read every customer's phone ciphertext, every staff session hash and every payment. It now holds
exactly what its code executes, enumerated below from `apps/worker` and the repository functions it
calls, and nothing by default: a table a later migration adds is invisible to the worker until this
list names it. That is deliberate. A worker path that needs a new table fails loudly in the
role-separated tests (`apps/worker/tests/test_worker_least_privilege.py`), which run every worker
path as `laundry_worker`; a worker that silently inherited new tables would never fail at all.

Column lists narrow a grant where the table holds something the worker must not read. The inbound
ledger (`webhook_events`) is read for one join column; the domain-event ledger is read for the
columns that find a run's enqueue event, never its payload.

**What each worker grant is for** (every entry is exercised as `laundry_worker` by the tests):

- `outbox_events` -- claim, complete, retry, dead-letter and recover internal events
  (`OutboxRepository.*_internal`); INSERT because every material change the worker commits writes
  its outbox row (`commit_material_change`, `AgentRunRepository.recover_expired`). The INSERT is
  **column-level** (`PLATFORM-RESIDUAL-009B` L2): exactly the columns those statements name, so a
  row the worker writes cannot choose its recipient, purpose, status or approved action -- the
  columns that would make it a send.
- `dead_letter_events` -- the DLQ row a dead or expired internal event leaves.
- `domain_events`, `audit_events` -- the event and audit rows of every worker mutation, INSERT on
  the columns `commit_material_change` writes; the narrow SELECT is
  `AgentRunRepository.recover_expired`'s lookup of a run's enqueue correlation. Column-level, so a
  column a later migration adds is not the worker's to fill until this list says so.
- `agent_runs` -- claim, complete, fail and recover runs; UPDATE only on the lifecycle columns.
- `agent_tool_calls` -- the redacted tool-call ledger (`record_tool_call`).
- `agent_drafts` -- the proposal filed for human review, and the "already filed" probe.
- `webhook_events`, `suppression_entries`, `automation_execution_gates` -- the facts the policy gate
  reads before any model call (`read_agent_run_policy_facts`); `suppression_entries`' `purpose` and
  `channel` too, for the marketing hold below.
- Not `automated_execution_envelopes`. Round 9b granted the kill-switch hold
  (`AutomationExecutionRepository.hold_if_disabled`) UPDATE of the envelope's status, and the
  verifier showed that the same grant lets the worker set a HELD or CANCELLED envelope back to
  PENDING: a column grant cannot say which values, and the table has no transition trigger. The
  grant is withdrawn (the base's position), and the path is un-designated from the worker; see
  `UNDESIGNATED_WORKER_PATHS`.

**Every repository path that names `OUTBOX_WORKER`** -- the actor that, in a deployment, *is* the
`laundry_worker` process -- is listed here exactly once: in `OUTBOX_WORKER_PATHS` when it is a
worker path, granted and executed as `laundry_worker` by a test; or in `UNDESIGNATED_WORKER_PATHS`
when it is not a worker path in this release, and the database refuses it to the worker whole. A
test scans the repository source, so a new path naming the actor cannot appear without an entry.

The worker holds no sequence privilege because the schema has no sequences (every key is a UUID).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

API_ROLE: Final = "laundry_api"
WORKER_ROLE: Final = "laundry_worker"
#: The identity that owns the schema and therefore creates every future table. Default privileges
#: are declared *for* this role, because they apply to what it creates.
MIGRATION_ROLE: Final = "laundry_migrate"
APPLICATION_ROLES: Final = (API_ROLE, WORKER_ROLE)

#: The API's grant, unchanged in substance since `apply_demo_grants.py` was written. `GRANT ... ON
#: ALL TABLES` is a one-shot snapshot; the default-privileges half covers tables created later.
API_GRANTS: Final = """
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM {role};
ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO {role};
ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO {role};
"""

#: Every table privilege PostgreSQL has; the worker audit checks all of them, not only the three an
#: application role normally uses.
TABLE_PRIVILEGES: Final = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
#: The privileges PostgreSQL can also grant per column.
COLUMN_PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "REFERENCES")


@dataclass(frozen=True, slots=True)
class TableGrant:
    """One privilege on one table, on the whole table (`columns=None`) or on named columns."""

    table: str
    privilege: str
    columns: tuple[str, ...] | None = None

    def statement(self, role: str) -> str:
        columns = "" if self.columns is None else " (" + ", ".join(self.columns) + ")"
        return f"GRANT {self.privilege}{columns} ON {self.table} TO {role}"


_OUTBOX_LIFECYCLE = (
    "status",
    "attempt_count",
    "held_reason",
    "claim_token",
    "claimed_at",
    "lease_expires_at",
    "sent_at",
    "available_at",
)
_AGENT_RUN_LIFECYCLE = (
    "status",
    "attempt_count",
    "claim_token",
    "claimed_at",
    "lease_expires_at",
    "result_safe_summary",
    "completed_at",
    "failure_code",
)

#: The columns the worker's ledger INSERTs name (`transactions.commit_material_change`,
#: `outbox.OutboxRepository.recover_expired_internal`, `agent_runs.AgentRunRepository.
#: recover_expired`). `apps/worker/tests/test_worker_least_privilege.py` pins each set.
_OUTBOX_INSERT = (
    "id",
    "aggregate_type",
    "aggregate_id",
    "event_type",
    "payload",
    "idempotency_key",
    "correlation_id",
    "occurred_at",
    "traceparent",
    "tracestate",
)
_DOMAIN_EVENT_INSERT = (
    "id",
    "aggregate_type",
    "aggregate_id",
    "aggregate_version",
    "event_type",
    "payload",
    "correlation_id",
    "occurred_at",
)
_AUDIT_INSERT = (
    "id",
    "aggregate_type",
    "aggregate_id",
    "action",
    "actor_type",
    "actor_id",
    "correlation_id",
    "details",
    "occurred_at",
)

#: Exactly what `laundry_worker` may do. Read the module docstring before adding to it.
WORKER_GRANTS: Final[tuple[TableGrant, ...]] = (
    TableGrant("outbox_events", "SELECT"),
    TableGrant("outbox_events", "INSERT", _OUTBOX_INSERT),
    TableGrant("outbox_events", "UPDATE", _OUTBOX_LIFECYCLE),
    TableGrant("dead_letter_events", "INSERT"),
    TableGrant("domain_events", "INSERT", _DOMAIN_EVENT_INSERT),
    TableGrant(
        "domain_events",
        "SELECT",
        ("id", "aggregate_type", "aggregate_id", "event_type", "occurred_at", "correlation_id"),
    ),
    TableGrant("audit_events", "INSERT", _AUDIT_INSERT),
    TableGrant("agent_runs", "SELECT"),
    TableGrant("agent_runs", "UPDATE", _AGENT_RUN_LIFECYCLE),
    TableGrant("agent_tool_calls", "INSERT"),
    TableGrant("agent_drafts", "INSERT"),
    TableGrant("agent_drafts", "SELECT", ("agent_run_id",)),
    TableGrant("webhook_events", "SELECT", ("id", "contact_binding_id")),
    TableGrant(
        "suppression_entries", "SELECT", ("contact_binding_id", "purpose", "channel", "state")
    ),
    TableGrant("automation_execution_gates", "SELECT"),
)


@dataclass(frozen=True, slots=True)
class WorkerPath:
    """One repository path designated for the `OUTBOX_WORKER` actor, and what the grant does."""

    granted: bool
    reason: str


#: `PLATFORM-RESIDUAL-009B` L2. The public repository methods that name `OUTBOX_WORKER` (as their
#: guard, their actor or their audit row) and ARE worker paths: every one granted, and run as
#: `laundry_worker` in `apps/worker/tests/test_worker_least_privilege.py`. The others are in
#: `UNDESIGNATED_WORKER_PATHS`.
OUTBOX_WORKER_PATHS: Final[dict[str, WorkerPath]] = {
    "outbox.OutboxRepository.claim_next_internal": WorkerPath(True, "the internal outbox loop"),
    "outbox.OutboxRepository.complete_internal": WorkerPath(True, "the internal outbox loop"),
    "outbox.OutboxRepository.retry_or_dead_letter_internal": WorkerPath(
        True, "the internal outbox loop"
    ),
    "outbox.OutboxRepository.recover_expired_internal": WorkerPath(
        True, "the supervisor's lease recovery"
    ),
    "marketing_delivery.MarketingDeliveryRepository.hold_if_not_authorized": WorkerPath(
        True,
        "the final suppression check before a marketing send: it only ever holds, and reads the "
        "suppression ledger's key, purpose, channel and state",
    ),
}


#: Round-9b integration, the lead's ruling on the L verifier's residual: the two repository paths
#: that name `OUTBOX_WORKER` but were neither granted nor un-designated (`WorkerPath(False,
#: "DECISION NEEDED ...")`) are UN-DESIGNATED from the worker. Fail closed: the worker runs no
#: sender in this release -- ADR-0002's "sole OUTBOX_WORKER sender" is unbuilt and DEC-001..006
#: keep every send gated -- so neither is a worker path, `laundry_worker` holds no grant for either,
#: and the database refuses each to the worker on its first statement, whole (proven by the tests
#: named in `test_worker_least_privilege.REFUSED_BY`). Nothing in `apps/` calls either; only the
#: eval suites do, as the schema owner. The actor name stays in their code because migration
#: 0007's CHECK (`approval_executions.claimed_by = 'OUTBOX_WORKER'`) and the audit trail name the
#: future sender's identity; designating a process to run them is a decision for when a sender is
#: built (the approval claim needs reads PLATFORM-SECURITY-009 P1 took away, or a move into the API
#: process; the kill-switch hold needs a transition trigger on automated_execution_envelopes).
UNDESIGNATED_WORKER_PATHS: Final[dict[str, str]] = {
    "automation.AutomationExecutionRepository.hold_if_disabled": (
        "Un-designated from OUTBOX_WORKER (ADR-0002; DEC-001..006 keep sending gated): no "
        "automated executor runs in this release. The kill-switch hold only ever moves an envelope "
        "from PENDING to HELD or CANCELLED, but the UPDATE it needs cannot be limited to those "
        "values by a grant -- with UPDATE(status) the worker could also set a HELD or CANCELLED "
        "envelope back to PENDING (round-9b verifier). The database refuses it to the worker on "
        "its first statement (SELECT ... FOR UPDATE), so nothing of it can half-happen."
    ),
    "approvals.ApprovalRepository.claim_execution": (
        "Un-designated from OUTBOX_WORKER (ADR-0002; DEC-001..006 keep sending gated): no sender "
        "runs in this release. The claim re-reads the approved resource under a row lock -- FOR "
        "SHARE on orders and quotes, which PostgreSQL allows only with UPDATE there -- and "
        "re-derives digests from the message draft's text and the export's content: reads "
        "PLATFORM-SECURITY-009 P1 took away from the worker. The database refuses it to the worker "
        "on its first read, so nothing of it can half-happen."
    ),
}


def _split(script: str) -> list[str]:
    """Split on semicolons, because a default-privileges statement spans two lines."""

    return [part.strip() for part in script.split(";") if part.strip()]


def api_statements(owner: str = MIGRATION_ROLE, role: str = API_ROLE) -> list[str]:
    return _split(API_GRANTS.format(role=role, owner=owner))


def worker_statements(owner: str = MIGRATION_ROLE, role: str = WORKER_ROLE) -> list[str]:
    """Take everything away, then grant the list. Re-runnable, and it narrows an old database.

    A deployment provisioned before this change holds the API's grant *and* default privileges that
    hand the worker every future table. Granting the list on top would leave both in place, so the
    revoke comes first: `REVOKE ... ON ALL TABLES` also removes column grants (PostgreSQL revokes a
    table's column privileges with the table's), and the default-privileges revoke stops the next
    migration's tables reaching the worker.
    """

    return [
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {role}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE ALL ON SEQUENCES FROM {role}",
        *(grant.statement(role) for grant in WORKER_GRANTS),
    ]


def grant_statements(owner: str = MIGRATION_ROLE) -> list[str]:
    """Both application roles, in the order they are applied."""

    return [*api_statements(owner), *worker_statements(owner)]


def grant_script(owner: str = MIGRATION_ROLE) -> str:
    """The same statements as SQL text, for `psql` on a host nothing else can reach."""

    return "\n".join(f"{statement};" for statement in grant_statements(owner))


def apply_role_grants(connection: Any, owner: str = MIGRATION_ROLE) -> list[str]:
    """Execute the grant set in one transaction and return what ran."""

    applied: list[str] = []
    with connection.transaction(), connection.cursor() as cursor:
        for statement in grant_statements(owner):
            cursor.execute(statement)
            applied.append(statement)
    return applied


def worker_expectation() -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]]]:
    """(table-level privileges by table, column set by (table, privilege)) for the worker."""

    table_level: dict[str, set[str]] = {}
    column_level: dict[tuple[str, str], set[str]] = {}
    for grant in WORKER_GRANTS:
        if grant.columns is None:
            table_level.setdefault(grant.table, set()).add(grant.privilege)
        else:
            column_level.setdefault((grant.table, grant.privilege), set()).update(grant.columns)
    return table_level, column_level


_TABLE_QUERY = """
SELECT c.relname, p.privilege, has_table_privilege(%s, c.oid, p.privilege)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN unnest(%s::text[]) AS p(privilege)
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY 1, 2
"""

_COLUMN_QUERY = """
SELECT c.relname, a.attname, p.privilege,
       has_column_privilege(%s, c.oid, a.attnum, p.privilege)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
CROSS JOIN unnest(%s::text[]) AS p(privilege)
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY 1, 2, 3
"""

_DEFAULT_ACL_QUERY = """
SELECT d.defaclobjtype, acl::text
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid = d.defaclnamespace
CROSS JOIN unnest(d.defaclacl) AS acl
WHERE n.nspname = 'public' AND acl::text LIKE %s
"""

_SEQUENCE_QUERY = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'S'
  AND (has_sequence_privilege(%s, c.oid, 'USAGE') OR has_sequence_privilege(%s, c.oid, 'SELECT')
       OR has_sequence_privilege(%s, c.oid, 'UPDATE'))
"""


def audit_worker(connection: Any, role: str = WORKER_ROLE) -> tuple[list[str], list[str]]:
    """Return (missing, excess) for the worker against `WORKER_GRANTS`, exactly.

    Checked at column granularity, because a column grant is invisible to `has_table_privilege`
    and a table grant makes every column readable: comparing tables alone would pass a worker that
    can read `customers.phone_ciphertext` through one stray column grant.
    """

    table_level, column_level = worker_expectation()
    missing: list[str] = []
    excess: list[str] = []
    with connection.cursor() as cursor:
        cursor.execute(_TABLE_QUERY, (role, list(TABLE_PRIVILEGES)))
        for table, privilege, held in cursor.fetchall():
            expected = privilege in table_level.get(table, set())
            if expected and not held:
                missing.append(f"{table}.{privilege}")
            elif held and not expected:
                excess.append(f"{table}.{privilege}")
        cursor.execute(_COLUMN_QUERY, (role, list(COLUMN_PRIVILEGES)))
        for table, column, privilege, held in cursor.fetchall():
            if privilege in table_level.get(table, set()):
                continue  # the table-level row above already decided it
            expected = column in column_level.get((table, privilege), set())
            if expected and not held:
                missing.append(f"{table}({column}).{privilege}")
            elif held and not expected:
                excess.append(f"{table}({column}).{privilege}")
        cursor.execute(_SEQUENCE_QUERY, (role, role, role))
        excess.extend(f"sequence {name}" for (name,) in cursor.fetchall())
        # A default privilege is a grant on every table not yet created; the worker must hold none.
        cursor.execute(_DEFAULT_ACL_QUERY, (f"{role}=%",))
        excess.extend(
            f"default privilege on future {'tables' if kind == 'r' else 'objects'}: {acl}"
            for kind, acl in cursor.fetchall()
        )
    # Every named table must exist: a renamed table would otherwise drop out of both lists.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND relname = ANY(%s)",
            (sorted({grant.table for grant in WORKER_GRANTS}),),
        )
        present = {str(row[0]) for row in cursor.fetchall()}
    missing.extend(
        f"{table} (table does not exist)"
        for table in sorted({grant.table for grant in WORKER_GRANTS} - present)
    )
    return missing, excess


__all__ = [
    "API_GRANTS",
    "API_ROLE",
    "APPLICATION_ROLES",
    "MIGRATION_ROLE",
    "OUTBOX_WORKER_PATHS",
    "UNDESIGNATED_WORKER_PATHS",
    "WORKER_GRANTS",
    "WORKER_ROLE",
    "TableGrant",
    "WorkerPath",
    "api_statements",
    "apply_role_grants",
    "audit_worker",
    "grant_script",
    "grant_statements",
    "worker_expectation",
    "worker_statements",
]
