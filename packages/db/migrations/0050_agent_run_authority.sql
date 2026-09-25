-- AGENT-SHADOW-DEFECTS-001: two facts about an agent run that only the server may establish.
--
-- 1. Data classification is derived from the source of the run's input (F5).
--
--    Since 0009 `agent_runs.data_classification` has been a CHECK-constrained label the enqueuer
--    chose, and three separate gates trusted it: the runner's real-customer refusal, the Tool
--    Facade's verifier, and the facade's synthetic-only policy. An enqueuer that wrote SYNTHETIC on
--    a real customer's message unlocked all three at once.
--
--    The value is now computed here, on insert, from facts the enqueuer cannot phrase:
--      * a run sourced from an inbound provider event is a real customer's input;
--      * a run for a contact that has a provider identity binding, or that has ever sent an
--        inbound provider event, concerns a real customer whatever its source;
--      * a run bound to a public order locator refers to a real order.
--    Anything else is SYNTHETIC. Whatever the INSERT supplied is overwritten, not compared: a
--    classification is not an input. The existing transition guard (0009/0010) already forbids
--    changing it afterwards.

CREATE FUNCTION derive_agent_run_data_classification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.source_webhook_event_id IS NOT NULL
       OR NEW.public_code IS NOT NULL
       OR EXISTS (
           SELECT 1 FROM contact_channel_bindings AS binding
           WHERE binding.contact_binding_id = NEW.contact_binding_id
       )
       OR EXISTS (
           SELECT 1 FROM webhook_events AS inbound
           WHERE inbound.contact_binding_id = NEW.contact_binding_id
       ) THEN
        NEW.data_classification := 'REAL_CUSTOMER';
    ELSE
        NEW.data_classification := 'SYNTHETIC';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER agent_runs_derive_data_classification
    BEFORE INSERT ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION derive_agent_run_data_classification();

-- 2. Every Tool Facade invocation is admitted once, against its run, under a per-run limit (F7).
--
--    The runner mints a fresh single-use bearer for each tool call. The facade verified the
--    signature and the time window but remembered nothing, so a captured bearer could be replayed
--    for its whole lifetime, and `x-agent-tool.max_calls_per_run` was enforced only inside the
--    runner's own process. One row per admitted bearer: the primary key refuses a replayed `jti`,
--    and the facade counts rows per (run, operation) under a transaction-scoped lock before
--    admitting another. Nothing but identifiers and timestamps is stored; no argument, no result.

CREATE TABLE agent_facade_invocations (
    jti UUID PRIMARY KEY,
    agent_run_id UUID NOT NULL REFERENCES agent_runs(id),
    operation_id TEXT NOT NULL CHECK (operation_id IN (
        'catalogResolve', 'orderRequestCreate', 'orderRequestRecordCustomerFacts',
        'quoteEstimate', 'deliveryEvaluate', 'capacityCheck', 'messageDraftCreate',
        'publicOrderStatusGet', 'incidentOpen', 'approvalRequestCreate'
    )),
    bearer_expires_at TIMESTAMPTZ NOT NULL,
    admitted_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX agent_facade_invocations_run_idx
    ON agent_facade_invocations (agent_run_id, operation_id);

CREATE TRIGGER agent_facade_invocations_append_only
    BEFORE UPDATE OR DELETE ON agent_facade_invocations
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
