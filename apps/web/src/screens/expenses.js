/**
 * Sổ thu chi: the shop's spending, month by month (`SHOP-CAPTURE-001`, `DEC-038`).
 *
 * How a Vietnamese shop owner already thinks about money, so it is how the console asks for it:
 * a date, a category (điện, nước, hoá chất, túi nhãn, lương, mặt bằng, sửa chữa, xăng xe, khác), an
 * amount and, if wanted, a short note. One month at a time.
 *
 * What this screen will not do, and why:
 *
 *   - **No money is added here.** The month's total and each category's total are PostgreSQL's
 *     sums, sent by the server; the lines are listed as recorded. A voided line is shown struck
 *     through and is not in any total -- the server left it out.
 *   - **Which categories margin still waits for is the server's answer** (`core_missing`, the
 *     domain's `DEC-038` list), shown as the one tier-1 line under the total. The screen holds no
 *     copy of the rule.
 *   - **A wrong line is voided, never edited.** The amount a person recorded stays readable; the
 *     correction is a void and a new line. Voiding takes two presses and the row version read.
 *   - **Writing is the owner's and the accountant's**; the auditor reads. A denied control stays
 *     visible with its reason.
 *
 * @module screens/expenses
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { businessDate, calendarDay, dateTime, integer, money, parseDong } from "../core/format.js";
import { EXPENSE_CATEGORY_VI } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated, markUpdated } from "../ui/components.js";
import {
  actionBar,
  button,
  confirmButton,
  emptyState,
  infoButton,
  inlineAlert,
  keyValues,
  list,
  listRow,
  moneyHero,
  moneyInput,
  page,
  section,
  segmented,
  sheet,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";

/** The categories in the order the owner reads a month's bills. */
const CATEGORIES = [
  "DIEN",
  "NUOC",
  "HOA_CHAT",
  "TUI_NHAN",
  "LUONG",
  "MAT_BANG",
  "SUA_CHUA",
  "XANG_XE",
  "KHAC",
];

/** Tier 2: what the book is for, and what it is not. */
const BOOK_RULE =
  "Ghi mọi khoản tiệm chi ra trong tháng: điện, nước, hoá chất, túi nhãn, lương, mặt bằng, sửa " +
  "chữa, xăng xe và khoản khác. Máy chủ cộng theo mục và theo tháng; màn hình này không tự cộng. " +
  "Báo cáo chỉ tính biên của một tháng khi tháng đó đã có đủ điện, nước, hoá chất, lương và mặt " +
  "bằng. Tiền thuê xe ngoài (Grab, Ahamove) cũng ghi ở đây, mục Xăng xe. Ghi sai thì huỷ dòng đó " +
  "rồi ghi dòng đúng; dòng đã huỷ vẫn hiện để đối chiếu nhưng không được cộng.";

/**
 * `YYYY-MM` of a shop-local calendar day, and the months around it.
 *
 * @param {string} day `YYYY-MM-DD`
 * @returns {string}
 */
function monthOf(day) {
  return day.slice(0, 7);
}

/**
 * @param {string} month `YYYY-MM`
 * @param {number} step -1 or +1
 * @returns {string}
 */
function shiftMonth(month, step) {
  const [year, number] = month.split("-").map((part) => Number.parseInt(part, 10));
  const index = year * 12 + (number - 1) + step;
  const shiftedYear = Math.floor(index / 12);
  const shiftedMonth = (index % 12) + 1;
  return `${shiftedYear}-${String(shiftedMonth).padStart(2, "0")}`;
}

/**
 * @param {string} month `YYYY-MM`
 * @returns {string} "Tháng 9/2026"
 */
export function monthLabel(month) {
  const [year, number] = month.split("-");
  return `Tháng ${Number.parseInt(number, 10)}/${year}`;
}

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  const me = principal();
  const writeVerdict = can(me, "EXPENSES_WRITE");
  const today = businessDate();
  const asked = String(context?.query?.get("month") || "");
  let month = /^\d{4}-\d{2}$/.test(asked) && asked <= monthOf(today) ? asked : monthOf(today);

  const subtitle = h("span", { class: "expenses__month" });
  const heroHost = h("div", null, skeletonRows(1));
  const tier1 = h("div");
  const categoriesHost = h("div", { class: "stack stack--tight" });
  const linesHost = h("div", { class: "stack stack--tight" }, skeletonRows(3));
  const truncation = h("div");
  const techHost = h("div");
  const stamp = h("span", { class: "updated", role: "status" });
  const sheetsHost = h("div", { class: "expenses__sheets" });
  const next = button({
    label: "Sau",
    icon: "chevron-right",
    variant: "quiet",
    data: { monthStep: "next" },
    onClick: () => void go(shiftMonth(month, 1)),
  });

  let generation = 0;

  /** @param {string} target */
  async function go(target) {
    month = target;
    next.disabled = month >= monthOf(businessDate());
    await load();
  }

  async function load() {
    const mine = ++generation;
    subtitle.textContent = monthLabel(month);
    render(heroHost, skeletonRows(1));
    render(tier1);
    render(categoriesHost);
    render(linesHost, skeletonRows(3));
    render(truncation);
    render(techHost);
    try {
      const found = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/expenses?month=${encodeURIComponent(month)}`,
      );
      if (mine !== generation) return;
      markUpdated(stamp);
      draw(found);
    } catch (error) {
      if (mine !== generation) return;
      render(heroHost);
      render(linesHost, errorNotice(error, { onRetry: () => void load() }));
    }
  }

  /** @param {any} found an `ExpenseMonthResponse` */
  function draw(found) {
    render(
      heroHost,
      moneyHero({
        label: "Đã chi trong tháng",
        amount: money(found.total_vnd),
        state: "neutral",
        caption: `${integer(found.entries)} dòng${
          found.voided_entries ? ` · ${integer(found.voided_entries)} dòng đã huỷ` : ""
        }`,
        info: infoButton("Sổ thu chi ghi những gì?", h("p", null, BOOK_RULE)),
      }),
    );
    const missing = Array.isArray(found.core_missing) ? found.core_missing : [];
    render(
      tier1,
      missing.length
        ? h(
            "p",
            { class: "hint expenses__missing", dataCoreMissing: missing.join(" ") },
            `Chưa đủ để tính biên: thiếu ${missing
              .map((code) => EXPENSE_CATEGORY_VI[code] || code)
              .join(", ")
              .toLowerCase()}.`,
          )
        : h(
            "p",
            { class: "hint expenses__complete", dataCoreMissing: "" },
            "Đã đủ các mục chính; báo cáo tính được biên của tháng này.",
          ),
    );
    const totals = Array.isArray(found.totals) ? found.totals : [];
    const recorded = totals.filter((total) => total.entries > 0);
    render(
      categoriesHost,
      recorded.length
        ? list(
            recorded.map((total) =>
              listRow({
                title: EXPENSE_CATEGORY_VI[total.category] || String(total.category),
                meta: `${integer(total.entries)} dòng`,
                trailing: h(
                  "span",
                  { class: "money", dataCategoryTotal: String(total.category) },
                  money(total.amount_vnd),
                ),
              }),
            ),
            { label: "Theo mục" },
          )
        : null,
    );
    const lines = Array.isArray(found.lines) ? found.lines : [];
    render(
      linesHost,
      lines.length
        ? list(lines.map(lineRow), { label: "Các dòng trong tháng" })
        : emptyState({
            icon: "cash",
            title: "Tháng này chưa ghi khoản chi nào",
            body: "Bấm “Ghi khoản chi” khi trả tiền điện, nước, lương hay mua hoá chất.",
          }),
    );
    render(
      truncation,
      found.truncated
        ? inlineAlert({
            state: "warn",
            title: "Chỉ hiện 200 dòng mới nhất của tháng",
            body: "Tổng tiền phía trên vẫn là của cả tháng.",
          })
        : null,
    );
    render(
      techHost,
      techDetails([
        ["Tháng", `${found.from_date} → ${found.to_date}`],
        ["Cửa hàng", String(found.store_id)],
      ]),
    );
  }

  /** @param {any} line an `ExpenseResponse` */
  function lineRow(line) {
    const voided = Boolean(line.voided_at);
    return listRow({
      title: EXPENSE_CATEGORY_VI[line.category] || String(line.category),
      meta: [calendarDay(line.spent_on, { weekday: false }), line.note].filter(Boolean).join(" · "),
      trailing: h(
        "span",
        { class: ["money", voided && "expenses__voided"] },
        h("span", { class: voided ? "expenses__struck" : null }, money(line.amount_vnd)),
        voided ? statusPill({ state: "neutral", text: "Đã huỷ", token: "VOIDED" }) : null,
      ),
      onClick: () => openLine(line),
      data: { expenseId: String(line.expense_id), expenseCategory: String(line.category) },
    });
  }

  /** @type {ReturnType<typeof sheet>|null} */
  let open = null;

  /** @param {{title: string, body: unknown, actions?: unknown, id?: string}} spec */
  function openFresh(spec) {
    open?.close();
    const made = sheet({ ...spec, onClose: () => made.node.remove() });
    render(sheetsHost, made.node);
    open = made;
    made.open();
    return made;
  }

  /** One line's detail, and its void for a writer. @param {any} line */
  function openLine(line) {
    const alertHost = h("div");
    const submission = new Submission("expense-void");
    const voidControl = confirmButton({
      label: "Huỷ dòng này",
      confirmLabel: "Bấm lần nữa để huỷ dòng",
      block: true,
      onConfirm: async () => {
        render(alertHost);
        try {
          const target = encodeURIComponent(String(line.expense_id));
          await request(
            `/internal/v1/stores/${encodeURIComponent(store)}/expenses/${target}/void`,
            { method: "POST", idempotencyKey: submission.key(), ifMatch: line.row_version },
          );
          submission.reset();
          toast("Đã huỷ dòng chi");
          made.close();
          void load();
        } catch (error) {
          render(alertHost, errorNotice(error));
        }
      },
    });
    voidControl.id = "expense-void";
    const made = openFresh({
      id: "expense-line",
      title: EXPENSE_CATEGORY_VI[line.category] || String(line.category),
      body: h(
        "div",
        { class: "stack" },
        keyValues([
          ["Số tiền", money(line.amount_vnd)],
          ["Ngày chi", calendarDay(line.spent_on)],
          ["Ghi chú", line.note || "—"],
          ["Người ghi", line.recorded_by_name || "—"],
          ["Ghi lúc", dateTime(line.recorded_at)],
          line.voided_at ? ["Đã huỷ lúc", dateTime(line.voided_at)] : null,
        ].filter(Boolean)),
        alertHost,
      ),
      actions: line.voided_at ? null : gated(voidControl, writeVerdict),
    });
  }

  /** Ghi khoản chi: date, category, amount, note. */
  function openAdd() {
    const alertHost = h("div");
    const submission = new Submission("expense-record");
    let category = "";
    const dateInput = /** @type {HTMLInputElement} */ (
      h("input", {
        id: "expense-date",
        type: "date",
        max: businessDate(),
        value: businessDate(),
        onInput: () => submission.reset(),
      })
    );
    const amount = moneyInput({
      id: "expense-amount",
      label: "Số tiền",
      echo: (text) => {
        if (!text.trim()) return "";
        const parsed = parseDong(text);
        return parsed === null ? "Chưa đọc được số tiền" : `= ${money(parsed)}`;
      },
      onInput: () => submission.reset(),
    });
    const note = /** @type {HTMLInputElement} */ (
      h("input", {
        id: "expense-note",
        type: "text",
        maxlength: "120",
        autocomplete: "off",
        placeholder: "Không bắt buộc",
        onInput: () => submission.reset(),
      })
    );
    const save = button({
      label: "Ghi vào sổ",
      variant: "primary",
      block: true,
      network: true,
      id: "expense-save",
      onClick: () => void send(),
    });

    async function send() {
      render(alertHost);
      const parsed = parseDong(amount.input.value);
      const problem = !category
        ? "Chọn mục chi."
        : parsed === null || parsed <= 0
          ? "Gõ số tiền, ví dụ 1250000."
          : !dateInput.value
            ? "Chọn ngày chi."
            : "";
      if (problem) {
        render(alertHost, inlineAlert({ state: "warn", title: "Chưa ghi được", body: problem }));
        return;
      }
      save.disabled = true;
      try {
        await request(`/internal/v1/stores/${encodeURIComponent(store)}/expenses`, {
          method: "POST",
          idempotencyKey: submission.key(),
          body: {
            spent_on: dateInput.value,
            category,
            amount_vnd: parsed,
            ...(note.value.trim() ? { note: note.value.trim() } : {}),
          },
        });
        submission.reset();
        toast(`Đã ghi ${EXPENSE_CATEGORY_VI[category]} · ${money(parsed)}`);
        made.close();
        void go(monthOf(dateInput.value));
      } catch (error) {
        render(alertHost, errorNotice(error));
      } finally {
        if (save.isConnected) save.disabled = false;
      }
    }

    const made = openFresh({
      id: "expense-add",
      title: "Ghi khoản chi",
      body: h(
        "div",
        { class: "stack" },
        h("p", { class: "field-label" }, "Mục chi"),
        segmented({
          label: "Mục chi",
          id: "expense-category",
          value: "",
          wrap: true,
          options: CATEGORIES.map((value) => ({ value, label: EXPENSE_CATEGORY_VI[value] })),
          onChange: (value) => {
            category = value;
            submission.reset();
          },
        }),
        h("label", { class: "field-label", for: "expense-amount" }, "Số tiền"),
        amount.node,
        h("label", { class: "field-label", for: "expense-date" }, "Ngày chi"),
        dateInput,
        h("label", { class: "field-label", for: "expense-note" }, "Ghi chú"),
        note,
        h("p", { class: "hint" }, "Không ghi số điện thoại của khách vào sổ."),
        alertHost,
      ),
      actions: gated(save, writeVerdict),
    });
  }

  if (!store) {
    render(
      linesHost,
      emptyState({ icon: "store", title: "Chưa chọn cửa hàng", body: "Chọn cửa hàng ở thanh trên." }),
    );
    render(heroHost);
  } else {
    void go(month);
  }

  return h(
    "section",
    { class: "screen expenses" },
    page({ title: "Sổ thu chi", action: stamp }),
    h(
      "div",
      { class: "expenses__nav", role: "group", "aria-label": "Chọn tháng" },
      button({
        label: "Trước",
        icon: "chevron-left",
        variant: "quiet",
        data: { monthStep: "previous" },
        onClick: () => void go(shiftMonth(month, -1)),
      }),
      h("strong", { class: "expenses__month-label" }, subtitle),
      next,
    ),
    heroHost,
    tier1,
    section({ title: "Theo mục", card: false, children: categoriesHost }),
    section({ title: "Các dòng", card: false, children: [truncation, linesHost] }),
    techHost,
    sheetsHost,
    actionBar(
      gated(
        button({
          label: "Ghi khoản chi",
          icon: "plus",
          variant: "primary",
          block: true,
          network: true,
          data: { expenseAdd: "true" },
          onClick: () => openAdd(),
        }),
        writeVerdict,
      ),
    ),
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/expenses",
  title: "Sổ thu chi",
  capability: "EXPENSES_READ",
  needsStore: true,
  render: render_,
};
