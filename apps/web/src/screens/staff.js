/**
 * Nhân sự: who works in the selected store, and the four identity commands an owner has.
 *
 * This screen is small and its shape is dictated almost entirely by what the API offers, so the
 * reasoning is written down rather than left to be rediscovered:
 *
 *   - **The staff list is per store, and only the owner's.** READ-PATHS-001 added
 *     `GET /internal/v1/stores/{store}/staff`: everyone assigned to the selected store, with their
 *     active roles, their account status and, for thirty days, an assignment that was revoked. Each
 *     row's buttons fill the existing forms below with that person's identifier — the forms stay
 *     as they were, because a person created a minute ago belongs to no store yet and appears in no
 *     list, so pasting the identifier the create reply handed across is still the way to reach them.
 *     No email and no OIDC subject are shown: the server does not return them.
 *   - **The list needs a store; the forms do not.** An owner with no store yet must still be able to
 *     create people and assign stores — including to themselves — so the screen does not declare
 *     `needsStore`. The list alone waits for a store, and says so instead of requesting one.
 *   - **Each command is its own form, its own `Submission` and its own result line.** They are three
 *     unrelated writes; sharing one idempotency key across them would let a replayed key from a role
 *     assignment collide with a disable.
 *   - **None of the three takes `If-Match`.** The identity repository locks and bumps the aggregate
 *     version itself, so there is no row version for the client to hold. The idempotency key is sent
 *     anyway: some routes ignore the header, and the cost of sending it is nothing next to the cost
 *     of a habit that omits it on a route that does not.
 *   - **Disabling is two-step and disarms itself.** `disable_staff` also revokes every session that
 *     user holds, which lands on someone mid-shift with no warning. The confirm state is therefore
 *     cleared the instant the UUID field changes, on any failure, and after a success — an armed
 *     button carried across an edited identifier would fire at the wrong person.
 *   - **Store assignment is a live panel, not a footnote.** STORE-ASSIGNMENT-001 added the
 *     owner-only grant/revoke routes, so the fourth form on this screen manages
 *     `staff_store_assignments` directly. A staff user created here, with a correct role, is still
 *     refused by every store-scoped route until a grant lands — the panel is what lands it.
 *
 * @module screens/staff
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, dateTime, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  copyable,
  empty,
  enumSelect,
  errorNotice,
  explain,
  facts,
  gated,
  labelled,
  panel,
  resultLine,
  setResult,
  skeleton,
} from "../ui/components.js";

/** `StaffRole`, in the order the database `CHECK` constraint lists them. */
const ROLES = ["OWNER_ADMIN", "OPS_APPROVER", "OPERATOR", "DRIVER", "ACCOUNTANT", "AUDITOR"];

/** `staff_users.oidc_subject CHECK (length BETWEEN 1 AND 255)`, mirrored to catch it before a trip. */
const MAX_SUBJECT = 255;

/** `staff_users.display_name CHECK (length BETWEEN 1 AND 200)`. */
const MAX_DISPLAY_NAME = 200;

/**
 * A UUID input. Used by both of the forms that address an existing staff user.
 *
 * @param {object} spec
 * @param {string} spec.value
 * @param {(value: string) => void} spec.onInput
 * @returns {HTMLElement}
 */
function uuidInput(spec) {
  return h("input", {
    type: "text",
    value: spec.value,
    autocomplete: "off",
    spellcheck: "false",
    dataFormat: "id",
    placeholder: "00000000-0000-0000-0000-000000000000",
    "aria-invalid": spec.value && !UUID.test(spec.value.trim()) ? "true" : null,
    onInput: (event) => {
      const value = event.target.value;
      // Updated in place rather than by redrawing the field: these forms deliberately survive a
      // keystroke without being rebuilt, so the flag has to be maintained where it is.
      if (value && !UUID.test(value.trim())) event.target.setAttribute("aria-invalid", "true");
      else event.target.removeAttribute("aria-invalid");
      spec.onInput(value);
    },
  });
}

/**
 * Tạo nhân sự.
 *
 * The request model is lenient — `StaffCreateRequest` is a plain `BaseModel`, so unknown keys are
 * ignored rather than rejected — which means an accidental extra field would be silently dropped
 * instead of failing loudly. Only the three real keys are ever sent.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {(staffUserId: string) => void} spec.onCreated
 * @returns {HTMLElement}
 */
function createStaffPanel(spec) {
  const submission = new Submission("staff-create");
  const draft = { subject: "", name: "", email: "" };

  const body = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();

  /** @returns {string} an operator-facing problem, or "" */
  function validate() {
    const subject = draft.subject.trim();
    const name = draft.name.trim();
    if (!subject) return "Chưa nhập OIDC subject.";
    if (subject.length > MAX_SUBJECT) return `OIDC subject dài quá ${MAX_SUBJECT} ký tự.`;
    if (!name) return "Chưa nhập tên hiển thị.";
    if (name.length > MAX_DISPLAY_NAME) return `Tên hiển thị dài quá ${MAX_DISPLAY_NAME} ký tự.`;
    return "";
  }

  /**
   * @param {SubmitEvent} event
   */
  async function submit(event) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setResult(result, "danger", problem);
      return;
    }

    const email = draft.email.trim();
    const payload = {
      oidc_subject: draft.subject.trim(),
      display_name: draft.name.trim(),
      // An empty box means "no email", which is `null`. Sending "" would store an empty string as
      // if it were an address, and the server checks nothing about this value.
      email: email || null,
    };

    setResult(result, "warn", "Đang tạo nhân sự…");
    render(resultHost);

    try {
      const created = await request("/internal/v1/staff", {
        method: "POST",
        body: payload,
        idempotencyKey: submission.key(),
      });
      // Confirmed exactly once, here. The next submission is a different person and a new intent.
      submission.reset();
      setResult(result, "ok", `Đã tạo nhân sự ${shortId(created.staff_user_id)}.`);
      render(resultHost, createdCard(created.staff_user_id, spec.onCreated));
      // The subject is what makes this person unique, so it is cleared: a second tap on a form that
      // still looks armed would hit the unique index and come back as a server fault, not a refusal.
      draft.subject = "";
      draft.email = "";
      render(body, form());
    } catch (error) {
      setResult(
        result,
        "danger",
        error.kind === "FAULT"
          ? "Máy chủ lỗi khi tạo. Rất có thể OIDC subject này đã tồn tại — kiểm tra trước khi thử lại."
          : "Không tạo được nhân sự.",
      );
      render(resultHost, errorNotice(error));
    }
  }

  /**
   * @returns {HTMLElement}
   */
  function form() {
    const subjectInput = h("input", {
      type: "text",
      value: draft.subject,
      autocomplete: "off",
      spellcheck: "false",
      dataFormat: "id",
      maxlength: String(MAX_SUBJECT),
      placeholder: "auth0|abc123",
      onInput: (event) => {
        draft.subject = event.target.value;
        submission.reset();
      },
    });

    const nameInput = h("input", {
      type: "text",
      value: draft.name,
      autocomplete: "off",
      maxlength: String(MAX_DISPLAY_NAME),
      placeholder: "Nguyễn Thị Lan",
      onInput: (event) => {
        draft.name = event.target.value;
        submission.reset();
      },
    });

    const emailInput = h("input", {
      // Deliberately `text`, not `email`. The browser's own `email` validation would refuse values
      // the server accepts, and would imply a check that does not exist anywhere behind this field.
      type: "text",
      value: draft.email,
      autocomplete: "off",
      spellcheck: "false",
      placeholder: "để trống nếu chưa có",
      onInput: (event) => {
        draft.email = event.target.value;
        submission.reset();
      },
    });

    const submitButton = h(
      "button",
      { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Tạo nhân sự",
    );

    return h(
      "form",
      { class: "form", onSubmit: submit },
      labelled({
        id: "staff-create-subject",
        label: "Định danh OIDC (subject)",
        hint:
          "Định danh của người này ở nhà cung cấp đăng nhập. Phải là duy nhất trong toàn hệ thống. " +
          "Lưu ý vận hành: nếu subject đã có người dùng, máy chủ trả về lỗi máy chủ chứ không phải " +
          "một lời từ chối gọn gàng — hãy kiểm tra trước khi tạo, và đừng bấm lại khi thấy lỗi đó.",
        control: subjectInput,
      }),
      labelled({
        id: "staff-create-name",
        label: "Tên hiển thị",
        hint: `Tên xuất hiện trên dòng thời gian kiểm toán. Tối đa ${MAX_DISPLAY_NAME} ký tự.`,
        control: nameInput,
      }),
      labelled({
        id: "staff-create-email",
        label: "Email (không bắt buộc)",
        hint:
          "Máy chủ không kiểm tra định dạng email và không gửi gì tới địa chỉ này. Nó chỉ được lưu " +
          "lại nguyên văn, nên gõ sai sẽ không có ai báo.",
        control: emailInput,
      }),
      h("div", { class: "action-bar" }, gated(submitButton, spec.verdict)),
      result,
    );
  }

  render(body, form());

  return panel({
    eyebrow: "Lệnh · POST /internal/v1/staff",
    title: "Tạo nhân sự",
    guardrail:
      "Tạo xong là có danh tính, chưa có vai trò và chưa có cửa hàng. Người này chưa làm được gì " +
      "cho tới khi được gán vai trò và được gán cửa hàng — cả hai việc đó làm ngay trên màn hình " +
      "này, ở các bảng Gán vai trò và Gán cửa hàng bên dưới, và chỉ chủ mới thực hiện được.",
    children: h("div", { class: "stack" }, body, resultHost),
  });
}

/**
 * The one place a staff UUID is ever shown. Nothing else in the API reads it back.
 *
 * @param {string} staffUserId
 * @param {(staffUserId: string) => void} onCarry
 * @returns {HTMLElement}
 */
function createdCard(staffUserId, onCarry) {
  return h(
    "div",
    { class: "card stack" },
    h("h3", null, "Nhân sự mới"),
    facts([
      ["Mã nhân viên", copyable({ value: String(staffUserId) }), { mono: true, span: true }],
    ]),
    h(
      "p",
      { class: "hint" },
      "Chép lại mã này. Người mới chưa thuộc cửa hàng nào nên chưa có trong danh sách nhân sự của " +
        "cửa hàng nào; nếu mất mã trước khi gán cửa hàng thì không tra lại được.",
    ),
    h(
      "div",
      { class: "form__actions" },
      h(
        "button",
        { type: "button", onClick: () => onCarry(String(staffUserId)) },
        "Điền mã này vào hai lệnh bên dưới",
      ),
    ),
  );
}

/**
 * Gán vai trò.
 *
 * A `204` with no body. The server tells us nothing beyond "it worked", so the confirmation says
 * exactly that and no more — inventing a rendered record of the assignment would be this screen
 * asserting a state it has not been shown.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {() => void} [spec.onDone] after a write the server accepted: re-read the staff list
 * @returns {{node: HTMLElement, setStaffId: (value: string) => void}}
 */
function assignRolePanel(spec) {
  const submission = new Submission("staff-role");
  const draft = { staffUserId: "", role: ROLES[2] };

  const body = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();

  const redraw = () => {
    submission.reset();
    render(body, form());
  };

  /**
   * @param {SubmitEvent} event
   */
  async function submit(event) {
    event.preventDefault();
    const staffUserId = draft.staffUserId.trim();
    if (!UUID.test(staffUserId)) {
      setResult(result, "danger", "Mã nhân viên phải là UUID.");
      return;
    }

    setResult(result, "warn", "Đang gán vai trò…");
    render(resultHost);

    try {
      await request(`/internal/v1/staff/${encodeURIComponent(staffUserId)}/roles`, {
        method: "POST",
        body: { role: draft.role },
        idempotencyKey: submission.key(),
      });
      submission.reset();
      setResult(result, "ok", `Đã gán ${enumLabel(draft.role)} cho ${shortId(staffUserId)}.`);
      spec.onDone?.();
      render(
        resultHost,
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Máy chủ trả về 204, không có nội dung"),
          h(
            "p",
            null,
            "Danh sách nhân sự ở đầu màn hình vừa được đọc lại. Nếu người này thuộc cửa hàng đang " +
              "chọn, vai trò hiện tại của họ hiện ở đó; nếu không, màn hình này chỉ biết lệnh vừa " +
              "rồi đã được nhận.",
          ),
        ),
      );
    } catch (error) {
      setResult(
        result,
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "MISSING"
          ? "Không có nhân viên đang hoạt động với mã này."
          : "Không gán được vai trò.",
      );
      render(resultHost, errorNotice(error));
    }
  }

  /**
   * @returns {HTMLElement}
   */
  function form() {
    const idInput = uuidInput({
      value: draft.staffUserId,
      onInput: (value) => {
        draft.staffUserId = value;
        submission.reset();
      },
    });

    const roleSelect = enumSelect("role", ROLES, draft.role);
    roleSelect.addEventListener("change", (event) => {
      draft.role = /** @type {HTMLSelectElement} */ (event.target).value;
      submission.reset();
    });

    const submitButton = h(
      "button",
      { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Gán vai trò",
    );

    return h(
      "form",
      { class: "form", onSubmit: submit },
      labelled({
        id: "staff-role-id",
        label: "Mã nhân viên",
        hint:
          "Bấm “Điền vào Gán vai trò” trên một người trong danh sách nhân sự phía trên, hoặc dán " +
          "UUID của người vừa tạo.",
        control: idInput,
      }),
      labelled({
        id: "staff-role-role",
        label: "Vai trò",
        hint:
          "Vai trò quyết định máy chủ cho phép những gì. OPERATOR và DRIVER không được bảo đảm đã " +
          "xác thực hai bước, nên hai vai trò này vẫn bị từ chối ở các thao tác đòi xác thực hai bước. Gán lại một vai trò " +
          "người này đã có là an toàn — máy chủ ghi đè dòng cũ, và việc đó cũng khôi phục một vai " +
          "trò từng bị thu hồi.",
        control: roleSelect,
      }),
      h("div", { class: "action-bar" }, gated(submitButton, spec.verdict)),
      result,
    );
  }

  render(body, form());

  return {
    node: panel({
      eyebrow: "Lệnh · POST /internal/v1/staff/{id}/roles",
      title: "Gán vai trò",
      guardrail:
        "Gán vai trò là cộng thêm, không phải thay thế: lệnh này không gỡ vai trò nào đang có. " +
        "Vai trò hiện có xem ở danh sách nhân sự phía trên; máy chủ chưa có cách thu hồi vai trò, " +
        "nên hãy chắc chắn trước khi gửi.",
      children: h("div", { class: "stack" }, body, resultHost),
    }),
    setStaffId: (value) => {
      draft.staffUserId = value;
      redraw();
    },
  };
}

/**
 * Vô hiệu hoá nhân sự.
 *
 * Two-step on purpose. The command flips the user to `DISABLED` and revokes every session they
 * hold in the same transaction, so it lands on a person standing at a counter with no warning and
 * there is no route that undoes it.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {() => void} [spec.onDone] after a write the server accepted: re-read the staff list
 * @returns {{node: HTMLElement, setStaffId: (value: string) => void}}
 */
function disableStaffPanel(spec) {
  const submission = new Submission("staff-disable");
  const draft = { staffUserId: "" };
  /** Whether the button is showing its confirm face. Never survives an edit, a failure or a commit. */
  let armed = false;

  const body = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();

  /**
   * The two nodes the confirm state owns, held so that arming and disarming touch only them.
   *
   * Rebuilding the whole form to flip the button's face would tear the identifier field out from
   * under the operator's cursor on the keystroke that disarms it, and this is the field in the
   * console it is least safe to mistype. `confirmHost` stays for the life of the panel;
   * `submitButton` is replaced whenever `form()` runs, and `setArmed` always writes to the
   * current one.
   */
  const confirmHost = h("div");
  let submitButton = h("button");

  const redraw = () => render(body, form());

  /**
   * @param {boolean} next
   */
  function setArmed(next) {
    armed = next;
    submitButton.textContent = armed ? "Xác nhận vô hiệu hoá" : "Vô hiệu hoá nhân sự";
    render(confirmHost, armed ? confirmNotice() : null);
  }

  /**
   * Return the control to its first step, and drop the instruction that went with it.
   *
   * The line being cleared names a specific person — "bấm lần nữa để vô hiệu hoá 3f2a…" — so
   * leaving it on screen after the identifier changed would be an instruction pointing at the
   * wrong staff member.
   */
  function disarm() {
    if (!armed) return;
    setArmed(false);
    setResult(result, null, null);
  }

  /**
   * @param {SubmitEvent} event
   */
  async function submit(event) {
    event.preventDefault();
    const staffUserId = draft.staffUserId.trim();
    if (!UUID.test(staffUserId)) {
      // Disarm first: it clears the result line, and the message below has to survive that.
      disarm();
      setResult(result, "danger", "Mã nhân viên phải là UUID.");
      return;
    }

    if (!armed) {
      setArmed(true);
      setResult(
        result,
        "warn",
        `Bấm lần nữa để vô hiệu hoá ${shortId(staffUserId)}. Mọi phiên của người này sẽ bị thu hồi ngay.`,
      );
      return;
    }

    setResult(result, "warn", "Đang vô hiệu hoá…");
    render(resultHost);

    try {
      await request(`/internal/v1/staff/${encodeURIComponent(staffUserId)}/disable`, {
        method: "POST",
        idempotencyKey: submission.key(),
      });
      submission.reset();
      draft.staffUserId = "";
      disarm();
      redraw();
      spec.onDone?.();
      setResult(
        result,
        "ok",
        `Đã vô hiệu hoá ${shortId(staffUserId)} và thu hồi mọi phiên của người này.`,
      );
      render(
        resultHost,
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Máy chủ trả về 204, không có nội dung"),
          h(
            "p",
            null,
            "Máy chủ chưa có cách bật lại một nhân viên đã vô hiệu hoá. Nếu cần người này làm việc " +
              "lại, phải xử lý trực tiếp ở cơ sở dữ liệu.",
          ),
        ),
      );
    } catch (error) {
      // Disarm on every failure. A 404 that the operator misreads as "try again" must not find a
      // button still holding a confirm state from thirty seconds ago.
      disarm();
      setResult(
        result,
        "danger",
        error.kind === "MISSING"
          ? "Máy chủ từ chối: không tìm thấy nhân viên đang hoạt động, hoặc đây là chủ đang hoạt động cuối cùng."
          : "Không vô hiệu hoá được.",
      );
      render(resultHost, errorNotice(error));
    }
  }

  /**
   * @returns {HTMLElement}
   */
  function confirmNotice() {
    return h(
      "div",
      { class: "notice", dataState: "danger" },
      h("p", { class: "notice__title" }, "Bấm lần nữa là thực hiện"),
      h(
        "p",
        null,
        "Người này sẽ bị chuyển sang DISABLED và mọi phiên đang mở của họ bị thu hồi ngay lập " +
          "tức, kể cả khi đang đứng ở quầy. Máy chủ từ chối nếu đây là chủ đang hoạt động cuối cùng.",
      ),
      h(
        "div",
        { class: "form__actions" },
        h("button", { type: "button", dataVariant: "quiet", onClick: disarm }, "Huỷ"),
      ),
    );
  }

  /**
   * @returns {HTMLElement}
   */
  function form() {
    const idInput = uuidInput({
      value: draft.staffUserId,
      onInput: (value) => {
        draft.staffUserId = value;
        submission.reset();
        // The confirm state belongs to one identifier. Editing the field is a different person.
        disarm();
      },
    });

    submitButton = /** @type {HTMLElement} */ (
      h(
        "button",
        {
          type: "submit",
          dataVariant: "danger",
          dataRequiresNetwork: "true",
          "aria-live": "polite",
        },
        armed ? "Xác nhận vô hiệu hoá" : "Vô hiệu hoá nhân sự",
      )
    );

    return h(
      "form",
      { class: "form", onSubmit: submit },
      labelled({
        id: "staff-disable-id",
        label: "Mã nhân viên",
        hint: "Kiểm tra kỹ mã này. Không có lệnh hoàn tác và máy chủ chưa có cách bật lại.",
        control: idInput,
      }),
      confirmHost,
      h("div", { class: "action-bar" }, gated(submitButton, spec.verdict)),
      result,
    );
  }

  render(body, form());

  return {
    node: panel({
      eyebrow: "Lệnh · POST /internal/v1/staff/{id}/disable",
      title: "Vô hiệu hoá nhân sự",
      guardrail:
        "Lệnh này cũng thu hồi toàn bộ phiên đăng nhập của người đó trong cùng một giao dịch. " +
        "Không có đường quay lại từ bảng vận hành.",
      children: h("div", { class: "stack" }, body, resultHost),
    }),
    setStaffId: (value) => {
      draft.staffUserId = value;
      submission.reset();
      disarm();
      redraw();
    },
  };
}

/**
 * Gán và thu hồi cửa hàng.
 *
 * STORE-ASSIGNMENT-001. This was the screen's own documented gap: a staff member created here, with
 * the right role, was refused by every store-scoped route until somebody inserted a row into
 * `staff_store_assignments` by hand. Both commands answer `204` with no body, so the confirmation
 * says what was asked and nothing more — the console cannot read an assignment back, and asserting
 * a state it has not been shown is how a console starts lying.
 *
 * Revoke is a separate, deliberate action rather than a toggle: granting and removing access to a
 * store's customers should not be one control that a mis-click reverses.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {() => void} [spec.onDone] after a write the server accepted: re-read the staff list
 * @returns {{node: HTMLElement, setStaffId: (value: string) => void}}
 */
function assignStorePanel(spec) {
  const submission = new Submission("staff-store");
  const draft = { staffUserId: "", storeId: "" };

  const body = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();

  const redraw = () => {
    submission.reset();
    render(body, form());
  };

  /**
   * @param {"grant"|"revoke"} intent
   */
  async function send(intent) {
    const staffUserId = draft.staffUserId.trim();
    const storeId = draft.storeId.trim();
    if (!UUID.test(staffUserId) || !UUID.test(storeId)) {
      setResult(result, "danger", "Mã nhân viên và mã cửa hàng đều phải là UUID.");
      return;
    }

    setResult(result, "warn", intent === "grant" ? "Đang gán cửa hàng…" : "Đang thu hồi…");
    render(resultHost);

    try {
      await request(
        `/internal/v1/staff/${encodeURIComponent(staffUserId)}/stores/${encodeURIComponent(storeId)}`,
        { method: intent === "grant" ? "POST" : "DELETE", idempotencyKey: submission.key() },
      );
      submission.reset();
      spec.onDone?.();
      setResult(
        result,
        "ok",
        intent === "grant"
          ? `Đã gán ${shortId(storeId)} cho ${shortId(staffUserId)}.`
          : `Đã thu hồi ${shortId(storeId)} khỏi ${shortId(staffUserId)}.`,
      );
      render(
        resultHost,
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Máy chủ trả về 204, không có nội dung"),
          h(
            "p",
            null,
            "Danh sách nhân sự phía trên chỉ nói về cửa hàng đang chọn và vừa được đọc lại. Máy " +
              "chủ chưa có cách đọc danh sách cửa hàng của một người, nên với cửa hàng khác thì " +
              "người đó đăng nhập rồi xem thanh trên cùng là cách kiểm tra chắc chắn.",
          ),
        ),
      );
    } catch (error) {
      setResult(
        result,
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Chỉ chủ cửa hàng đang hoạt động mới gán hoặc thu hồi được."
          : "Không thực hiện được lệnh.",
      );
      render(resultHost, errorNotice(error));
    }
  }

  /**
   * @returns {HTMLElement}
   */
  function form() {
    const staffInput = uuidInput({
      value: draft.staffUserId,
      onInput: (value) => {
        draft.staffUserId = value;
        submission.reset();
      },
    });
    const storeInput = uuidInput({
      value: draft.storeId,
      onInput: (value) => {
        draft.storeId = value;
        submission.reset();
      },
    });

    const grantButton = h(
      "button",
      { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Gán cửa hàng",
    );
    const revokeButton = h(
      "button",
      {
        type: "button",
        dataVariant: "quiet",
        dataRequiresNetwork: "true",
        onClick: () => void send("revoke"),
      },
      "Thu hồi",
    );

    return h(
      "form",
      {
        class: "form",
        onSubmit: (event) => {
          event.preventDefault();
          void send("grant");
        },
      },
      labelled({
        id: "staff-store-staff",
        label: "Mã nhân viên",
        hint: "UUID của người đã được tạo và đã có vai trò.",
        control: staffInput,
      }),
      labelled({
        id: "staff-store-store",
        label: "Mã cửa hàng",
        // "Chưa có bảng stores" was true when written and stopped being true with migration 0035,
        // which created `stores` and put a foreign key on `staff_store_assignments.store_id`. The
        // difference matters at the counter: it used to mean any UUID was accepted, and now a code
        // that names no real shop is refused by the database. Saying the old thing invites the
        // owner to read a refusal as a bug.
        hint:
          "UUID của cửa hàng, và phải là cửa hàng có thật — mã không tồn tại sẽ bị từ chối. " +
          "Chưa có danh sách để chọn ở màn hình này, và chủ không cần thuộc cửa hàng đó mới gán " +
          "được — nhưng muốn tự xem dữ liệu thì phải tự gán mình vào.",
        control: storeInput,
      }),
      h(
        "div",
        { class: "action-bar" },
        gated(grantButton, spec.verdict),
        gated(revokeButton, spec.verdict),
      ),
      result,
    );
  }

  render(body, form());

  return {
    node: panel({
      eyebrow: "Lệnh · POST/DELETE /internal/v1/staff/{id}/stores/{store}",
      title: "Gán cửa hàng",
      guardrail:
        "Không có dòng gán thì mọi màn hình theo cửa hàng đều từ chối người đó, kể cả khi vai trò " +
        "đã đúng. Thu hồi có hiệu lực ngay ở yêu cầu kế tiếp; dòng gán vẫn được giữ lại để biết " +
        "ai đã cấp và ai đã thu hồi.",
      children: h("div", { class: "stack" }, body, resultHost),
    }),
    setStaffId: (value) => {
      draft.staffUserId = value;
      redraw();
    },
  };
}

/**
 * Nhân sự của cửa hàng này — `GET /internal/v1/stores/{store}/staff` (READ-PATHS-001).
 *
 * Read-only. Each row carries buttons that put the person's identifier into the matching form
 * further down and scroll to it; nothing is sent until the operator presses that form's own
 * button, so the two-step disable keeps its confirm and the idempotency keys stay per form. The
 * disable button is offered only for an active account, because disabling a disabled one is a 404.
 *
 * Without a selected store nothing is requested: the route is store-scoped, and a `/stores/null/`
 * request would come back as a refusal the owner would misread as "you are not allowed".
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {(target: "role"|"store"|"disable", staffUserId: string) => void} spec.onPick
 * @returns {{node: HTMLElement, reload: () => Promise<void>}}
 */
function directoryPanel(spec) {
  const store = storeId();
  const host = h("div", { id: "staff-directory", class: "stack" });
  const countNode = h("span", { class: "count" }, "…");
  const truncation = h("p", { class: "hint" });

  async function reload() {
    if (!store || !spec.verdict.allowed) return;
    render(host, skeleton(3));
    try {
      const body = await request(`/internal/v1/stores/${encodeURIComponent(store)}/staff`);
      const staff = Array.isArray(body?.staff) ? body.staff : [];
      countNode.textContent = String(staff.length);
      truncation.textContent = body?.truncated
        ? `Máy chủ cắt danh sách ở ${staff.length} người; có thể còn nữa.`
        : "";
      render(
        host,
        staff.length
          ? h("ul", { class: "stack" }, staff.map((entry) => directoryRow(entry, spec)))
          : empty("Chưa có ai được gán vào cửa hàng này."),
      );
    } catch (error) {
      countNode.textContent = UNKNOWN;
      truncation.textContent = "";
      render(host, errorNotice(error, { onRetry: () => void reload() }));
    }
  }

  if (!spec.verdict.allowed) {
    countNode.textContent = UNKNOWN;
    render(
      host,
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Vai trò này không đọc được danh sách nhân sự"),
        h("p", null, spec.verdict.reason),
      ),
    );
  } else if (store) {
    void reload();
  } else {
    countNode.textContent = UNKNOWN;
    render(
      host,
      h(
        "div",
        { class: "notice", dataState: "info" },
        h("p", { class: "notice__title" }, "Chưa chọn cửa hàng"),
        h(
          "p",
          null,
          "Danh sách nhân sự đọc theo từng cửa hàng. Chọn cửa hàng ở thanh trên cùng để xem ai " +
            "đang làm ở đó; các lệnh bên dưới vẫn dùng được khi chưa chọn.",
        ),
      ),
    );
  }

  return {
    node: panel({
      eyebrow: "Đọc · GET /internal/v1/stores/{store}/staff",
      title: "Nhân sự của cửa hàng này",
      count: countNode,
      children: h(
        "div",
        { class: "stack" },
        h(
          "p",
          { class: "hint" },
          "Vai trò là của người đó ở mọi cửa hàng, không riêng cửa hàng này. Người bị thu hồi " +
            "khỏi cửa hàng vẫn hiện thêm 30 ngày, có ghi rõ lúc thu hồi.",
        ),
        truncation,
        host,
      ),
    }),
    reload,
  };
}

/**
 * One person on the directory. Every string from the server is a text node (`core/dom`).
 *
 * @param {any} entry a `StaffDirectoryEntryResponse`
 * @param {{onPick: (target: "role"|"store"|"disable", staffUserId: string) => void}} spec
 * @returns {HTMLElement}
 */
function directoryRow(entry, spec) {
  const id = String(entry.staff_user_id || "");
  const roles = Array.isArray(entry.roles) ? entry.roles : [];
  const active = entry.status === "ACTIVE";
  const revoked = Boolean(entry.assignment_revoked_at);
  /**
   * @param {"role"|"store"|"disable"} target
   * @param {string} label
   */
  const pick = (target, label) =>
    h(
      "button",
      {
        type: "button",
        dataVariant: "quiet",
        dataPrefill: target,
        onClick: () => spec.onPick(target, id),
      },
      label,
    );
  return h(
    "li",
    { class: "card stack stack--tight", dataStaffId: id },
    h(
      "div",
      { class: "row" },
      h("h3", null, String(entry.display_name || UNKNOWN)),
      badge(
        active
          ? { token: "ACTIVE", gloss: "Đang hoạt động", state: "ok" }
          : { token: String(entry.status || UNKNOWN), gloss: "Đã vô hiệu hoá", state: "danger" },
      ),
      revoked ? badge({ token: "REVOKED", gloss: "Đã thu hồi khỏi cửa hàng", state: "warn" }) : null,
    ),
    facts([
      [
        "Vai trò",
        roles.length ? roles.map((role) => enumLabel(role)).join(", ") : "Chưa có vai trò nào",
        { span: true },
      ],
      ["Gán vào cửa hàng lúc", dateTime(entry.assigned_at)],
      revoked ? ["Thu hồi lúc", dateTime(entry.assignment_revoked_at)] : null,
      ["Mã nhân viên", copyable({ value: id, display: shortId(id) }), { mono: true }],
    ]),
    h(
      "div",
      { class: "form__actions" },
      pick("role", "Điền vào Gán vai trò"),
      pick("store", "Điền vào Gán cửa hàng"),
      active ? pick("disable", "Điền vào Vô hiệu hoá") : null,
    ),
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const verdict = can(principal(), "STAFF_ADMIN");

  /** Re-read the list after a write the server accepted, so it shows state rather than intent. */
  const refresh = () => void directory.reload();
  const roles = assignRolePanel({ verdict, onDone: refresh });
  const stores = assignStorePanel({ verdict, onDone: refresh });
  const disable = disableStaffPanel({ verdict, onDone: refresh });
  const directory = directoryPanel({
    verdict,
    onPick: (target, staffUserId) => {
      const chosen = target === "role" ? roles : target === "store" ? stores : disable;
      chosen.setStaffId(staffUserId);
      chosen.node.scrollIntoView({ block: "start", behavior: "smooth" });
    },
  });

  const create = createStaffPanel({
    verdict,
    onCreated: (staffUserId) => {
      roles.setStaffId(staffUserId);
      stores.setStaffId(staffUserId);
      disable.setStaffId(staffUserId);
      roles.node.scrollIntoView({ block: "start", behavior: "smooth" });
    },
  });

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Chỉ chủ · danh sách theo cửa hàng đang chọn"),
      h("h1", null, "Nhân sự"),
      h(
        "p",
        { class: "screen__lede" },
        "Ai đang làm ở cửa hàng đang chọn, cùng vai trò và trạng thái, đọc từ máy chủ; bên dưới là " +
          "các lệnh danh tính. Sau mỗi lệnh được nhận, danh sách được đọc lại, nên điều nó hiện là " +
          "trạng thái máy chủ đang giữ chứ không phải điều màn hình này đoán.",
      ),
    ),
    explain(
      "Thứ tự cấp quyền cho một người mới — gồm mấy bước?",
      h(
        "p",
        null,
        "1) Tạo nhân sự — 2) Gán vai trò — 3) Gán cửa hàng. Cả ba bước đều làm trên màn hình " +
          "này; một tài khoản mới bị máy chủ từ chối ở mọi thao tác theo cửa hàng cho tới khi bước ba xong.",
      ),
    ),
    directory.node,
    create,
    roles.node,
    stores.node,
    disable.node,
  );
}

export const screen = {
  path: "/staff",
  title: "Nhân sự",
  capability: "STAFF_ADMIN",
  render: render_,
};
