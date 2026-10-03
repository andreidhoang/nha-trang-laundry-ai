from pathlib import Path

import pytest
from nha_trang_laundry_db.migrations import discover_migrations


def test_discovers_forward_only_transaction_foundation() -> None:
    migrations = discover_migrations()

    assert [(migration.version, migration.name) for migration in migrations] == [
        ("0001", "transaction_foundation"),
        ("0002", "configuration_publication"),
        ("0003", "staff_identity"),
        ("0004", "staff_session_idle_timeout"),
        ("0005", "quote_snapshots"),
        ("0006", "quote_snapshot_constraints"),
        ("0007", "operations_control"),
        ("0008", "operations_constraints"),
        ("0009", "agent_run_ledger"),
        ("0010", "agent_run_binding"),
        ("0011", "manual_send_integrity"),
        ("0012", "automation_execution_gates"),
        ("0013", "quote_acknowledgment_evidence"),
        ("0014", "customer_incidents"),
        ("0015", "order_request_drafts"),
        ("0016", "worker_claim_leases"),
        ("0017", "worker_claim_lease_invariants"),
        ("0018", "human_approval_decision_evidence"),
        ("0019", "outbox_trace_context"),
        ("0020", "channel_envelope"),
        ("0021", "shadow_console"),
        ("0022", "consent_egress_guard"),
        ("0023", "retention_control"),
        ("0024", "store_assignment_revocation"),
        ("0025", "order_settlement"),
        ("0026", "assistant_turns"),
        ("0027", "assistant_transcript_retention"),
        ("0028", "agent_draft_review_keyset"),
        ("0029", "quote_approval_integrity"),
        ("0030", "finalize_quote_action"),
        ("0031", "quote_acceptance"),
        ("0032", "counter_ticket"),
        ("0033", "delivery_leg"),
        ("0034", "approval_store_binding"),
        ("0035", "store_registry"),
        ("0036", "order_acquisition_source"),
        ("0037", "production_ready_clock"),
        ("0038", "disposable_payload_store"),
        ("0039", "incident_evidence_store"),
        ("0040", "assistant_transcript_store"),
        ("0041", "keyed_commitments"),
        ("0042", "remedy_proposals"),
        ("0043", "range_price_proposals"),
        ("0044", "ops_board_export"),
        # No 0045: it was reserved for the remedy fixes, which turned out to need no schema change.
        ("0046", "order_refunds"),
        ("0047", "order_lookup"),
        ("0048", "prepaid_dropoff"),
        ("0049", "store_scoped_send_records"),
        ("0050", "agent_run_authority"),
        ("0051", "remedy_loss_to_owner"),
        ("0052", "remedy_garment_identity"),
        ("0053", "transactional_suppression"),
        ("0054", "export_range"),
        ("0055", "customer_records"),
        ("0056", "order_payments"),
        ("0057", "order_promise"),
        ("0058", "shop_capture"),
        ("0059", "account_customers"),
        # UNCLAIMED-001: 0060 is the number reserved for this slice; 0059 is another wave-2 slice's.
        ("0060", "unclaimed_laundry"),
        # Round 7 wave 2 integration: a storage fee fixed by the account charge the goods left on.
        ("0061", "account_storage_fee"),
        # AUTHZ-LIFECYCLE-001: an ID token is exchanged for at most one staff session.
        ("0062", "identity_token_single_use"),
        # Round 8, renumbered after 0062 above reached main first (numbers were reserved as
        # 0062-0064 before it did): EINVOICE-REQUEST-001, LATE-CREDIT-002, PICKUP-REMIND-001.
        ("0063", "invoice_requests"),
        ("0064", "late_delivery_decisions"),
        ("0065", "reminder_steps"),
        # Round 9, MONEY-LIFECYCLE-009: a fee part already paid stays owed-for (M1), a hold
        # pauses the fee (DEC-047), a cancellation voids, nets or reissues credits (DEC-045/046).
        ("0066", "money_lifecycle"),
        # Round 9: 0066 is reserved for MONEY-LIFECYCLE-009 (slice A); GOODS-AND-DRAWER-009 records
        # how a refund's money went back (review M4).
        ("0067", "refund_method"),
        # Round 9 (numbers reserved per slice; 0066/0067 are slices A and B): INVOICE-TRUTH-009.
        ("0068", "invoice_snapshots"),
        # Round 9 slice H (OPS-OBSERVABILITY-009, review P7); 0066-0069 are reserved for A, B, C, G.
        ("0070", "event_aggregate_id_indexes"),
        # Round 9b, MONEY-RESIDUAL-009B (J6a): an issued invoice kept at its printed figure.
        ("0071", "invoice_printed_total"),
        # Round 9b (0071 is reserved for MONEY-RESIDUAL-009B): CASH-COUNT-009, DEC-049.
        ("0072", "cash_count"),
    ]
    assert all(len(migration.checksum) == 64 for migration in migrations)


def test_rejects_invalid_migration_filename(tmp_path: Path) -> None:
    (tmp_path / "not_a_migration.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid migration filename"):
        discover_migrations(tmp_path)
