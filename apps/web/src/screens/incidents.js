/**
 * Khiếu nại: record that a customer complained about an order, and take it to an outcome.
 *
 * `CONSOLE-REDESIGN-004` (spec V2 §5.6) turned the V1 "one form, one list" screen into two task
 * surfaces:
 *
 *   - **`#/incidents`** — the list ("Phiếu 17 · what the customer said · status · age") and the
 *     "＋ Ghi khiếu nại" sheet. The order is chosen by the number on the customer's ticket
 *     (`GET …/orders?ticket=N`), or arrives already chosen from the order page; pasting an order
 *     UUID survives only under "Nhập mã thủ công". A recorded complaint opens its own page.
 *   - **`#/incidents/:incidentId`** — the complaint, its order, its status, and the whole remedy flow
 *     inline (`remedyFlow` from `screens/remedies.js`): options read at once, the ceiling before any
 *     number box, the proposals with the server's next step as their button.
 *
 * Four choices from the V1 screen are kept, because they are the point of it:
 *
 *   - **Opening an incident decides nothing.** `FR-INC-002`/`FR-INC-003` separate the intake record
 *     from fault and remedy. The server always answers `fault_decided` and `remedy_decided` false for
 *     a new incident; `REMEDY-001` gave `remedy_decided` one writer (carrying out a proposal, which
 *     also closes the incident once every claim has an outcome) and `fault_decided` still has none.
 *     Both render as "chưa quyết định", never as a blank.
 *   - **The console sends words and computes no digest** (`DEC-028`): exactly `order_id` and
 *     `evidence_summary`. The contact scope and the evidence digest are the server's to derive.
 *   - **There is no category picker, because there is no category field.** The server hardcodes
 *     `SERVICE_QUALITY` and `actor_type=STAFF`; stored rows may still carry
 *     `AUTOMATED_MESSAGE_ERROR` (the agent path), so the page renders whatever comes back.
 *   - **`order_id` is required** although the domain permits a message-bound incident:
 *     `IncidentOpenRequest` types it as a plain `UUID`.
 *
 * @module screens/incidents
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, ago, dateOnly, dateTime, matchesFilter, money, shortId } from "../core/format.js";
import { enumLabel, enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { navigate } from "../core/router.js";
import { principal, storeId } from "../core/session.js";
import {
  boundInput,
  errorNotice,
  gated,
  gatedFields,
  labelled,
  listView,
  resultLine,
  setResult,
} from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  inlineAlert,
  keyValues,
  list,
  listRow,
  page,
  searchField,
  section,
  sheet,
  show,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";
import { remedyFlow } from "./remedies.js";

const LIST_LIMIT = 100;

/**
 * `IncidentOpenRequest.evidence_summary` is `1..2000` characters, and the domain re-checks that
 * length after NFC normalisation and trimming. Held here so a staff member meets the limit while
 * typing instead of as a 422 after the round trip. The server is the authority.
 */
const SUMMARY_MAX = 2000;

/**
 * The two domain refusals the create route can answer with, keyed by the server's exact string.
 * Rendered beside `errorNotice`, never instead of it.
 */
const REFUSAL_NOTE = {
  "incident order binding is unavailable":
    "Mã đơn không tồn tại trong cửa hàng đang chọn. Kiểm tra lại mã đơn, hoặc kiểm tra bạn đang " +
    "đứng đúng cửa hàng. Không có sự cố nào được ghi.",
  "incident binding is invalid":
    "Ràng buộc của sự cố không qua được kiểm tra miền. Lỗi này không nằm ở chữ bạn vừa gõ. " +
    "Không có sự cố nào được ghi: bấm lại một lần, nếu vẫn vậy thì ghi ra sổ kèm số phiếu và " +
    "báo kỹ thuật.",
};

/**
 * The cross-screen hand-off staged by the order page's "Ghi khiếu nại" action.
 *
 * Module state, in memory only — invariant 3 of the UX refactor spec permits nothing else. The list
 * reads it once on render, clears it, and opens the sheet with that order already chosen.
 */
let orderPrefill = "";

/**
 * Stage an order UUID for this screen's next render.
 *
 * @param {string} orderId
 */
export function setIncidentOrderPrefill(orderId) {
  orderPrefill = String(orderId || "");
}

/**
 * Never blank, never an empty state. `false` does not mean "no fault"; it means nobody has decided.
 *
 * @param {boolean|null|undefined} decided
 * @returns {string}
 */
function decisionLabel(decided) {
  return decided === true ? "đã quyết định" : "chưa quyết định";
}

/** @param {string} status */
function statusState(status) {
  return status === "OPEN" ? "warn" : status === "UNDER_REVIEW" ? "info" : status === "CLOSED" ? "ok" : "neutral";
}

/**
 * @param {any} item an `IncidentSummaryResponse`
 * @returns {string}
 */
function ticketTitle(item) {
  return Number.isInteger(item?.ticket_number) ? `Phiếu ${item.ticket_number}` : "Khiếu nại";
}

/**
 * The standing statement that recording a complaint adjudicates nothing: tier 2, one tap away from
 * every place a complaint is recorded or read.
 *
 * @returns {HTMLElement}
 */
function recordOnlyInfo() {
  return infoButton(
    "Ghi khiếu nại quyết định những gì?",
    h(
      "p",
      { class: "hint" },
      "Mở sự cố chỉ ghi lại rằng có chuyện xảy ra. Máy chủ luôn trả về fault_decided = false và " +
        "remedy_decided = false cho sự cố vừa mở, và màn hình này không đặt được giá trị nào " +
        "khác. Ai chịu lỗi vẫn không có chỗ nào ghi; khách được bù gì thì có, nhưng là một lệnh " +
        "riêng do người quyết.",
    ),
    h(
      "p",
      { class: "hint" },
      "Sự cố mở ở đây luôn là SERVICE_QUALITY do nhân viên ghi, luôn ở trạng thái OPEN, và luôn " +
        "chưa quyết định lỗi lẫn bồi hoàn. Không có route nào trong API này thay đổi ba điều đó.",
    ),
    h(
      "p",
      { class: "hint" },
      "Máy chủ tự đặt loại sự cố và người ghi. Yêu cầu không có trường loại sự cố, nên không có ô " +
        "chọn loại. Loại lỗi tin nhắn tự động chỉ do đường agent ghi, và vẫn hiện trong danh sách.",
    ),
    h(
      "p",
      { class: "hint" },
      "Bồi hoàn là một lệnh riêng, làm ngay trên trang của khiếu nại. Ở đó máy chủ hiện trần, " +
        "thời hạn và việc có cần chủ tiệm duyệt hay không trước khi bạn gõ số. Khiếu nại chỉ đóng " +
        "khi mọi đề nghị trên nó đã có kết cục.",
    ),
  );
}

/**
 * What the customer said, or the honest statement that it is no longer held.
 *
 * `evidence_summary` is null in two normal cases and no abnormal one: the agent path never stores
 * one, and a staff summary is purged at 365 days under `INCIDENT_EVIDENCE` while the incident row
 * and its digest survive. That is the retention schedule working, and the row says so.
 *
 * @param {string|null|undefined} summary
 * @returns {string}
 */
function summaryLine(summary) {
  const text = typeof summary === "string" ? summary.trim() : "";
  return text || "Không còn lời khách — xoá theo lịch giữ dữ liệu, không phải mất dữ liệu.";
}

/**
 * One complaint on the list.
 *
 * @param {any} item
 * @returns {HTMLElement}
 */
function incidentRow(item) {
  const hasSummary = typeof item.evidence_summary === "string" && item.evidence_summary.trim();
  return listRow({
    href: `#/incidents/${encodeURIComponent(String(item.incident_id || ""))}`,
    leading: "incident",
    title: ticketTitle(item),
    // Untrusted text: `listRow` places it as a text node, one line, clipped by CSS.
    meta: h("span", { class: ["row-item__clip", !hasSummary && "muted"] }, summaryLine(item.evidence_summary)),
    trailing: statusPill({ state: statusState(item.status), text: enumVi(item.status), token: item.status }),
    trailingMeta: ago(item.opened_at),
    data: { incidentId: String(item.incident_id || ""), incidentStatus: String(item.status || "") },
  });
}

/**
 * "＋ Ghi khiếu nại" — choose the order by its ticket, type what the customer said, record it.
 *
 * @param {object} spec
 * @param {string} spec.store
 * @param {{allowed: boolean, reason: string}} spec.verdict
 * @returns {{node: HTMLElement, open: (orderId?: string) => void}}
 */
function createSheet(spec) {
  const { store } = spec;
  const submission = new Submission("incident-open");
  /** @type {{manualOrderId: string, evidenceSummary: string, ticket: string, ticketDate: string}} */
  const draft = { manualOrderId: "", evidenceSummary: "", ticket: "", ticketDate: "" };
  /** @type {{orderId: string, title: string, meta: string}|null} the order the complaint is about */
  let chosen = null;
  /**
   * The exact payload of the last successful commit. The key is reset after a commit, so an
   * unchanged resubmission would record a second incident under a fresh key; it is refused here
   * with an instruction instead. Nothing is retried automatically; this only declines to send.
   */
  let lastCommitted = "";

  const result = resultLine();
  const errorHost = h("div");
  const pickedHost = h("div");
  const searchHost = h("div", { class: "stack stack--tight" });

  const ticket = searchField({
    id: "incident-ticket",
    label: "Số phiếu",
    placeholder: "Số phiếu của khách, vd 17",
    inputmode: "numeric",
    onInput: (value) => {
      draft.ticket = value.trim();
    },
    onSubmit: () => void search(),
  });
  const ticketDate = h("input", {
    type: "date",
    id: "incident-ticket-date",
    onInput: (event) => {
      draft.ticketDate = event.target.value;
    },
  });

  /** @param {any} order an `OrderViewResponse` */
  function describe(order) {
    const number = Number.isInteger(order.ticket_number) ? `Phiếu ${order.ticket_number}` : "Đơn của khách qua kênh";
    const day = order.ticket_issued_on ? dateOnly(`${order.ticket_issued_on}T12:00:00+07:00`) : dateOnly(order.created_at);
    return {
      orderId: String(order.order_id),
      title: `${number} · ${day}`,
      meta: `${enumVi(order.commercial)} · ${money(order.payable_total_vnd)}`,
    };
  }

  function choose(value) {
    chosen = value;
    submission.reset();
    renderPicked();
  }

  function renderPicked() {
    if (!chosen) {
      render(pickedHost);
      searchHost.hidden = false;
      return;
    }
    searchHost.hidden = true;
    render(
      pickedHost,
      h(
        "div",
        { class: "picked", dataOrderId: chosen.orderId },
        h(
          "div",
          { class: "picked__main" },
          h("strong", null, chosen.title),
          h("span", { class: "row-item__meta" }, chosen.meta),
        ),
        button({
          label: "Đổi",
          variant: "quiet",
          onClick: () => {
            chosen = null;
            submission.reset();
            renderPicked();
            ticket.input.focus();
          },
        }),
      ),
    );
  }

  async function search() {
    const number = draft.ticket.replace(/\D/g, "");
    if (!number) {
      show(searchResults, inlineAlert({ state: "warn", title: "Gõ số phiếu trên giấy của khách." }));
      return;
    }
    render(searchResults, skeletonRows(1));
    const date = draft.ticketDate ? `&ticket_date=${encodeURIComponent(draft.ticketDate)}` : "";
    try {
      const found = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?ticket=${encodeURIComponent(number)}${date}`,
      );
      const orders = Array.isArray(found) ? found : [];
      if (orders.length === 1) {
        render(searchResults);
        choose(describe(orders[0]));
        summaryInput.focus();
        return;
      }
      render(
        searchResults,
        orders.length
          ? list(
              orders.map((order) => {
                const described = describe(order);
                return listRow({
                  onClick: () => {
                    render(searchResults);
                    choose(described);
                  },
                  title: described.title,
                  meta: described.meta,
                  data: { orderId: described.orderId },
                });
              }),
              { label: "Đơn khớp số phiếu" },
            )
          : inlineAlert({
              state: "info",
              title: `Không có đơn nào mang phiếu ${number} ${draft.ticketDate ? "ngày đó" : "hôm nay"}.`,
              body: h("p", null, "Số phiếu đánh lại mỗi ngày: chọn đúng ngày trên phiếu rồi tìm lại."),
            }),
      );
    } catch (error) {
      show(searchResults, errorNotice(error, { onRetry: () => void search() }));
    }
  }

  const searchResults = h("div");
  render(
    searchHost,
    h(
      "div",
      { class: "ticket-search" },
      ticket.node,
      button({ label: "Tìm", onClick: () => void search(), id: "incident-ticket-find" }),
    ),
    labelled({
      id: "incident-ticket-date",
      label: "Ngày trên phiếu",
      hint: "Bỏ trống là hôm nay. Số phiếu đánh lại mỗi ngày.",
      control: ticketDate,
    }),
    searchResults,
    h(
      "details",
      { class: "manual" },
      h("summary", null, "Nhập mã thủ công"),
      labelled({
        id: "incident-order",
        label: "Mã đơn (order_id)",
        hint: "Chỉ khi không tìm được theo số phiếu. Đơn phải thuộc cửa hàng đang chọn.",
        control: boundInput({
          target: draft,
          key: "manualOrderId",
          pattern: UUID,
          placeholder: "00000000-0000-0000-0000-000000000000",
          submission,
        }),
      }),
    ),
  );

  // Not `boundInput`: a complaint is prose. The same binding is kept — the draft updates on every
  // keystroke and the idempotency key is retired, so an edited body is never sent under the key of
  // the body before it. Stored raw, trimmed at submit.
  const summaryInput = h("textarea", {
    rows: "4",
    maxlength: String(SUMMARY_MAX),
    autocomplete: "off",
    placeholder: "Khách báo áo sơ mi trắng bị ố vàng ở cổ, nhận đồ sáng nay.",
    onInput: (event) => {
      draft.evidenceSummary = event.target.value;
      submission.reset();
      event.target.setAttribute(
        "aria-invalid",
        event.target.value && !event.target.value.trim() ? "true" : "false",
      );
    },
  });

  /** @returns {string} the order the complaint names, or "" */
  function orderId() {
    if (chosen) return chosen.orderId;
    return UUID.test(draft.manualOrderId) ? draft.manualOrderId : "";
  }

  /** @returns {string} empty when the draft may be sent */
  function validate() {
    if (!chosen && draft.manualOrderId && !UUID.test(draft.manualOrderId)) {
      return "Mã đơn nhập tay phải là UUID đủ 36 ký tự.";
    }
    if (!orderId()) return "Chưa chọn đơn. Tìm theo số phiếu trên giấy của khách.";
    if (!draft.evidenceSummary.trim()) {
      return "Chưa ghi khách phàn nàn chuyện gì. Viết ít nhất một câu, bằng lời của khách.";
    }
    if (draft.evidenceSummary.trim().length > SUMMARY_MAX) {
      return `Nội dung khách phàn nàn dài quá ${SUMMARY_MAX} ký tự. Rút gọn lại còn ý chính.`;
    }
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
    // Exactly the two keys `IncidentOpenRequest` declares. It is a `StrictRequest`, so a third key
    // is a 422 rather than a field quietly ignored.
    const payload = { order_id: orderId(), evidence_summary: draft.evidenceSummary.trim() };
    const signature = JSON.stringify(payload);
    if (signature === lastCommitted) {
      setResult(
        result,
        "warn",
        "Nội dung này vừa được ghi thành công. Gửi lại y nguyên sẽ tạo thêm một sự cố thứ hai, " +
          "nên máy không gửi. Sửa dữ liệu nếu đây là sự cố khác, hoặc xem danh sách bên dưới.",
      );
      return;
    }
    setResult(result, "warn", "Đang ghi khiếu nại…");
    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/incidents`, {
        method: "POST",
        body: payload,
        idempotencyKey: submission.key(),
      });
      submission.reset();
      lastCommitted = signature;
      setResult(result, null, null);
      toast(
        created.replayed
          ? "Khiếu nại này đã được ghi trước đó — không tạo khiếu nại mới"
          : `Đã ghi khiếu nại${chosen ? ` · ${chosen.title.split(" · ")[0]}` : ""}`,
      );
      dialog.close();
      navigate(`/incidents/${encodeURIComponent(String(created.incident_id))}`);
    } catch (error) {
      // A refusal is the system working. A binding refusal, a denial and a REQUIRE_HUMAN are
      // outcomes with a cause the operator can act on; only the rest are presented as breakage.
      const api = /** @type {any} */ (error);
      const note = REFUSAL_NOTE[api?.detail] || REFUSAL_NOTE[api?.message];
      const refused = Boolean(note) || api.kind === "DENIED" || api.kind === "REQUIRE_HUMAN";
      setResult(
        result,
        refused ? "warn" : "danger",
        api.kind === "DENIED"
          ? "Máy chủ từ chối thao tác này cho phiên hiện tại. Không có sự cố nào được ghi."
          : refused
            ? "Máy chủ từ chối ràng buộc của sự cố. Không có sự cố nào được ghi."
            : "Không ghi được khiếu nại.",
      );
      show(
        errorHost,
        h(
          "div",
          { class: "stack stack--tight" },
          errorNotice(error),
          note
            ? inlineAlert({
                state: "info",
                title: "Máy chủ từ chối vì ràng buộc, không phải vì lỗi hệ thống",
                body: h("p", null, note),
              })
            : null,
        ),
      );
    }
  }

  const form = gatedFields(
    h(
      "form",
      { class: "form", onSubmit: submit, id: "incident-create-form" },
      h(
        "p",
        { class: "fact-line" },
        h("span", { class: "hint" }, "Chỉ ghi lời khách — chưa quyết ai lỗi, chưa bồi hoàn gì."),
        recordOnlyInfo(),
      ),
      h("div", { class: "stack stack--tight" }, h("p", { class: "remedy-label" }, "Đơn nào?"), pickedHost, searchHost),
      labelled({
        id: "incident-summary",
        label: "Khách phàn nàn chuyện gì",
        hint: "Theo lời khách: món nào, hỏng hay thiếu thế nào. Không cần tên hay số điện thoại.",
        control: summaryInput,
      }),
      gated(
        button({
          label: "Ghi khiếu nại",
          type: "submit",
          variant: "primary",
          block: true,
          network: true,
          id: "incident-submit",
        }),
        spec.verdict,
      ),
      result,
      errorHost,
    ),
    spec.verdict,
  );

  const dialog = sheet({ title: "Ghi khiếu nại", body: form, id: "incident-create" });

  /** @param {string} [prefillOrderId] */
  async function open(prefillOrderId) {
    render(errorHost);
    setResult(result, null, null);
    dialog.open();
    if (!prefillOrderId || !UUID.test(prefillOrderId)) {
      if (!chosen) ticket.input.focus();
      return;
    }
    // The order page handed its order over: show it by its ticket, read from the server.
    choose({ orderId: prefillOrderId, title: "Đơn đang mở", meta: "Đang đọc đơn…" });
    try {
      const order = await request(`/internal/v1/orders/${encodeURIComponent(prefillOrderId)}`);
      if (chosen && chosen.orderId === prefillOrderId) choose(describe(order));
    } catch {
      if (chosen && chosen.orderId === prefillOrderId) {
        choose({ orderId: prefillOrderId, title: `Đơn ${shortId(prefillOrderId)}`, meta: "Chưa đọc được đơn" });
      }
    }
    summaryInput.focus();
  }

  return { node: dialog.node, open: (id) => void open(id) };
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const writeVerdict = can(principal(), "INCIDENTS_WRITE");

  const staged = orderPrefill;
  orderPrefill = "";

  const create = createSheet({ store, verdict: writeVerdict });

  /**
   * The recorded complaints. The filter narrows the rows already fetched and nothing else: the
   * ticket number, the ids, the status and the customer's own words, because "cái áo sơ mi trắng"
   * is how a customer refers to their complaint. While it is active both counts stay on screen.
   */
  const view = listView({
    limit: LIST_LIMIT,
    fetch: () =>
      request(`/internal/v1/stores/${encodeURIComponent(store)}/incidents?limit=${LIST_LIMIT}`),
    renderItem: incidentRow,
    renderRows: (nodes) => list(nodes, { label: "Khiếu nại của cửa hàng", id: "incident-list" }),
    renderEmpty: (text) => emptyState({ icon: "incident", title: text }),
    skeleton: () => skeletonRows(4),
    emptyText: "Chưa có khiếu nại nào trong cửa hàng này.",
    filter: {
      placeholder: "Lọc theo số phiếu, lời khách, trạng thái…",
      noun: "khiếu nại",
      filteredEmptyText: "Không có khiếu nại nào khớp bộ lọc.",
      matches: (item, needle) =>
        matchesFilter(
          [
            Number.isInteger(item.ticket_number) ? `phiếu ${item.ticket_number}` : "",
            item.incident_id,
            item.order_id,
            item.status,
            enumVi(item.status),
            item.evidence_summary,
          ],
          needle,
        ),
    },
  });
  void view.reload();

  const addButton = button({
    label: "＋ Ghi khiếu nại",
    variant: "primary",
    id: "incident-create-open",
    onClick: () => create.open(),
  });

  const node = h(
    "section",
    { class: "screen" },
    page({
      title: "Khiếu nại",
      action: gated(addButton, writeVerdict),
      info: recordOnlyInfo(),
    }),
    view.bar.node,
    view.filterStatus,
    view.truncation,
    view.host,
    create.node,
  );
  // After the router has put the screen in the document, so the sheet opens over it.
  if (staged) setTimeout(() => create.open(staged), 0);
  return node;
}

/**
 * `#/incidents/:incidentId` — one complaint and its remedy flow.
 *
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
export function renderDetail(context) {
  const store = storeId();
  const incidentId = String(context?.params?.incidentId || "");
  const back = { href: "#/incidents", label: "Khiếu nại" };

  if (!UUID.test(incidentId)) {
    return h(
      "section",
      { class: "screen" },
      page({ title: "Khiếu nại", back }),
      inlineAlert({
        state: "warn",
        title: "Địa chỉ này không chỉ tới khiếu nại nào",
        body: h("p", null, "Mở khiếu nại từ danh sách."),
      }),
    );
  }

  const headHost = h("div", null, page({ title: "Khiếu nại", back }));
  const cardHost = h("div", null, skeletonRows(3));
  let generation = 0;

  async function load() {
    generation += 1;
    const mine = generation;
    try {
      const item = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/incidents/${encodeURIComponent(incidentId)}`,
      );
      if (mine !== generation) return;
      render(
        headHost,
        page({
          title: ticketTitle(item),
          back,
          subtitle: `Ghi ${ago(item.opened_at)} · ${dateTime(item.opened_at)}`,
          action: statusPill({ state: statusState(item.status), text: enumVi(item.status), token: item.status }),
        }),
      );
      render(cardHost, complaintCard(item));
      flow.setStatus(String(item.status || ""));
    } catch (error) {
      if (mine !== generation) return;
      const api = /** @type {any} */ (error);
      show(
        cardHost,
        api?.status === 404
          ? inlineAlert({
              state: "warn",
              title: "Không tìm thấy khiếu nại này trong cửa hàng đang chọn",
              body: h("p", null, "Kiểm tra bạn đang đứng đúng cửa hàng, hoặc mở lại từ danh sách."),
            })
          : errorNotice(error, { onRetry: () => void load() }),
      );
    }
  }

  const flow = remedyFlow({ store, incidentId, onChanged: () => void load() });
  void load();

  return h("section", { class: "screen" }, headHost, cardHost, flow.node);
}

/**
 * What the customer said, the order it is about, and the two undecided flags.
 *
 * @param {any} item an `IncidentSummaryResponse`
 * @returns {HTMLElement}
 */
function complaintCard(item) {
  const text = typeof item.evidence_summary === "string" ? item.evidence_summary.trim() : "";
  const orderLink = item.order_id
    ? h(
        "a",
        { href: `#/orders/${encodeURIComponent(String(item.order_id))}`, dataField: "incident-order-link" },
        Number.isInteger(item.ticket_number) ? `Phiếu ${item.ticket_number}` : "Mở đơn",
      )
    : UNKNOWN;
  return section({
    title: "Khách phàn nàn",
    info: recordOnlyInfo(),
    children: h(
      "div",
      { class: "stack" },
      text
        ? h("blockquote", { class: "complaint", dataField: "incident-summary" }, text)
        : h(
            "p",
            { class: "complaint complaint--absent", dataField: "incident-summary" },
            h(
              "span",
              { class: "hint" },
              "Không còn giữ lời khách phàn nàn. Sự cố do đường agent ghi vốn không kèm mô tả, còn mô tả " +
                "do nhân viên ghi bị xoá sau 365 ngày theo lịch giữ dữ liệu. Bản ghi sự cố thì vẫn " +
                "nguyên: đây là chuyện bình thường, không phải mất dữ liệu.",
            ),
          ),
      keyValues([
        ["Đơn", orderLink],
        ["Lỗi thuộc về ai", decisionLabel(item.fault_decided)],
        ["Bồi hoàn cho khách", decisionLabel(item.remedy_decided)],
      ]),
      techDetails([
        ["Mã khiếu nại", shortId(item.incident_id), { copy: String(item.incident_id || "") }],
        item.order_id ? ["Mã đơn", shortId(item.order_id), { copy: String(item.order_id) }] : null,
        ["Trạng thái", enumLabel(item.status)],
        ["Loại", enumLabel(item.category)],
        ["fault_decided", String(item.fault_decided)],
        ["remedy_decided", String(item.remedy_decided)],
        ["Ghi lúc", dateTime(item.opened_at), { mono: false }],
      ]),
    ),
  });
}

export const screen = {
  path: "/incidents",
  title: "Khiếu nại",
  capability: "INCIDENTS_READ",
  needsStore: true,
  render: render_,
};

export const detailScreen = {
  path: "/incidents/:incidentId",
  title: "Khiếu nại",
  capability: "INCIDENTS_READ",
  needsStore: true,
  render: renderDetail,
};
