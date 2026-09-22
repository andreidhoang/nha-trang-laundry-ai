/**
 * Xuất dữ liệu: the owner taking a copy of the shop's own day, with somebody accountable for it.
 *
 * `OPS-BOARD-001`. `ApprovalAction.EXPORT_SANITIZED_DATA` has existed since the first approval
 * migration with nothing behind it. This screen is the front of what is now behind it, and its
 * shape is the point rather than its convenience.
 *
 * **Three steps, deliberately not one button.** An export moves the shop's records out of every
 * control this system has. `APPROVAL_POLICIES` maps the action to the owner-financial policy —
 * owner role, MFA, separation of duty, a ten-minute window — and that is owner policy, not a
 * setting this console may collapse. So the screen shows the act as what it is: ask for a named
 * day, raise the envelope, and only then, after somebody who is not the requester has approved it,
 * release the file. A single "Xuất" button would have to either skip the approval or hide it, and
 * both are the same lie.
 *
 * **The requester cannot be the approver, and the screen says so before the refusal does.** The
 * server refuses a decision from the staff member who raised the request. In a shop with one owner
 * that means the owner cannot both ask and approve — which is the separation of duty working, and
 * is far kinder to learn here than at the moment the approval is rejected.
 *
 * **"Sanitized" is shown as a list, not claimed as a word.** The server returns the exact columns
 * the file will carry and the exact things it withholds, and both are rendered before anything is
 * requested. They are also what the owner's approval binds: widen the column list and the rendered
 * digest moves, so an approval already granted stops matching instead of quietly authorising more.
 *
 * **The date has no default.** A day nobody chose is a day nobody is accountable for having
 * exported, and "unknown means stop" is not only about prices.
 *
 * @module screens/exports
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, dateTime, integer, shortHash, shortId } from "../core/format.js";
import { REASON_NOTE } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  errorNotice,
  facts,
  gated,
  gatedFields,
  labelled,
  panel,
  resultLine,
  revealError,
  setResult,
} from "../ui/components.js";

/** `ApprovalAction.EXPORT_SANITIZED_DATA`, the one action this screen ever raises. */
const EXPORT_ACTION = "EXPORT_SANITIZED_DATA";

/**
 * The reason code a refusal carried, wherever the server put it.
 *
 * A `REQUIRE_HUMAN` refusal arrives with `reasonCodes`; a conflict arrives with the bare code as
 * its message. Both are the server naming one thing that is wrong, and the screen treats them the
 * same way so that a staff member gets the same sentence either way.
 *
 * @param {any} error
 * @returns {string}
 */
function reasonCodeOf(error) {
  const fromList = Array.isArray(error?.reasonCodes) ? error.reasonCodes[0] : "";
  return fromList || (typeof error?.message === "string" ? error.message : "");
}

/**
 * The plain-language note for a refusal this screen recognises, beside the server's own words.
 *
 * @param {any} error
 * @returns {HTMLElement|null}
 */
function refusalNote(error) {
  const note = REASON_NOTE[reasonCodeOf(error)];
  if (!note) return null;
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Máy chủ từ chối vì một điều kiện, không phải vì lỗi hệ thống"),
    h("p", null, note),
  );
}

/**
 * What the file will and will not carry, from the server rather than from this file's memory.
 *
 * Rendered from the request's own answer, so it cannot drift from what the export actually does:
 * the same two lists are hashed into the document the owner approves.
 *
 * The day boundary is here for a narrower and sharper reason. This console shows two numbers under
 * the words *tiền đã thu*: the takings box on `#/today`, summed over when money was taken, and the
 * money columns in this file, which belong to the orders OPENED on the named day. An order opened
 * yesterday and paid this morning is in one and not the other, and the two figures diverge most on
 * exactly the busy day somebody would try to reconcile them. Neither is wrong; shipping both under
 * one label with no statement of the difference would be. `statement_vi` is the server's own
 * sentence — the string hashed into `rendered_hash` — and is printed verbatim rather than
 * paraphrased, because a paraphrase is a description of a document nobody signed.
 *
 * @param {any} created
 * @returns {HTMLElement}
 */
function contentsNotice(created) {
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Bản xuất này mang gì, và cố ý không mang gì"),
    h(
      "p",
      null,
      "Có: ",
      h("span", { class: "mono" }, created.columns.join(", ")),
      ".",
    ),
    h(
      "p",
      null,
      "Không có: ",
      h("span", { class: "mono" }, created.excludes.join(", ")),
      ". Lời khách phàn nàn và mô tả bằng chứng nằm trong lịch xoá dữ liệu của hệ thống; một bản " +
        "sao mang ra ngoài sẽ không còn được lịch đó bảo vệ, nên nó không được mang ra.",
    ),
    h("p", null, created.statement_vi || UNKNOWN),
    h(
      "p",
      { class: "hint" },
      "Cắt ngày theo: ",
      h("span", { class: "mono" }, created.day_boundary || UNKNOWN),
      " · Truy vấn: ",
      h("span", { class: "mono" }, created.query_version),
    ),
  );
}

/**
 * Hand the produced file to the browser, once, without keeping it anywhere.
 *
 * An object URL over an in-memory blob: nothing is written to the device by this console, which is
 * the same rule every other screen obeys. The URL is revoked immediately after the click, so the
 * bytes do not outlive the download the operator asked for.
 *
 * @param {any} produced
 */
function download(produced) {
  const blob = new Blob([produced.content_csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = h("a", {
    href: url,
    download: `ntl-${produced.business_date}-${String(produced.export_id).slice(0, 8)}.csv`,
  });
  anchor.click();
  URL.revokeObjectURL(url);
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const verdict = can(principal(), "EXPORT_DATA");
  const requestSubmission = new Submission("export-request");
  const approvalSubmission = new Submission("export-approval");
  const executeSubmission = new Submission("export-execute");

  /** @type {{businessDate: string}} */
  const draft = { businessDate: "" };

  /**
   * The request this screen is working on, and the envelope raised for it. Module-free, in memory
   * only: an export request identifier is a step in a conversation, not something to persist.
   *
   * @type {{created: any, approvalId: string}}
   */
  const stage = { created: null, approvalId: "" };

  const result = resultLine();
  const stageHost = h("div", { class: "stack" });
  const producedHost = h("div", { class: "stack" });

  const dateInput = h("input", {
    type: "date",
    autocomplete: "off",
    onInput: (event) => {
      draft.businessDate = event.target.value;
      requestSubmission.reset();
      // Changing the day invalidates everything staged for the previous one, envelope included.
      // Leaving the old approval on screen beside a new date is how somebody exports a day nobody
      // approved.
      stage.created = null;
      stage.approvalId = "";
      render(stageHost);
      render(producedHost);
    },
  });

  /**
   * Step 1. Record what is wanted. Nothing leaves the system on this call.
   */
  async function createRequest() {
    if (!draft.businessDate) {
      setResult(result, "danger", "Chưa chọn ngày. Bản xuất luôn thuộc về một ngày làm việc cụ thể.");
      return;
    }
    setResult(result, "warn", "Đang ghi yêu cầu xuất…");
    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/exports`, {
        method: "POST",
        body: { business_date: draft.businessDate },
        idempotencyKey: requestSubmission.key(),
      });
      requestSubmission.reset();
      stage.created = created;
      stage.approvalId = "";
      setResult(
        result,
        "ok",
        created.replayed
          ? `Yêu cầu này đã được ghi trước đó (${shortId(created.export_request_id)}). Không có yêu cầu mới nào được tạo.`
          : `Đã ghi yêu cầu xuất ${shortId(created.export_request_id)}. Chưa có dữ liệu nào ra khỏi hệ thống.`,
      );
      renderStage();
    } catch (error) {
      setResult(result, "danger", "Không ghi được yêu cầu xuất.");
      const notice = errorNotice(error);
      render(stageHost, notice, refusalNote(error));
      revealError(notice);
    }
  }

  /**
   * Step 2. Raise the owner envelope from the binding the server handed back.
   *
   * The digests are sent back exactly as they arrived. A console that derived its own would be
   * asking the owner to approve a document the server never stored, and invariant 8 would then be
   * binding an approval to content nobody can produce again.
   */
  async function raiseApproval() {
    const created = stage.created;
    if (!created) return;
    setResult(result, "warn", "Đang tạo phong bì duyệt…");
    try {
      const approval = await request("/internal/v1/approvals", {
        method: "POST",
        body: {
          store_id: created.store_id,
          action: EXPORT_ACTION,
          resource_type: created.resource_type,
          resource_id: created.export_request_id,
          resource_version: created.resource_version,
          snapshot_hash: created.snapshot_hash,
          rendered_hash: created.rendered_hash,
          policy_version: created.policy_version,
        },
        idempotencyKey: approvalSubmission.key(),
      });
      approvalSubmission.reset();
      stage.approvalId = approval.approval_request_id;
      setResult(
        result,
        "ok",
        `Đã tạo phong bì duyệt ${shortId(approval.approval_request_id)}. Chủ tiệm — một người khác ` +
          "với người đã tạo yêu cầu xuất — mở màn hình Duyệt để quyết định.",
      );
      renderStage();
    } catch (error) {
      setResult(result, "danger", "Không tạo được phong bì duyệt.");
      const notice = errorNotice(error);
      render(producedHost, notice, refusalNote(error));
      revealError(notice);
    }
  }

  /**
   * Step 3. Release the file, once.
   *
   * The server refuses a second attempt by name. That refusal is not a failure to handle quietly:
   * one approval releases one file, and an operator who taps twice must be told the first file is
   * the one they were given rather than handed a second copy nobody approved.
   */
  async function produce() {
    const created = stage.created;
    if (!created || !stage.approvalId) return;
    setResult(result, "warn", "Đang xuất…");
    try {
      const produced = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/exports/${encodeURIComponent(created.export_request_id)}/execution`,
        {
          method: "POST",
          body: { approval_id: stage.approvalId },
          idempotencyKey: executeSubmission.key(),
        },
      );
      executeSubmission.reset();
      setResult(
        result,
        "ok",
        `Đã xuất ${produced.row_count} dòng cho ngày ${produced.business_date}. Bản ghi việc xuất ` +
          "này đã vào sổ kiểm toán kèm phong bì duyệt.",
      );
      render(
        producedHost,
        h(
          "div",
          { class: "card stack" },
          h("h3", null, "Đã xuất"),
          facts([
            ["Số dòng", integer(produced.row_count)],
            ["Ngày làm việc", String(produced.business_date)],
            [
              "Phong bì duyệt",
              h("span", { title: produced.approval_request_id }, shortId(produced.approval_request_id)),
              { mono: true },
            ],
            ["Vân tay nội dung", shortHash(produced.content_hash), { mono: true, span: true }],
            ["Truy vấn", produced.query_version, { mono: true, span: true }],
            ["Xuất lúc", dateTime(produced.produced_at)],
          ]),
          h(
            "div",
            { class: "action-bar" },
            h(
              "button",
              { type: "button", dataVariant: "primary", onClick: () => download(produced) },
              "Tải tệp CSV",
            ),
          ),
          h(
            "p",
            { class: "hint" },
            "Hệ thống không giữ lại nội dung tệp — chỉ giữ vân tay, số dòng và phong bì duyệt. " +
              "Tệp đã tải về nằm ngoài mọi lịch xoá dữ liệu của hệ thống; giữ nó ở đâu là trách " +
              "nhiệm của người đã tải.",
          ),
        ),
      );
    } catch (error) {
      setResult(result, "warn", "Máy chủ chưa cho xuất bản này.");
      const notice = errorNotice(error);
      render(producedHost, notice, refusalNote(error));
      revealError(notice);
    }
  }

  /**
   * What has been staged so far, and the one control that is next.
   */
  function renderStage() {
    const created = stage.created;
    if (!created) {
      render(stageHost);
      return;
    }
    render(
      stageHost,
      h(
        "div",
        { class: "card stack" },
        h("h3", null, "Yêu cầu xuất đã ghi"),
        facts([
          [
            "Mã yêu cầu",
            h("span", { title: created.export_request_id }, shortId(created.export_request_id)),
            { mono: true },
          ],
          ["Ngày làm việc", String(created.business_date)],
          ["Bộ dữ liệu", created.dataset, { mono: true }],
          ["Vân tay nội dung được duyệt", shortHash(created.rendered_hash), { mono: true, span: true }],
          [
            "Phong bì duyệt",
            stage.approvalId
              ? h("span", { title: stage.approvalId }, shortId(stage.approvalId))
              : h("span", null, `${UNKNOWN} chưa tạo`),
            { mono: true },
          ],
        ]),
        contentsNotice(created),
        stage.approvalId
          ? h(
              "div",
              { class: "notice", dataState: "warn" },
              h("p", { class: "notice__title" }, "Chờ chủ tiệm duyệt"),
              h(
                "p",
                null,
                "Phong bì có hiệu lực 10 phút và phải do một chủ tiệm khác với người đã tạo " +
                  "yêu cầu xuất này quyết định. Mở ",
                h("a", { href: "#/approvals" }, "màn hình Duyệt"),
                " để chủ tiệm xử lý, rồi quay lại bấm Xuất tệp.",
              ),
            )
          : null,
        h(
          "div",
          { class: "action-bar" },
          stage.approvalId
            ? gated(
                h(
                  "button",
                  { type: "button", dataVariant: "primary", dataRequiresNetwork: "true", onClick: () => void produce() },
                  "Xuất tệp",
                ),
                verdict,
              )
            : gated(
                h(
                  "button",
                  { type: "button", dataVariant: "primary", dataRequiresNetwork: "true", onClick: () => void raiseApproval() },
                  "Xin chủ tiệm duyệt",
                ),
                verdict,
              ),
        ),
      ),
    );
  }

  const form = gatedFields(
    h(
      "form",
      {
        class: "form",
        onSubmit: (event) => {
          event.preventDefault();
          void createRequest();
        },
      },
      labelled({
        id: "export-date",
        label: "Ngày làm việc cần xuất",
        hint:
          "Bắt buộc, và không có giá trị mặc định. Ngày tính theo giờ Việt Nam, đúng ngày nhân " +
          "viên đi làm, không phải theo giờ UTC.",
        control: dateInput,
      }),
      h(
        "div",
        { class: "action-bar" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
            "Tạo yêu cầu xuất",
          ),
          verdict,
        ),
      ),
      result,
    ),
    verdict,
  );

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Chủ tiệm duyệt · Có ghi sổ kiểm toán"),
      h("h1", null, "Xuất dữ liệu"),
      h(
        "p",
        { class: "screen__lede" },
        "Lấy bản sao hồ sơ của chính cửa hàng cho một ngày: mã đơn, trạng thái, mốc thời gian và " +
          "số tiền đã thu của những đơn mở trong ngày đó. Ngày cắt theo lúc mở đơn, nên tổng tiền " +
          "trong tệp không bằng ô “tiền đã thu hôm nay” ở màn hình Hôm nay — ô đó cộng theo lúc " +
          "thu. Mỗi lần xuất đều cần chủ tiệm duyệt và đều được ghi lại.",
      ),
    ),
    panel({
      eyebrow: "Lệnh",
      title: "Ba bước cho một lần xuất",
      guardrail:
        "Người tạo yêu cầu xuất không được tự duyệt yêu cầu của mình — kể cả khi phong bì duyệt " +
        "do người khác mở. Quy tắc tính theo người đã chọn dữ liệu nào rời khỏi hệ thống, và máy " +
        "chủ từ chối; đó là tách trách nhiệm chứ không phải lỗi. Một lần duyệt cho đúng một tệp.",
      children: h("div", { class: "stack" }, form, stageHost, producedHost),
    }),
  );
}

export const screen = {
  path: "/exports",
  title: "Xuất dữ liệu",
  capability: "EXPORT_DATA",
  needsStore: true,
  render: render_,
};
