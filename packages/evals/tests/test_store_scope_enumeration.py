"""Every internal route is classified for store scope, and the classification is checked.

`STORE-SCOPING-001` enumerated store-scoped routes by URL shape — every path containing
`/stores/{store_id}/`. `POST /internal/v1/orders/{order_id}/transition` is keyed by `order_id`, so
it was not in that list and went unfixed: any principal with an operations role and MFA could drive
any order's commercial state in any store. That is a cross-store *write*, and it was found by a
person reading the route table against the repository, not by a test.

That is the finding this module exists for. Hand enumeration by URL shape will miss the next route
keyed by something other than `store_id` too, so the enumeration is made mechanical here:

  * every `/internal/` route must appear in `ROUTE_SCOPE` — a new route fails this module until
    somebody states what its store scope is, which is a decision rather than an oversight;
  * every route classified `STORE_SCOPED` must name a repository method that really calls
    `require_store_membership`, verified by reading the source, so deleting the call fails the
    build;
  * the routes that are deliberately not membership-checked are named here with the item or
    decision that owns them, so they cannot be silently ratified by absence.

The check reads source rather than behaviour on purpose. A behavioural test proves one path is
guarded on the day it is written; this proves the guard is still written down in every method that
claims one, which is the property that decays.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from nha_trang_laundry_api.main import app

ROOT = Path(__file__).resolve().parents[3]
DB_SOURCE = ROOT / "packages/db/src/nha_trang_laundry_db"
MEMBERSHIP_CALL = "require_store_membership"
# Everywhere application SQL lives. Scripts are included because the demo seed writes assignments.
SOURCE_ROOTS_WITH_SQL = (DB_SOURCE, ROOT / "apps", ROOT / "scripts")

Classification = Literal["STORE_SCOPED", "GLOBAL_BY_DESIGN", "NOT_STORE_DATA", "KNOWN_GAP"]


@dataclass(frozen=True)
class RouteScope:
    """What this route does about store membership, and where that is enforced or recorded."""

    classification: Classification
    # (module stem, ClassName.method) whose source must contain the membership call.
    enforced_in: tuple[str, str] | None
    note: str


def store_scoped(module: str, method: str) -> RouteScope:
    return RouteScope("STORE_SCOPED", (module, method), "membership enforced in the repository")


#: Every `/internal/` route, and what it does about store scope. Keyed by (method, path).
ROUTE_SCOPE: dict[tuple[str, str], RouteScope] = {
    # --- store-scoped: the repository must require membership -------------------------------
    ("POST", "/internal/v1/stores/{store_id}/orders"): store_scoped(
        "orders", "OrderRepository.create"
    ),
    ("GET", "/internal/v1/stores/{store_id}/orders"): store_scoped(
        "orders", "OrderRepository.list_for_store"
    ),
    # DEC-029's owner review: membership of the named store, then every entry through `read`.
    ("GET", "/internal/v1/stores/{store_id}/range-price-reviews"): store_scoped(
        "range_prices", "RangePriceProposalRepository.list_for_store_day"
    ),
    ("POST", "/internal/v1/orders/{order_id}/transition"): store_scoped(
        "orders", "OrderRepository.transition"
    ),
    # ORDER-LOOKUP-001. Keyed by `order_id` like the transition, so its store is read from the row
    # and membership is required against that store; a non-member gets the missing-order answer.
    ("GET", "/internal/v1/orders/{order_id}"): store_scoped(
        "orders", "OrderRepository.read_for_principal"
    ),
    # Intake and production move through the same locked row and the same guard as the
    # commercial transition above; `OperationsService` only chooses which target it carries. They
    # are keyed by `order_id` for the same reason and would have gone unclassified for the same
    # reason -- which is what this module's enumeration exists to make impossible.
    ("POST", "/internal/v1/orders/{order_id}/intake-transition"): store_scoped(
        "orders", "OrderRepository.transition"
    ),
    ("POST", "/internal/v1/orders/{order_id}/production-transition"): store_scoped(
        "orders", "OrderRepository.transition"
    ),
    # ORDER-STEPS-001. A composite business step is a sequence of the transitions above, keyed by
    # `order_id` the same way: membership of the row's store before the idempotency lookup and
    # again on the cursor holding the row lock, inside the repository.
    ("POST", "/internal/v1/orders/{order_id}/steps"): store_scoped(
        "orders", "OrderRepository.execute_step"
    ),
    ("POST", "/internal/v1/orders/{order_id}/settlement"): store_scoped(
        "settlement", "SettlementRepository.record"
    ),
    # PROMISE-001. Hẹn lại is keyed by `order_id`: membership of the row's store before the
    # idempotency lookup and again on the cursor holding the row lock, inside the repository.
    ("POST", "/internal/v1/orders/{order_id}/promise"): store_scoped(
        "order_promises", "OrderPromiseRepository.change"
    ),
    ("GET", "/internal/v1/orders/{order_id}/promise"): RouteScope(
        "STORE_SCOPED",
        None,
        "keyed by order_id. OrderPromiseRepository.read reads the store off the order row and "
        "answers OrderNotVisibleError (404) unless the caller is a member of it -- the order "
        "read's rule, so a non-member cannot tell a stranger's order from a missing one; asserted "
        "behaviourally in apps/api/tests/test_order_promise_http.py",
    ),
    # PREPAID-DROPOFF-001 (`DEC-032`). Keyed by order_id, like the settlement it completes, so a
    # URL-shape enumeration would miss it: the store is read from the locked order row and
    # membership required on that cursor, inside the repository.
    ("POST", "/internal/v1/orders/{order_id}/collection"): store_scoped(
        "settlement", "SettlementRepository.record_collection"
    ),
    ("GET", "/internal/v1/stores/{store_id}/settlements/today"): store_scoped(
        "settlement", "SettlementRepository.collected_today"
    ),
    ("POST", "/internal/v1/stores/{store_id}/quotes"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.create_quote before any pricing work, and "
        "outside the idempotency wrapper so a revoked member cannot replay a key; asserted "
        "behaviourally in apps/api/tests/test_quote_command.py",
    ),
    ("POST", "/internal/v1/orders/{order_id}/delivery-legs"): RouteScope(
        "STORE_SCOPED",
        None,
        "keyed by order_id, so a URL-shape enumeration would miss it. Membership is enforced "
        "inside DeliveryLegRepository.record on the same cursor that locks the order row, "
        "against the store_id read from that row",
    ),
    ("POST", "/internal/v1/stores/{store_id}/counter-tickets"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced inside CounterTicketRepository.issue on the same cursor as the "
        "insert, and the ticket is written with the store_id it was issued for, so a ticket "
        "cannot be issued for or used by another store",
    ),
    ("POST", "/internal/v1/stores/{store_id}/quotes/{quote_id}/acceptance"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.accept_quote before the revision is read, "
        "and the quote row is selected with store_id in the predicate so a quote in another "
        "store cannot be accepted even by a member of this one",
    ),
    ("GET", "/internal/v1/stores/{store_id}/quotes"): store_scoped(
        "quotes", "QuoteRepository.list_for_store"
    ),
    ("GET", "/internal/v1/stores/{store_id}/quotes/{quote_id}"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.read_quote before the container is located, "
        "and QuoteRepository.find_container_by_id carries store_id in its predicate, so another "
        "store's quote id is indistinguishable from one that does not exist",
    ),
    ("POST", "/internal/v1/stores/{store_id}/quotes/{quote_id}/range-prices"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.propose_range_prices before the band is read, "
        "and again inside ApprovalRepository.request; the approval is written with the store it "
        "belongs to, which is what ApprovalRepository.decide checks membership against",
    ),
    (
        "POST",
        "/internal/v1/stores/{store_id}/quotes/{quote_id}/range-prices/{approval_id}",
    ): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.apply_range_prices before the approval or the "
        "revision is read, and _require_range_price_approval refuses an envelope whose own "
        "store_id is not this store, so an approval raised elsewhere cannot price this quote",
    ),
    ("POST", "/internal/v1/stores/{store_id}/incidents"): store_scoped(
        "incidents", "IncidentRepository.open"
    ),
    ("GET", "/internal/v1/stores/{store_id}/incidents"): store_scoped(
        "incidents", "IncidentRepository.list_for_store"
    ),
    # READ-ENRICH-001. Membership of the named store, then the store in the predicate, so another
    # store's order or incident id reads as nothing -- the same answer as an id that does not exist.
    ("GET", "/internal/v1/stores/{store_id}/orders/{order_id}/incidents"): store_scoped(
        "incidents", "IncidentRepository.list_for_order"
    ),
    ("GET", "/internal/v1/stores/{store_id}/incidents/{incident_id}"): store_scoped(
        "incidents", "IncidentRepository.read_for_store"
    ),
    # --- REMEDY-001 --------------------------------------------------------------------------
    ("GET", "/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-options"): (
        store_scoped("remedies", "RemedyProposalRepository.options")
    ),
    ("POST", "/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals"): (
        store_scoped("remedies", "RemedyProposalRepository.propose")
    ),
    ("POST", "/internal/v1/remedy-proposals/{proposal_id}/execution"): RouteScope(
        "STORE_SCOPED",
        ("remedies", "RemedyProposalRepository.execute"),
        "keyed by proposal_id, so a URL-shape enumeration would miss it exactly as it missed the "
        "order transition. The store comes from the proposal row the method has already locked "
        "FOR UPDATE, never from the request, and membership is required on that same cursor while "
        "the lock is held -- so a concurrently revoked assignment cannot be raced past it. This "
        "route pays money to a customer, which is why it is keyed by the artefact that carries the "
        "approval rather than by a store a caller could name.",
    ),
    ("POST", "/internal/v1/stores/{store_id}/quotes/{quote_id}/remedy-credits"): RouteScope(
        "STORE_SCOPED",
        ("remedies", "RemedyCreditRepository.redeem"),
        "membership is required before the credit or the quote is read, and both lookups carry "
        "store_id in their predicates: the credit is selected WHERE id = %s AND store_id = %s and "
        "the quote through QuoteRepository.find_container_by_id. A credit issued by one shop "
        "therefore cannot be spent at another, and a credit id from another store is "
        "indistinguishable from one that does not exist. The credit is a bearer instrument across "
        "customers by design (DEC-015) but never across shops.",
    ),
    # --- READ-PATHS-001 -----------------------------------------------------------------------
    ("GET", "/internal/v1/stores/{store_id}/staff"): RouteScope(
        "STORE_SCOPED",
        ("staff_directory", "StaffDirectoryRepository.list_for_store"),
        "the owner's staff directory. Owner-gated at the route (`require_owner`, the gate every "
        "staff write uses); the repository re-reads the owner role from the database, requires "
        "MFA, and then requires membership of the named store -- an owner is not implicitly a "
        "member of every store, so a directory of a shop the owner does not work in is refused "
        "with the same opaque 403 as an unknown store or a wrong role. Lists only rows of "
        "staff_store_assignments carrying that store_id.",
    ),
    ("GET", "/internal/v1/stores/{store_id}/orders/{order_id}/remedy-credits"): RouteScope(
        "STORE_SCOPED",
        ("remedy_reads", "RemedyReadRepository.list_order_credits"),
        "membership of the named store is required before anything is read, then the order is "
        "located WHERE id = %s AND store_id = %s and the credits WHERE store_id = %s AND "
        "issued_from_order_id = %s, so another store's order answers exactly as a missing one "
        "(404) and no credit crosses a shop.",
    ),
    ("GET", "/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals"): RouteScope(
        "STORE_SCOPED",
        ("remedy_reads", "RemedyReadRepository.list_incident_proposals"),
        "membership of the named store first; the incident is located with store_id in its "
        "predicate and the proposals selected WHERE store_id = %s AND incident_id = %s, so "
        "another store's incident is indistinguishable from one that does not exist.",
    ),
    # --- CREDIT-PICK-001 / CONTACT-PICK-001 ----------------------------------------------------
    ("GET", "/internal/v1/stores/{store_id}/remedy-credits"): RouteScope(
        "STORE_SCOPED",
        ("remedy_reads", "RemedyReadRepository.list_store_credits"),
        "role and MFA, then membership of the named store, before anything is read; the credits "
        "are selected WHERE c.store_id = %(store)s AND c.redeemed_at IS NULL, the issuing order "
        "joined with the credit's own store and its ticket with the order's, so no credit and no "
        "ticket number of another shop can appear. The credit is a bearer instrument across "
        "customers (DEC-015) but never across shops, and the row carries no contact field.",
    ),
    ("GET", "/internal/v1/stores/{store_id}/contacts/recent"): RouteScope(
        "STORE_SCOPED",
        ("recent_contacts", "RecentContactRepository.list_for_store"),
        "role and MFA, then membership of the named store; a binding is listed only when an "
        "order_requests or orders row WHERE store_id = %(store)s names it, and every per-row read "
        "carries the same store predicate. A binding served only by another store, or by none, is "
        "indistinguishable from one that does not exist. No handle and no message text is read.",
    ),
    # --- REMEDY-OWNER-DECIDE-001 --------------------------------------------------------------
    (
        "GET",
        "/internal/v1/stores/{store_id}/remedy-proposals/{proposal_id}/approval-binding",
    ): RouteScope(
        "STORE_SCOPED",
        ("remedy_reads", "RemedyReadRepository.read_approval_binding"),
        "the owner's read of one APPROVE_REMEDY envelope. The deciding role and MFA first, then "
        "membership of the named store, then the proposal selected WHERE id = %s AND store_id = "
        "%s with its envelope's own store_id required to equal it -- so another store's proposal "
        "answers exactly as a missing one (404) after the caller was proved a member of the store "
        "they named, and an unknown store is the same opaque 403 as a store the caller is not in.",
    ),
    # --- OPS-BOARD-001 ------------------------------------------------------------------------
    ("GET", "/internal/v1/stores/{store_id}/sla-board"): store_scoped(
        "shadow_console", "ShadowConsoleRepository.sla_risk_board"
    ),
    ("GET", "/internal/v1/stores/{store_id}/day-summary"): RouteScope(
        "STORE_SCOPED",
        ("assistant", "today_status_counts"),
        "membership enforced inside `today_status_counts`, which calls require_store_membership on "
        "the caller's own cursor before the COUNT runs. It is a module-level function rather than "
        "a repository method, and the first version of this entry opted out of the mechanical "
        "check for that reason and paid for it with a prose claim -- which is the arrangement this "
        "module exists to make impossible. `method_source` resolves a module-level function too "
        "now, so deleting the call fails the build here exactly as it would in a class. The route "
        "gate is additionally require_operations_staff, which is the same role set the function "
        "checks plus MFA.",
    ),
    # --- REPORT-DASHBOARD-001 ------------------------------------------------------------------
    ("GET", "/internal/v1/stores/{store_id}/reports/summary"): RouteScope(
        "STORE_SCOPED",
        ("reports", "ReportRepository.store_report"),
        "role and MFA first (REPORT_READ_ROLES, the same set `require_report_reader` gates on), "
        "then require_store_membership on the report's own cursor before either statement runs; "
        "every fact in both statements is selected WHERE store_id = %(store)s (transitions are "
        "joined to orders of that store), so no figure crosses a shop and an unknown store is the "
        "same opaque 403 as a store the caller is not in.",
    ),
    ("GET", "/internal/v1/stores/{store_id}/reports/daily"): RouteScope(
        "STORE_SCOPED",
        ("reports", "ReportRepository.store_report"),
        "the same repository call as the summary -- one statement yields the per-day rows and the "
        "window row -- so it carries the same membership check and the same store predicate.",
    ),
    ("POST", "/internal/v1/stores/{store_id}/exports"): store_scoped(
        "exports", "SanitizedExportRepository.request"
    ),
    ("POST", "/internal/v1/stores/{store_id}/exports/{export_request_id}/execution"): RouteScope(
        "STORE_SCOPED",
        ("exports", "SanitizedExportRepository.execute"),
        "keyed by export_request_id as well as store_id, and the path store is NOT what is "
        "checked: the repository locks the export_requests row, reads the store off it, and "
        "requires membership on that same cursor while the lock is held. The path value is passed "
        "down as an assertion only, and a URL naming a different shop is refused before any file "
        "exists. This route moves the shop's own records out of the system, which is why it is "
        "keyed by the artefact carrying the owner's approval.",
    ),
    ("GET", "/internal/v1/stores/{store_id}/shadow/drafts"): store_scoped(
        "shadow_console", "ShadowConsoleRepository.list_pending_drafts"
    ),
    # MESSAGE-DRAFT-BINDING-001. The words a SEND_MESSAGE envelope binds, with its digests. A
    # module-level function rather than a repository method, checked mechanically all the same
    # (MANUAL-SEND-RESUME: it now also returns the latest send's progress, read after the check):
    # role and MFA, then membership of the named store, then a draft of another store answers None.
    ("GET", "/internal/v1/stores/{store_id}/message-drafts/{agent_run_id}/binding"): (
        store_scoped("message_drafts", "read_message_draft_send_state_for_store")
    ),
    # CONSENT-TRANSACTIONAL-001 (DEC-033). A contact's TRANSACTIONAL state and the messages a
    # release may cite, and the release itself. Module-level functions, checked mechanically: role
    # and MFA, then membership of the named store (unknown store = the same opaque 403), then the
    # contact's footprint in that store -- a draft, an intake request or an order -- so a contact
    # another shop dealt with answers exactly as one that does not exist (404).
    ("GET", "/internal/v1/stores/{store_id}/contacts/{contact_binding_id}/service-messaging"): (
        store_scoped("transactional_consent", "read_service_messaging_state")
    ),
    (
        "POST",
        "/internal/v1/stores/{store_id}/contacts/{contact_binding_id}/service-messaging/release",
    ): store_scoped("transactional_consent", "release_transactional_suppression"),
    ("GET", "/internal/v1/stores/{store_id}/shadow/reviews"): store_scoped(
        "shadow_console", "ShadowConsoleRepository.list_reviewed_drafts"
    ),
    ("POST", "/internal/v1/stores/{store_id}/assistant/turns"): store_scoped(
        "assistant", "AssistantTurnRepository.record_turn"
    ),
    ("GET", "/internal/v1/stores/{store_id}/assistant/turns"): store_scoped(
        "assistant", "AssistantTurnRepository.list_recent"
    ),
    ("GET", "/internal/v1/stores/{store_id}/assistant/turns/{turn_id}/stream"): store_scoped(
        "assistant", "AssistantTurnRepository.get_scoped"
    ),
    ("POST", "/internal/v1/stores/{store_id}/order-requests"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.create_order_request before any write, and "
        "outside the idempotency wrapper so a revoked member cannot replay a key; asserted "
        "behaviourally in apps/api/tests/test_intake_order_requests.py",
    ),
    ("GET", "/internal/v1/stores/{store_id}/order-requests"): store_scoped(
        "intake", "OrderRequestRepository.list_for_store"
    ),
    ("GET", "/internal/v1/stores/{store_id}/order-requests/{order_request_id}"): store_scoped(
        "intake", "OrderRequestRepository.get_for_store"
    ),
    # --- deliberately not membership-checked, each owned by something ------------------------
    ("GET", "/internal/v1/stores/{store_id}/shadow/audit/{aggregate_id}"): RouteScope(
        "KNOWN_GAP",
        None,
        "Requires membership of the *path* store and then filters only on aggregate_id, so a "
        "member of store A can read store B's audit trail by supplying its aggregate identifier. "
        "Named in context/tasks/TASK-store-scoping-002.md as out of scope for that item and "
        "scoped to its own; recorded here so the enumeration does not report it as compliant.",
    ),
    # API-INTEGRITY-002 reversed the GLOBAL_BY_DESIGN classification these two carried. Its
    # premise -- "a receipt with no confirmed recipient has no store to scope to" -- confused the
    # recipient with the shop: the send was the shop's whatever happened to it, and a
    # human-approved send names its approval, which names its store. Migration 0049 records the
    # store on every receipt; an old receipt whose store cannot be derived stays unattributed and is
    # shown to nobody rather than to everybody.
    ("GET", "/internal/v1/stores/{store_id}/shadow/unknown-sends"): store_scoped(
        "shadow_console", "ShadowConsoleRepository.list_unknown_sends"
    ),
    ("POST", "/internal/v1/shadow/unknown-sends/{receipt_id}/reconcile"): RouteScope(
        "STORE_SCOPED",
        ("shadow_console", "ShadowConsoleRepository.resolve_unknown_send"),
        "keyed by receipt_id; the repository locks the receipt, reads its store and requires "
        "membership of it in the same transaction as the update. A missing, foreign or "
        "unattributed receipt is one opaque refusal.",
    ),
    ("POST", "/internal/v1/shadow/drafts/{agent_run_id}/decision"): RouteScope(
        "STORE_SCOPED",
        ("shadow_console", "ShadowConsoleRepository.decide_draft"),
        "keyed by agent_run_id; the repository resolves the run's store and requires membership",
    ),
    # --- not store data ----------------------------------------------------------------------
    ("POST", "/internal/v1/auth/session"): RouteScope(
        "NOT_STORE_DATA", None, "identity exchange; establishes who the caller is"
    ),
    ("POST", "/internal/v1/auth/logout"): RouteScope(
        "NOT_STORE_DATA", None, "ends the caller's own session"
    ),
    ("GET", "/internal/v1/session"): RouteScope(
        "NOT_STORE_DATA", None, "the caller's own principal"
    ),
    ("POST", "/internal/v1/sessions/{session_id}/revoke"): RouteScope(
        "NOT_STORE_DATA", None, "own session, or any session for OWNER_ADMIN"
    ),
    # SESSION-LIST-001. Sessions are a person's, not a shop's: a phone signed in as Lan is Lan's
    # in every store she works in, so neither read names or filters by a store.
    ("GET", "/internal/v1/sessions"): RouteScope(
        "NOT_STORE_DATA", None, "the caller's own live sessions; no secret or hash is returned"
    ),
    ("GET", "/internal/v1/staff/{staff_user_id}/sessions"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "one person's live sessions, OWNER_ADMIN only: `require_owner` at the route, and the "
        "identity repository re-reads the owner role from the database before reading a row",
    ),
    ("GET", "/internal/v1/stores"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "answers 'which stores am I in' and is derived from the caller's own assignments, so "
        "membership is the result rather than a precondition",
    ),
    ("POST", "/internal/v1/staff"): RouteScope(
        "NOT_STORE_DATA", None, "staff administration, OWNER_ADMIN only"
    ),
    ("POST", "/internal/v1/staff/{staff_user_id}/roles"): RouteScope(
        "NOT_STORE_DATA", None, "staff administration, OWNER_ADMIN only"
    ),
    ("POST", "/internal/v1/staff/{staff_user_id}/disable"): RouteScope(
        "NOT_STORE_DATA", None, "staff administration, OWNER_ADMIN only"
    ),
    ("POST", "/internal/v1/staff/{staff_user_id}/stores/{store_id}"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "STORE-ASSIGNMENT-001. Grants membership rather than consuming it, so requiring the "
        "grantor to already be a member would make the first assignment in a new deployment "
        "impossible and force the hand-written INSERT this item removed. Owner-gated at the route "
        "and re-checked against the database in the repository; the answer to 'may an owner assign "
        "into a store they are not a member of' is yes, and it is asserted in "
        "packages/db/tests/test_store_assignment.py so a later change to it fails a test.",
    ),
    ("DELETE", "/internal/v1/staff/{staff_user_id}/stores/{store_id}"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "STORE-ASSIGNMENT-001. The revoke side of the grant above, owner-gated the same way and "
        "audited the same way; membership is the thing being removed, not a precondition.",
    ),
    ("POST", "/internal/v1/approvals"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "approval envelopes are keyed by resource_type/resource_id and carry no store column; "
        "scoping them is part of the approval surface's own item, not silently assumed here",
    ),
    ("GET", "/internal/v1/approvals"): RouteScope(
        "NOT_STORE_DATA", None, "pending approval queue, gated by approval role with MFA"
    ),
    ("POST", "/internal/v1/approvals/{approval_id}/decisions"): RouteScope(
        "NOT_STORE_DATA", None, "decision on an approval envelope; see above"
    ),
    ("POST", "/internal/v1/approvals/{approval_id}/manual-send"): RouteScope(
        "NOT_STORE_DATA", None, "manual send envelope bound to an approval; see above"
    ),
    # --- RANGE-APPROVAL-VISIBILITY-001 -------------------------------------------------------
    ("GET", "/internal/v1/approvals/{approval_id}/export-request"): RouteScope(
        "STORE_SCOPED",
        ("exports", "SanitizedExportRepository.read_for_approval"),
        "keyed by approval_id and named by no store at all, so URL-shape enumeration would miss "
        "it -- the same shape as the range-price read above and for the same reason: the caller "
        "must not be able to name a store here. The store is read off the export_requests row the "
        "envelope points at and membership is required against that value on the same cursor, so "
        "an approval belonging to another shop is refused with the same opaque 403 as any other "
        "non-membership, and an approval that does not exist (or is not an export) is a 404. A "
        "caller cannot use the pair to discover which shop an approval belongs to. It is a read "
        "and authorises nothing: it exists so an owner can see the business date, the column list "
        "and the stated exclusions before the approve control on #/approvals is reachable, which "
        "is the standard RANGE-APPROVAL-VISIBILITY-001 set for this console. The one field about "
        "the caller, requested_by_you, is true only of the caller themselves and so discloses no "
        "other account.",
    ),
    ("GET", "/internal/v1/approvals/{approval_id}/range-price-proposal"): RouteScope(
        "STORE_SCOPED",
        ("range_prices", "RangePriceProposalRepository.read"),
        "keyed by approval_id, so a URL-shape enumeration would miss it exactly as it missed the "
        "order transition -- and this one returns money a customer will be charged, which is why "
        "it is keyed by the artefact carrying the approval rather than by a store a caller could "
        "name. The store is read off the proposal row and membership is required against that "
        "value on the same cursor, so an approval id from another shop is refused with the same "
        "opaque 403 as any other non-membership, and an id that does not exist is a 404: a caller "
        "cannot use the pair to discover which shop an approval belongs to. The row itself was "
        "written with the store the proposing staff member was already proved a member of.",
    ),
    ("POST", "/internal/v1/manual-sends/{manual_send_id}/attest"): RouteScope(
        "NOT_STORE_DATA", None, "attestation against a manual send envelope; see above"
    ),
    ("GET", "/internal/v1/queue-recovery"): RouteScope(
        "NOT_STORE_DATA", None, "process-wide queue counters; contains no customer data"
    ),
    ("GET", "/internal/v1/pricebook/services"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "the published pricebook is deployment-global configuration, not store data; the read "
        "carries no store column and no customer data, and is role-gated like the pricing "
        "surface it feeds",
    ),
}


def internal_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/internal/"):
            continue
        for verb in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            found.add((verb, path))
    return found


def method_source(module: str, qualified: str) -> ast.FunctionDef:
    """Return the AST of `Class.method`, or of a bare `function`, in `packages/db/.../{module}.py`.

    The bare form was added by `OPS-BOARD-FIX-001`. `today_status_counts` guards
    `GET /internal/v1/stores/{store_id}/day-summary` and is a module-level function, and because
    this resolver could only see methods the route was registered with `enforced_in=None` and a
    sentence explaining that the check did not apply to it. A route whose guarantee is prose is
    the thing this module was written to prevent, so the resolver learned the other shape instead.
    """
    class_name, _, method_name = qualified.rpartition(".")
    tree = ast.parse((DB_SOURCE / f"{module}.py").read_text(encoding="utf-8"))
    if not class_name:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == method_name:
                return node
        raise AssertionError(f"{module}.py has no function {qualified}")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{module}.py has no method {qualified}")


def called_names(node: ast.AST) -> set[str]:
    """Every simple name this node calls, whether bare, `self.x`, or `Class.x`."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        if isinstance(inner.func, ast.Name):
            names.add(inner.func.id)
        elif isinstance(inner.func, ast.Attribute):
            names.add(inner.func.attr)
    return names


def guard_names(module: str) -> frozenset[str]:
    """Names that amount to a membership check in this module, resolved transitively.

    A repository may call `require_store_membership` directly, or through a local helper that does.
    Following the indirection matters: `ShadowConsoleRepository` guards its methods through
    `_require_store_access`, and a checker that only looked for the direct call would report two
    correctly guarded methods as unguarded — which it did, before this existed.

    The fixpoint is deliberately confined to one module. A helper defined elsewhere would not be
    resolved, and that is the conservative direction to fail in.
    """
    tree = ast.parse((DB_SOURCE / f"{module}.py").read_text(encoding="utf-8"))
    functions: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = node

    guards = {MEMBERSHIP_CALL}
    changed = True
    while changed:
        changed = False
        for name, function in functions.items():
            if name not in guards and called_names(function) & guards:
                guards.add(name)
                changed = True
    return frozenset(guards)


def calls_membership_check(node: ast.AST, guards: frozenset[str] | None = None) -> bool:
    return bool(called_names(node) & (guards or frozenset({MEMBERSHIP_CALL})))


def test_every_internal_route_is_classified_for_store_scope() -> None:
    """A new route must be classified before it can ship.

    This is the check that would have caught `STORE-SCOPING-002` at the time the route was added:
    the transition route existed, was store-scoped in effect, and nothing forced anyone to say so.
    """
    live = internal_routes()
    classified = set(ROUTE_SCOPE)
    unclassified = live - classified
    stale = classified - live
    assert not unclassified, (
        "these routes have no store-scope classification; add them to ROUTE_SCOPE and state "
        f"whether membership applies: {sorted(unclassified)}"
    )
    assert not stale, f"ROUTE_SCOPE names routes that no longer exist: {sorted(stale)}"


@pytest.mark.parametrize(
    ("route", "scope"),
    [
        (route, scope)
        for route, scope in sorted(ROUTE_SCOPE.items())
        if scope.classification == "STORE_SCOPED" and scope.enforced_in is not None
    ],
)
def test_store_scoped_routes_enforce_membership_in_their_repository(
    route: tuple[str, str], scope: RouteScope
) -> None:
    assert scope.enforced_in is not None
    module, qualified = scope.enforced_in
    assert calls_membership_check(method_source(module, qualified), guard_names(module)), (
        f"{route[0]} {route[1]} is classified STORE_SCOPED but {module}.{qualified} does not call "
        f"{MEMBERSHIP_CALL}. A route is a place a check can be forgotten; the repository is the "
        "only path to the data, so the check belongs there."
    )


def test_the_transition_route_is_the_one_this_item_fixed() -> None:
    """Named explicitly, so a revert is a failing test rather than a silent regression.

    Rollback of this item restores a cross-store write path. That is a security regression, and it
    is recorded as one in the evidence for `STORE-SCOPING-002`.
    """
    assert calls_membership_check(
        method_source("orders", "OrderRepository.transition"), guard_names("orders")
    )


def test_the_enumeration_fails_when_a_membership_check_is_removed() -> None:
    """Prove the check has teeth, by deleting it from a copy of the source and re-running.

    A test that only ever sees compliant source cannot distinguish "the check is present" from
    "the checker is broken". This removes the call from the real `transition` source in memory and
    requires the detector to notice.
    """
    original = method_source("orders", "OrderRepository.transition")
    assert calls_membership_check(original)

    class _StripMembership(ast.NodeTransformer):
        def visit_Expr(self, node: ast.Expr) -> ast.Expr | None:
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == MEMBERSHIP_CALL
            ):
                return None
            return node

    stripped = _StripMembership().visit(ast.parse(ast.unparse(original)))
    assert not calls_membership_check(stripped), (
        "the detector reported a membership call in source it had just been removed from, so it "
        "would not fail if someone deleted the real one"
    )


def test_every_read_of_the_assignment_table_excludes_revoked_rows() -> None:
    """`STORE-ASSIGNMENT-001` made revocation soft, which is only correct if every reader filters.

    A revoked assignment is still a row. If one query anywhere forgets `revoked_at IS NULL`, a
    revoked staff member keeps access on exactly that path and nowhere else — the quietest possible
    security failure, and the same shape as the defect `STORE-SCOPING-002` closed: a check present
    in most places and missing in one.

    So the constraint is mechanical rather than remembered. Every SQL statement in the source that
    reads `staff_store_assignments` must also mention `revoked_at`, whether to filter it (reads) or
    to set it (the revoke itself). Adding a query without one fails here.
    """
    offenders: list[str] = []
    for root in SOURCE_ROOTS_WITH_SQL:
        for module in sorted(root.rglob("*.py")):
            # Application source only. A test may legitimately count every row including revoked
            # ones — proving a replay wrote no duplicate needs exactly that — and a test is not an
            # access path. What must never forget the filter is code that answers a request.
            if "tests" in module.parts:
                continue
            for statement in _assignment_reads(module.read_text(encoding="utf-8")):
                if "revoked_at" not in statement:
                    offenders.append(f"{module.name}: {' '.join(statement.split())[:110]}")
    assert not offenders, (
        "these statements read staff_store_assignments without filtering revoked_at, so a revoked "
        "assignment would still count as membership there:\n  " + "\n  ".join(offenders)
    )


def _assignment_reads(source: str) -> list[str]:
    """String literals that *read* the assignment table.

    Reads are the risk, so only `FROM` and `JOIN` count. An `INSERT` legitimately omits the column —
    a new assignment is active, and `revoked_at` defaults to NULL — and prose that happens to name
    the table is not a query. Both were false positives on the first version of this check.
    """
    reads: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        collapsed = " ".join(text.split()).lower()
        if "from staff_store_assignments" in collapsed or "join staff_store_assignments" in (
            collapsed
        ):
            reads.append(text)
    return reads


def test_known_gaps_and_global_routes_each_carry_a_reason() -> None:
    """Absence of a check must be a recorded decision, never an accident nobody noticed."""
    deliberate = {
        route: scope
        for route, scope in ROUTE_SCOPE.items()
        if scope.classification in {"KNOWN_GAP", "GLOBAL_BY_DESIGN"}
    }
    assert deliberate, "the two known cases must stay classified rather than quietly enforced"
    for route, scope in deliberate.items():
        assert len(scope.note) > 80, f"{route} is unscoped with no stated reason"

    audit = ROUTE_SCOPE[("GET", "/internal/v1/stores/{store_id}/shadow/audit/{aggregate_id}")]
    assert audit.classification == "KNOWN_GAP"
    assert "aggregate_id" in audit.note

    # API-INTEGRITY-002: the unknown-send queue is no longer an exception. Pinned in the stricter
    # direction, so parking it back on GLOBAL_BY_DESIGN fails here rather than passing silently.
    for route in (
        ("GET", "/internal/v1/stores/{store_id}/shadow/unknown-sends"),
        ("POST", "/internal/v1/shadow/unknown-sends/{receipt_id}/reconcile"),
    ):
        assert ROUTE_SCOPE[route].classification == "STORE_SCOPED"
        assert ROUTE_SCOPE[route].enforced_in is not None
