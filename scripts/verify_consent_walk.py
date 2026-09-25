"""The consent walk (`DEC-033`), driven in a real browser against the real API — and filmable.

`CONSENT-TRANSACTIONAL-001` built the rule; this proves it where staff meet it. A customer's STOP
must block every message the shop starts on that channel, service messages included; a block may
be lifted only on the customer's own later message, by an owner or approver; and a service message
needs a basis the server can show and a policy the owner has published.

Two things in this walk are placed by the harness, and the film says so on screen:

  * **The draft.** The AI is switched off in this release (no provider is authorised), so no draft
    is ever written by a model. The harness writes one agent draft into the store exactly as the
    runtime would (`packages/db/tests/message_draft_test_data.seed_message_draft`).
  * **The customer's messages.** No channel adapter exists yet, so there is no inbound webhook
    route. The harness records the customer's messages — including the STOP — through the same
    `InboxRepository.record` ingress path production will use.

Everything else is a person pressing buttons in the console, as the role that is allowed to:
the owner decides the draft and lifts the block, the operator reads and asks for approval, locks
the envelope and attests the send, an approver decides the envelope on Duyệt. The owner publishing
the messaging policy is the one step done at the command line, because that is where the owner
does it (`scripts/publish_messaging_policy.py`) — it is the owner's legal confirmation, not a
console button.

Usage:
    uv run --with playwright python scripts/verify_consent_walk.py \\
        --base-url http://127.0.0.1:8100 --idp-url http://127.0.0.1:9000 \\
        --store 11111111-2222-4333-8444-555555555555 --database-url <migration url> \\
        [--video DIR --slow-mo 220]

The harness writes inbound messages through the real ingress path, which keys a digest with the
deployment hash key: run it with the same `NTL_HASH_KEY` / `NTL_HASH_KEY_FILE` the API uses.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "db" / "tests"))

import workspace_env  # noqa: E402,F401  (every scripts/ entry point imports it first)
from console_recording import Recorder, add_arguments, watch_render_defects  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--base-url", default="http://127.0.0.1:8100")
parser.add_argument("--idp-url", default="http://127.0.0.1:9000")
parser.add_argument("--store", default="11111111-2222-4333-8444-555555555555")
parser.add_argument("--database-url", required=True, help="a URL that may write seed rows")
add_arguments(parser)
arguments = parser.parse_args()

BASE = arguments.base_url.rstrip("/")
CONSOLE = f"{BASE}/staff/"
STORE = UUID(arguments.store)
REC = Recorder(arguments.video, arguments.slow_mo, "dong-y-nhan-tin")
RESULTS: list[tuple[str, bool]] = []


def ok(name: str, passed: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(passed)))
    print(
        f"  {'PASS' if passed else 'FAIL'}  {name}{f'  — {detail}' if detail else ''}", flush=True
    )
    REC.check(name, bool(passed))
    return bool(passed)


def head(number: str, title: str) -> None:
    print(f"\n{'=' * 78}\n{number}. {title}\n{'=' * 78}", flush=True)
    REC.section(f"{number}. {title}")


def note(text: str) -> None:
    print(f"  ··   {text}", flush=True)
    REC.note(text)


def token(subject: str) -> str:
    with urllib.request.urlopen(f"{arguments.idp_url}/token?sub={subject}") as response:
        return json.load(response)["id_token"]


def connect() -> Any:
    import psycopg

    return psycopg.connect(arguments.database_url, autocommit=True)


def sql(query: str, *params: object) -> Any:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
        return row[0] if row else None


class Console:
    def __init__(self, page: Any) -> None:
        self.page = page

    def sign_in(self, subject: str) -> int:
        self.page.goto(CONSOLE, wait_until="networkidle")
        status = self.page.evaluate(
            """async (t) => (await fetch('/internal/v1/auth/session', {method: 'POST',
                 credentials: 'include', headers: {'Authorization': 'Bearer ' + t}})).status""",
            token(subject),
        )
        self.page.evaluate("(id) => localStorage.setItem('staff_store_id', id)", str(STORE))
        self.page.reload(wait_until="networkidle")
        self.page.wait_for_timeout(900)
        return int(status)

    def open(self, route: str, settle: int = 1300) -> None:
        self.page.goto("about:blank")
        self.page.goto(f"{CONSOLE}{route}", wait_until="networkidle")
        self.page.wait_for_timeout(settle)

    def main_text(self) -> str:
        try:
            return self.page.locator("main").inner_text()
        except Exception:
            return ""

    def shot(self, name: str) -> None:
        directory = os.environ.get("CONSENT_WALK_SHOTS")
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=f"{directory}/{name}.png", full_page=True)


def consent_state(page: Any) -> dict[str, str]:
    return page.evaluate(
        """() => {
          const s = document.querySelector(
            '#manual-service-messaging section[data-transactional-state]');
          if (!s) return {};
          const d = s.dataset;
          return {state: d.transactionalState || '', decision: d.egressDecision || '',
                  reason: d.egressReason || ''};
        }"""
    )


def main() -> int:
    from message_draft_test_data import record_customer_message, seed_message_draft
    from nha_trang_laundry_domain.consent import OptOutDisposition
    from playwright.sync_api import sync_playwright

    now = datetime.now(UTC)
    with connect() as connection:
        draft = seed_message_draft(connection, STORE)
        record_customer_message(
            connection, draft.contact_binding_id, received_at=now - timedelta(minutes=20)
        )
    owner_id = sql("select id from staff_users where oidc_subject = 'demo-owner'")
    draft_id = str(draft.agent_run_id)

    browser_path = os.environ.get("CONSOLE_BROWSER_PATH")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            **({"executable_path": browser_path} if browser_path else {}),
            **REC.launch_options(),  # type: ignore[arg-type]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900}, **REC.context_options()
        )
        defects = watch_render_defects(context)
        console = Console(REC.film(context, context.new_page(), "tin-dich-vu"))
        page = console.page

        head(
            "1", "BẢN NHÁP — chủ tiệm duyệt nội dung (AI đang tắt: bản nháp do bộ kiểm thử đặt vào)"
        )
        note("AI chưa được bật ở bản này — bộ kiểm thử ghi một bản nháp như agent sẽ ghi")
        note("Khách đã nhắn tiệm 20 phút trước (ghi qua đường nhận tin, chưa có kênh thật)")
        ok("the owner signs in", console.sign_in("demo-owner") in (200, 201))
        console.open("#/shadow")
        card = page.locator("article", has_text=draft.text).first
        ok("the draft is on Bản nháp AI, as text", card.count() == 1)
        console.shot("01-shadow")
        card.get_by_role("button", name="Duyệt", exact=True).click()
        page.wait_for_timeout(1500)
        decided = sql(
            "select decision from agent_draft_reviews where agent_run_id = %s", draft.agent_run_id
        )
        ok("the owner's decision is recorded as APPROVE", decided == "APPROVE")

        head("2", "CHÍNH SÁCH CHƯA CÔNG BỐ — không tin dịch vụ nào được gửi")
        ok("the operator signs in", console.sign_in("demo-operations") in (200, 201))
        console.open(f"#/exceptions?draft={draft_id}", settle=2200)
        ok("the exact words to be sent are shown", draft.text in console.main_text())
        state = consent_state(page)
        note(f"máy chủ trả lời: {state}")
        ok(
            "without a published policy the service send is refused, and says why",
            state.get("reason") == "MESSAGING_POLICY_UNPUBLISHED"
            and "chưa công bố" in console.main_text(),
        )
        console.shot("02-unpublished")

        head("3", "CHỦ TIỆM CÔNG BỐ CHÍNH SÁCH — bằng lệnh quản trị, là xác nhận của chủ tiệm")
        published = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/publish_messaging_policy.py"),
                "--actor-id",
                str(owner_id),
            ],
            env={**os.environ, "DATABASE_URL": arguments.database_url},
            capture_output=True,
            text=True,
            check=False,
        )
        note((published.stdout or published.stderr).strip().splitlines()[-1][:120])
        ok("the owner publishes the messaging policy", published.returncode == 0)
        page.get_by_role("button", name="Đọc lại tin nhắn").click()
        page.wait_for_timeout(1500)
        state = consent_state(page)
        ok(
            "with the policy and the customer's own message, a service send is allowed",
            state.get("decision") == "ALLOW"
            and page.locator("[data-service-allowed='true']").count() >= 1,
        )
        console.shot("03-allowed")

        head("4", "KHÁCH NHẮN “STOP” — mọi tin tiệm chủ động gửi trên kênh này bị chặn")
        with connect() as connection:
            record_customer_message(
                connection,
                draft.contact_binding_id,
                received_at=datetime.now(UTC) - timedelta(minutes=5),
                disposition=OptOutDisposition.WITHDRAW,
            )
        note("khách nhắn STOP (ghi qua đường nhận tin)")
        page.get_by_role("button", name="Đọc lại tin nhắn").click()
        page.wait_for_timeout(1500)
        state = consent_state(page)
        ok(
            "the STOP blocks the service message too",
            state.get("state") == "SUPPRESSED" and state.get("decision") != "ALLOW",
        )
        ok(
            "the operator reads why, in Vietnamese",
            "dừng nhận tin" in console.main_text(),
        )
        # A blocked send looks blocked: the press is shut, with the reason as visible text, and
        # the STOP sentence is said once -- on the card.
        raise_control = page.locator("button[data-raise-envelope]")
        ok(
            "asking to approve is shut on screen, with the reason written under it",
            raise_control.count() == 1
            and not raise_control.is_enabled()
            and raise_control.get_attribute("data-consent-blocked") == "SUPPRESSED"
            and "không nhận tin dịch vụ" in page.locator("#manual-step-2").inner_text(),
        )
        visible = page.locator("main").inner_text()
        ok(
            "the STOP sentence is on screen exactly once",
            visible.count("Khách đã yêu cầu dừng nhận tin trên kênh này") == 1,
        )
        # And the shut press is only the screen's prediction: the server refuses the same request
        # on its own, from this session, with the same reason.
        refusal = page.evaluate(
            """async (draft) => {
              const csrf = document.cookie.split("; ").find((c) => c.startsWith("staff_csrf="));
              const store = localStorage.getItem("staff_store_id");
              const read = await (await fetch(
                `/internal/v1/stores/${store}/message-drafts/${draft}/binding`,
                {credentials: "include"})).json();
              const answer = await fetch("/internal/v1/approvals", {
                method: "POST", credentials: "include",
                headers: {"Content-Type": "application/json",
                          "Idempotency-Key": "walk-" + crypto.randomUUID(),
                          "X-CSRF-Token": decodeURIComponent((csrf || "=").split("=")[1])},
                body: JSON.stringify({
                  store_id: read.store_id, action: read.action,
                  resource_type: read.resource_type, resource_id: read.resource_id,
                  resource_version: read.resource_version, snapshot_hash: read.snapshot_hash,
                  rendered_hash: read.rendered_hash, policy_version: read.policy_version})});
              return {status: answer.status, body: await answer.json()};
            }""",
            draft_id,
        )
        note(f"máy chủ trả lời yêu cầu xin duyệt: {refusal.get('status')}")
        ok(
            "asking to approve the send is refused by the server, not just shut on screen",
            refusal.get("status") == 422
            and (refusal.get("body") or {}).get("detail", {}).get("reason_code") == "SUPPRESSED",
            json.dumps(refusal, ensure_ascii=False)[:200],
        )
        ok(
            "the operator cannot lift the block themselves",
            page.locator("button[data-service-release]:not([disabled])").count() == 0,
        )
        console.shot("04-stopped")

        head("5", "KHÁCH NHẮN LẠI — chủ tiệm gỡ chặn, dựa đúng tin nhắn đó của khách")
        with connect() as connection:
            record_customer_message(
                connection, draft.contact_binding_id, received_at=datetime.now(UTC)
            )
        note("khách chủ động nhắn lại sau STOP (ghi qua đường nhận tin)")
        ok("the owner signs in", console.sign_in("demo-owner") in (200, 201))
        console.open(f"#/exceptions?draft={draft_id}", settle=2200)
        page.get_by_role("button", name="Gỡ chặn tin dịch vụ", exact=False).first.click()
        page.wait_for_timeout(700)
        evidence = page.locator("select#service-release-evidence")
        options = evidence.locator("option").all_inner_texts()
        note(f"tin của khách để dựa vào: {options}")
        ok(
            "the owner picks the customer's message by its time, never by an id",
            bool(options) and all("-" not in o or "lúc" in o for o in options),
        )
        console.shot("05-release-sheet")
        page.locator("button[data-service-release]").click()
        page.wait_for_timeout(1800)
        released = sql(
            "select state from suppression_entries where contact_binding_id = %s "
            "and purpose = 'TRANSACTIONAL'",
            draft.contact_binding_id,
        )
        ok("the block is lifted for service messages only", released == "CLEAR")
        marketing = sql(
            "select state from suppression_entries where contact_binding_id = %s "
            "and purpose = 'MARKETING'",
            draft.contact_binding_id,
        )
        ok("marketing stays blocked", marketing == "SUPPRESSED")

        head("6", "XIN DUYỆT → NGƯỜI DUYỆT DUYỆT → KHOÁ → GHI NHẬN ĐÃ GỬI — không dán mã nào")

        def manual_entry_touched() -> bool:
            """Was any "Nhập mã thủ công" drawer opened, or any of its fields typed into?"""
            return bool(
                page.evaluate(
                    """() => [...document.querySelectorAll("details.manual-entry")]
                             .some((d) => d.open)"""
                )
            )

        ok("the operator signs in", console.sign_in("demo-operations") in (200, 201))
        console.open(f"#/exceptions?draft={draft_id}", settle=2200)
        ok(
            "the send is allowed again",
            consent_state(page).get("decision") == "ALLOW",
        )
        page.get_by_role("button", name="Xin duyệt gửi đúng tin này").click()
        page.wait_for_timeout(1800)
        approval_id = sql(
            "select id from approval_requests where resource_id = %s order by requested_at desc",
            draft.agent_run_id,
        )
        ok("an approval is raised for exactly this draft", approval_id is not None)
        console.shot("06-raised")

        # A new session on the same draft: the server says where the send stands.
        note("phiên mới — mở lại bản nháp: máy chủ cho biết phiếu đang chờ duyệt")
        ok("the operator signs in again", console.sign_in("demo-operations") in (200, 201))
        console.open(f"#/exceptions?draft={draft_id}", settle=2200)
        waiting = page.locator("#manual-step-2 [data-send-phase='waiting']")
        ok(
            "reopened, step 2 says 'Đang chờ duyệt' for the approval the server names",
            waiting.count() == 1
            and str(approval_id)[:8] in page.locator("#manual-step-2").inner_text()
            and page.locator("button[data-raise-envelope]").count() == 0,
        )
        console.shot("06b-resumed-waiting")

        ok("the approver signs in", console.sign_in("demo-approver") in (200, 201))
        console.open("#/approvals", settle=2200)
        card = page.locator("article.card", has_text=draft.text).first
        ok("Duyệt shows the exact words that will be sent", card.count() == 1)
        console.shot("07-approvals")
        card.get_by_role("button", name="Duyệt", exact=True).click()
        page.wait_for_timeout(1800)
        decision = sql(
            "select status from approval_request_states where approval_request_id = %s",
            approval_id,
        )
        ok("the approver approves the envelope", decision == "APPROVED")

        # Zero paste (spec V2 principle 2). The approver decided on another device; the operator
        # reopens the draft and the server hands back the approval id, version and both digests.
        # The harness types nothing: the fields under "Nhập mã thủ công" are never opened.
        ok("the operator signs in", console.sign_in("demo-operations") in (200, 201))
        console.open(f"#/exceptions?draft={draft_id}", settle=2200)
        note("phiếu được duyệt ở máy khác — mở lại bản nháp, bước 3 đã sẵn sàng, không dán mã")
        ok(
            "reopened, step 3 is ready with the approval the server names",
            page.locator(
                "#manual-step-3[data-state='current'] [data-send-phase='approved']"
            ).count()
            == 1
            and str(approval_id)[:8] in page.locator("#manual-step-3").inner_text(),
        )
        prefilled = page.evaluate(
            """() => ({
              approval: document.querySelector("#manual-approval-id")?.value,
              version: document.querySelector("#manual-resource-version")?.value,
              snapshot: document.querySelector("#manual-snapshot-hash")?.value,
              rendered: document.querySelector("#manual-rendered-hash")?.value,
            })"""
        )
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select resource_version, snapshot_hash, rendered_hash from approval_requests "
                "where id = %s",
                (approval_id,),
            )
            version, snapshot, rendered = cursor.fetchone()
        ok(
            "the four values came from the server, equal to the approval's own",
            prefilled
            == {
                "approval": str(approval_id),
                "version": str(version),
                "snapshot": snapshot,
                "rendered": rendered,
            },
        )
        console.shot("08a-resumed-approved")
        page.locator("button[data-lock-envelope]").click()
        page.wait_for_timeout(1800)
        envelope = sql(
            "select id from manual_send_envelopes where approval_request_id = %s", approval_id
        )
        ok(
            "one press locks the envelope for the operator who will send by hand",
            envelope is not None,
        )
        ok("the manual-entry fields were never needed", not manual_entry_touched())
        console.shot("08-locked")

        # Another new session: the envelope this operator locked is carried into step 4.
        note("phiên mới — phong bì bạn đã khoá: bước 4 sẵn sàng, không dán mã")
        ok("the operator signs in again", console.sign_in("demo-operations") in (200, 201))
        console.open(f"#/exceptions?draft={draft_id}", settle=2200)
        ok(
            "reopened, step 4 is ready for the envelope the server names",
            page.locator("#manual-step-4[data-state='current']").count() == 1
            and page.locator("#attest-envelope-id").input_value() == str(envelope),
        )
        console.shot("08b-resumed-locked")
        page.get_by_role("button", name="Vừa gửi xong").click()
        attest = page.get_by_role("button", name="Chứng thực rằng tôi đã gửi tin này")
        attest.click()
        page.wait_for_timeout(700)
        if attest.count() and attest.is_enabled():
            attest.click()  # a two-press attestation
        page.wait_for_timeout(1800)
        recorded = sql("select status from manual_send_envelopes where id = %s", envelope)
        ok("the send is recorded as MANUAL_SEND_RECORDED", recorded == "MANUAL_SEND_RECORDED")
        ok(
            "the screen says recorded is not the same as delivered",
            "không có nghĩa là khách đã nhận" in console.main_text(),
        )
        ok("and still nothing was pasted", not manual_entry_touched())
        console.shot("09-recorded")

        console.open(f"#/exceptions?draft={draft_id}", settle=2200)
        ok(
            "reopened once more, the send is done, and still not called delivered",
            page.locator("#manual-step-4[data-state='done'] [data-send-phase='recorded']").count()
            == 1
            and "không có nghĩa là khách đã nhận" in console.main_text(),
        )
        console.shot("09b-resumed-recorded")

        ok("no screen showed [object Object], undefined ₫ or NaN", not defects)
        REC.finish()
        context.close()
        for film in REC.save():
            print(f"  video: {film}")
        browser.close()

    failed = [name for name, passed in RESULTS if not passed]
    print(f"\nRESULT: {len(RESULTS) - len(failed)} ok, {len(failed)} failed")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
