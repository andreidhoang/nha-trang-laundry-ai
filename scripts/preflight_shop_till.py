"""Everything that must already be true before the till stack is brought up, checked in one place.

`docs/runbooks/shop-till-mac.md` has eight steps and six of them fail in ways that do not name
themselves: a bind mount to a missing directory becomes an empty root-owned one and every archive
write fails; an untrusted certificate authority makes the console refuse to load with no error a
person would recognise; a checkout inside iCloud Drive breaks Docker mounts, Python imports and the
scheduled checks, each intermittently.

So this runs before `docker compose up` rather than after the shop has opened. It reads nothing
secret -- only whether files exist -- and it changes nothing.

    uv run python scripts/preflight_shop_till.py

Exit status is the number of blocking problems, so it composes with a shell `&&`.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE_HOST = os.environ.get("R1_CONSOLE_HOST", "console.giatlasachcong.lan")
CONSOLE_PORT = 8443
SHOP_COMPOSE_PROJECT = "nha-trang-laundry-shop"
SHOP_TLS_SERVICE = "tls"


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    blocking: bool = True


def _run(*command: str) -> tuple[int, str]:
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return finished.returncode, (finished.stdout or finished.stderr).strip()


def check_docker() -> Result:
    if shutil.which("docker") is None:
        return Result("Docker is installed", False, "no docker on PATH", "Install Docker Desktop.")
    code, output = _run("docker", "info", "--format", "{{.ServerVersion}}")
    return Result(
        "Docker is running",
        code == 0,
        output.splitlines()[0][:60] if output else "no answer",
        "Start Docker Desktop and wait for the whale to stop animating.",
    )


def check_checkout_location() -> Result:
    """The single most consequential check, and the one nobody would think to make.

    iCloud evicts idle files to dataless placeholders. A dataless file reads fine from the Finder
    and fails inside a container with `EDEADLK`, so a container that started yesterday refuses to
    start today with nothing changed. The same provider sets `UF_HIDDEN` on `.venv/*.pth`, which
    CPython silently skips, and macOS TCC refuses a launch agent read access to these directories.
    """

    home = Path.home()
    synced_parents = [home / "Desktop", home / "Documents", home / "Downloads"]
    inside = any(str(ROOT).startswith(str(parent) + os.sep) for parent in synced_parents)
    if not inside:
        return Result("The checkout is outside iCloud Drive", True, str(ROOT))

    accounts = home / "Library/Preferences/MobileMeAccounts.plist"
    desktop_synced = False
    if accounts.is_file():
        try:
            with accounts.open("rb") as handle:
                payload = plistlib.load(handle)
            desktop_synced = "CLOUDDESKTOP" in str(payload)
        except Exception:  # a malformed plist is not worth failing the preflight over
            desktop_synced = True
    return Result(
        "The checkout is outside iCloud Drive",
        not desktop_synced,
        f"{ROOT} is under a synced folder",
        f"mv {ROOT} ~/laundry && cd ~/laundry && uv sync --all-packages --all-groups",
    )


def check_dataless_files() -> Result:
    """Files iCloud has already evicted. Each one is a container that will not start."""

    code, output = _run("find", str(ROOT / "deploy"), "-flags", "+dataless")
    evicted = [line for line in output.splitlines() if line.strip()] if code == 0 else []
    return Result(
        "No deployment file has been evicted to iCloud",
        not evicted,
        f"{len(evicted)} dataless file(s)" if evicted else "none",
        "Move the checkout out of iCloud, or open each file once to fetch it back.",
    )


def check_archive_path() -> Result:
    configured = os.environ.get("R1_LOCAL_ARCHIVE_PATH", "")
    if not configured:
        return Result(
            "The archive directory is configured",
            False,
            "R1_LOCAL_ARCHIVE_PATH is unset",
            "export R1_LOCAL_ARCHIVE_PATH=/Volumes/<drive>/laundry-archive",
        )
    path = Path(configured)
    if not path.is_dir():
        return Result(
            "The archive directory exists",
            False,
            f"{path} is not a directory",
            "Attach the drive and `mkdir -p` it. Docker would otherwise create an empty "
            "root-owned directory here and every archive write would fail.",
        )
    writable = os.access(path, os.W_OK)
    same_disk = ""
    try:
        if path.stat().st_dev == ROOT.stat().st_dev:
            same_disk = " — WARNING: same disk as the database it protects"
    except OSError:
        pass
    return Result("The archive directory is writable", writable, f"{path}{same_disk}")


def check_secret_material() -> Result:
    """Existence only. Nothing here reads a secret, and nothing here should."""

    required = [
        ".shop/secrets/migration_database_url",
        ".shop/secrets/api_database_url",
        ".shop/secrets/postgres_password",
        ".shop/secrets/tls_certificate",
        ".shop/secrets/tls_private_key",
        ".shop/secrets/backup_encryption_recipients",
        ".shop/ca/ca.crt",
    ]
    missing = [name for name in required if not (ROOT / name).exists()]
    return Result(
        "The machine has been bootstrapped",
        not missing,
        f"missing: {', '.join(missing)}" if missing else "all present",
        "uv run python scripts/bootstrap_shop_local.py --backup-recipient 'age1...'",
    )


def check_archive_credential() -> Result:
    """Existence, and deliberately not content.

    Nothing here reads a credential. That means this check cannot tell an rclone configuration for
    a local drive apart from one pointing at an object store nobody has an account for any more --
    so it says "present", not "correct", and the runbook asks you to confirm which one it is.
    """

    path = ROOT / ".shop/secrets/backup_repository_credential"
    return Result(
        "An archive client configuration is present (not read, so not verified)",
        path.exists(),
        "present — confirm it is the [archive] type = local remote if you are using a drive"
        if path.exists()
        else "missing",
        "printf '[archive]\\ntype = local\\n' > .shop/secrets/backup_repository_credential "
        "&& chmod 600 .shop/secrets/backup_repository_credential",
    )


def check_hostname() -> Result:
    try:
        addresses = {str(info[4][0]) for info in socket.getaddrinfo(CONSOLE_HOST, None)}
    except OSError:
        return Result(
            f"{CONSOLE_HOST} resolves",
            False,
            "does not resolve",
            f'echo "127.0.0.1 {CONSOLE_HOST}" | sudo tee -a /etc/hosts',
        )
    loopback = addresses <= {"127.0.0.1", "::1"}
    return Result(
        f"{CONSOLE_HOST} resolves to this machine",
        loopback,
        ", ".join(sorted(addresses)),
        f'echo "127.0.0.1 {CONSOLE_HOST}" | sudo tee -a /etc/hosts',
    )


def check_certificate_authority_trust() -> Result:
    """Untrusted, the console does not load and the service worker does not register.

    Neither says why, which is what makes this worth a check rather than a sentence in a runbook.
    """

    certificate = ROOT / ".shop/ca/ca.crt"
    if not certificate.is_file():
        return Result("The private CA is trusted by this Mac", False, "no .shop/ca/ca.crt")
    code, output = _run("security", "verify-cert", "-c", str(certificate))
    return Result(
        "The private CA is trusted by this Mac",
        code == 0,
        "trusted" if code == 0 else output.splitlines()[-1][:70] if output else "not trusted",
        "sudo security add-trusted-cert -d -r trustRoot "
        f"-k /Library/Keychains/System.keychain {certificate}",
    )


def check_port_free() -> Result:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        busy = probe.connect_ex(("127.0.0.1", CONSOLE_PORT)) == 0

    if not busy:
        return Result(f"Port {CONSOLE_PORT} is available", True, "free")

    code, output = _run(
        "docker",
        "ps",
        "--filter",
        f"publish={CONSOLE_PORT}",
        "--format",
        '{{.Label "com.docker.compose.project"}}/{{.Label "com.docker.compose.service"}}',
    )
    expected_owner = f"{SHOP_COMPOSE_PROJECT}/{SHOP_TLS_SERVICE}"
    owners = {line.strip() for line in output.splitlines() if line.strip()}
    if code == 0 and expected_owner in owners:
        return Result(
            f"Port {CONSOLE_PORT} is owned by the shop console",
            True,
            expected_owner,
        )

    owner_detail = ", ".join(sorted(owners)) if owners else "an unknown process"
    return Result(
        f"Port {CONSOLE_PORT} is available or owned by the shop console",
        False,
        f"occupied by {owner_detail}",
        f"Stop the conflicting listener before starting {expected_owner}.",
    )


def main() -> int:
    checks = (
        check_docker(),
        check_checkout_location(),
        check_dataless_files(),
        check_secret_material(),
        check_archive_credential(),
        check_archive_path(),
        check_hostname(),
        check_certificate_authority_trust(),
        check_port_free(),
    )

    blocking = 0
    print("Preflight — the shop's Mac as the till\n")
    for result in checks:
        mark = "ok  " if result.ok else ("FAIL" if result.blocking else "note")
        print(f"  {mark}  {result.name}" + (f"  — {result.detail}" if result.detail else ""))
        if not result.ok and result.fix:
            for line in result.fix.splitlines():
                print(f"        {line}")
        if not result.ok and result.blocking:
            blocking += 1

    print()
    if blocking:
        print(f"{blocking} thing(s) to fix before bringing the stack up.")
    else:
        print("Ready. Bring it up:\n")
        print("  C='-f compose.r1.yaml -f compose.shop-local.yaml -f compose.shop-till.yaml'")
        print("  docker compose $C --profile self-managed-database up -d --build")
    return blocking


if __name__ == "__main__":
    sys.exit(main())
