/**
 * Nhân sự: who works in the selected store, and everything an owner does about a person — in one
 * place, on the person (spec V2 §5.8).
 *
 * The V1 screen was four forms, one per endpoint, each asking for a pasted staff UUID. This one is
 * built around the hiring task instead: "＋ Thêm nhân sự" → the new person's sheet opens by itself →
 * add a role → assign the store. Nobody types an identifier for anyone the screen can name.
 *
 * What the API offers still dictates the shape, so the reasoning is written down:
 *
 *   - **The list is per store, and only the owner's.** `GET /internal/v1/stores/{store}/staff`
 *     (READ-PATHS-001): everyone assigned to the selected store, their active roles, their account
 *     status and, for thirty days, an assignment that was revoked. No email and no OIDC subject: the
 *     server does not return them.
 *   - **A person created a minute ago is in no list.** They belong to no store yet. The create reply
 *     hands back their identifier, and the console carries it straight into that person's sheet —
 *     this is the one place the identifier travels, and it travels in memory, never by paste.
 *   - **The list needs a store; the commands do not.** An owner with no store yet must still be able
 *     to create people and assign stores — including to themselves — so the screen does not declare
 *     `needsStore` (it is in `STORE_GATE_EXEMPT`). The list alone waits for a store, and says so
 *     instead of requesting one.
 *   - **The store picker offers the owner's own stores** (the session's `memberStoreIds` and
 *     `storeNames`). No route lists every store or a person's stores, so a store the owner does not
 *     belong to is reachable only by its code, under "Nhập mã thủ công".
 *   - **Each command keeps its own `Submission`.** Three unrelated writes; sharing one idempotency
 *     key across them would let a replayed key from a role grant collide with a disable. Every key
 *     is reset when the person, the role or the store changes, and after a success.
 *   - **None of them takes `If-Match`.** The identity repository locks and bumps the aggregate
 *     version itself, so there is no row version for the client to hold.
 *   - **Disabling is two-press and disarms itself** (`confirmButton`). `disable_staff` also revokes
 *     every session that user holds, which lands on someone mid-shift with no warning; that fact is
 *     printed beside the button, not behind an ⓘ.
 *   - **Every write ends in a re-read.** The three commands answer `204` with no body, so the list
 *     is read again and the sheet redrawn from it. For a person who is not in the selected store's
 *     list the sheet says the server accepted the command and nothing more — it has not been shown
 *     the new state, and asserting it would be the console guessing.
 *
 * @module screens/staff
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, dateTime, shortId } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, snapshot, storeId } from "../core/session.js";
import { errorNotice, gated, labelled, resultLine, setResult } from "../ui/components.js";
import {
  avatar,
  button,
  confirmButton,
  emptyState,
  infoButton,
  inlineAlert,
  list,
  listRow,
  page,
  section,
  segmented,
  sheet,
  show,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";

/** `StaffRole`, in the order the database `CHECK` constraint lists them. */
const ROLES = ["OWNER_ADMIN", "OPS_APPROVER", "OPERATOR", "DRIVER", "ACCOUNTANT", "AUDITOR"];

/** `staff_users.oidc_subject CHECK (length BETWEEN 1 AND 255)`, mirrored to catch it before a trip. */
const MAX_SUBJECT = 255;

/** `staff_users.display_name CHECK (length BETWEEN 1 AND 200)`. */
const MAX_DISPLAY_NAME = 200;

/**
 * @typedef {object} DirectoryEntry a `StaffDirectoryEntryResponse`
 * @property {string} staff_user_id
 * @property {string} display_name
 * @property {string} status
 * @property {string[]} roles
 * @property {string} assigned_at
 * @property {string|null} assignment_revoked_at
 *
 * @typedef {object} Person who a sheet is about
 * @property {string} id
 * @property {string} name
 * @property {DirectoryEntry|null} entry the list row, when the person is in the selected store's list
 */

/**
 * The one status word a person earns, most serious first. The token stays in the pill's `title`
 * and in the technical drawer (spec §4.1).
 *
 * @param {DirectoryEntry|null} entry
 * @returns {{state: "ok"|"warn"|"danger", text: string, token: string}}
 */
function statusOf(entry) {
  if (entry && entry.status !== "ACTIVE") {
    return { state: "danger", text: "Đã vô hiệu hoá", token: String(entry.status || UNKNOWN) };
  }
  if (entry && entry.assignment_revoked_at) {
    return { state: "warn", text: "Đã thu hồi khỏi cửa hàng", token: "REVOKED" };
  }
  return { state: "ok", text: "Đang hoạt động", token: "ACTIVE" };
}

/**
 * Roles as pills, glossed through the one map (`enumVi`), token in `title`.
 *
 * @param {string[]} roles
 * @returns {HTMLElement}
 */
function rolePills(roles) {
  return h(
    "span",
    { class: "row" },
    roles.length
      ? roles.map((role) => statusPill({ state: "info", text: enumVi(role), token: role }))
      : h("span", null, "Chưa có vai trò nào"),
  );
}

/**
 * One person on the list. Every string from the server is a text node (`core/dom`).
 *
 * @param {DirectoryEntry} entry
 * @param {(person: Person) => void} onOpen
 * @returns {HTMLElement}
 */
function directoryRow(entry, onOpen) {
  const id = String(entry.staff_user_id || "");
  const name = String(entry.display_name || UNKNOWN);
  const roles = Array.isArray(entry.roles) ? entry.roles.map(String) : [];
  return listRow({
    onClick: () => onOpen({ id, name, entry }),
    leading: avatar(name),
    title: name,
    meta: [
      h("span", { class: "row" }, statusPill(statusOf(entry)), rolePills(roles)),
      entry.assignment_revoked_at
        ? h("span", null, `Thu hồi lúc ${dateTime(entry.assignment_revoked_at)}`)
        : null,
    ],
    data: { staffId: id, staffStatus: String(entry.status || "") },
  });
}

/**
 * Nhân sự của cửa hàng này — `GET /internal/v1/stores/{store}/staff` (READ-PATHS-001).
 *
 * Without a selected store nothing is requested: the route is store-scoped, and a `/stores/null/`
 * request would come back as a refusal the owner would misread as "you are not allowed".
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {(person: Person) => void} spec.onOpen
 * @param {(entries: DirectoryEntry[]) => void} spec.onLoaded
 * @returns {{node: HTMLElement, reload: () => Promise<void>, find: (id: string) => DirectoryEntry|null}}
 */
function directoryPanel(spec) {
  const store = storeId();
  const host = h("div", { id: "staff-directory" }, skeletonRows(3));
  const truncation = h("p", { class: "hint", role: "status" });
  /** @type {DirectoryEntry[]} */
  let entries = [];

  async function reload() {
    if (!store || !spec.verdict.allowed) return;
    try {
      const body = await request(`/internal/v1/stores/${encodeURIComponent(store)}/staff`);
      entries = Array.isArray(body?.staff) ? body.staff : [];
      truncation.textContent = body?.truncated
        ? `Máy chủ cắt danh sách ở ${entries.length} người; có thể còn nữa.`
        : "";
      render(
        host,
        entries.length
          ? list(
              entries.map((entry) => directoryRow(entry, spec.onOpen)),
              { label: "Nhân sự của cửa hàng này" },
            )
          : emptyState({
              icon: "staff",
              title: "Chưa có ai được gán vào cửa hàng này.",
              body: "Bấm “＋ Thêm nhân sự”, rồi gán người đó vào cửa hàng ngay trong thẻ của họ.",
            }),
      );
      spec.onLoaded(entries);
    } catch (error) {
      truncation.textContent = "";
      show(host, errorNotice(error, { onRetry: () => void reload() }));
    }
  }

  if (!spec.verdict.allowed) {
    render(
      host,
      inlineAlert({
        state: "warn",
        title: "Vai trò này không đọc được danh sách nhân sự",
        body: spec.verdict.reason,
      }),
    );
  } else if (store) {
    void reload();
  } else {
    render(
      host,
      inlineAlert({
        state: "info",
        title: "Chưa chọn cửa hàng",
        body: h(
          "p",
          { class: "hint" },
          "Danh sách nhân sự đọc theo từng cửa hàng. Chọn cửa hàng ở thanh trên cùng để xem ai " +
            "đang làm ở đó; thêm nhân sự và gán cửa hàng vẫn làm được khi chưa chọn.",
        ),
      }),
    );
  }

  return {
    node: h("div", { class: "stack stack--tight" }, truncation, host),
    reload,
    find: (id) => entries.find((entry) => String(entry.staff_user_id) === id) || null,
  };
}

/**
 * "＋ Thêm nhân sự" — the create form, in a sheet.
 *
 * The request model is lenient — `StaffCreateRequest` is a plain `BaseModel`, so unknown keys are
 * ignored rather than rejected — which means an accidental extra field would be silently dropped
 * instead of failing loudly. Only the three real keys are ever sent.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {(person: Person) => void} spec.onCreated
 * @returns {{node: HTMLElement, open: () => void}}
 */
function createSheet(spec) {
  const submission = new Submission("staff-create");
  const draft = { subject: "", name: "", email: "" };
  const result = resultLine();
  const errorHost = h("div");

  /** @returns {string} an operator-facing problem, or "" */
  function validate() {
    const subject = draft.subject.trim();
    const name = draft.name.trim();
    if (!subject) return "Chưa nhập định danh đăng nhập (OIDC subject).";
    if (subject.length > MAX_SUBJECT) return `OIDC subject dài quá ${MAX_SUBJECT} ký tự.`;
    if (!name) return "Chưa nhập tên hiển thị.";
    if (name.length > MAX_DISPLAY_NAME) return `Tên hiển thị dài quá ${MAX_DISPLAY_NAME} ký tự.`;
    return "";
  }

  /** @param {SubmitEvent} event */
  async function submit(event) {
    event.preventDefault();
    render(errorHost);
    const problem = validate();
    if (problem) {
      setResult(result, "danger", problem);
      return;
    }
    const email = draft.email.trim();
    const name = draft.name.trim();
    const payload = {
      oidc_subject: draft.subject.trim(),
      display_name: name,
      // An empty box means "no email", which is `null`. Sending "" would store an empty string as
      // if it were an address, and the server checks nothing about this value.
      email: email || null,
    };
    setResult(result, "warn", "Đang tạo nhân sự…");
    try {
      const created = await request("/internal/v1/staff", {
        method: "POST",
        body: payload,
        idempotencyKey: submission.key(),
      });
      // Confirmed exactly once, here. The next submission is a different person and a new intent.
      submission.reset();
      setResult(result, null, null);
      for (const field of form.querySelectorAll("input")) field.value = "";
      draft.subject = "";
      draft.name = "";
      draft.email = "";
      dialog.close();
      toast(`Đã tạo ${name}`);
      spec.onCreated({ id: String(created.staff_user_id), name, entry: null });
    } catch (error) {
      // The subject is unique; since the create route answers a taken one with a 409 that names
      // it, the refusal says exactly that instead of the generic conflict sentence.
      const taken = typeof error?.detail === "string" && error.detail.includes("OIDC subject");
      setResult(
        result,
        "danger",
        taken
          ? "Không tạo được: định danh đăng nhập này đã thuộc về một nhân sự khác."
          : "Không tạo được nhân sự.",
      );
      show(errorHost, errorNotice(error));
    }
  }

  /**
   * @param {string} key
   * @param {Record<string, string|null>} props
   * @returns {HTMLInputElement}
   */
  const input = (key, props) =>
    /** @type {HTMLInputElement} */ (
      h("input", {
        type: "text",
        autocomplete: "off",
        ...props,
        onInput: (event) => {
          draft[key] = event.target.value;
          submission.reset();
        },
      })
    );

  const form = h(
    "form",
    { class: "form", onSubmit: submit },
    h(
      "div",
      { class: "fact-line" },
      h(
        "p",
        { class: "hint" },
        "Tạo xong, người này chưa làm được gì cho tới khi có vai trò và cửa hàng — thêm ngay ở " +
          "thẻ của họ, mở ra sau khi tạo.",
      ),
      infoButton(
        "Tạo nhân sự gồm những gì?",
        h(
          "p",
          { class: "hint" },
          "Tạo xong là có danh tính, chưa có vai trò và chưa có cửa hàng. Người này chưa làm được " +
            "gì cho tới khi được gán vai trò và được gán cửa hàng — cả hai việc đó làm ngay trong " +
            "thẻ của người đó, và chỉ chủ mới thực hiện được.",
        ),
        h(
          "p",
          { class: "hint" },
          "Định danh OIDC là định danh của người này ở nhà cung cấp đăng nhập, và phải là duy " +
            "nhất trong toàn hệ thống. Nếu định danh đã thuộc về một người, máy chủ từ chối và " +
            "không tạo bản ghi thứ hai.",
        ),
        h(
          "p",
          { class: "hint" },
          "Máy chủ không kiểm tra định dạng email và không gửi gì tới địa chỉ này. Nó chỉ được lưu " +
            "lại nguyên văn, nên gõ sai sẽ không có ai báo.",
        ),
      ),
    ),
    labelled({
      id: "staff-create-subject",
      label: "Định danh đăng nhập (OIDC subject)",
      control: input("subject", {
        spellcheck: "false",
        dataFormat: "id",
        maxlength: String(MAX_SUBJECT),
        placeholder: "auth0|abc123",
      }),
    }),
    labelled({
      id: "staff-create-name",
      label: "Tên hiển thị",
      control: input("name", {
        maxlength: String(MAX_DISPLAY_NAME),
        placeholder: "Nguyễn Thị Lan",
      }),
    }),
    labelled({
      id: "staff-create-email",
      label: "Email (không bắt buộc)",
      // Deliberately `text`, not `email`. The browser's own `email` validation would refuse values
      // the server accepts, and would imply a check that does not exist anywhere behind this field.
      control: input("email", { spellcheck: "false", placeholder: "để trống nếu chưa có" }),
    }),
    gated(
      button({
        label: "Tạo nhân sự",
        type: "submit",
        variant: "primary",
        block: true,
        network: true,
        id: "staff-create-submit",
      }),
      spec.verdict,
    ),
    result,
    errorHost,
  );

  const dialog = sheet({ title: "Thêm nhân sự", body: form, id: "staff-create" });
  return { node: dialog.node, open: () => dialog.open() };
}

/**
 * One person's sheet: status, roles, store, disable, and the technical record.
 *
 * One sheet per screen, redrawn per person. Its result lines and error hosts are per command and
 * are cleared whenever a different person is opened, so a refusal about one person is never left
 * standing on another's sheet.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {() => Promise<void>} spec.reload re-read the list after a write the server accepted
 * @returns {{node: HTMLElement, open: (person: Person) => void, update: (find: (id: string) => DirectoryEntry|null) => void}}
 */
function personSheet(spec) {
  const roleSubmission = new Submission("staff-role");
  const storeSubmission = new Submission("staff-store");
  const disableSubmission = new Submission("staff-disable");

  /** @type {Person|null} */
  let person = null;
  /** Set when this sheet disabled a person who is not in the list, so it can say so. */
  let disabledHere = false;
  const draft = { role: "OPERATOR", store: "", manualStore: "" };

  const roleResult = resultLine();
  const roleErrors = h("div");
  const storeResult = resultLine();
  const storeErrors = h("div");
  const disableResult = resultLine();
  const disableErrors = h("div");

  const dialog = sheet({ title: "Nhân sự", body: null, id: "staff-person" });
  const titleNode = dialog.node.querySelector(".sheet__title");

  /** The stores the owner can pick without typing: their own, by name. */
  function ownStores() {
    const state = snapshot();
    const names = state.storeNames || {};
    return (state.memberStoreIds || []).map((id) => ({
      value: id,
      label: names[id] || `Cửa hàng ${shortId(id)}`,
    }));
  }

  function clearResults() {
    for (const line of [roleResult, storeResult, disableResult]) setResult(line, null, null);
    for (const host of [roleErrors, storeErrors, disableErrors]) render(host);
  }

  /** @param {Person} next */
  function open(next) {
    person = next;
    roleSubmission.reset();
    storeSubmission.reset();
    disableSubmission.reset();
    const held = next.entry?.roles || [];
    draft.role = held.includes("OPERATOR")
      ? ROLES.find((role) => !held.includes(role)) || "OPERATOR"
      : "OPERATOR";
    const stores = ownStores();
    const selected = storeId();
    draft.store =
      selected && stores.some((option) => option.value === selected)
        ? selected
        : stores[0]?.value || "";
    draft.manualStore = "";
    clearResults();
    draw();
    dialog.open();
  }

  /** Redraw from the list the screen just re-read, if this person is in it. */
  function update(find) {
    if (!person || !dialog.node.open) return;
    const entry = find(person.id);
    if (entry) {
      // The "not in this store's list, so not read back" line is false the moment they are.
      if (!person.entry) setResult(roleResult, null, null);
      person = { ...person, entry };
    }
    draw();
  }

  async function addRole() {
    if (!person) return;
    const target = person;
    const role = draft.role;
    render(roleErrors);
    setResult(roleResult, "warn", "Đang thêm vai trò…");
    try {
      await request(`/internal/v1/staff/${encodeURIComponent(target.id)}/roles`, {
        method: "POST",
        body: { role },
        idempotencyKey: roleSubmission.key(),
      });
      roleSubmission.reset();
      toast(`Đã thêm vai trò ${enumVi(role)} cho ${target.name}`);
      await spec.reload();
      setResult(
        roleResult,
        "ok",
        person?.entry
          ? `Đã thêm ${enumVi(role)}. Vai trò ở trên vừa được đọc lại từ máy chủ.`
          : `Máy chủ đã nhận vai trò ${enumVi(role)}. Người này chưa thuộc cửa hàng đang chọn, ` +
              "nên vai trò của họ chưa đọc lại được ở đây.",
      );
    } catch (error) {
      setResult(
        roleResult,
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "MISSING"
          ? "Không có nhân viên đang hoạt động với mã này."
          : "Không thêm được vai trò.",
      );
      show(roleErrors, errorNotice(error));
    }
  }

  /** @param {"grant"|"revoke"} intent */
  async function sendStore(intent) {
    if (!person) return;
    const target = person;
    const manual = draft.manualStore.trim();
    const store = manual || draft.store;
    render(storeErrors);
    if (!UUID.test(store)) {
      setResult(
        storeResult,
        "danger",
        manual ? "Mã cửa hàng phải là UUID." : "Chưa chọn cửa hàng.",
      );
      return;
    }
    const storeName = snapshot().storeNames?.[store] || `cửa hàng ${shortId(store)}`;
    setResult(storeResult, "warn", intent === "grant" ? "Đang gán cửa hàng…" : "Đang thu hồi…");
    try {
      await request(
        `/internal/v1/staff/${encodeURIComponent(target.id)}/stores/${encodeURIComponent(store)}`,
        { method: intent === "grant" ? "POST" : "DELETE", idempotencyKey: storeSubmission.key() },
      );
      storeSubmission.reset();
      toast(
        intent === "grant"
          ? `Đã gán ${target.name} vào ${storeName}`
          : `Đã thu hồi ${target.name} khỏi ${storeName}`,
      );
      await spec.reload();
      setResult(
        storeResult,
        "ok",
        store === storeId()
          ? "Máy chủ đã nhận; danh sách của cửa hàng đang chọn vừa được đọc lại."
          : "Máy chủ đã nhận. Đây không phải cửa hàng đang chọn, nên màn hình này không đọc lại " +
              "được — người đó đăng nhập rồi xem thanh trên cùng là cách kiểm tra chắc chắn.",
      );
    } catch (error) {
      setResult(
        storeResult,
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Chỉ chủ cửa hàng đang hoạt động mới gán hoặc thu hồi được."
          : "Không thực hiện được.",
      );
      show(storeErrors, errorNotice(error));
    }
  }

  async function disable() {
    if (!person) return;
    const target = person;
    render(disableErrors);
    setResult(disableResult, "warn", "Đang vô hiệu hoá…");
    try {
      await request(`/internal/v1/staff/${encodeURIComponent(target.id)}/disable`, {
        method: "POST",
        idempotencyKey: disableSubmission.key(),
      });
      disableSubmission.reset();
      toast(`Đã vô hiệu hoá ${target.name}`);
      // Known without a read: the server answered 204 to exactly this command for exactly this
      // person, and a re-read below replaces it with the list's own row when there is one.
      person = {
        ...target,
        entry: target.entry
          ? { ...target.entry, status: "DISABLED" }
          : null,
      };
      disabledHere = true;
      setResult(disableResult, null, null);
      await spec.reload();
      draw();
    } catch (error) {
      setResult(
        disableResult,
        "danger",
        error.kind === "MISSING"
          ? "Máy chủ từ chối: không tìm thấy nhân viên đang hoạt động, hoặc đây là chủ đang hoạt động cuối cùng."
          : "Không vô hiệu hoá được.",
      );
      show(disableErrors, errorNotice(error));
    }
  }

  function draw() {
    if (!person) return;
    const entry = person.entry;
    const status = statusOf(entry);
    const active = !(entry && entry.status !== "ACTIVE") && !(disabledHere && !entry);
    if (titleNode) titleNode.textContent = person.name;
    dialog.node.setAttribute("aria-label", person.name);
    dialog.node.dataset.staffId = person.id;

    const stores = ownStores();
    const selectedStore = storeId();

    const summary = h(
      "div",
      { class: "who" },
      avatar(person.name),
      h(
        "div",
        { class: "who__main" },
        statusPill(active ? status : { state: "danger", text: "Đã vô hiệu hoá", token: "DISABLED" }),
        h(
          "p",
          { class: "who__meta" },
          entry
            ? entry.assignment_revoked_at
              ? `Đã thu hồi khỏi cửa hàng đang chọn lúc ${dateTime(entry.assignment_revoked_at)}`
              : `Làm ở cửa hàng đang chọn từ ${dateTime(entry.assigned_at)}`
            : selectedStore
              ? "Chưa thuộc cửa hàng đang chọn."
              : "Chưa chọn cửa hàng nên chưa biết người này thuộc đâu.",
        ),
      ),
    );

    // --- Vai trò ----------------------------------------------------------------------------
    const held = entry?.roles || [];
    const roleNode = section({
      title: "Vai trò",
      info: infoButton(
        "Thêm vai trò hoạt động thế nào?",
        h(
          "p",
          { class: "hint" },
          "Gán vai trò là cộng thêm, không phải thay thế: lệnh này không gỡ vai trò nào đang có. " +
            "Máy chủ chưa có cách thu hồi vai trò, nên hãy chắc chắn trước khi thêm.",
        ),
        h(
          "p",
          { class: "hint" },
          "Vai trò quyết định máy chủ cho phép những gì, và là của người đó ở mọi cửa hàng, " +
            "không riêng cửa hàng này. Nhân viên vận hành và tài xế không được bảo đảm đã xác " +
            "thực hai bước, nên hai vai trò này vẫn bị từ chối ở các thao tác đòi xác thực hai bước.",
        ),
        h(
          "p",
          { class: "hint" },
          "Thêm lại một vai trò người này đã có là an toàn — máy chủ ghi đè dòng cũ, và việc đó " +
            "cũng khôi phục một vai trò từng bị thu hồi.",
        ),
      ),
      children: h(
        "div",
        { class: "stack" },
        entry
          ? rolePills(held)
          : h("p", { class: "muted" }, "Vai trò hiện có chỉ đọc được khi người này thuộc cửa hàng đang chọn."),
        segmented({
          label: "Chọn vai trò để thêm",
          id: "staff-role-pick",
          wrap: true,
          value: draft.role,
          options: ROLES.map((role) => ({
            value: role,
            label: held.includes(role) ? `${enumVi(role)} ✓` : enumVi(role),
          })),
          onChange: (value) => {
            draft.role = value;
            roleSubmission.reset();
          },
        }),
        h("p", { class: "hint" }, "Thêm là cộng dồn và chưa gỡ lại được — chọn cho đúng."),
        gated(
          button({
            label: "Thêm vai trò",
            variant: "primary",
            block: true,
            network: true,
            id: "staff-role-submit",
            onClick: () => void addRole(),
          }),
          active ? spec.verdict : { allowed: false, reason: "Tài khoản đã vô hiệu hoá." },
        ),
        roleResult,
        roleErrors,
      ),
    });

    // --- Cửa hàng ---------------------------------------------------------------------------
    const manualInput = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "text",
        value: draft.manualStore,
        autocomplete: "off",
        spellcheck: "false",
        dataFormat: "id",
        placeholder: "00000000-0000-0000-0000-000000000000",
        onInput: (event) => {
          const value = event.target.value;
          draft.manualStore = value;
          storeSubmission.reset();
          if (value && !UUID.test(value.trim())) event.target.setAttribute("aria-invalid", "true");
          else event.target.removeAttribute("aria-invalid");
        },
      })
    );
    const storeNode = section({
      title: "Cửa hàng",
      info: infoButton(
        "Gán cửa hàng để làm gì?",
        h(
          "p",
          { class: "hint" },
          "Không có dòng gán thì mọi màn hình theo cửa hàng đều từ chối người đó, kể cả khi vai trò " +
            "đã đúng. Thu hồi có hiệu lực ngay ở yêu cầu kế tiếp; dòng gán vẫn được giữ lại để biết " +
            "ai đã cấp và ai đã thu hồi.",
        ),
        h(
          "p",
          { class: "hint" },
          "Danh sách ở màn hình này chỉ nói về cửa hàng đang chọn. Máy chủ chưa có cách đọc danh " +
            "sách cửa hàng của một người, nên với cửa hàng khác thì người đó đăng nhập rồi xem " +
            "thanh trên cùng là cách kiểm tra chắc chắn.",
        ),
        h(
          "p",
          { class: "hint" },
          "Chỉ các cửa hàng của chính bạn có sẵn để chọn. Chủ không cần thuộc một cửa hàng mới gán " +
            "được người khác vào đó — nhưng khi đó phải nhập mã cửa hàng, và mã không tồn tại sẽ " +
            "bị từ chối.",
        ),
      ),
      children: h(
        "div",
        { class: "stack" },
        stores.length > 1
          ? segmented({
              label: "Chọn cửa hàng",
              id: "staff-store-pick",
              wrap: true,
              value: draft.store,
              options: stores,
              onChange: (value) => {
                draft.store = value;
                storeSubmission.reset();
              },
            })
          : stores.length === 1
            ? h("p", null, h("strong", null, stores[0].label))
            : h("p", { class: "muted" }, "Bạn chưa thuộc cửa hàng nào — nhập mã cửa hàng bên dưới."),
        h(
          "details",
          { class: "manual-entry", open: stores.length === 0 || Boolean(draft.manualStore) },
          h("summary", null, "Nhập mã thủ công"),
          labelled({
            id: "staff-store-manual",
            label: "Mã cửa hàng",
            hint: "Chỉ dùng cho cửa hàng không có trong danh sách trên. Để trống thì dùng cửa hàng đã chọn.",
            control: manualInput,
          }),
        ),
        h(
          "div",
          { class: "btn-stack" },
          gated(
            button({
              label: "Gán vào cửa hàng này",
              variant: "primary",
              block: true,
              network: true,
              id: "staff-store-submit",
              onClick: () => void sendStore("grant"),
            }),
            active ? spec.verdict : { allowed: false, reason: "Tài khoản đã vô hiệu hoá." },
          ),
          gated(
            button({
              label: "Thu hồi khỏi cửa hàng này",
              block: true,
              network: true,
              id: "staff-store-revoke",
              onClick: () => void sendStore("revoke"),
            }),
            spec.verdict,
          ),
        ),
        storeResult,
        storeErrors,
      ),
    });

    // --- Vô hiệu hoá ------------------------------------------------------------------------
    const disableButton = confirmButton({
      label: "Vô hiệu hoá",
      confirmLabel: "Bấm lần nữa để vô hiệu hoá",
      block: true,
      onConfirm: () => void disable(),
    });
    disableButton.id = "staff-disable-submit";
    const disableNode = section({
      title: "Vô hiệu hoá",
      info: infoButton(
        "Vô hiệu hoá làm gì?",
        h(
          "p",
          { class: "hint" },
          "Người này sẽ bị chuyển sang DISABLED và mọi phiên đang mở của họ bị thu hồi ngay lập " +
            "tức, kể cả khi đang đứng ở quầy. Máy chủ từ chối nếu đây là chủ đang hoạt động cuối cùng.",
        ),
        h(
          "p",
          { class: "hint" },
          "Lệnh này thu hồi toàn bộ phiên đăng nhập của người đó trong cùng một giao dịch. Không " +
            "có đường quay lại từ bảng vận hành: máy chủ chưa có cách bật lại một nhân viên đã vô " +
            "hiệu hoá. Nếu cần người này làm việc lại, phải xử lý trực tiếp ở cơ sở dữ liệu.",
        ),
      ),
      children: active
        ? h(
            "div",
            { class: "stack" },
            h(
              "p",
              { class: "hint", dataState: "danger" },
              "Mọi phiên đang mở của người này bị thu hồi ngay, kể cả khi đang ở quầy. Chưa có " +
                "cách bật lại.",
            ),
            gated(disableButton, spec.verdict),
            disableResult,
            disableErrors,
          )
        : inlineAlert({
            state: "warn",
            title: "Tài khoản đã vô hiệu hoá",
            body: "Máy chủ chưa có cách bật lại một nhân viên đã vô hiệu hoá.",
          }),
    });

    render(
      dialog.body,
      summary,
      roleNode,
      storeNode,
      disableNode,
      techDetails([
        ["Mã nhân viên", person.id, { copy: person.id }],
        ["Trạng thái", entry ? String(entry.status) : active ? "ACTIVE" : "DISABLED"],
        entry ? ["Vai trò", held.length ? held.join(", ") : UNKNOWN] : null,
        entry ? ["Gán vào cửa hàng lúc", dateTime(entry.assigned_at), { mono: false }] : null,
        entry?.assignment_revoked_at
          ? ["Thu hồi lúc", dateTime(entry.assignment_revoked_at), { mono: false }]
          : null,
      ]),
    );
  }

  return {
    node: dialog.node,
    open: (next) => {
      disabledHere = false;
      open(next);
    },
    update,
  };
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const verdict = can(principal(), "STAFF_ADMIN");

  const person = personSheet({ verdict, reload: () => directory.reload() });
  const directory = directoryPanel({
    verdict,
    onOpen: (who) => person.open(who),
    onLoaded: () => person.update(directory.find),
  });
  const create = createSheet({
    verdict,
    // The hiring flow: the new person's sheet opens at once, so role and store are added without
    // their identifier ever being shown to, copied or typed by anybody.
    onCreated: (who) => person.open({ ...who, entry: directory.find(who.id) }),
  });

  const addButton = button({
    label: "＋ Thêm nhân sự",
    variant: "primary",
    id: "staff-create-open",
    onClick: () => create.open(),
  });

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Nhân sự",
      action: gated(addButton, verdict),
      info: infoButton(
        "Thứ tự cấp quyền cho một người mới — gồm mấy bước?",
        h(
          "p",
          { class: "hint" },
          "1) Thêm nhân sự — 2) Thêm vai trò — 3) Gán cửa hàng. Thẻ của người mới tự mở sau bước " +
            "một; một tài khoản mới bị máy chủ từ chối ở mọi thao tác theo cửa hàng cho tới khi bước ba xong.",
        ),
        h(
          "p",
          { class: "hint" },
          "Danh sách đọc từ máy chủ và được đọc lại sau mỗi lệnh được nhận, nên điều nó hiện là " +
            "trạng thái máy chủ đang giữ chứ không phải điều màn hình này đoán.",
        ),
      ),
    }),
    section({
      title: "Nhân sự của cửa hàng này",
      card: false,
      info: infoButton(
        "Danh sách này gồm những ai?",
        h(
          "p",
          { class: "hint" },
          "Vai trò là của người đó ở mọi cửa hàng, không riêng cửa hàng này. Người bị thu hồi " +
            "khỏi cửa hàng vẫn hiện thêm 30 ngày, có ghi rõ lúc thu hồi.",
        ),
      ),
      children: directory.node,
    }),
    create.node,
    person.node,
  );
}

export const screen = {
  path: "/staff",
  title: "Nhân sự",
  capability: "STAFF_ADMIN",
  render: render_,
};
