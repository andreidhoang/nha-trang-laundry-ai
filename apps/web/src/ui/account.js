/**
 * Công nợ on a business customer's page (`PAYMENT-002`, `DEC-035`, the B2B half).
 *
 *   - **Mở công nợ** (owner): the limit the owner types, or none yet -- then nothing leaves on the
 *     account until one is typed (`ACCOUNT_LIMIT_UNSET`). The recommended 3.000.000 ₫ is shown as a
 *     hint beside the field and never filled in.
 *   - **The account card**: *Đang nợ* large, the limit and what can still be charged, this month's
 *     statement (đầu kỳ, phát sinh, đã trả, cuối kỳ, hạn trả), the overdue banner, the orders on the
 *     account oldest first, and the last payments. Every figure is the server's.
 *   - **Thu công nợ** (the counter): the outstanding prefilled, "Khách trả một phần" for less, cash
 *     or transfer as on an order (`PAYMENT-001`); the server allocates oldest first and the sheet
 *     says which orders it reached.
 *   - **The owner's controls**: change the limit, stop or restart the account, lift an overdue
 *     block through a day with a reason.
 *
 * What this never does: compute money (every figure is a server integer through `money()`), decide
 * whether an order may leave (the server's `handover_refusal`), or retry a write.
 *
 * @module ui/account
 */

import { Submission, request } from "../core/api.js";
import {
  ACCOUNT_STATUS_VI,
  accountRefusalText,
  currentMonth,
  monthName,
  statementPath,
} from "../core/accounts.js";
import { h, render } from "../core/dom.js";
import { addDays, businessDate, calendarDay, dateOnly, dateTime, money, parseDong } from "../core/format.js";
import { PAYMENT_METHOD_VI } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { errorNotice, gated } from "./components.js";
import {
  button,
  confirmButton,
  infoButton,
  inlineAlert,
  keyValues,
  list,
  listRow,
  moneyHero,
  moneyInput,
  section,
  segmented,
  sheet,
  show,
  skeletonRows,
  statusPill,
  toast,
} from "./kit.js";

/** `DEC-035`'s terms, behind ⓘ on the card (tier 2). */
const TERMS_NOTE =
  "Chỉ khách doanh nghiệp được chủ tiệm mở công nợ, với hạn mức chủ tiệm tự đặt. Khách nhận đồ " +
  "chưa trả tiền khi số đang nợ cộng đơn đó không vượt hạn mức. Sao kê theo tháng; hạn trả là " +
  "ngày 15 tháng sau. Có kỳ quá hạn thì đơn mới trả tại quầy, trừ khi chủ tiệm tạm mở chặn — mỗi " +
  "lần mở đều được ghi lại. Tiền khách trả được trừ vào đơn nợ cũ nhất trước (DEC-035).";

/**
 * @param {string|null|undefined} day `YYYY-MM-DD`
 * @returns {string}
 */
function dayText(day) {
  return calendarDay(day, { weekday: false }) + (day ? `/${String(day).slice(0, 4)}` : "");
}

/**
 * The account section of a customer's page.
 *
 * @param {object} spec
 * @param {string} spec.store
 * @param {string} spec.customerId
 * @param {any} spec.customer the profile the page read
 * @param {any} spec.who the signed-in principal
 * @returns {HTMLElement}
 */
export function accountSection(spec) {
  const { store, customerId, customer, who } = spec;
  const ownerVerdict = can(who, "ACCOUNTS_OWNER");
  const collectVerdict = can(who, "ACCOUNTS_COLLECT");
  const storeKey = encodeURIComponent(store);
  const customerKey = encodeURIComponent(customerId);
  const base = `/internal/v1/stores/${storeKey}/customers/${customerKey}/account`;
  const host = h("div", { id: "customer-account", class: "stack stack--tight" }, skeletonRows(2));
  const alertHost = h("div");
  const sheetsHost = h("div");
  const node = h("div", { class: "account" }, host, alertHost, sheetsHost);

  /** @type {any|null} */
  let read = null;

  async function load() {
    try {
      read = await request(base);
      draw();
    } catch (error) {
      render(host, errorNotice(error, { onRetry: () => void load() }));
    }
  }

  function draw() {
    if (!read) return;
    const account = read.account;
    if (!account) {
      render(host, openSection());
      return;
    }
    render(host, accountCard(account));
  }

  // --- no account yet -------------------------------------------------------------------------

  function openSection() {
    if (customer.erased_at) return null;
    const unpublished = read.open_refusal === "ACCOUNT_TERMS_UNPUBLISHED";
    const open = gated(
      button({
        label: "Mở công nợ",
        icon: "plus",
        id: "account-open",
        disabled: Boolean(read.open_refusal),
        onClick: () => openAccountSheet(),
      }),
      ownerVerdict,
    );
    return section({
      title: "Công nợ",
      id: "customer-account-section",
      info: infoButton("Công nợ là gì?", h("p", { class: "hint" }, TERMS_NOTE)),
      children: h(
        "div",
        { class: "stack stack--tight" },
        unpublished
          ? inlineAlert({
              state: "warn",
              title: "Chủ tiệm cần công bố điều khoản công nợ trước khi mở công nợ.",
            })
          : h("p", { class: "muted" }, "Khách này chưa có công nợ: mọi đơn trả tại quầy."),
        open,
      ),
    });
  }

  function openAccountSheet() {
    const submission = new Submission("account-open");
    let typed = "";
    const sheetAlert = h("div");
    const field = moneyInput({
      id: "account-limit",
      label: "Hạn mức công nợ",
      placeholder: "Để trống nếu chưa đặt",
      echo: (text) => {
        const parsed = parseDong(text);
        return text.trim() && parsed !== null ? `= ${money(parsed)}` : "";
      },
      onInput: (text) => {
        typed = text;
        submission.reset();
      },
    });
    const save = button({
      label: "Mở công nợ",
      variant: "primary",
      block: true,
      network: true,
      id: "account-open-save",
      onClick: () => void send(),
    });
    async function send() {
      const limit = typed.trim() ? parseDong(typed) : null;
      if (typed.trim() && (limit === null || limit < 1)) {
        show(sheetAlert, inlineAlert({ state: "danger", title: "Gõ hạn mức là số nguyên đồng, ví dụ 3.000.000." }));
        return;
      }
      save.disabled = true;
      try {
        read = await request(base, {
          method: "POST",
          body: { credit_limit_vnd: limit },
          idempotencyKey: submission.key(),
        });
        made.close();
        toast("Đã mở công nợ");
        draw();
      } catch (error) {
        save.disabled = false;
        show(sheetAlert, errorNotice(error, { title: accountRefusalText(error) || undefined }));
      }
    }
    const made = sheet({
      id: "account-open-sheet",
      title: "Mở công nợ",
      body: h(
        "div",
        { class: "stack" },
        h("label", { for: "account-limit", class: "field-label" }, "Hạn mức (chủ tiệm đặt)"),
        field.node,
        h(
          "p",
          { class: "hint", id: "account-limit-hint" },
          `Gợi ý: ${money(read?.recommended_limit_vnd)} — chủ tiệm tự quyết. Để trống thì chưa ghi nợ được.`,
        ),
        sheetAlert,
      ),
      actions: save,
      onClose: () => made.node.remove(),
    });
    render(sheetsHost, made.node);
    made.open();
  }

  // --- the account card -----------------------------------------------------------------------

  /** @param {any} account */
  function accountCard(account) {
    const statement = account.current_statement || {};
    const stopped = account.status === "SUSPENDED";
    const limitUnset = account.credit_limit_vnd === null || account.credit_limit_vnd === undefined;
    const banner = account.overdue_vnd > 0
      ? account.block_lifted
        ? inlineAlert({
            state: "warn",
            title: `Quá hạn ${money(account.overdue_vnd)} — chủ tiệm tạm mở chặn tới hết ${dateOnly(
              previousInstant(account.overdue_block_lifted_until),
            )}.`,
          })
        : inlineAlert({
            state: "danger",
            title: `Quá hạn ${money(account.overdue_vnd)} (${monthName(account.overdue_month)}, hạn ${dayText(
              account.overdue_due_on,
            )}). Đơn mới trả tại quầy.`,
          })
      : null;
    const hero = moneyHero({
      label: "Đang nợ",
      amount: money(account.outstanding_vnd),
      caption: limitUnset
        ? "Chưa có hạn mức — chưa ghi nợ được."
        : `Hạn mức ${money(account.credit_limit_vnd)} · còn ghi được ${money(account.available_vnd)}`,
      state: account.overdue_vnd > 0 && !account.block_lifted ? "danger" : null,
    });
    const figures = h(
      "div",
      { class: "account__statement", dataStatement: String(statement.month || "") },
      h("p", { class: "field-label" }, `Kỳ này · ${monthName(statement.month)}`),
      keyValues([
        ["Đầu kỳ", money(statement.opening_vnd)],
        ["Phát sinh", `${money(statement.charges_vnd)} · ${statement.charge_count ?? 0} đơn`],
        ["Đã trả", money(statement.payments_vnd)],
        ["Cuối kỳ", money(statement.closing_vnd)],
        ["Hạn trả", dayText(statement.due_on)],
      ]),
    );
    const collect = gated(
      button({
        label: "Thu công nợ",
        icon: "cash",
        variant: "primary",
        block: true,
        id: "account-collect",
        disabled: !(account.outstanding_vnd > 0),
        onClick: () => openCollectSheet(account),
      }),
      collectVerdict,
    );
    const statementLink = h(
      "a",
      {
        class: ["button", "btn"],
        href: statementPath(customerId, currentMonth()),
        id: "account-statement",
      },
      "Xem sao kê",
    );
    const ownerControls = ownerVerdict.allowed
      ? h(
          "div",
          { class: "account__owner" },
          button({
            label: limitUnset ? "Đặt hạn mức" : "Sửa hạn mức",
            variant: limitUnset ? "primary" : "quiet",
            id: "account-limit-edit",
            onClick: () => openLimitSheet(account),
          }),
          account.overdue_vnd > 0
            ? button({
                label: "Tạm mở chặn",
                variant: "quiet",
                id: "account-lift",
                onClick: () => openLiftSheet(account),
              })
            : null,
          statusControl(account),
        )
      : null;
    return section({
      title: "Công nợ",
      id: "customer-account-section",
      action: statusPill({
        state: stopped ? "warn" : "ok",
        text: ACCOUNT_STATUS_VI[account.status] || account.status,
        token: account.status,
      }),
      info: infoButton("Công nợ tính thế nào?", h("p", { class: "hint" }, TERMS_NOTE)),
      children: h(
        "div",
        { class: "stack stack--tight account__card", dataAccount: String(account.account_id) },
        banner,
        stopped
          ? inlineAlert({ state: "warn", title: "Công nợ đang ngưng: đơn mới trả tại quầy." })
          : null,
        hero,
        figures,
        h("div", { class: "account__actions" }, collect, statementLink),
        ownerControls,
        openCharges(account),
        recentPayments(account),
      ),
    });
  }

  /**
   * The lift's end is exclusive (the next local midnight); the owner chose the day before it.
   *
   * @param {string} value
   * @returns {string}
   */
  function previousInstant(value) {
    const parsed = new Date(String(value));
    return new Date(parsed.getTime() - 1000).toISOString();
  }

  /** @param {any} account */
  function openCharges(account) {
    const rows = account.open_charges || [];
    if (!rows.length) return null;
    return h(
      "div",
      { class: "stack stack--tight" },
      h("p", { class: "field-label" }, "Đơn đang nợ · cũ nhất trước"),
      list(
        rows.map((item) =>
          listRow({
            href: `#/orders/${encodeURIComponent(String(item.order_id))}`,
            leading: "order",
            title: Number.isInteger(item.ticket_number)
              ? `Phiếu ${item.ticket_number} · ${calendarDay(item.ticket_issued_on, { weekday: false })}`
              : `Đơn ${String(item.order_id).slice(0, 8).toUpperCase()}`,
            meta: `Ghi nợ ${dateOnly(item.charged_at)}`,
            trailing: money(item.remaining_vnd),
            data: { accountOrder: String(item.order_id) },
          }),
        ),
        { label: "Đơn đang nợ", id: "account-open-charges" },
      ),
      account.open_charges_truncated
        ? h("p", { class: "hint" }, `Chỉ hiện ${rows.length} đơn cũ nhất; số đang nợ vẫn tính đủ.`)
        : null,
    );
  }

  /** @param {any} account */
  function recentPayments(account) {
    const rows = account.recent_payments || [];
    if (!rows.length) return null;
    return h(
      "div",
      { class: "stack stack--tight" },
      h("p", { class: "field-label" }, "Lần trả gần đây"),
      list(
        rows.map((item) =>
          listRow({
            leading: "cash",
            title: PAYMENT_METHOD_VI[item.method] || String(item.method),
            meta: `${dateTime(item.recorded_at)} · ${item.order_count} đơn${
              item.bank_ref_last ? ` · …${item.bank_ref_last}` : ""
            }`,
            trailing: money(item.amount_vnd),
            chevron: false,
            data: { accountPayment: String(item.payment_id) },
          }),
        ),
        { label: "Lần trả gần đây", id: "account-payments" },
      ),
      account.recent_payments_truncated
        ? h("p", { class: "hint" }, `Chỉ hiện ${rows.length} lần gần nhất; xem đủ ở sao kê.`)
        : null,
    );
  }

  // --- Thu công nợ ----------------------------------------------------------------------------

  /** @param {any} account */
  function openCollectSheet(account) {
    const submission = new Submission("account-payment");
    let editing = false;
    let typed = "";
    let method = "TIEN_MAT";
    let seen = false;
    let reference = "";
    const sheetAlert = h("div");
    const amountHost = h("div", { class: "stack stack--tight" });
    const transferHost = h("div", { class: "stack stack--tight" });
    const field = moneyInput({
      id: "account-payment-amount",
      label: "Số tiền khách trả lần này",
      placeholder: "Ví dụ 500.000",
      echo: (text) => {
        const parsed = parseDong(text);
        return text.trim() && parsed !== null ? `= ${money(parsed)}` : "";
      },
      onInput: (text) => {
        typed = text;
        submission.reset();
      },
    });

    function drawAmount() {
      render(
        amountHost,
        editing
          ? h(
              "div",
              { class: "stack stack--tight" },
              h("label", { for: "account-payment-amount", class: "field-label" }, "Khách trả lần này"),
              field.node,
              button({
                label: `Trả đủ ${money(account.outstanding_vnd)}`,
                variant: "quiet",
                id: "account-payment-full",
                onClick: () => {
                  editing = false;
                  typed = "";
                  field.input.value = "";
                  submission.reset();
                  drawAmount();
                },
              }),
            )
          : button({
              label: "Khách trả một phần",
              variant: "quiet",
              id: "account-payment-edit",
              onClick: () => {
                editing = true;
                submission.reset();
                drawAmount();
                setTimeout(() => field.input.focus(), 30);
              },
            }),
      );
    }

    function drawTransfer() {
      if (method !== "CHUYEN_KHOAN") {
        render(transferHost);
        return;
      }
      render(
        transferHost,
        h(
          "label",
          { class: "check-line", for: "account-payment-seen" },
          h("input", {
            type: "checkbox",
            id: "account-payment-seen",
            checked: seen,
            onChange: (event) => {
              seen = /** @type {HTMLInputElement} */ (event.target).checked;
              submission.reset();
            },
          }),
          h("span", null, "Đã thấy tiền vào tài khoản"),
        ),
        h("input", {
          id: "account-payment-ref",
          type: "text",
          class: "input",
          autocomplete: "off",
          maxlength: "40",
          placeholder: "Mã giao dịch (tuỳ chọn)",
          "aria-label": "Mã giao dịch, vài số cuối, không bắt buộc",
          value: reference,
          onInput: (event) => {
            reference = /** @type {HTMLInputElement} */ (event.target).value;
            submission.reset();
          },
        }),
      );
    }

    const submit = button({
      label: "Ghi nhận đã thu",
      variant: "primary",
      block: true,
      network: true,
      id: "account-payment-submit",
      onClick: () => void send(),
    });

    async function send() {
      const amount = editing ? parseDong(typed) : account.outstanding_vnd;
      if (amount === null || amount === undefined) {
        show(
          sheetAlert,
          inlineAlert({ state: "danger", title: "Gõ số tiền khách trả, số nguyên đồng — ví dụ 500.000." }),
        );
        return;
      }
      const transfer = method === "CHUYEN_KHOAN";
      submit.disabled = true;
      render(sheetAlert);
      try {
        const paid = await request(`/internal/v1/stores/${storeKey}/customers/${customerKey}/account/payments`, {
          method: "POST",
          body: {
            amount_vnd: amount,
            method,
            transfer_seen: transfer && seen,
            bank_ref_last: transfer && reference.trim() ? reference.trim() : null,
          },
          ifMatch: account.row_version,
          idempotencyKey: submission.key(),
        });
        submission.reset();
        toast(`Đã thu công nợ ${money(paid.amount_vnd)}`);
        const names = new Map(
          (account.open_charges || []).map((item) => [
            String(item.order_id),
            Number.isInteger(item.ticket_number) ? `Phiếu ${item.ticket_number}` : "Đơn",
          ]),
        );
        const reached = (paid.allocations || []).map(
          (item) =>
            `${names.get(String(item.order_id)) || "Đơn"} ${money(item.amount_vnd)}${
              item.settled ? " (đủ)" : ""
            }`,
        );
        render(
          made.body,
          inlineAlert({
            state: "ok",
            title: `Đã ghi ${money(paid.amount_vnd)} · ${PAYMENT_METHOD_VI[paid.method] || paid.method}.`,
            body: h(
              "div",
              { class: "stack stack--tight" },
              h("p", { dataAllocations: String((paid.allocations || []).length) }, `Trừ vào: ${reached.join(", ")}.`),
              h("p", null, `Còn nợ ${money(paid.outstanding_after_vnd)}.`),
            ),
          }),
          button({ label: "Xong", block: true, id: "account-payment-done", onClick: () => made.close() }),
        );
        made.node.querySelector(".sheet__actions")?.remove();
        await load();
      } catch (error) {
        submit.disabled = false;
        show(sheetAlert, errorNotice(error, { title: accountRefusalText(error) || undefined }));
      }
    }

    const made = sheet({
      id: "account-payment-sheet",
      title: "Thu công nợ",
      body: h(
        "div",
        { class: "stack" },
        moneyHero({ label: "Đang nợ", amount: money(account.outstanding_vnd) }),
        amountHost,
        h("p", { class: "field-label" }, "Khách trả bằng"),
        segmented({
          label: "Khách trả bằng",
          id: "account-payment-method",
          options: [
            { value: "TIEN_MAT", label: PAYMENT_METHOD_VI.TIEN_MAT },
            { value: "CHUYEN_KHOAN", label: PAYMENT_METHOD_VI.CHUYEN_KHOAN },
          ],
          value: method,
          onChange: (value) => {
            method = value;
            submission.reset();
            drawTransfer();
          },
        }),
        transferHost,
        h("p", { class: "hint" }, "Tiền trừ vào đơn nợ cũ nhất trước. Ghi rồi không sửa được."),
        sheetAlert,
      ),
      actions: submit,
      onClose: () => made.node.remove(),
    });
    render(sheetsHost, made.node);
    drawAmount();
    drawTransfer();
    made.open();
  }

  // --- the owner's controls -------------------------------------------------------------------

  /** @param {any} account */
  function openLimitSheet(account) {
    const submission = new Submission("account-limit");
    let typed = "";
    const sheetAlert = h("div");
    const field = moneyInput({
      id: "account-limit-new",
      label: "Hạn mức mới",
      placeholder: "Ví dụ 3.000.000",
      echo: (text) => {
        const parsed = parseDong(text);
        return text.trim() && parsed !== null ? `= ${money(parsed)}` : "";
      },
      onInput: (text) => {
        typed = text;
        submission.reset();
      },
    });
    const save = button({
      label: "Lưu hạn mức",
      variant: "primary",
      block: true,
      network: true,
      id: "account-limit-save",
      onClick: () => void send(),
    });
    async function send() {
      const limit = parseDong(typed);
      if (limit === null || limit < 1) {
        show(sheetAlert, inlineAlert({ state: "danger", title: "Gõ hạn mức là số nguyên đồng, ví dụ 3.000.000." }));
        return;
      }
      save.disabled = true;
      try {
        read = await request(base, {
          method: "PATCH",
          body: { credit_limit_vnd: limit },
          ifMatch: account.row_version,
          idempotencyKey: submission.key(),
        });
        made.close();
        toast(`Hạn mức: ${money(limit)}`);
        draw();
      } catch (error) {
        save.disabled = false;
        show(sheetAlert, errorNotice(error, { title: accountRefusalText(error) || undefined }));
      }
    }
    const made = sheet({
      id: "account-limit-sheet",
      title: account.credit_limit_vnd ? "Sửa hạn mức" : "Đặt hạn mức",
      body: h(
        "div",
        { class: "stack" },
        account.credit_limit_vnd
          ? h("p", { class: "muted" }, `Đang là ${money(account.credit_limit_vnd)}.`)
          : null,
        h("label", { for: "account-limit-new", class: "field-label" }, "Hạn mức"),
        field.node,
        h("p", { class: "hint" }, `Gợi ý: ${money(read?.recommended_limit_vnd)} — chủ tiệm tự quyết.`),
        sheetAlert,
      ),
      actions: save,
      onClose: () => made.node.remove(),
    });
    render(sheetsHost, made.node);
    made.open();
  }

  /** @param {any} account */
  function openLiftSheet(account) {
    const submission = new Submission("account-lift");
    const today = businessDate();
    let until = addDays(today, 7);
    let reason = "";
    const sheetAlert = h("div");
    const save = button({
      label: "Mở chặn tới ngày này",
      variant: "primary",
      block: true,
      network: true,
      id: "account-lift-save",
      onClick: () => void send(),
    });
    async function send() {
      save.disabled = true;
      try {
        read = await request(`/internal/v1/stores/${storeKey}/customers/${customerKey}/account/block-lift`, {
          method: "POST",
          body: { reason: reason.trim(), until },
          ifMatch: account.row_version,
          idempotencyKey: submission.key(),
        });
        made.close();
        toast(`Đã tạm mở chặn tới hết ${dayText(until)}`);
        draw();
      } catch (error) {
        save.disabled = false;
        show(sheetAlert, errorNotice(error, { title: accountRefusalText(error) || undefined }));
      }
    }
    const made = sheet({
      id: "account-lift-sheet",
      title: "Tạm mở chặn công nợ",
      body: h(
        "div",
        { class: "stack" },
        h(
          "p",
          { class: "muted" },
          `Quá hạn ${money(account.overdue_vnd)}. Trong thời gian mở chặn, đơn mới được ghi nợ trong hạn mức.`,
        ),
        h("label", { for: "account-lift-until", class: "field-label" }, "Mở chặn tới hết ngày"),
        h("input", {
          id: "account-lift-until",
          type: "date",
          class: "input",
          value: until,
          min: today,
          max: addDays(today, 31),
          onChange: (event) => {
            until = /** @type {HTMLInputElement} */ (event.target).value;
            submission.reset();
          },
        }),
        h("label", { for: "account-lift-reason", class: "field-label" }, "Lý do"),
        h("textarea", {
          id: "account-lift-reason",
          maxlength: "200",
          placeholder: "Ví dụ: khách hẹn chuyển khoản ngày 20",
          onInput: (event) => {
            reason = /** @type {HTMLTextAreaElement} */ (event.target).value;
            submission.reset();
          },
        }),
        h("p", { class: "hint" }, "Mỗi lần mở chặn đều được ghi lại, kèm lý do."),
        sheetAlert,
      ),
      actions: save,
      onClose: () => made.node.remove(),
    });
    render(sheetsHost, made.node);
    made.open();
  }

  /** @param {any} account */
  function statusControl(account) {
    const stopping = account.status === "ACTIVE";
    const submission = new Submission("account-status");
    const control = confirmButton({
      label: stopping ? "Ngưng công nợ" : "Cho dùng lại công nợ",
      confirmLabel: stopping ? "Bấm lần nữa để ngưng" : "Bấm lần nữa để mở lại",
      variant: stopping ? "danger" : "secondary",
      onConfirm: async () => {
        render(alertHost);
        try {
          read = await request(base, {
            method: "PATCH",
            body: { status: stopping ? "SUSPENDED" : "ACTIVE" },
            ifMatch: account.row_version,
            idempotencyKey: submission.key(),
          });
          toast(stopping ? "Đã ngưng công nợ" : "Công nợ dùng lại được");
          draw();
        } catch (error) {
          show(alertHost, errorNotice(error, { title: accountRefusalText(error) || undefined }));
        }
      },
    });
    control.id = "account-status";
    return control;
  }

  void load();
  return node;
}
