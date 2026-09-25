/**
 * The V2 component kit — the vocabulary every rebuilt screen is made of.
 *
 * `docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md` §3.3. The V1 vocabulary (`components.js`) was built
 * around the *server command*: a panel per endpoint, a guardrail box above each form. This one is
 * built around the *staff task*: a list of things, a page for one thing, one obvious next step, and
 * explanation layered behind it (§4 honesty tiers). `components.js` stays — its exports are used
 * everywhere and are restyled to match — and nothing here re-implements what it already does well
 * (`errorNotice`, `gated`, `copyable`, `listView`'s fetch/truncation bookkeeping).
 *
 * The same invariants hold for every function in this file, and they are why it is safe to hand
 * to six engineers at once:
 *
 *   - **No HTML sinks.** Everything is built with `h()`; untrusted text is a text node.
 *   - **No money arithmetic.** Money arrives already formatted by `format.money()`; nothing here
 *     takes a money integer. `moneyHero` and `listRow` show strings.
 *   - **No storage.** Open/closed, selected tab, a sheet's contents: memory only (V1 invariant 3).
 *   - **Nothing clickable under 44 px**, and a primary action is 48 px.
 *   - **Disabled is visible.** A control that is off carries its reason as text (V1 invariant 5).
 *
 * @module ui/kit
 */

import { h, render } from "../core/dom.js";
import { copyable, icon } from "./components.js";

// ---------------------------------------------------------------------------------------------
// Page structure
// ---------------------------------------------------------------------------------------------

/**
 * A screen's header: a large title, an optional one-line subtitle, an optional back link, and at
 * most one header action. No lede paragraphs — anything longer than a line belongs in an
 * `infoButton` (tier 2).
 *
 * @param {object} spec
 * @param {string} spec.title
 * @param {unknown} [spec.subtitle] one line of plain text or a node
 * @param {{href: string, label: string}} [spec.back]
 * @param {unknown} [spec.action] a button or link, shown at the right
 * @param {unknown} [spec.info] an `infoButton` shown beside the title
 * @returns {HTMLElement}
 */
export function page(spec) {
  return h(
    "header",
    { class: "page-head" },
    spec.back
      ? h(
          "a",
          { class: "page-head__back", href: spec.back.href },
          icon("chevron-left"),
          h("span", null, spec.back.label),
        )
      : null,
    h(
      "div",
      { class: "page-head__row" },
      h(
        "div",
        { class: "page-head__titles" },
        h("h1", { class: "page-head__title" }, spec.title, spec.info || null),
        spec.subtitle ? h("p", { class: "page-head__subtitle" }, spec.subtitle) : null,
      ),
      spec.action ? h("div", { class: "page-head__action" }, spec.action) : null,
    ),
  );
}

/**
 * A titled group of content. Replaces `panel()` on rebuilt screens: no eyebrow, no guardrail box,
 * one short title, and an optional action at the right of the title (e.g. "Xem tất cả").
 *
 * @param {object} spec
 * @param {string} [spec.title]
 * @param {unknown} [spec.action]
 * @param {unknown} [spec.info] an `infoButton` beside the title
 * @param {boolean} [spec.card] draw the content on a card surface (default true)
 * @param {string} [spec.id]
 * @param {unknown} [spec.children]
 * @returns {HTMLElement}
 */
export function section(spec) {
  return h(
    "section",
    { class: "group", id: spec.id || null },
    spec.title || spec.action
      ? h(
          "div",
          { class: "group__head" },
          spec.title ? h("h2", { class: "group__title" }, spec.title, spec.info || null) : null,
          spec.action ? h("div", { class: "group__action" }, spec.action) : null,
        )
      : null,
    h("div", { class: spec.card === false ? "group__body" : "group__body surface" }, spec.children),
  );
}

// ---------------------------------------------------------------------------------------------
// Lists
// ---------------------------------------------------------------------------------------------

/**
 * A list of rows on one card surface, separated by hairlines (inset-grouped).
 *
 * @param {unknown[]} rows `listRow` nodes
 * @param {{label?: string, id?: string}} [options]
 * @returns {HTMLElement}
 */
export function list(rows, options = {}) {
  return h(
    "ul",
    { class: "rows", id: options.id || null, "aria-label": options.label || null },
    rows.filter(Boolean).map((row) => h("li", { class: "rows__item" }, row)),
  );
}

/**
 * One tappable row: the standard way to show a thing in a list.
 *
 * A row with `href` is a link; with `onClick` a button; with neither it is static. A disabled row
 * stays visible and states why (`reason`), exactly like a denied nav entry.
 *
 * @param {object} spec
 * @param {string} [spec.href]
 * @param {(event: Event) => void} [spec.onClick]
 * @param {unknown} [spec.leading] an icon name (string) or a node (avatar, status dot)
 * @param {unknown} spec.title
 * @param {unknown} [spec.meta] secondary line(s)
 * @param {unknown} [spec.trailing] right-aligned value (money, count, pill)
 * @param {unknown} [spec.trailingMeta] small line under the trailing value
 * @param {boolean} [spec.chevron] default: true for links and buttons
 * @param {boolean} [spec.disabled]
 * @param {string} [spec.reason] why it is disabled — rendered as text
 * @param {Record<string, string>} [spec.data] data-* attributes (test hooks), camelCase keys
 * @returns {HTMLElement}
 */
export function listRow(spec) {
  const interactive = Boolean(spec.href || spec.onClick) && !spec.disabled;
  const leading =
    typeof spec.leading === "string"
      ? h("span", { class: "row-item__icon" }, icon(spec.leading))
      : spec.leading
        ? h("span", { class: "row-item__leading" }, spec.leading)
        : null;
  const chevron =
    (spec.chevron ?? interactive) && interactive
      ? h("span", { class: "row-item__chevron" }, icon("chevron-right"))
      : null;
  const body = [
    leading,
    h(
      "span",
      { class: "row-item__main" },
      h("span", { class: "row-item__title" }, spec.title),
      spec.meta ? h("span", { class: "row-item__meta" }, spec.meta) : null,
      spec.disabled && spec.reason ? h("span", { class: "row-item__reason" }, spec.reason) : null,
    ),
    spec.trailing || spec.trailingMeta
      ? h(
          "span",
          { class: "row-item__trailing" },
          spec.trailing ? h("span", { class: "row-item__value" }, spec.trailing) : null,
          spec.trailingMeta ? h("span", { class: "row-item__value-meta" }, spec.trailingMeta) : null,
        )
      : null,
    chevron,
  ];
  const props = {
    class: ["row-item", interactive && "row-item--interactive"],
    "aria-disabled": spec.disabled ? "true" : null,
  };
  for (const [key, value] of Object.entries(spec.data || {})) {
    props[`data${key[0].toUpperCase()}${key.slice(1)}`] = value;
  }
  if (spec.href && !spec.disabled) return h("a", { ...props, href: spec.href }, body);
  if (spec.onClick && !spec.disabled) {
    return h("button", { ...props, type: "button", onClick: spec.onClick }, body);
  }
  return h("div", props, body);
}

/**
 * An empty list, said kindly and usefully: what is empty, and the one thing to do about it.
 *
 * @param {object} spec
 * @param {string} [spec.icon]
 * @param {string} spec.title
 * @param {unknown} [spec.body]
 * @param {unknown} [spec.action]
 * @returns {HTMLElement}
 */
export function emptyState(spec) {
  return h(
    "div",
    { class: "empty-state" },
    spec.icon ? h("span", { class: "empty-state__icon" }, icon(spec.icon)) : null,
    h("p", { class: "empty-state__title" }, spec.title),
    spec.body ? h("p", { class: "empty-state__body" }, spec.body) : null,
    spec.action || null,
  );
}

/**
 * Placeholder rows in the shape of the list that is coming.
 *
 * @param {number} [rows]
 * @returns {HTMLElement}
 */
export function skeletonRows(rows = 4) {
  return h(
    "div",
    { class: "rows rows--skeleton", "aria-busy": "true", "aria-label": "Đang tải" },
    Array.from({ length: rows }, () =>
      h("div", { class: "row-item" }, h("span", { class: "skeleton skeleton--line" })),
    ),
  );
}

// ---------------------------------------------------------------------------------------------
// Status and money
// ---------------------------------------------------------------------------------------------

/**
 * One status word with a colour and a dot. The word carries the meaning; colour only speeds up
 * scanning (V1 badge rule). The enum token, when there is one, is kept in `title` for the person
 * who needs to quote it — operator surfaces show the gloss only (spec §4.1).
 *
 * @param {object} spec
 * @param {"ok"|"warn"|"danger"|"info"|"neutral"} [spec.state]
 * @param {string} spec.text
 * @param {string} [spec.token]
 * @returns {HTMLElement}
 */
export function statusPill(spec) {
  return h(
    "span",
    {
      class: "pill",
      dataState: spec.state || "neutral",
      title: spec.token || null,
      dataToken: spec.token || null,
    },
    spec.text,
  );
}

/**
 * The one number a screen exists to show (takings, amount due), large.
 *
 * @param {object} spec
 * @param {string} spec.label
 * @param {string} spec.amount already formatted by `format.money()`
 * @param {unknown} [spec.caption]
 * @param {"ok"|"warn"|"danger"|"neutral"} [spec.state]
 * @param {unknown} [spec.info]
 * @returns {HTMLElement}
 */
export function moneyHero(spec) {
  return h(
    "div",
    { class: "money-hero", dataState: spec.state || null },
    h("p", { class: "money-hero__label" }, spec.label, spec.info || null),
    h("p", { class: "money-hero__amount money" }, spec.amount),
    spec.caption ? h("p", { class: "money-hero__caption" }, spec.caption) : null,
  );
}

/**
 * A horizontal step tracker: where a thing is in its life.
 *
 * Presentation only: the caller maps server state to steps; this decides nothing.
 *
 * @param {Array<{label: string, state: "done"|"current"|"todo"|"blocked"}>} steps
 * @param {{label?: string}} [options]
 * @returns {HTMLElement}
 */
export function progress(steps, options = {}) {
  return h(
    "ol",
    { class: "progress", "aria-label": options.label || "Tiến trình" },
    steps.map((step) =>
      h(
        "li",
        {
          class: "progress__step",
          dataState: step.state,
          "aria-current": step.state === "current" ? "step" : null,
        },
        h(
          "span",
          { class: "progress__dot", "aria-hidden": "true" },
          step.state === "done" ? icon("check") : null,
        ),
        h("span", { class: "progress__label" }, step.label),
      ),
    ),
  );
}

/**
 * Initials in a circle, for a person.
 *
 * @param {string} name
 * @returns {HTMLElement}
 */
export function avatar(name) {
  const words = String(name || "?").trim().split(/\s+/);
  const initials = (
    words.length > 1 ? words[0][0] + words[words.length - 1][0] : words[0].slice(0, 2)
  ).toUpperCase();
  return h("span", { class: "avatar", "aria-hidden": "true" }, initials);
}

// ---------------------------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------------------------

/**
 * A button in the kit's vocabulary.
 *
 * @param {object} spec
 * @param {unknown} spec.label
 * @param {"primary"|"secondary"|"quiet"|"danger"} [spec.variant]
 * @param {string} [spec.icon]
 * @param {(event: Event) => void} [spec.onClick]
 * @param {"button"|"submit"} [spec.type]
 * @param {boolean} [spec.block] full width
 * @param {boolean} [spec.network] the action needs the network (`data-requires-network`)
 * @param {boolean} [spec.disabled]
 * @param {string} [spec.id]
 * @param {Record<string, string>} [spec.data]
 * @returns {HTMLButtonElement}
 */
export function button(spec) {
  const props = {
    type: spec.type || "button",
    class: ["btn", spec.block && "btn--block"],
    dataVariant: spec.variant && spec.variant !== "secondary" ? spec.variant : null,
    dataRequiresNetwork: spec.network ? "true" : null,
    disabled: spec.disabled || null,
    id: spec.id || null,
    onClick: spec.onClick || null,
  };
  for (const [key, value] of Object.entries(spec.data || {})) {
    props[`data${key[0].toUpperCase()}${key.slice(1)}`] = value;
  }
  return /** @type {HTMLButtonElement} */ (
    h("button", props, spec.icon ? icon(spec.icon) : null, h("span", null, spec.label))
  );
}

/**
 * A link styled as a button (navigation, never a write).
 *
 * @param {object} spec
 * @param {string} spec.href
 * @param {unknown} spec.label
 * @param {"primary"|"secondary"|"quiet"} [spec.variant]
 * @param {string} [spec.icon]
 * @param {boolean} [spec.block]
 * @returns {HTMLElement}
 */
export function linkButton(spec) {
  return h(
    "a",
    {
      class: ["button", "btn", spec.block && "btn--block"],
      href: spec.href,
      dataVariant: spec.variant && spec.variant !== "secondary" ? spec.variant : null,
    },
    spec.icon ? icon(spec.icon) : null,
    h("span", null, spec.label),
  );
}

/**
 * The sticky bar that holds a screen's primary action, above the phone tab bar and within thumb
 * reach. At most one primary; a secondary may sit beside it.
 *
 * @param {...unknown} children buttons, and optionally one short tier-1 line (a `p.hint`)
 * @returns {HTMLElement}
 */
export function actionBar(...children) {
  return h("div", { class: "action-bar action-bar--v2" }, ...children);
}

/**
 * A destructive or irreversible action that needs a second press: the first press arms it and
 * says what will happen, the second commits. Leaving it (blur, Escape, 6 s) disarms it.
 *
 * @param {object} spec
 * @param {string} spec.label
 * @param {string} spec.confirmLabel what the armed button says ("Bấm lần nữa để huỷ đơn")
 * @param {() => void} spec.onConfirm
 * @param {"danger"|"primary"} [spec.variant]
 * @param {boolean} [spec.block]
 * @returns {HTMLButtonElement}
 */
export function confirmButton(spec) {
  let armed = false;
  let timer = 0;
  const node = button({
    label: spec.label,
    variant: spec.variant || "danger",
    block: spec.block,
    network: true,
    onClick: () => {
      if (!armed) {
        armed = true;
        node.dataset.armed = "true";
        setLabel(spec.confirmLabel);
        clearTimeout(timer);
        timer = setTimeout(disarm, 6000);
        return;
      }
      disarm();
      spec.onConfirm();
    },
  });
  function setLabel(text) {
    const span = node.querySelector("span");
    if (span) span.textContent = text;
  }
  function disarm() {
    armed = false;
    delete node.dataset.armed;
    setLabel(spec.label);
    clearTimeout(timer);
  }
  node.addEventListener("blur", disarm);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Escape") disarm();
  });
  return node;
}

// ---------------------------------------------------------------------------------------------
// Choice controls
// ---------------------------------------------------------------------------------------------

/**
 * A segmented control: 2–5 mutually exclusive options, one tap each. Used for list filters and
 * short enumerations (delivery mode). State lives in the DOM (`aria-pressed`) and the callback.
 *
 * @param {object} spec
 * @param {string} spec.label accessible name of the group
 * @param {Array<{value: string, label: string, count?: string}>} spec.options
 * @param {string} spec.value
 * @param {(value: string) => void} spec.onChange
 * @param {string} [spec.id]
 * @param {boolean} [spec.wrap] allow wrapping onto several lines (chips) instead of equal shares
 * @returns {HTMLElement & {setValue: (value: string) => void}}
 */
export function segmented(spec) {
  const buttons = spec.options.map((option) =>
    h(
      "button",
      {
        type: "button",
        class: "segmented__option",
        "aria-pressed": option.value === spec.value ? "true" : "false",
        dataValue: option.value,
        onClick: () => select(option.value, true),
      },
      h("span", null, option.label),
      option.count ? h("span", { class: "segmented__count" }, option.count) : null,
    ),
  );
  const root = /** @type {HTMLElement & {setValue: (value: string) => void}} */ (
    h(
      "div",
      {
        class: ["segmented", spec.wrap && "segmented--wrap"],
        role: "group",
        "aria-label": spec.label,
        id: spec.id || null,
      },
      buttons,
    )
  );
  function select(value, notify) {
    for (const node of buttons) {
      node.setAttribute("aria-pressed", node.dataset.value === value ? "true" : "false");
    }
    if (notify) spec.onChange(value);
  }
  root.setValue = (value) => select(value, false);
  return root;
}

/**
 * A search field with a leading icon; `inputmode` lets a ticket search open the number pad.
 *
 * @param {object} spec
 * @param {string} spec.id
 * @param {string} spec.label accessible name (and placeholder when none given)
 * @param {string} [spec.placeholder]
 * @param {"text"|"numeric"|"search"} [spec.inputmode]
 * @param {(value: string) => void} [spec.onInput]
 * @param {(value: string) => void} [spec.onSubmit] Enter
 * @returns {{node: HTMLElement, input: HTMLInputElement}}
 */
export function searchField(spec) {
  const input = /** @type {HTMLInputElement} */ (
    h("input", {
      id: spec.id,
      type: "search",
      class: "search__input",
      inputmode: spec.inputmode || "search",
      enterkeyhint: "search",
      autocomplete: "off",
      placeholder: spec.placeholder || spec.label,
      "aria-label": spec.label,
      onInput: (event) => spec.onInput?.(event.target.value),
      onKeydown: (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          spec.onSubmit?.(event.target.value);
        }
      },
    })
  );
  const node = h(
    "div",
    { class: "search", role: "search" },
    h("span", { class: "search__icon" }, icon("search")),
    input,
  );
  return { node, input };
}

/**
 * A quantity with − and + beside it, for phones where typing "5.5" is slow.
 *
 * The value stays a string the server parses (`format.parseQuantity`), and the buttons step it in
 * whole units or tenths using integer arithmetic on the digits, so no float ever rounds a weight.
 * This is a quantity of laundry, never an amount of money.
 *
 * @param {object} spec
 * @param {string} spec.id
 * @param {string} spec.label
 * @param {string} [spec.value]
 * @param {string} [spec.unit] shown after the field ("kg", "cái")
 * @param {boolean} [spec.decimal] allow one decimal (kg); otherwise whole units
 * @param {(value: string) => void} [spec.onChange]
 * @param {string} [spec.describedBy]
 * @returns {{node: HTMLElement, input: HTMLInputElement}}
 */
export function stepperInput(spec) {
  const input = /** @type {HTMLInputElement} */ (
    h("input", {
      id: spec.id,
      type: "text",
      class: "stepper__input",
      inputmode: spec.decimal ? "decimal" : "numeric",
      autocomplete: "off",
      value: spec.value ?? "",
      "aria-describedby": spec.describedBy || null,
      onInput: (event) => spec.onChange?.(event.target.value),
    })
  );
  /** Tenths as an integer, or null when the field does not hold a plain non-negative number. */
  function tenths(text) {
    const match = /^(\d{1,4})(?:[.,](\d))?$/.exec(String(text).trim());
    if (!match) return null;
    return Number(match[1]) * 10 + (match[2] ? Number(match[2]) : 0);
  }
  function step(direction) {
    const unit = spec.decimal ? 5 : 10;
    const current = tenths(input.value) ?? 0;
    const next = Math.max(0, current + direction * unit);
    const whole = (next - (next % 10)) / 10;
    const rest = next % 10;
    input.value = rest ? `${whole}.${rest}` : String(whole);
    spec.onChange?.(input.value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  const node = h(
    "div",
    { class: "stepper" },
    h(
      "button",
      { type: "button", class: "stepper__btn", "aria-label": `Bớt ${spec.label}`, onClick: () => step(-1) },
      "−",
    ),
    input,
    spec.unit ? h("span", { class: "stepper__unit" }, spec.unit) : null,
    h(
      "button",
      { type: "button", class: "stepper__btn", "aria-label": `Thêm ${spec.label}`, onClick: () => step(1) },
      "+",
    ),
  );
  return { node, input };
}

// ---------------------------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------------------------

/**
 * An inline alert at the point of action: a refusal with its reason and the way out, or a
 * tier-1 safety fact. `role=alert` for danger/warn so a screen reader hears it at once.
 *
 * @param {object} spec
 * @param {"ok"|"warn"|"danger"|"info"} [spec.state]
 * @param {unknown} [spec.title]
 * @param {unknown} [spec.body]
 * @param {unknown} [spec.actions]
 * @returns {HTMLElement}
 */
export function inlineAlert(spec) {
  const state = spec.state || "info";
  return h(
    "div",
    {
      class: "alert",
      dataState: state,
      role: state === "danger" || state === "warn" ? "alert" : "status",
    },
    h(
      "span",
      { class: "alert__icon" },
      icon(state === "ok" ? "check" : state === "info" ? "info" : "exception"),
    ),
    h(
      "div",
      { class: "alert__main" },
      spec.title ? h("p", { class: "alert__title" }, spec.title) : null,
      spec.body ? h("div", { class: "alert__body" }, spec.body) : null,
      spec.actions ? h("div", { class: "alert__actions" }, spec.actions) : null,
    ),
  );
}

/** The single live region toasts are announced from. Created on first use. */
let toastHost = /** @type {HTMLElement|null} */ (null);

/**
 * A short confirmation that a write landed ("Đã bắt đầu giặt · Phiếu 17"). Announced politely,
 * gone after four seconds. Only for success: a refusal is never a toast — it stays inline at the
 * control, where the person can read it and act (spec §2.8).
 *
 * @param {string} text
 */
export function toast(text) {
  if (!toastHost || !toastHost.isConnected) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastHost);
  }
  const node = h("div", { class: "toast" }, icon("check"), h("span", null, text));
  toastHost.append(node);
  setTimeout(() => node.remove(), 4000);
}

// ---------------------------------------------------------------------------------------------
// Sheets and layered explanation
// ---------------------------------------------------------------------------------------------

/**
 * A modal sheet: a bottom sheet on a phone, a centred dialog on a desk. Used for a form that
 * belongs to one thing (thu tiền, thêm vai trò) and for tier-2 explanations.
 *
 * Built on native `<dialog>` + `showModal()`: focus is trapped and Escape closes it without any
 * code here, and focus returns to the control that opened it. The node is created once and kept
 * in the DOM (closed), so what it says is present for print and for the disclosure registry.
 *
 * @param {object} spec
 * @param {string} spec.title
 * @param {unknown} spec.body
 * @param {unknown} [spec.actions]
 * @param {() => void} [spec.onClose]
 * @param {string} [spec.id]
 * @returns {{node: HTMLDialogElement, open: () => void, close: () => void, body: HTMLElement}}
 */
export function sheet(spec) {
  let opener = /** @type {Element|null} */ (null);
  const body = h("div", { class: "sheet__body" }, spec.body);
  const node = /** @type {HTMLDialogElement} */ (
    h(
      "dialog",
      {
        class: "sheet",
        id: spec.id || null,
        "aria-label": spec.title,
        onClose: () => {
          spec.onClose?.();
          if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
        },
        onClick: (event) => {
          // A click on the backdrop lands on the dialog element itself.
          if (event.target === node) node.close();
        },
      },
      h(
        "div",
        { class: "sheet__panel" },
        h(
          "div",
          { class: "sheet__head" },
          h("h2", { class: "sheet__title" }, spec.title),
          h(
            "button",
            {
              type: "button",
              class: "sheet__close",
              "aria-label": "Đóng",
              onClick: () => node.close(),
            },
            icon("close"),
          ),
        ),
        body,
        spec.actions ? h("div", { class: "sheet__actions" }, spec.actions) : null,
      ),
    )
  );
  return {
    node,
    body,
    open() {
      opener = document.activeElement;
      if (!node.isConnected) document.body.append(node);
      if (!node.open) node.showModal();
    },
    close() {
      if (node.open) node.close();
    },
  };
}

/**
 * Tier-2 explanation behind an ⓘ: a small round button that opens a sheet titled with the topic.
 * The topic is a plain question ("Con số này gồm những gì?"), never "Xem thêm" (V1 rule kept).
 *
 * What may go inside is the V1 `explain()` rule: context and limits, never a point-of-action
 * safety fact, a capability refusal or a truncation notice — those stay visible (tier 1).
 *
 * @param {string} topic
 * @param {...unknown} body
 * @returns {HTMLElement}
 */
export function infoButton(topic, ...body) {
  const dialog = sheet({ title: topic, body: h("div", { class: "stack stack--tight" }, ...body) });
  const trigger = h(
    "button",
    {
      type: "button",
      class: "info-btn",
      "aria-label": topic,
      title: topic,
      onClick: (event) => {
        event.preventDefault();
        event.stopPropagation();
        dialog.open();
      },
    },
    icon("info"),
  );
  return h("span", { class: "info" }, trigger, dialog.node);
}

/**
 * Tier-3: the technical record of a thing — ids, versions, hashes, enum tokens — closed by
 * default, with copy buttons, printed expanded. Operators never need it; auditors and whoever is
 * on the phone with the developer do.
 *
 * @param {Array<[string, unknown, {copy?: string, mono?: boolean}?]|null>} entries
 * @param {{summary?: string}} [options]
 * @returns {HTMLElement}
 */
export function techDetails(entries, options = {}) {
  return h(
    "details",
    { class: "tech" },
    h("summary", null, options.summary || "Chi tiết kỹ thuật"),
    h(
      "dl",
      { class: "tech__list" },
      entries.filter(Boolean).map(([term, value, extra]) =>
        h(
          "div",
          { class: "tech__row" },
          h("dt", null, term),
          h(
            "dd",
            { class: extra?.mono === false ? null : "mono" },
            extra?.copy ? copyable({ value: extra.copy, display: String(value ?? "") }) : value,
          ),
        ),
      ),
    ),
  );
}

/**
 * Key/value facts in plain words, two columns on a card (tier 1 — what a person reads).
 *
 * @param {Array<[string, unknown]|null>} entries
 * @returns {HTMLElement}
 */
export function keyValues(entries) {
  return h(
    "dl",
    { class: "kv" },
    entries
      .filter(Boolean)
      .map(([term, value]) =>
        h("div", { class: "kv__row" }, h("dt", null, term), h("dd", null, value)),
      ),
  );
}

/**
 * Replace a host's content with a node and move focus to it if it holds an alert. Small, but it is
 * the one way results are shown, so every screen announces refusals the same way.
 *
 * @param {HTMLElement} host
 * @param {unknown} content
 */
export function show(host, content) {
  render(host, content);
  const alert = host.querySelector('[role="alert"]');
  if (alert instanceof HTMLElement) {
    alert.setAttribute("tabindex", "-1");
    alert.focus({ preventScroll: false });
  }
}
