/**
 * Thêm: every destination the phone's five-slot tab bar has no room for, grouped by the kind of
 * work, each with one line saying what it is for (spec V2 §3.2).
 *
 * CONSOLE-SHELL-009 (C6): the groups list what this person can open, and nothing else, so a new
 * member of staff reads their own job and not a wall of shut doors. A destination their role may
 * not open is still never silently absent (V1 invariant 5): it is listed under the closed "Cần
 * quyền khác" at the bottom, disabled, with whom to ask as visible text ("Chỉ Chủ / quản trị, …");
 * the full reason is the row's `title`, and tapping the row opens the route's guard screen, which
 * prints it in full. The table is `core/nav.js`, the same one the desktop sidebar renders, so the
 * two cannot drift -- on a desk this screen is "Tất cả màn hình" at the end of the sidebar's "Khác".
 *
 * Reads nothing from the server and writes nothing.
 *
 * @module screens/more
 */

import { h } from "../core/dom.js";
import { FOLD_GROUP, navPlan } from "../core/nav.js";
import { principal } from "../core/session.js";
import { list, listRow, page, section } from "../ui/kit.js";

/**
 * @param {(typeof import("../core/nav.js").NAV_ITEMS)[number]} item
 * @returns {HTMLElement}
 */
function openRow(item) {
  return listRow({
    href: `#${item.path}`,
    leading: item.icon,
    title: item.label,
    meta: item.hint,
    data: { nav: item.path },
  });
}

/**
 * A shut destination: disabled, whom to ask in words, and still a way in -- the guard screen prints
 * the whole reason, as a deep link does.
 *
 * @param {{item: (typeof import("../core/nav.js").NAV_ITEMS)[number], verdict: import("../core/nav.js").NavVerdict}} entry
 * @returns {HTMLElement}
 */
function deniedRow({ item, verdict }) {
  return h(
    "a",
    {
      class: "row-link",
      href: `#${item.path}`,
      title: `${item.label} — ${verdict.reason}`,
      "aria-disabled": "true",
      dataNavDenied: item.path,
    },
    listRow({
      leading: item.icon,
      title: item.label,
      meta: item.hint,
      disabled: true,
      reason: verdict.short || verdict.reason,
      data: { nav: item.path },
    }),
  );
}

function build() {
  const { shown, denied } = navPlan(principal());
  /** @type {Map<string, HTMLElement[]>} */
  const groups = new Map();
  for (const { item, tab } of shown) {
    // The tab bar already holds this person's tabs; "Tất cả màn hình" is this screen.
    if (item.phoneOnly || item.deskOnly || tab) continue;
    const group = item.fold ? FOLD_GROUP : item.group;
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)?.push(openRow(item));
  }
  return h(
    "section",
    { class: "screen" },
    page({ title: "Thêm" }),
    [...groups.entries()].map(([group, rows]) =>
      section({ title: group, card: false, children: list(rows, { label: group }) }),
    ),
    denied.length
      ? h(
          "details",
          { class: "more-denied", dataNavDeniedGroup: "true" },
          h("summary", { class: "more-denied__summary" }, `Cần quyền khác (${denied.length})`),
          list(denied.map(deniedRow), { label: "Cần quyền khác" }),
        )
      : null,
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/more",
  title: "Thêm",
  render: () => build(),
};
