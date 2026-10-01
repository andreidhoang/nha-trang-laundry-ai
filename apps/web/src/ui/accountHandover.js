/**
 * "Giao đồ — ghi công nợ" on the order page (`PAYMENT-002`, `DEC-035`, the B2B half).
 *
 * For an order whose customer has an account, the server says whether its finished goods may leave
 * now with what it owes put on the account (`GET /orders/{id}/account-handover`) -- the same
 * decision the charge makes again under its locks: a limit typed, outstanding plus this order within
 * it, no statement overdue unless the owner lifted the block. When it may, this offers the press;
 * when it may not, it says why in one line. It never decides either.
 *
 * The press opens a sheet that shows what goes on the account (the server's figures), then posts
 * the charge with the order's row version (`If-Match`). A counter customer takes the bag in the same
 * press; the sheet's success state then offers the server's closing step ("Giao đồ & đóng đơn"),
 * exactly as after a payment. An order with no account, or no customer record, shows nothing.
 *
 * @module ui/accountHandover
 */

import { request } from "../core/api.js";
import { HANDOVER_WHY_NOT_VI } from "../core/accounts.js";
import { h, render } from "../core/dom.js";
import { money } from "../core/format.js";
import { gated } from "./components.js";
import { button, moneyHero } from "./kit.js";
// UNCLAIMED-001 (round 7 wave 2 integration): laundry that waited past the free days goes on the
// account with its storage fee; the server's figure already includes it, this only says so.
import { storageChargeLine } from "./unclaimed.js";

/** The refusals that say nothing worth a line on the page: nothing to charge, or already done. */
const QUIET = new Set(["NOTHING_OWED", "ALREADY_COLLECTED", "ORDER_NOT_ACTIVE", "NOT_AN_ACCOUNT_CUSTOMER"]);

/**
 * @typedef {object} OrderPageHooks the order page's own machinery, so a charge behaves as a payment
 * @property {(spec: {title: string, body: unknown, actions?: unknown, id?: string}) => any} openFresh
 * @property {(made: any, title: string, body: unknown, order: any) => void} successState
 * @property {() => Promise<any|null>} reread
 * @property {(error: any) => HTMLElement} refusal
 * @property {(error: any, where: {made: any, alertHost: HTMLElement,
 *   redraw?: (fresh: any) => boolean|Promise<boolean>}) => HTMLElement} sheetRefusal the refusal
 *   inside a sheet, whose "tải lại" keeps the sheet open on the fresh read (COUNTER-UI-RACE-009)
 * @property {(control: HTMLButtonElement|null|undefined, work: () => Promise<void>) => Promise<void>} pressing
 * @property {(intent: string) => string} keyFor
 * @property {() => void} releaseKey
 */

/**
 * @param {object} spec
 * @param {string} spec.orderId
 * @param {{allowed: boolean, reason: string}} spec.verdict `ACCOUNTS_COLLECT` for the signed-in role
 * @param {OrderPageHooks} spec.hooks
 * @returns {{node: HTMLElement, refresh: (order: any) => void}}
 */
export function accountHandover(spec) {
  const { orderId, verdict, hooks } = spec;
  const id = encodeURIComponent(orderId);
  const node = h("div", { class: "account-handover", id: "order-account" });
  let generation = 0;

  /** @param {any} order */
  function refresh(order) {
    generation += 1;
    const mine = generation;
    const owing = ["UNPAID", "PARTIALLY_PAID"].includes(String(order?.balance));
    if (!order?.customer_id || order.commercial !== "ACTIVE" || !owing) {
      render(node);
      return;
    }
    request(`/internal/v1/orders/${id}/account-handover`)
      .then((read) => {
        if (mine === generation) draw(read?.handover || null, order);
      })
      // A read that failed leaves the counter with the ordinary way out (Thu tiền); nothing here is
      // needed to take money, so the failure is not dressed up as a refusal.
      .catch(() => {
        if (mine === generation) render(node);
      });
  }

  /**
   * @param {any|null} handover
   * @param {any} order
   */
  function draw(handover, order) {
    if (!handover) {
      render(node);
      return;
    }
    const customerLink = h(
      "a",
      {
        class: "account-handover__link",
        href: `#/customers/${encodeURIComponent(String(handover.customer_id))}`,
        id: "order-account-link",
      },
      "Xem công nợ",
    );
    if (!handover.offered) {
      const code = String(handover.refusal || "");
      if (QUIET.has(code)) {
        render(node);
        return;
      }
      render(
        node,
        h(
          "p",
          { class: "hint account-handover__why", dataAccountRefusal: code, title: code },
          HANDOVER_WHY_NOT_VI[code] || `Chưa ghi công nợ được (${code}).`,
          " ",
          customerLink,
        ),
      );
      return;
    }
    // "Gồm phí lưu kho …" when the server's charges carry a storage fee; the amount is theirs.
    const fee = storageChargeLine(order);
    const press = gated(
      button({
        label: "Giao đồ — ghi công nợ",
        icon: "tag",
        block: true,
        network: true,
        id: "order-account-charge",
        onClick: () => openSheet(handover, order),
      }),
      verdict,
    );
    render(
      node,
      press,
      h(
        "p",
        { class: "hint account-handover__what" },
        `Ghi ${money(handover.order_remaining_vnd)} vào công nợ · đang nợ ${money(
          handover.outstanding_vnd,
        )} / hạn mức ${money(handover.credit_limit_vnd)}. `,
        fee ? `${fee}. ` : null,
        customerLink,
      ),
    );
  }

  /**
   * @param {any} handover
   * @param {any} order
   */
  function openSheet(handover, order) {
    const alertHost = h("div");
    // What the sheet shows; a reload inside it re-reads both and redraws them (C4).
    let shownHandover = handover;
    let shownOrder = order;
    const collected = handover.collected_by_customer === true;
    const confirm = button({
      label: "Giao đồ — ghi công nợ",
      variant: "primary",
      block: true,
      network: true,
      id: "order-account-confirm",
      onClick: () => void send(),
    });

    async function send() {
      // The version of the order whose figures are on the sheet: a stale one is refused, and
      // "tải lại" then redraws the figures before the next press (C4).
      const now = shownOrder;
      render(alertHost);
      await hooks.pressing(confirm, async () => {
        try {
          const charged = await request(`/internal/v1/orders/${id}/account-charge`, {
            method: "POST",
            body: { collected_by_customer: collected },
            ifMatch: now.row_version,
            idempotencyKey: hooks.keyFor(`account|${now.row_version}|${collected}`),
          });
          hooks.releaseKey();
          const view = await hooks.reread();
          hooks.successState(
            made,
            `Đã ghi ${money(charged.amount_vnd)} vào công nợ.`,
            collected
              ? `Khách đang nợ ${money(charged.outstanding_after_vnd)}. Đã ghi khách nhận đồ.`
              : `Khách đang nợ ${money(charged.outstanding_after_vnd)}. Đơn đóng khi có chuyến giao thành công.`,
            view,
          );
        } catch (error) {
          render(alertHost, hooks.sheetRefusal(error, { made, alertHost, redraw: reloadFigures }));
        }
      });
    }

    /**
     * After "tải lại": the fresh order and its handover read. The sheet stays only while the
     * server still offers the charge for the same handover choice; its figures are the new ones.
     *
     * @param {any} fresh
     * @returns {Promise<boolean>}
     */
    async function reloadFigures(fresh) {
      let read = null;
      try {
        read = await request(`/internal/v1/orders/${id}/account-handover`);
      } catch {
        return false;
      }
      const next = read?.handover || null;
      if (!next?.offered || (next.collected_by_customer === true) !== collected) return false;
      shownHandover = next;
      shownOrder = fresh;
      drawHero();
      return true;
    }

    const heroHost = h("div", { dataField: "account-hero" });
    function drawHero() {
      render(
        heroHost,
        moneyHero({
          label: "Ghi vào công nợ",
          amount: money(shownHandover.order_remaining_vnd),
          caption: [
            `Đang nợ ${money(shownHandover.outstanding_vnd)} → ${money(
              shownHandover.outstanding_after_vnd,
            )} · hạn mức ${money(shownHandover.credit_limit_vnd)}`,
            storageChargeLine(shownOrder),
          ]
            .filter(Boolean)
            .join(" · "),
        }),
      );
    }
    drawHero();

    const made = hooks.openFresh({
      id: "order-account-sheet",
      title: "Giao đồ — ghi công nợ",
      body: h(
        "div",
        { class: "stack" },
        heroHost,
        h(
          "p",
          null,
          collected
            ? "Khách nhận đồ ngay. Không thu tiền tại quầy — tiền vào sao kê tháng này."
            : "Đồ đi giao; không thu tiền khi giao — tiền vào sao kê tháng này.",
        ),
        alertHost,
      ),
      actions: gated(confirm, verdict),
    });
  }

  return { node, refresh };
}
