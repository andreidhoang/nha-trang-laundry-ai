/**
 * Thêm: every destination the phone's five-slot tab bar has no room for, grouped by the kind of
 * work, each with one line saying what it is for (spec V2 §3.2).
 *
 * A destination this role may not open is listed, disabled, with the server's reason as visible
 * text — never removed (V1 invariant 5), and never only in a tooltip a thumb cannot reach. The
 * table is `core/nav.js`, the same one the desktop sidebar renders, so the two cannot drift.
 *
 * Reads nothing from the server and writes nothing.
 *
 * @module screens/more
 */

import { h } from "../core/dom.js";
import { NAV_ITEMS, navVerdict } from "../core/nav.js";
import { principal } from "../core/session.js";
import { list, listRow, page, section } from "../ui/kit.js";

function build() {
  const who = principal();
  const groups = new Map();
  for (const item of NAV_ITEMS) {
    if (item.phoneOnly || item.tab) continue;
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }
  return h(
    "section",
    { class: "screen" },
    page({ title: "Thêm" }),
    [...groups.entries()].map(([group, items]) =>
      section({
        title: group,
        card: false,
        children: list(
          items.map((item) => {
            const verdict = navVerdict(who, item);
            return listRow({
              href: `#${item.path}`,
              leading: item.icon,
              title: item.label,
              meta: item.hint,
              disabled: !verdict.allowed,
              reason: verdict.reason,
              data: { nav: item.path },
            });
          }),
          { label: group },
        ),
      }),
    ),
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/more",
  title: "Thêm",
  render: () => build(),
};
