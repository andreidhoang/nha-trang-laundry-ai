/**
 * The screen registry.
 *
 * One import per screen, one entry in `ROUTES`, and nothing else. Keeping the table here rather
 * than letting screens register themselves means the full set of reachable paths is readable in
 * one place and testable as data — a contract test asserts that every capability named below is a
 * real capability, and that every store-scoped screen declares `needsStore`.
 *
 * Order matters only for the `:orderId` pattern, which must not shadow a literal sibling. The
 * router matches on segment count and literal equality, so `/orders` and `/orders/:orderId` cannot
 * collide, but the ordering is kept deliberate anyway.
 *
 * @module screens/index
 */

import { screen as approvals } from "./approvals.js";
import { screen as exceptions } from "./exceptions.js";
import { screen as gaps } from "./gaps.js";
import { screen as incidents } from "./incidents.js";
import { screen as orderDetail } from "./orderDetail.js";
import { screen as orders } from "./orders.js";
import { screen as quotes } from "./quotes.js";
import { screen as shadow } from "./shadow.js";
import { screen as staff } from "./staff.js";
import { screen as system } from "./system.js";
import { screen as today } from "./today.js";

/** @type {import("../core/router.js").Route[]} */
export const ROUTES = [
  today,
  quotes,
  orders,
  orderDetail,
  approvals,
  shadow,
  exceptions,
  incidents,
  system,
  staff,
  gaps,
];
