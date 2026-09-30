"""`OPS-OBSERVABILITY-009` (review P3): the first real restore drill, and what it found.

The round-9 drill ran the repository's own scripts end to end in a container -- `archive-wal.sh`
under a real `archive_command`, `base-backup.sh`, `restore.sh`, then PostgreSQL recovering from the
encrypted archive (log: the slice report). `restore.sh` succeeded and PostgreSQL then refused to
start on the directory it had prepared:

    FATAL:  data directory "/var/tmp/ntl-drill-r9/restore" has invalid permissions

The runbook tells the operator to make an empty directory; `mkdir` makes it 0755, and PostgreSQL
accepts only 0700 or 0750. Nothing in the script set it, so every restore on a clean host stopped at
"start PostgreSQL" with the clock running -- the improvisation `restore-drill.md` §5 calls a failed
drill. And a PGDATA that did not exist yet passed the "must be empty" check and then failed every
candidate at `tar -C`, reporting that no backup could be restored.

These tests run the whole `restore.sh` with a real `age` key, real gzip and tar, and a local
list/fetch pair standing in for rclone, and hold the directory the server will be started on.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RESTORE = ROOT / "deploy/production/backup/restore.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("age") and shutil.which("age-keygen") and shutil.which("gzip")),
    reason="age and gzip are required to run restore.sh for real",
)


def _archive(tmp_path: Path, labels: tuple[str, ...] = ("20260930T010000Z",)) -> tuple[Path, Path]:
    """A repository holding encrypted base backups (named by label), and the identity."""

    identity = tmp_path / "keyholder" / "age-identity.txt"
    identity.parent.mkdir()
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    recipient = next(
        line.split(": ", 1)[1]
        for line in identity.read_text("utf-8").splitlines()
        if line.startswith("# public key: ")
    )

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, body in (("global/pg_control", b"\x01" * 64), ("PG_VERSION", b"16\n")):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    repository = tmp_path / "repo"
    (repository / "base").mkdir(parents=True)
    compressed = subprocess.run(
        ["gzip", "-c"], input=buffer.getvalue(), check=True, capture_output=True
    ).stdout
    for label in labels:
        subprocess.run(
            ["age", "-r", recipient, "-o", str(repository / f"base/base-{label}.tar.gz.age")],
            input=compressed,
            check=True,
            capture_output=True,
        )

    # The read side of the archive, as `r1-archive-list.sh` / `r1-archive-fetch.sh` behave.
    (tmp_path / "list.sh").write_text(
        '#!/bin/sh\nfor f in "$1"*; do [ -f "$f" ] && printf "%s\\n" "$f"; done\n', "utf-8"
    )
    (tmp_path / "fetch.sh").write_text('#!/bin/sh\nexec cat "$1"\n', "utf-8")
    for script in ("list.sh", "fetch.sh"):
        (tmp_path / script).chmod(0o755)
    return repository, identity


def _restore(
    tmp_path: Path,
    data_directory: Path,
    *,
    target: str = "2026-09-30 09:00:00+07",
    labels: tuple[str, ...] = ("20260930T010000Z",),
) -> subprocess.CompletedProcess[str]:
    repository, identity = _archive(tmp_path, labels)
    return subprocess.run(
        ["sh", str(RESTORE)],
        env={
            "PATH": os.environ.get("PATH", ""),
            "BACKUP_IDENTITY_FILE": str(identity),
            "BACKUP_REPOSITORY_PREFIX": str(repository),
            "RECOVERY_TARGET_TIME": target,
            "PGDATA": str(data_directory),
            "BACKUP_LIST_COMMAND": str(tmp_path / "list.sh"),
            "BACKUP_FETCH_COMMAND": str(tmp_path / "fetch.sh"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("created_as", [None, 0o755, 0o775, 0o750, 0o700])
def test_the_prepared_directory_is_one_postgresql_will_start_on(
    tmp_path: Path, created_as: int | None
) -> None:
    """Whatever the operator's `mkdir` made -- or no directory at all -- the result is 0700."""

    data_directory = tmp_path / "restore"
    if created_as is not None:
        data_directory.mkdir()
        data_directory.chmod(created_as)

    result = _restore(tmp_path, data_directory)

    assert result.returncode == 0, result.stderr
    assert (data_directory / "global/pg_control").is_file()
    assert (data_directory / "recovery.signal").is_file()
    assert "restore_command" in (data_directory / "postgresql.auto.conf").read_text("utf-8")
    assert _mode(data_directory) == 0o700, oct(_mode(data_directory))


def test_a_directory_that_is_not_empty_is_still_refused_and_left_alone(tmp_path: Path) -> None:
    data_directory = tmp_path / "restore"
    data_directory.mkdir()
    data_directory.chmod(0o755)
    (data_directory / "something").write_text("keep me", "utf-8")

    result = _restore(tmp_path, data_directory)

    assert result.returncode == 2
    assert "is not empty" in result.stderr
    assert (data_directory / "something").read_text("utf-8") == "keep me"
    assert _mode(data_directory) == 0o755, "a refused restore must not change anything"


# --- "everything we have": the most common restore could not be expressed ----------------------
#
# The clean re-run of the drill restored to a time just after the last commit -- and then, obeying
# the runbook's rule exactly, to the last archived time. Both times PostgreSQL replayed every
# segment and stopped with `FATAL: recovery ended before configured recovery target was
# reached`, because a time target is only "reached" by a commit *after* it, and a quiet shop has
# none. The case behind most restores -- the host is gone, bring back all of it -- needs no
# target at all.

TWO_BACKUPS = ("20260929T010000Z", "20260930T010000Z")


@pytest.mark.parametrize("spelling", ["latest", "LATEST"])
def test_latest_recovers_to_the_end_of_the_archive_from_the_newest_backup(
    tmp_path: Path, spelling: str
) -> None:
    data_directory = tmp_path / "restore"
    result = _restore(tmp_path, data_directory, target=spelling, labels=TWO_BACKUPS)

    assert result.returncode == 0, result.stderr
    assert "base-20260930T010000Z" in result.stdout, result.stdout
    settings = (data_directory / "postgresql.auto.conf").read_text("utf-8")
    assert "restore_command" in settings
    assert "recovery_target_time" not in settings
    assert "recovery_target_action" not in settings
    assert (data_directory / "recovery.signal").is_file()
    assert _mode(data_directory) == 0o700


def test_a_time_target_still_chooses_the_backup_before_it(tmp_path: Path) -> None:
    data_directory = tmp_path / "restore"
    result = _restore(tmp_path, data_directory, target="2026-09-29 12:00:00+07", labels=TWO_BACKUPS)

    assert result.returncode == 0, result.stderr
    assert "base-20260929T010000Z" in result.stdout
    assert "base-20260930T010000Z.tar.gz.age (taken after the target)" in result.stderr
    settings = (data_directory / "postgresql.auto.conf").read_text("utf-8")
    assert "recovery_target_time = '2026-09-29 12:00:00+07'" in settings
    assert "recovery_target_action = 'promote'" in settings
