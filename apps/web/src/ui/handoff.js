/**
 * "Tạo đơn cho khách này" — the hand-off from a conversation to ＋ Nhận đồ (`CONTACT-PICK-001`).
 *
 * A customer who wrote to the shop is identified by an opaque contact binding, and the screens
 * where staff meet that customer's words already hold it from a server read: the approvals
 * `SEND_MESSAGE` card and the manual send both read `GET …/message-drafts/{draft}/binding`, whose
 * `recipient_binding_id` is exactly that binding. This hands it to `#/new?contact=<binding>`, which
 * opens the intake and lands on step 2 — so nobody copies a UUID from one screen into another.
 *
 * Three rules, each a way this could go wrong:
 *
 *   - **The binding is only ever one the server returned.** `newOrderForContact` takes it from the
 *     caller's read; `newOrderForDraft` (for `#/shadow`, whose draft list does not carry it) reads
 *     the same binding route on press and hands over its answer. Nothing here accepts a typed one,
 *     and `POST …/order-requests` still refuses a binding the server never recorded.
 *   - **The order is opened in the store the conversation belongs to, or not at all.** `#/new` works
 *     in the selected store; a draft of another store (the approvals queue spans stores) gets a
 *     one-line reason instead of a button that would file the customer under the wrong shop.
 *   - **A role that may not create an intake sees the control disabled, with the reason** (`gated`),
 *     like every other denied control in this console.
 *
 * @module ui/handoff
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UUID } from "../core/format.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated } from "./components.js";
import { button } from "./kit.js";

export const NEW_ORDER_LABEL = "Tạo đơn cho khách này";

/**
 * @param {string} binding
 * @returns {string}
 */
export function newOrderHref(binding) {
  return `#/new?contact=${encodeURIComponent(binding)}`;
}

/**
 * The hand-off for a binding this screen already read from the server.
 *
 * @param {unknown} binding the read's contact binding (`recipient_binding_id`)
 * @param {unknown} ownerStore the store the read belongs to (`store_id`)
 * @returns {HTMLElement|null}
 */
export function newOrderForContact(binding, ownerStore) {
  const contact = String(binding || "");
  if (!UUID.test(contact)) return null;
  const elsewhere = sameStoreRefusal(ownerStore);
  if (elsewhere) return elsewhere;
  const control = button({
    label: NEW_ORDER_LABEL,
    icon: "plus",
    variant: "quiet",
    data: { newOrderContact: contact },
    onClick: () => {
      location.hash = newOrderHref(contact);
    },
  });
  return gated(control, can(principal(), "QUOTES_WRITE"));
}

/**
 * The hand-off for a draft whose binding this screen has not read: read it on press, then go.
 *
 * @param {unknown} ownerStore the draft's store (the store its list was read from)
 * @param {unknown} draftId the draft's `agent_run_id`
 * @returns {HTMLElement|null}
 */
export function newOrderForDraft(ownerStore, draftId) {
  const draft = String(draftId || "");
  const store = String(ownerStore || "");
  if (!UUID.test(draft) || !UUID.test(store)) return null;
  const elsewhere = sameStoreRefusal(store);
  if (elsewhere) return elsewhere;
  const errorHost = h("div");
  const control = button({
    label: NEW_ORDER_LABEL,
    icon: "plus",
    variant: "quiet",
    network: true,
    data: { newOrderDraft: draft },
    onClick: async () => {
      control.disabled = true;
      render(errorHost);
      try {
        const read = await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/message-drafts/${encodeURIComponent(draft)}/binding`,
        );
        const contact = String(read?.recipient_binding_id || "");
        if (!UUID.test(contact) || read?.store_id !== store) {
          throw new Error("the draft's binding read did not name a customer of this store");
        }
        location.hash = newOrderHref(contact);
      } catch (error) {
        if (control.getAttribute("data-denied") !== "true") control.disabled = false;
        render(errorHost, errorNotice(error));
      }
    },
  });
  return h(
    "div",
    { class: "stack stack--tight" },
    gated(control, can(principal(), "QUOTES_WRITE")),
    errorHost,
  );
}

/**
 * @param {unknown} ownerStore
 * @returns {HTMLElement|null} a one-line reason when the conversation is not the selected store's
 */
function sameStoreRefusal(ownerStore) {
  if (typeof ownerStore === "string" && ownerStore && ownerStore === storeId()) return null;
  return h(
    "p",
    { class: "hint", dataNewOrderRefused: "store" },
    "Tin này của cửa hàng khác cửa hàng đang chọn — đổi cửa hàng để tạo đơn cho khách.",
  );
}
