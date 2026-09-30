"""Print the SQL that turns a fresh PostgreSQL into this shop's database.

Four roles and their grants, read back from `.shop/secrets/` so the passwords are the ones the
containers will actually present rather than ones an operator retyped.

**Why this prints rather than connects.** On the self-managed branch `postgres` publishes no port
and sits only on `internal: true` networks, so nothing on the host can reach it; the API image
carries no `scripts/` directory, so it cannot be run there either; and `docker cp` fails because
the root filesystem is read-only. Both deploy runbooks told the operator to run
`apply_demo_grants.py` from the host, which cannot work on that branch. Piping SQL to `psql` on
stdin is the one path that does, so this produces the SQL and the runbook does the piping.

    uv run python scripts/emit_shop_database_setup.py | docker compose ... exec -T postgres \\
        psql -U laundry_migrate -d nha_trang_laundry -v ON_ERROR_STOP=1
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.role_grants import MIGRATION_ROLE, grant_script

ROOT = _Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".shop/secrets"


def _password_from_dsn(name: str) -> str:
    dsn = (SECRETS / f"{name}_database_url").read_text(encoding="utf-8").strip()
    return dsn.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]


def _grants() -> str:
    """Reuse the grant statements rather than restating them.

    A second copy of a privilege model is a second thing to keep correct, and the one that drifts
    is always the copy nobody runs. The model is `nha_trang_laundry_db.role_grants`, which
    `apply_demo_grants.py` executes and `verify_database_grants.py` audits. This used to extract
    the grant template from `apply_demo_grants.py` with regular expressions, and so printed the
    API's full grant for the worker too (`PLATFORM-SECURITY-009` P1).
    """

    return grant_script(MIGRATION_ROLE)


def main() -> int:
    if not SECRETS.is_dir():
        raise SystemExit(
            f"{SECRETS} does not exist. Run scripts/bootstrap_shop_local.py first -- these "
            "passwords must be the ones the containers are already mounted with."
        )

    keycloak = (SECRETS / "keycloak_database_password").read_text(encoding="utf-8").strip()
    backup = (SECRETS / "backup_source_password").read_text(encoding="utf-8").strip()

    print("-- Re-runnable, because a runbook step gets run twice. PostgreSQL has no")
    print("-- CREATE ROLE IF NOT EXISTS, so each role is created or has its password set to the")
    print("-- one in .shop/secrets/ -- which is what the containers are already mounted with, so")
    print("-- a second run repairs a mismatch rather than raising on it.")
    print()
    print("-- Application roles. laundry_migrate is the superuser the container was initialised")
    print("-- with, so it is not created here.")
    for role, secret in (
        ("laundry_api", _password_from_dsn("api")),
        ("laundry_worker", _password_from_dsn("worker")),
        # The backup role has REPLICATION and nothing else: it reads the whole cluster byte for
        # byte, which is every privilege separation here at once. Its password is the one
        # base-backup.sh reads from /run/secrets/backup_source_password.
        ("laundry_backup", backup),
        # Keycloak owns its own schema, in its own database. The migration role must never be able
        # to rewrite staff credentials.
        ("keycloak", keycloak),
    ):
        options = "LOGIN REPLICATION" if role == "laundry_backup" else "LOGIN"
        print(f"""DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
        ALTER ROLE {role} WITH {options} PASSWORD '{secret}';
    ELSE
        CREATE ROLE {role} {options} PASSWORD '{secret}';
    END IF;
END $$;""")
    print()
    print("-- `CREATE DATABASE` cannot run inside a DO block, so it is guarded with \\gexec:")
    print("-- the SELECT produces the statement only when the database is absent.")
    print("SELECT 'CREATE DATABASE keycloak OWNER keycloak'")
    print(" WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'keycloak')\\gexec")
    print()
    print(_grants())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
