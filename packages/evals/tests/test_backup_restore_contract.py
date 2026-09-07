from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from nha_trang_laundry_db.recovery import (
    CONTIGUOUSLY_VERSIONED_AGGREGATES,
    RecoveryValidationError,
    load_backup_policy,
    parse_restore_drill,
)

ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = ROOT / "deploy/backup/backup-policy-v1.json"


def _valid_evidence() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "environment": "STAGING",
        "primary_failure_domain": "primary-host-a",
        "recovery_failure_domain": "recovery-host-b",
        "encrypted_off_host_copy_verified": True,
        "object_evidence_verified": True,
        "reviewer_attested": True,
        "remediation_closed": True,
        "incident_started_at": "2026-08-02T01:00:00Z",
        "source_latest_commit_at": "2026-08-02T01:10:00Z",
        "selected_recovery_point_at": "2026-08-02T01:00:00Z",
        "restore_completed_at": "2026-08-02T03:00:00Z",
        "quote_id": "00000000-0000-0000-0000-000000000901",
        "quote_revision": 1,
        "published_config_id": "00000000-0000-0000-0000-000000000902",
        "correlation_id": "00000000-0000-0000-0000-000000000903",
    }


def test_backup_policy_requires_continuous_encrypted_off_host_recovery() -> None:
    policy = load_backup_policy(POLICY_PATH)
    raw = POLICY_PATH.read_text(encoding="utf-8")

    assert policy.target_rpo_seconds == 900
    assert policy.target_rto_seconds == 14_400
    assert policy.wal_archive_max_interval_seconds <= policy.target_rpo_seconds
    assert policy.base_backup_max_interval_seconds <= 86_400
    assert policy.daily_recovery_copy_retention_days >= 35
    assert "postgresql://" not in raw and "secret" not in json.dumps(json.loads(raw)).casefold()


def test_restore_drill_computes_rpo_and_rto_in_deterministic_code() -> None:
    policy = load_backup_policy(POLICY_PATH)

    evidence = parse_restore_drill(_valid_evidence(), policy)

    assert evidence.achieved_rpo_seconds == 600
    assert evidence.achieved_rto_seconds == 7200


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selected_recovery_point_at", "2026-08-02T00:54:59Z"),
        ("restore_completed_at", "2026-08-02T05:00:01Z"),
        ("recovery_failure_domain", "primary-host-a"),
        ("encrypted_off_host_copy_verified", False),
        ("object_evidence_verified", False),
        ("reviewer_attested", False),
    ],
)
def test_restore_drill_fails_closed_without_real_recovery_evidence(
    field: str, value: object
) -> None:
    document = deepcopy(_valid_evidence())
    document[field] = value

    with pytest.raises(RecoveryValidationError):
        parse_restore_drill(document, load_backup_policy(POLICY_PATH))


def test_restore_validator_is_read_only_and_checks_duplicate_delivery() -> None:
    recovery = (ROOT / "packages/db/src/nha_trang_laundry_db/recovery.py").read_text(
        encoding="utf-8"
    )
    validator = (ROOT / "scripts/validate_restore_drill.py").read_text(encoding="utf-8")

    assert "SET TRANSACTION READ ONLY" in recovery
    assert "schema_migrations" in recovery
    assert "canonical_document" in recovery
    assert "delivery_attempts" in recovery and "provider_message_id" in recovery
    assert "outbox_events WHERE status = 'PROCESSING'" in recovery
    assert "INSERT " not in recovery and "UPDATE " not in recovery and "DELETE " not in recovery
    assert "no release authority was granted" in validator


def test_a_production_drill_can_be_validated_at_all() -> None:
    """`SHOP-RECOVERY-001`: `PRODUCTION` was not an admissible environment.

    `BACKUP-RESTORE-001` completes on a drill result rather than on configuration, so a validator
    that rejects the only environment whose drill counts made the item unclosable. This test and
    the fixture it uses both said `STAGING`, so the gap was invisible from inside the test suite --
    the same shape as a migration whose backfill only ever ran against an empty database.
    """

    policy = load_backup_policy(POLICY_PATH)

    for environment in ("STAGING", "PRODUCTION"):
        document = _valid_evidence() | {"environment": environment}
        assert parse_restore_drill(document, policy).achieved_rpo_seconds == 600

    with pytest.raises(RecoveryValidationError, match="environment is invalid"):
        parse_restore_drill(_valid_evidence() | {"environment": "LAPTOP"}, policy)


def test_the_continuity_check_speaks_only_where_contiguity_is_promised() -> None:
    """It ran over every aggregate type and made the drill unpassable on any real database.

    Two defects, both measured against a database built from all 35 migrations. It compared
    `count(*)` on the stated claim that `domain_events` is unique on the version triple -- the
    constraint is on (aggregate_type, aggregate_id, aggregate_version, **event_type**), so two
    events at one version are legal, counted twice, and flagged. And it asked the question of
    writers that never promised an answer: `agent_runs` hardcodes version 8 for a completion,
    `automation` writes only version 2, `quotes` writes the accepted revision number.

    A/B on a real database differing only in that number: version 8 failed the validator, version 4
    passed. Since `domain_events` is append-only -- `reject_ledger_mutation` refuses the repair --
    any shop holding one completed agent run could never pass a drill, and BACKUP-RESTORE-001
    completes on a drill result. A check that cannot be passed is not strict, it is ignored.

    The list is the contract, so this test names what must be in it and what must not.
    """

    assert set(CONTIGUOUSLY_VERSIONED_AGGREGATES) == {
        "AGENT_DRAFT",
        "APPROVAL",
        "MANUAL_SEND",
        "ORDER",
        "ORDER_REQUEST",
        "STAFF_STORE_ASSIGNMENT",
    }

    # Each of these writes 1 on creation and n + 1 thereafter. If a writer stops doing that, the
    # aggregate belongs out of the list rather than the check being weakened for everyone.
    sources = ROOT / "packages/db/src/nha_trang_laundry_db"
    hardcoded_high = (sources / "agent_runs.py").read_text("utf-8")
    assert "aggregate_version=8" in hardcoded_high, (
        "AGENT_RUN no longer hardcodes a high version; if it is contiguous now it should be "
        "checked, and this test should say so rather than being deleted"
    )


def test_the_continuity_check_still_catches_a_lost_event_including_a_masked_one() -> None:
    """The direction that matters, and the one the first version got wrong in silence.

    An aggregate that lost version 2 but happens to carry two events at version 1 has
    `max = 3, count(*) = 3` -- so the original query passed it. Counting distinct versions is what
    makes a real gap visible whether or not a same-version pair hides it.
    """

    def flagged(versions: list[int]) -> bool:
        # The predicate the query applies, per aggregate.
        return max(versions) != len(set(versions))

    assert flagged([1, 2, 3]) is False
    assert flagged([1, 3]) is True
    assert flagged([1, 1, 3]) is True, "a lost version masked by a same-version pair must fail"
    assert flagged([1, 1, 2]) is False


def test_the_backup_scripts_can_actually_run_where_they_are_mounted() -> None:
    """Three separate ways the archiving could not work as shipped, all closed together.

    `base-backup.sh` was referenced by no compose file, no scheduler and no runbook, while DEC-026
    recorded that the base-backup sidecar "exists". `BACKUP_UPLOAD_COMMAND` was execed as a single
    executable by both scripts and the image contained no object-store client at all -- not rclone,
    aws, s3cmd, mc, curl, gsutil or az -- with `--no-cache` leaving no apk index and a read-only
    root filesystem preventing an install. And the runbooks said `<your rclone/aws wrapper>` with
    no step that produced a wrapper.
    """

    compose = (ROOT / "compose.r1.yaml").read_text("utf-8")
    dockerfile = (ROOT / "deploy/production/Postgres.Dockerfile").read_text("utf-8")

    assert "base-backup.sh" in compose, "nothing schedules or mounts the base backup"
    assert "r1-archive-upload.sh" in compose
    assert "rclone=" in dockerfile, "the upload command has no client to exec"
    assert (ROOT / "deploy/production/backup/r1-archive-upload.sh").is_file()

    # Staged before upload: a streamed pipeline stores its own truncation, and DEC-026 requires a
    # credential that cannot delete, so nobody can remove the bad object afterwards.
    assert "BACKUP_STAGING_DIR" in compose
    base_backup = (ROOT / "deploy/production/backup/base-backup.sh").read_text("utf-8")
    assert "pg_basebackup" in base_backup
    assert base_backup.count("nothing was uploaded") >= 3, (
        "every failure before the upload must say that nothing was uploaded; POSIX sh has no "
        "pipefail and the first version reported success for an empty backup"
    )
    assert "last-success" in base_backup, "the operations check reads a marker this must write"


def test_the_upload_wrapper_refuses_an_unknown_first_argument() -> None:
    """`--if-not-exists` failed open: a client that does not know it took it as a filename.

    The callers cannot verify this and did not; the wrapper they call now does.
    """

    wrapper = (ROOT / "deploy/production/backup/r1-archive-upload.sh").read_text("utf-8")
    assert "--if-not-exists) shift ;;" in wrapper
    assert "exit 64" in wrapper, "an unrecognised flag must be a hard error, not a filename"
    assert "--immutable" in wrapper, "a race past the existence check must still not overwrite"


def test_the_restore_command_is_built_from_values_that_cannot_break_it() -> None:
    """All three shapes reported success and failed hours later, inside the four-hour clock.

    The key is handed over on removable media at drill time, so a path like "/Volumes/USB DRIVE/"
    is the expected shape. Spaces are handled by quoting inside the command; the characters
    quoting cannot save are refused before anything is written.
    """

    restore = (ROOT / "deploy/production/backup/restore.sh").read_text("utf-8")
    assert 'restore_command = \'"${fetch_command}"' in restore, (
        "the interpolated paths must be quoted inside the command, or a space in the identity "
        "path yields FATAL: could not locate required checkpoint record"
    )
    assert "exit 4" in restore, "a % or a quote must be refused rather than written"
    # Newest first, but not newest only.
    assert "sort -r" in restore
    assert "global/pg_control" in restore, (
        "tar succeeding is not proof: a truncated stream extracts a prefix, and the truncated "
        "backup is the one that sorts newest"
    )


def test_every_flag_a_runbook_passes_to_a_script_exists() -> None:
    """The runbooks cited three scripts with arguments those scripts do not have.

    `staging_smoke.py` was invoked bare in one deploy runbook (exits 2, two required arguments
    missing) and with `--expect-host` in the other, which does not exist. `validate_restore_drill.py
    --database-url` does not exist either -- the flag takes a path to a file -- and it was printed
    in the wrong form in three places, on the command that produces the evidence
    `BACKUP-RESTORE-001` completes on. `deploy-today.md` claimed "every command has been run except
    those needing a host"; all three of those failures are argument parsing and need no host.

    Reading the flags out of the source rather than running `--help` keeps this test from needing
    the scripts' dependencies, and it is the same text `argparse` would read.
    """

    import re

    scripts = ROOT / "scripts"
    known: dict[str, set[str]] = {}
    for script in scripts.glob("*.py"):
        text = script.read_text("utf-8")
        flags = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', text))
        if flags:
            known[script.name] = flags | {"--help"}

    invocation = re.compile(r"scripts/([a-z_0-9]+\.py)((?:[ \t]+(?:--[a-z0-9-]+|[^\s\\]+))*)")
    offenders: list[str] = []
    for runbook in sorted((ROOT / "docs/runbooks").glob("*.md")):
        for line in runbook.read_text("utf-8").splitlines():
            # Only lines that are commands. Prose mentions a script name without arguments.
            if line.lstrip().startswith(("#", "*", "-", "|", ">")):
                continue
            for name, tail in invocation.findall(line):
                if name not in known:
                    continue
                for flag in re.findall(r"(--[a-z0-9-]+)", tail):
                    if flag not in known[name]:
                        offenders.append(f"{runbook.name}: {name} has no {flag}")

    assert not offenders, "runbooks pass flags these scripts do not accept:\n  " + "\n  ".join(
        sorted(set(offenders))
    )


def test_the_restore_drill_exports_everything_restore_sh_requires() -> None:
    """`restore.sh` aborts under `set -eu` on any `:?` variable, and two were never exported.

    The drill halted at its own first command, on the stopwatch, inside the four-hour RTO.
    """

    import re

    script = (ROOT / "deploy/production/backup/restore.sh").read_text("utf-8")
    required = set(re.findall(r"\$\{([A-Z_]+):\?", script))
    assert required, "the pattern no longer matches; restore.sh changed shape"

    drill = (ROOT / "docs/runbooks/restore-drill.md").read_text("utf-8")
    exported = set(re.findall(r"export ([A-Z_]+)=", drill))
    missing = sorted(required - exported)
    assert not missing, f"restore-drill.md never exports: {missing}"

    # And the commands those two name have to exist, or the runbook points at nothing again.
    for helper in ("r1-archive-list.sh", "r1-archive-fetch.sh", "r1-archive-upload.sh"):
        assert (ROOT / "deploy/production/backup" / helper).is_file(), helper


def test_the_base_backup_is_chosen_by_the_recovery_target_not_by_recency() -> None:
    """A backup taken after the target cannot be recovered from, and that is the ordinary case.

    Backups run nightly, so undoing something from yesterday afternoon means reaching back past
    last night's backup. Measured during the first real drill: restoring to 05:31:50 selected the
    05:31:57 backup and PostgreSQL refused to start --
    `FATAL: could not locate required checkpoint record`, after the restore step had printed
    "data directory prepared" and exited 0.

    With the fix, the same command skipped it by name and reached the 04:20:27 backup, replayed
    the archived WAL onto it, and stopped before the first transaction after the target.
    """

    restore = (ROOT / "deploy/production/backup/restore.sh").read_text("utf-8")
    assert "target_stamp" in restore, "the target must be compared against each backup's label"
    assert "taken after the target" in restore, "skipped candidates must say why"
    # The comparison has to survive an offset: the runbook's own example is '+07'.
    assert "_shift" in restore and "date -u -d" in restore
    # And it must still be newest-first among the eligible, not oldest-first.
    assert "sort -r" in restore

    drill = (ROOT / "docs/runbooks/restore-drill.md").read_text("utf-8")
    assert "RCLONE_CONFIG" in drill, (
        "restore_command runs as a child of the server and fetches every segment; without this in "
        "PostgreSQL's environment recovery stops at the base backup"
    )


def test_every_database_command_in_a_runbook_can_reach_the_database() -> None:
    """Eleven lines told the operator to run a script the machine could not run.

    `postgres` publishes no port and sits only on `internal: true` networks -- deliberate, and what
    ADR-0007 §1 asks for -- so `DATABASE_URL=... uv run python scripts/...` cannot connect on the
    self-managed branch. Every step that creates the store, binds the owner, publishes the
    pricebook or verifies the grants was in that form, which is the whole of the shop's setup.

    `scripts/shop-admin` runs the same script inside the database's own network, in the API image,
    with the repository mounted read-only for the `scripts/` directory the image does not ship.
    """

    wrapper = ROOT / "scripts/shop-admin"
    assert wrapper.is_file() and wrapper.stat().st_mode & 0o111, "shop-admin must be executable"

    text = wrapper.read_text("utf-8")
    assert "--network" in text and "database-private" in text
    assert "--volume" in text and ":ro" in text, "the repository mount must be read-only"
    assert "DATABASE_URL" in text, "an explicit DATABASE_URL must still win, for other branches"

    # And the runbooks must actually use it, or the wrapper is decoration.
    needs_database = ("bootstrap_store.py", "bootstrap_owner.py", "publish_pricebook.py")
    offenders: list[str] = []
    for name in ("shop-pilot.md", "deploy-today.md", "production-deploy-day.md"):
        runbook = (ROOT / "docs/runbooks" / name).read_text("utf-8")
        for line in runbook.splitlines():
            if "uv run python scripts/" not in line:
                continue
            if any(script in line for script in needs_database):
                offenders.append(f"{name}: {line.strip()[:90]}")

    assert not offenders, (
        "these runbook lines cannot connect on the self-managed branch; "
        "use ./scripts/shop-admin:\n  " + "\n  ".join(offenders)
    )


def test_the_deploy_gate_can_run_against_the_console_it_verifies() -> None:
    """`staging_smoke.py` accepted `--base-url` and then compared it to one hardcoded literal.

    So deploy day's first verification step -- "prove it before letting staff in" -- refused with
    `staging URL must be exactly https://staging.internal:8443`, on a page whose whole subject is a
    different host. And its staff-shell marker was `b"Staff Operations"`, a string that exists
    nowhere in `apps/web/`: the console was localised to Vietnamese and this was never updated, so
    the check failed against *every* deployment, including the staging host it was pinned to.
    Nobody saw it, because both runbooks invoked the script with arguments it does not take.

    Every structural refusal stays; what the hostname pin was doing is done properly by TLS.
    """

    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    # `scripts/` is not a package on mypy's path; the import is real and is exercised below.
    from staging_smoke import validate_target  # type: ignore[import-not-found]

    ca = ROOT / "pyproject.toml"  # any regular file; the CA's contents are not read here
    assert validate_target("https://console.giatlasachcong.lan:8443", ca)
    assert validate_target("https://staging.internal:8443", ca), "the staging host must still work"

    for refused in (
        "http://console.giatlasachcong.lan:8443",  # plain http
        "https://console.giatlasachcong.lan",  # no explicit port
        "https://u:p@console.giatlasachcong.lan:8443",  # credentials in the authority
        "https://console.giatlasachcong.lan:8443/x",  # a path prefix
        "https://console.giatlasachcong.lan:8443?q=1",  # a query
    ):
        with pytest.raises(ValueError):
            validate_target(refused, ca)

    # The real invariant, and the one that cannot rot the way a hardcoded English title did: every
    # marker the smoke test looks for has to be in the shell it is looking at.
    import re as _re

    smoke = (ROOT / "scripts/staging_smoke.py").read_text("utf-8")
    shell = (ROOT / "apps/web/index.html").read_bytes()
    markers = _re.findall(r'b(?:"|\')((?:[^"\'\\]|\\.)+)(?:"|\')', smoke)
    looked_for = [m for m in markers if "title" in m or "console-signin-path" in m]
    assert looked_for, "the staff-shell markers are no longer recognisable in this script"
    for marker in looked_for:
        raw = marker.encode("utf-8").decode("unicode_escape").encode("latin-1")
        assert raw in shell, (
            f"staging_smoke.py looks for {raw!r} and apps/web/index.html does not contain it. "
            "That is how this check came to fail against every deployment: the console was "
            "localised and the marker was not."
        )


def test_the_console_check_can_verify_a_private_certificate() -> None:
    """The console's name is internal, so no public CA can issue for it.

    Without a CA file `urlopen` fails `CERTIFICATE_VERIFY_FAILED` -- measured against the pilot
    stack, reporting the console unreachable while it was serving perfectly. That is a permanent
    false alarm on the check the runbooks call "the console is the business", every five minutes.

    Turning verification off would have made it green and worthless: it could no longer tell the
    shop's console from anything answering on that address.
    """

    checks = (ROOT / "scripts/check_shop_operations.py").read_text("utf-8")
    assert "--console-ca-file" in checks
    assert "ssl.create_default_context(cafile=ca_file)" in checks
    assert "verify_mode" not in checks and "CERT_NONE" not in checks, (
        "verification must never be disabled to make this check pass"
    )
    for name in ("shop-pilot.md", "deploy-today.md", "production-deploy-day.md"):
        runbook = (ROOT / "docs/runbooks" / name).read_text("utf-8")
        if "R1_CONSOLE_HEALTH_URL" in runbook:
            assert "R1_CONSOLE_CA_FILE" in runbook, name
