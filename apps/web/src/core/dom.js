/**
 * Safe DOM construction.
 *
 * Every string that reaches this module becomes a text node. There is no escaping function here
 * and no `html` template tag, because the moment one exists someone reaches for it to render an
 * agent-authored draft or a customer's message and the console gains a script-injection path
 * through exactly the untrusted text it was built to review. The absence of that API is the
 * defence; `assertNoRawHtml` below exists so a test can prove the absence rather than trust it.
 *
 * @module core/dom
 */

const SVG_NS = "http://www.w3.org/2000/svg";

/** Attributes that must be set as properties rather than attributes to behave correctly. */
const PROPERTY_ATTRIBUTES = new Set(["value", "checked", "selected", "disabled", "indeterminate"]);

/**
 * Append one child spec to a parent. Arrays flatten; `null`, `undefined`, `false` and `true` are
 * dropped so callers can write `cond && node` inline without guarding.
 *
 * @param {Node} parent
 * @param {unknown} child
 */
function appendChild(parent, child) {
  if (child === null || child === undefined || typeof child === "boolean") return;
  if (Array.isArray(child)) {
    for (const item of child) appendChild(parent, item);
    return;
  }
  if (child instanceof Node) {
    parent.appendChild(child);
    return;
  }
  parent.appendChild(document.createTextNode(String(child)));
}

/**
 * Create an element.
 *
 * Props are interpreted by prefix:
 *   `on*`      — an event listener, e.g. `onClick`, `onSubmit`, `onInput`
 *   `data*`    — a dataset entry, e.g. `dataVariant: "primary"` becomes `data-variant="primary"`
 *   `aria-*`   — set verbatim as an attribute
 *   `class`    — a string, or an array whose falsy entries are dropped
 *   `style`    — an object of camelCase CSS properties; strings are rejected so that no caller can
 *                smuggle a `url(...)` or an `expression(...)` through a concatenated value
 *   anything else — an attribute, unless it is in PROPERTY_ATTRIBUTES
 *
 * @param {string} tag
 * @param {Record<string, unknown> | null} [props]
 * @param {...unknown} children
 * @returns {HTMLElement | SVGElement}
 */
export function h(tag, props, ...children) {
  const element =
    tag === "svg" || tag === "path" || tag === "circle" || tag === "rect" || tag === "line"
      ? document.createElementNS(SVG_NS, tag)
      : document.createElement(tag);

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;

    if (key === "class") {
      const names = Array.isArray(value) ? value.filter(Boolean) : [value];
      if (names.length) element.setAttribute("class", names.join(" "));
      continue;
    }

    if (key === "style") {
      if (typeof value !== "object") {
        throw new TypeError("style must be an object of properties, not a string");
      }
      for (const [property, setting] of Object.entries(value)) {
        if (setting !== null && setting !== undefined) {
          element.style.setProperty(
            property.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`),
            String(setting),
          );
        }
      }
      continue;
    }

    if (key.startsWith("on") && typeof value === "function") {
      element.addEventListener(key.slice(2).toLowerCase(), value);
      continue;
    }

    if (key.startsWith("data") && key.length > 4 && key[4] === key[4].toUpperCase()) {
      element.dataset[key.slice(4, 5).toLowerCase() + key.slice(5)] = String(value);
      continue;
    }

    if (PROPERTY_ATTRIBUTES.has(key)) {
      element[key] = value; // deliberate property assignment, not setAttribute
      continue;
    }

    element.setAttribute(key, value === true ? "" : String(value));
  }

  for (const child of children) appendChild(element, child);
  return element;
}

/**
 * A document fragment holding the given children.
 *
 * @param {...unknown} children
 * @returns {DocumentFragment}
 */
export function frag(...children) {
  const fragment = document.createDocumentFragment();
  for (const child of children) appendChild(fragment, child);
  return fragment;
}

/**
 * Replace everything inside `parent` with `children`.
 *
 * @param {Element} parent
 * @param {...unknown} children
 * @returns {Element} the same parent, for chaining
 */
export function render(parent, ...children) {
  parent.replaceChildren();
  for (const child of children) appendChild(parent, child);
  return parent;
}

/**
 * A definition list row: `<div><dt>term</dt><dd>value</dd></div>`.
 *
 * Used everywhere a record's fields are shown. The wrapping div is what lets the grid align the
 * terms into a column on wide screens and stack them on a phone.
 *
 * @param {string} term
 * @param {unknown} value
 * @param {{ mono?: boolean, money?: boolean, span?: boolean }} [options]
 * @returns {HTMLElement}
 */
export function field(term, value, options = {}) {
  const classes = [options.mono && "mono", options.money && "money"].filter(Boolean);
  return h(
    "div",
    { class: options.span ? "field field--span" : "field" },
    h("dt", null, term),
    h("dd", classes.length ? { class: classes } : null, value),
  );
}

/**
 * Move keyboard focus to a container without scrolling the page under the user's thumb.
 *
 * Called on every route change. Without it a screen-reader user who activates a nav link stays
 * parked at the top of the navigation and has to tab back through it to reach the new screen.
 *
 * @param {HTMLElement} element
 */
export function focusContainer(element) {
  if (!element.hasAttribute("tabindex")) element.setAttribute("tabindex", "-1");
  element.focus({ preventScroll: true });
}

/**
 * Find raw-HTML escape hatches in a module's source.
 *
 * The needles are assembled from fragments so that this file passes its own check and can be
 * included in the scan like every other module. The browser suite runs it across all of `src/`;
 * the claim lives here, next to the code it constrains, rather than only in the test.
 *
 * @param {string} source the text of a module
 * @returns {string[]} the forbidden constructs found; empty means the invariant holds
 */
export function findRawHtmlEscapes(source) {
  const forbidden = [
    "inner" + "HTML",
    "outer" + "HTML",
    "insertAdjacent" + "HTML",
    "document." + "write",
    "eval" + "(",
    "new " + "Function(",
  ];
  return forbidden.filter((needle) => source.includes(needle));
}
