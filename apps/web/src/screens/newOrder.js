/**
 * ＋ Nhận đồ — the counter's one flow for a customer handing over laundry (spec V2 §5.2).
 *
 * Foundation placeholder: the route, its place in the tab bar and its permission exist so the
 * shell is whole; `CONSOLE-REDESIGN-001` replaces this body with the three-step flow (Khách →
 * Đồ & giá → Xác nhận) that carries every identifier forward from the server's own responses.
 * Until then it hands off to the two existing screens in order.
 *
 * @module screens/newOrder
 */

import { h } from "../core/dom.js";
import { list, listRow, page } from "../ui/kit.js";

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/new",
  title: "Nhận đồ",
  capability: "QUOTES_WRITE",
  needsStore: true,
  render: () =>
    h(
      "section",
      { class: "screen" },
      page({ title: "Nhận đồ", subtitle: "Khách gửi đồ tại quầy." }),
      list([
        listRow({ href: "#/order-requests", leading: "intake", title: "1. Tiếp nhận khách" }),
        listRow({ href: "#/quotes", leading: "quote", title: "2. Tính giá và chốt giá" }),
      ]),
    ),
};
