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
import { enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  empty,
  errorNotice,
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
  return badge({ token: enumLabel(value), gloss: "", state: "neutral" });
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
          "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
            "yêu cầu mới nào được tạo.",
        )
      : null,
    facts([
      ["Tiếp nhận lúc", dateTime(result.created_at)],
      ["Liên hệ", shortId(result.contact_binding_id), { mono: true }],
      ["Phiên bản dòng", `v${result.row_version}`],
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
    h("p", { class: "hint mono" }, `mã yêu cầu: ${result.order_request_id}`),
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
        ? `Máy chủ trả tối đa ${LIST_LIMIT} bản ghi và đã trả đủ; có thể còn nữa. API này không có phân trang.`
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
      result.textContent = "Mã liên hệ phải là một UUID hợp lệ.";
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
        label: "Mã liên hệ của khách (contact binding UUID)",
        hint:
          "Liên hệ phải đã tồn tại: nó được tạo khi khách nhắn qua kênh chính thức và máy chủ " +
          "xác minh. Màn hình này không tạo liên hệ, không lưu tên hay số điện thoại — mã không " +
          "tồn tại sẽ bị máy chủ từ chối (CONTACT_BINDING_UNKNOWN).",
        control: contactInput,
      }),
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
      h("p", { class: "eyebrow" }, "TIẾP NHẬN · KHÔNG TẠO LIÊN HỆ · KHÔNG LƯU LỜI KHÁCH"),
      h("h1", null, "Tiếp nhận"),
      h(
        "p",
        { class: "screen__lede" },
        "Ghi nhận khách đang ở quầy, gắn với một liên hệ máy chủ đã biết. Bước tiếp theo là báo giá.",
      ),
    ),
    panel({
      eyebrow: "LỆNH",
      title: "Tiếp nhận một yêu cầu",
      guardrail:
        "Tiếp nhận chỉ mở một yêu cầu ở trạng thái nháp: không giá, không lịch, không cam kết. " +
        "Mọi con số thuộc về màn hình Báo giá và do máy chủ quyết.",
      children: h("div", { class: "stack" }, formHost, resultHost),
    }),
    panel({
      eyebrow: "ĐÃ GHI",
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
