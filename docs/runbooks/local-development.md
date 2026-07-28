# Local development runbook

## Prerequisites

- uv (installs/manages the Python 3.12 interpreter declared in `.python-version`)
- Docker Desktop or another Compose-compatible runtime

## Bootstrap

```text
uv sync --all-packages --all-groups
Copy-Item .env.example .env
docker compose up -d postgres
uv run python scripts/verify_contracts.py
uv run pytest
```

Use only synthetic data locally. Public channels and automated sends are disabled by the default
environment template.

## Quality gate

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages
uv run pytest
uv run python scripts/verify_contracts.py
```

## Stop local database

```text
docker compose down
```

This preserves the local named database volume. Do not use volume deletion commands unless local data
is intentionally disposable.
## Database migrations

The application database URL is supplied only through `DATABASE_URL`; never commit a real credential.
For the local Compose database, copy `.env.example` and use its local-only value.

```text
docker compose up -d postgres
uv run python scripts/apply_migrations.py
```

Migrations are forward-only. `scripts/apply_migrations.py` is run by the migration identity in a real
environment; the application runtime identity must not receive DDL rights.
