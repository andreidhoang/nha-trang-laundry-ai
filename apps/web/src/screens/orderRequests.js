/**
 * Tiếp nhận: record that a customer is at the counter, then hand the draft to Báo giá.
 *
 * This screen exists because the quote screen used to demand a pasted order-request UUID, which
 * is not something a person at a counter has. Its restraint matters as much as its existence:
 *
 *   - **It stores nothing about the person, and now never has to.** `DEC-013` (2026-08-26)
 *     settled the walk-in case the way that needs no consent record: the counter issues a number
 *     and nothing about the customer is kept. "Phát phiếu" below calls that route and fills the
 *     field with the reference it returns. A name or a phone number typed here would be PII with
 *     no consent behind it, so the form still has no such fields and never will; an unknown code
 *     comes back `CONTACT_BINDING_UNKNOWN` as a `REQUIRE_HUMAN` refusal, not as a quiet creation.
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
import { count, dateTime, matchesFilter, shortId } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  copyable,
  empty,
  errorNotice,
  explain,
  facts,
  gated,
  gatedFields,
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
      // Copyable, not just shortened. `Tạo đơn` on the order board asks for this exact value and
      // its own hint says "chép từ màn hình Tiếp nhận" -- but this card rendered `shortId()` with
      // no title and no copy control, and the form clears the input it was typed into, so for any
      // intake older than the current one the full id existed nowhere a human could reach it. The
      // instruction was real and impossible to follow.
      [
        "Khách",
        copyable({
          value: String(result.contact_binding_id),
          display: shortId(result.contact_binding_id),
        }),
        { mono: true, span: true },
      ],
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
          [
            "Liên hệ",
            copyable({
              value: String(item.contact_binding_id),
              display: shortId(item.contact_binding_id),
            }),
            { mono: true, span: true },
          ],
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
  /**
   * The "Mã khách" box of the current form build. Held so a successful intake can clear the box
   * together with `draft.contactId` -- clearing only the draft left the used id on screen while
   * the form would submit nothing, and the next press answered "Mã khách chưa đúng dạng" about a
   * value the operator could plainly see was a well-formed id.
   *
   * @type {HTMLInputElement|null}
   */
  let contactField = null;

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
    const needle = filterText.trim();
    if (!needle) return fetched;
    return fetched.filter((item) =>
      matchesFilter([item.order_request_id, item.status, item.contact_binding_id], needle),
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
      if (contactField) {
        contactField.value = "";
        contactField.setAttribute("aria-invalid", "false");
      }
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
      // No `id` here: `labelled` puts it on the field it can find, and setting it in two places is
      // what produced a duplicate when this input was wrapped alongside the ticket button.
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
    contactField = /** @type {HTMLInputElement} */ (contactInput);

    // "Phát phiếu" — DEC-013. The route takes no body because nothing about the customer is
    // collected; it hands back a number to say out loud and a reference to carry on the order.
    const ticketSubmission = new Submission("counter-ticket");
    const ticketNote = h("span", { class: "hint" });
    const ticketButton = h(
      "button",
      {
        type: "button",
        class: "button button--quiet",
        // It POSTs a counter ticket, so it has to go dead with the network like every other write.
        // Without this it stayed lit while the form's own submit greyed out and explained itself --
        // and this is the first button of the shop's day, so an operator with no wifi would press
        // it, get nothing, and press it again.
        dataRequiresNetwork: "true",
        onClick: async () => {
          ticketButton.disabled = true;
          ticketNote.textContent = "Đang phát phiếu…";
          try {
            const issued = await request(
              `/internal/v1/stores/${encodeURIComponent(store)}/counter-tickets`,
              // `request()` refuses any mutating call without a key, so omitting one threw here
              // in the browser and this button never reached the server at all.
              { method: "POST", body: {}, idempotencyKey: ticketSubmission.key() },
            );
            ticketSubmission.reset();
            draft.contactId = issued.ticket_id;
            contactInput.value = issued.ticket_id;
            contactInput.setAttribute("aria-invalid", "false");
            submission.reset();
            ticketNote.textContent = `Phiếu số ${issued.ticket_number} — đọc số này cho khách.`;
          } catch (error) {
            ticketNote.textContent = "Không phát được phiếu.";
            render(resultHost, errorNotice(error));
            revealError(resultHost);
          } finally {
            // Never re-arm a control `gated()` disabled for the role; that is its own reason.
            if (ticketButton.getAttribute("data-denied") !== "true") ticketButton.disabled = false;
          }
        },
      },
      "Phát phiếu (khách vãng lai)",
    );
    // Gated like every other write on this form. `issue_counter_ticket` depends on
    // `require_operations_staff`, the same gate as the intake itself, so it shares the form's
    // verdict: an AUDITOR used to get a live button here and a 403 after pressing it.
    const ticketRow = h("div", { class: "row" }, gated(ticketButton, writeVerdict), ticketNote);

    return h(
      "form",
      { class: "form", onSubmit: submit },
      labelled({
        id: "intake-contact",
        label: "Mã khách",
        hint:
          "Khách vãng lai: bấm \u201cPhát phiếu\u201d để lấy mã — không lưu tên, số điện thoại " +
          "hay địa chỉ của khách. Khách đã từng nhắn tin qua kênh chính thức thì dùng mã sinh ra " +
          "từ lần nhắn đó. Mã lạ sẽ bị từ chối chứ không được tự tạo.",
        control: h("div", { class: "stack stack--tight" }, contactInput, ticketRow),
      }),
      // The question every counter shift asks on its first day. It is answered here, next to the
      // field that raises it, because sending somebody to a register of unsupported capabilities
      // to learn that a walk-in cannot be served is answering it too late.
      explain(
        "Khách đi thẳng vào tiệm, chưa từng nhắn tin thì sao?",
        h(
          "p",
          null,
          "Bấm “Phát phiếu (khách vãng lai)” ở trên. Quầy phát một số phiếu, đọc số đó cho khách, " +
            "và ô “Mã khách” tự điền. Không cần khách nhắn tin trước, và không cần ghi tay.",
        ),
        h(
          "p",
          null,
          "Hệ thống không lưu tên, số điện thoại hay địa chỉ của khách vãng lai — chỉ một con số " +
            "do quầy phát. Vì không có thông tin cá nhân nào được lưu nên cũng không cần xin phép " +
            "khách điều gì. Đây là quyết định của chủ tiệm ngày 26/08/2026 (DEC-013), không phải " +
            "một khoảng trống.",
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

  // The "Mã khách" box goes dead for a role that cannot submit, like `#/exports` and the order
  // board: typing an id into a form whose buttons are refused is work thrown away.
  render(formHost, gatedFields(buildForm(), writeVerdict));
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
