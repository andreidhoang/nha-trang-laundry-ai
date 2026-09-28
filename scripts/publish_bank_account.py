"""Publish the shop's bank account, so every bill can show an exact VietQR for what is owed.

`VIETQR-001` (`DEC-041`). The QR on the order page (*Thu tiền → Chuyển khoản*), on *Phiếu cho
khách* and on an account customer's statement sends the customer's money to the account published
here, for the exact amount still owed, with a transfer code (`NTL2809012`) that names the order. It
is built and checksummed on the server; no bank or provider is called.

**A wrong BIN is caught by a 1.000 ₫ test, not by a customer at the counter.** First:

    uv run python scripts/publish_bank_account.py --preview \\
        --bank-bin 970436 --account-number 0123456789 \\
        --account-name "NGUYEN VAN A" --bank-display-name Vietcombank

writes `vietqr-test-1000.svg` (a QR for 1.000 ₫, memo `NTLTEST`) and prints its payload. It
publishes nothing and needs no database. Scan it with your own phone's bank app, check the name the
app shows is the shop's, and pay it. When the 1.000 ₫ has arrived, publish with the same four
values and `--test-transfer-confirmed`:

    DATABASE_URL=... uv run python scripts/publish_bank_account.py \\
        --actor-id <owner-staff-uuid> --test-transfer-confirmed \\
        --bank-bin 970436 --account-number 0123456789 \\
        --account-name "NGUYEN VAN A" --bank-display-name Vietcombank

**Only an active OWNER_ADMIN can publish it.** `--actor-id` must be the owner's own staff id; any
other id is refused and nothing is written. It is a script a human runs, never a seed applied on
boot: an account that appears because a process started is an account nobody tested.

Until this is run every QR is refused `BANK_ACCOUNT_UNPUBLISHED` and the counter works as it did:
staff take transfers by looking at the bank app. Publishing the same account again changes nothing.
`--withdraw` publishes the reversal `DEC-041` names -- no QR is shown from then on:

    DATABASE_URL=... uv run python scripts/publish_bank_account.py \\
        --actor-id <owner-staff-uuid> --withdraw

The account holder's name is written as the bank prints it: upper-case, no accents
("NGUYEN VAN A"). The BIN is the bank's six-digit NAPAS number (Vietcombank 970436, VietinBank
970415, BIDV 970418, Techcombank 970407, MB 970422, ACB 970416 ...) -- the test transfer is what
proves you picked the right one.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import segno
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.bank_transfer import (
    BankAccountAuthorizationError,
    publish_bank_account,
)
from nha_trang_laundry_domain.vietqr import (
    PREVIEW_AMOUNT_VND,
    PREVIEW_TRANSFER_CODE,
    BankAccountError,
    bank_account_document,
    bank_account_withdrawal_document,
    parse_payload,
    vietqr_payload,
)

PREVIEW_FILE = "vietqr-test-1000.svg"
SHOP_ZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--actor-id", type=UUID, help="the OWNER_ADMIN publishing the account")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--bank-bin", help="the bank's six-digit NAPAS BIN")
    parser.add_argument("--account-number", help="the account number, 6 to 19 letters or digits")
    parser.add_argument("--account-name", help="the holder's name as the bank prints it")
    parser.add_argument("--bank-display-name", help="the bank's short name, e.g. Vietcombank")
    parser.add_argument(
        "--preview",
        action="store_true",
        help=f"write {PREVIEW_FILE} (a 1.000 ₫ test QR) and print its payload; publish nothing",
    )
    parser.add_argument(
        "--out",
        type=_Path,
        default=_Path(PREVIEW_FILE),
        help=f"where --preview writes the test QR (default ./{PREVIEW_FILE})",
    )
    parser.add_argument(
        "--test-transfer-confirmed",
        action="store_true",
        help="you paid the --preview QR from your own phone and saw the 1.000 ₫ arrive",
    )
    parser.add_argument(
        "--withdraw",
        action="store_true",
        help="publish the withdrawal: no QR is shown from now on (DEC-041 reversal)",
    )
    return parser


def _document(arguments: argparse.Namespace, confirmed_at: str) -> dict[str, str]:
    missing = [
        flag
        for flag, value in (
            ("--bank-bin", arguments.bank_bin),
            ("--account-number", arguments.account_number),
            ("--account-name", arguments.account_name),
            ("--bank-display-name", arguments.bank_display_name),
        )
        if not value
    ]
    if missing:
        raise BankAccountError("missing " + ", ".join(missing))
    return bank_account_document(
        bank_bin=arguments.bank_bin,
        account_number=arguments.account_number,
        account_name=arguments.account_name,
        bank_display_name=arguments.bank_display_name,
        test_transfer_confirmed_at=confirmed_at,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    now = datetime.now(SHOP_ZONE).isoformat(timespec="seconds")

    if arguments.preview:
        if arguments.withdraw or arguments.test_transfer_confirmed:
            print("refused: --preview publishes nothing; run it on its own", file=_sys.stderr)
            return 2
        try:
            document = _document(arguments, now)
            payload = vietqr_payload(
                bank_bin=document["bank_bin"],
                account_number=document["account_number"],
                amount_vnd=PREVIEW_AMOUNT_VND,
                transfer_code=PREVIEW_TRANSFER_CODE,
            )
        except (BankAccountError, ValueError) as error:
            print(f"refused: {error}", file=_sys.stderr)
            return 2
        parse_payload(payload)  # the CRC and every field read back, before anyone scans it
        segno.make_qr(payload, error="M", boost_error=False).save(
            str(arguments.out), kind="svg", scale=8, border=4
        )
        print(f"payload: {payload}")
        print(f"test QR written: {arguments.out}")
        print(
            f"Scan it with your own bank app: it must show {document['account_name']} at "
            f"{document['bank_display_name']}, 1.000 ₫, memo {PREVIEW_TRANSFER_CODE}. Pay it; when "
            "the money has arrived, publish with --test-transfer-confirmed. Nothing was published."
        )
        return 0

    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    if arguments.actor_id is None:
        raise SystemExit("--actor-id is required to publish or withdraw")
    if arguments.withdraw:
        payload_document: dict[str, object] = bank_account_withdrawal_document()
    else:
        if not arguments.test_transfer_confirmed:
            print(
                "refused: pay the --preview QR from your own phone first, then publish with "
                "--test-transfer-confirmed (DEC-041). Nothing was published.",
                file=_sys.stderr,
            )
            return 2
        try:
            payload_document = dict(_document(arguments, now))
        except BankAccountError as error:
            print(f"refused: {error}", file=_sys.stderr)
            return 2
    try:
        with psycopg.connect(arguments.database_url) as connection:
            digest, created = publish_bank_account(
                connection, actor_id=arguments.actor_id, payload=payload_document
            )
    except BankAccountAuthorizationError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    state = "published" if created else "already in force"
    what = "bank account withdrawal" if arguments.withdraw else "bank account"
    print(f"{what} {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
