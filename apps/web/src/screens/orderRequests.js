/**
 * Tiếp nhận: record that a customer is at the counter, then hand the draft to Báo giá.
 *
 * This screen exists because the quote screen used to demand a pasted order-request UUID, which
 * is not something a person at a counter has. Its restraint matters as much as its existence:
 *
 *   - **It creates no contact.** The one field is a contact binding that must already exist,
 *     because the domain's only source of contact bindings is the verified channel envelope
 *     (`ContactChannelBindingRepository`). A name or a phone number typed here would be PII with
 *     no consent record behind it, so the form has no such fields and never will; an unknown
 *     binding comes back `CONTACT_BINDING_UNKNOWN` as a `REQUIRE_HUMAN` refusal, not as a quiet
 *     creation.
 *   - **It records no customer words.** The aggregate has no column for what the customer said,
 *     so there is no note field. What the customer asked for is priced on the next screen, from
 *     the catalog, by the server.
 *   - **The UUID is not the product.** A created intake leads with its state and its time; the
 *     identifier survives as a low-emphasis caption for the conversation with engineering. The
 *     primary action is "Báo giá ngay", which carries the id to the quote screen itself.
 *   - **It edits and cancels nothing.** The only command wired is create. A wrong intake today is
 *     outlived by its quotes, not rewritten — there is no route that mutates one, and this screen
 *     does not pretend otherwise.
 *
 * @module screens/orderRequests
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { count, dateTime, shortId } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  empty,
  errorNotice,
  explain,
  facts,
  gated,
  labelled,
  markUpdated,
  panel,
  resultLine,
  revealError,
  skeleton,
  toolbar,
} from "../ui/components.js";

const LIST_LIMIT = 100;

/** A shape check on the contact binding, to save a round trip on an obvious typo. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * One intake row's state as a neutral badge. The console holds no opinion about which intake
 * state is good or bad; the enum value carries the meaning.
 *
 * @param {string|null|undefined} value
 * @returns {HTMLElement}
 */
function statusBadge(value) {
  return badge({ token: enumVi(value), gloss: "", state: "neutral", title: value });
}

/**
 * The card shown once the server has committed an intake. State and time lead; the identifier is
 * the caption. "Báo giá ngay" is the whole point of the screen.
 *
 * @param {any} result an `OrderRequestResponse`
 * @returns {HTMLElement}
 */
function createdCard(result) {
  return h(
    "div",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("h3", null, "Đã tiếp nhận"),
      statusBadge(result.status),
    ),
    result.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Lượt tiếp nhận này đã được ghi trước đó — đây là kết quả cũ hiện lại. Không có yêu " +
            "cầu mới nào được tạo.",
        )
      : null,
    facts([
      ["Tiếp nhận lúc", dateTime(result.created_at)],
      ["Khách", shortId(result.contact_binding_id), { mono: true }],
    ]),
    h(
      "div",
      { class: "form__actions" },
      h(
        "a",
        {
          id: "intake-quote-now",
          class: "button",
          dataVariant: "primary",
          href: `#/quotes?request=${encodeURIComponent(result.order_request_id)}`,
        },
        "Báo giá ngay",
      ),
    ),
    explain(
      "Mã kỹ thuật của lượt tiếp nhận này",
      h(
        "p",
        null,
        "Chỉ cần khi bạn báo lỗi cho kỹ thuật. Thao tác thường ngày không dùng tới nó — nút " +
          "“Báo giá ngay” ở trên đã mang sẵn mã này sang màn hình Báo giá.",
      ),
      h("p", { class: "mono" }, `${result.order_request_id} · v${result.row_version}`),
    ),
  );
}

/**
 * @param {any[]} items
 * @returns {HTMLElement}
 */
function intakeList(items) {
  if (!items.length) return empty("Chưa có lượt tiếp nhận nào trong cửa hàng này.");
  return h(
    "div",
    { class: "stack" },
    items.map((item) =>
      h(
        "article",
        { class: "card" },
        h(
          "div",
          { class: "spread" },
          h(
            "strong",
            { class: "mono", title: item.order_request_id },
            shortId(item.order_request_id),
          ),
          statusBadge(item.status),
        ),
        facts([
          ["Tiếp nhận lúc", dateTime(item.created_at)],
          ["Liên hệ", shortId(item.contact_binding_id), { mono: true }],
          ["Phiên bản dòng", `v${item.row_version}`],
        ]),
        h(
          "div",
          { class: "form__actions" },
          h(
            "a",
            {
              class: "button",
              dataVariant: "quiet",
              href: `#/quotes?request=${encodeURIComponent(item.order_request_id)}`,
            },
            "Báo giá yêu cầu này",
          ),
        ),
      ),
    ),
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const me = principal();
  const writeVerdict = can(me, "QUOTES_WRITE");
  const submission = new Submission("order-request-create");

  /** @type {{contactId: string}} */
  const draft = { contactId: "" };

  const formHost = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();
  const listHost = h("div", null, skeleton(2));
  const listCount = h("span", { class: "count" }, "…");
  const truncation = h("p", { class: "hint" });
  const filterStatus = h("p", { class: "filter-status" });
  filterStatus.hidden = true;

  /** The rows as the server returned them. The filter narrows a copy, never this list. */
  let fetched = /** @type {any[]} */ ([]);
  let filterText = "";

  function visibleItems() {
    const needle = filterText.trim().toLowerCase();
    if (!needle) return fetched;
    return fetched.filter((item) =>
      [item.order_request_id, item.status, item.contact_binding_id].some(
        (value) => value && String(value).toLowerCase().includes(needle),
      ),
    );
  }

  function renderList() {
    const visible = visibleItems();
    const active = Boolean(filterText.trim());
    filterStatus.hidden = !active;
    if (active) {
      filterStatus.textContent = `Đang lọc ${visible.length}/${fetched.length} lượt tiếp nhận`;
    }
    render(
      listHost,
      active && !visible.length
        ? empty("Không có lượt tiếp nhận nào khớp bộ lọc.")
        : intakeList(visible),
    );
  }

  const bar = toolbar({
    onReload: loadList,
    filter: {
      placeholder: "Lọc theo mã, trạng thái, liên hệ…",
      label: "Lọc danh sách tiếp nhận",
      onChange: (value) => {
        filterText = value;
        renderList();
      },
    },
  });

  async function loadList() {
    render(listHost, skeleton(2));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/order-requests?limit=${LIST_LIMIT}`,
      );
      fetched = items;
      markUpdated(bar.stamp);
      listCount.textContent = count(items, LIST_LIMIT);
      truncation.textContent = isTruncated(items, LIST_LIMIT)
        ? `Đang hiện ${LIST_LIMIT} lượt gần nhất; có thể còn nữa ở phía trước.`
        : "";
      renderList();
    } catch (error) {
      render(listHost, errorNotice(error, { onRetry: () => void loadList() }));
    }
  }

  async function submit(event) {
    event.preventDefault();
    const contactId = draft.contactId.trim();
    if (!UUID.test(contactId)) {
      result.dataset.state = "danger";
      result.textContent = "Mã khách chưa đúng dạng. Chép lại nguyên văn từ kênh chat của khách.";
      return;
    }

    result.dataset.state = "warn";
    result.textContent = "Đang ghi nhận tiếp nhận…";
    render(resultHost);

    try {
      const created = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/order-requests`,
        {
          method: "POST",
          body: { contact_binding_id: contactId },
          idempotencyKey: submission.key(),
        },
      );
      // Confirmed exactly once, in one place. The next intake is a new intent.
      submission.reset();
      draft.contactId = "";
      result.dataset.state = "ok";
      result.textContent = "Đã ghi nhận. Có thể báo giá cho yêu cầu này.";
      render(resultHost, createdCard(created));
      await loadList();
    } catch (error) {
      result.dataset.state = error.kind === "REQUIRE_HUMAN" ? "warn" : "danger";
      result.textContent =
        error.kind === "REQUIRE_HUMAN"
          ? "Máy chủ không nhận liên hệ này. Không có yêu cầu nào được tạo."
          : "Không ghi nhận được lượt tiếp nhận.";
      render(resultHost, errorNotice(error));
      revealError(resultHost);
    }
  }

  function buildForm() {
    const contactInput = h("input", {
      id: "intake-contact",
      type: "text",
      value: draft.contactId,
      autocomplete: "off",
      dataFormat: "id",
      placeholder: "00000000-0000-0000-0000-000000000000",
      "aria-invalid": draft.contactId && !UUID.test(draft.contactId) ? "true" : "false",
      onInput: (event) => {
        const input = event.target;
        draft.contactId = input.value;
        input.setAttribute(
          "aria-invalid",
          input.value && !UUID.test(input.value) ? "true" : "false",
        );
        submission.reset();
      },
    });

    return h(
      "form",
      { class: "form", onSubmit: submit },
      labelled({
        id: "intake-contact",
        label: "Mã khách",
        hint:
          "Khách phải đã từng nhắn tin cho tiệm qua kênh chính thức — mã này sinh ra từ lần " +
          "nhắn đó. Màn hình này không tạo khách mới và không lưu tên hay số điện thoại; mã lạ " +
          "sẽ bị từ chối chứ không được tự tạo. Khách vãng lai chưa nhắn tin thì chưa tiếp nhận " +
          "được ở đây — xem mục bên dưới.",
        control: contactInput,
      }),
      // The question every counter shift asks on its first day. It is answered here, next to the
      // field that raises it, because sending somebody to a register of unsupported capabilities
      // to learn that a walk-in cannot be served is answering it too late.
      explain(
        "Khách đi thẳng vào tiệm, chưa từng nhắn tin thì sao?",
        h(
          "p",
          null,
          "Chưa tiếp nhận được ở màn hình này. Mã khách chỉ sinh ra từ một tin nhắn khách đã gửi " +
            "qua kênh chính thức, nên người chưa nhắn bao giờ thì chưa có mã.",
        ),
        h(
          "p",
          null,
          "Đây là khoảng trống quy trình đã ghi nhận, không phải lỗi. Tạo khách ngay tại quầy là " +
            "lưu thông tin cá nhân khi chưa có cơ sở đồng ý, nên hệ thống từ chối thay vì tự làm. " +
            "Trước mắt: nhận đồ và ghi tay như cũ. ",
          h("a", { href: "#/gaps" }, "Xem khoảng trống này"),
          ".",
        ),
      ),
      h(
        "div",
        { class: "action-bar" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
            "Ghi nhận tiếp nhận",
          ),
          writeVerdict,
        ),
      ),
      result,
    );
  }

  render(formHost, buildForm());
  void loadList();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Quầy tiếp khách"),
      h("h1", null, "Tiếp nhận"),
      h(
        "p",
        { class: "screen__lede" },
        "Ghi nhận khách đang ở quầy, gắn với một liên hệ máy chủ đã biết. Bước tiếp theo là báo giá.",
      ),
    ),
    panel({
      eyebrow: "Lệnh",
      title: "Tiếp nhận một khách",
      guardrail:
        "Bước này chỉ mở một phiếu nháp: chưa có giá, chưa hẹn giờ, chưa hứa gì với khách. " +
        "Giá tính ở màn hình Báo giá và do máy chủ quyết.",
      children: h("div", { class: "stack" }, formHost, resultHost),
    }),
    panel({
      eyebrow: "Đã ghi",
      title: "Tiếp nhận gần đây",
      count: listCount,
      children: h("div", { class: "stack" }, bar.node, filterStatus, truncation, listHost),
    }),
  );
}

export const screen = {
  path: "/order-requests",
  title: "Tiếp nhận",
  capability: "QUOTES_READ",
  needsStore: true,
  render: render_,
};
