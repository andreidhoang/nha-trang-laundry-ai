-- CONSENT-TRANSACTIONAL-001 (DEC-033): a STOP stops every message the shop initiates on that channel.
--
-- Before this, `consent_events` and `suppression_entries` could only name the MARKETING purpose, so
-- a customer who wrote STOP could still be sent a service message by staff: the manual send is
-- hardcoded TRANSACTIONAL and nothing modelled a transactional suppression to check.
--
-- Forward-only and additive:
--
-- 1. Both purpose CHECKs admit TRANSACTIONAL.
-- 2. `consent_events` gains a RELEASE event type, and the two facts only a release carries -- the
--    staff member who released it and the shop they released it for. Both are NULL on every other
--    event type and required on a release; a release is only ever TRANSACTIONAL. Marketing is not
--    released by this path: a marketing grant needs a real consent request (SECURITY spec §8.4).
-- 3. Every existing SUPPRESSED / PENDING_REVIEW_BLOCKED MARKETING row gets a TRANSACTIONAL twin
--    citing the same source consent event. The conservative reading: a withdrawal recorded before
--    purposes were separated is read as the withdrawal of everything the shop initiates.
-- 4. The 0008 projection guard is replaced, not loosened for marketing. Everything it refused it
--    still refuses, with exactly one exception: a TRANSACTIONAL row may leave SUPPRESSED (or
--    PENDING_REVIEW_BLOCKED / UNKNOWN_BLOCKED) for CLEAR only when the row's new source is a RELEASE
--    consent event for the same contact, purpose and channel whose evidence is an inbound message
--    from that contact, on that channel, received after the event the row was suppressed by.

ALTER TABLE consent_events DROP CONSTRAINT consent_events_purpose_check;
ALTER TABLE consent_events ADD CONSTRAINT consent_events_purpose_check
    CHECK (purpose IN ('MARKETING', 'TRANSACTIONAL'));

ALTER TABLE consent_events DROP CONSTRAINT consent_events_event_type_check;
ALTER TABLE consent_events ADD CONSTRAINT consent_events_event_type_check
    CHECK (event_type IN (
        'WITHDRAW', 'PENDING_REVIEW_BLOCK', 'POLICY_UNAVAILABLE_BLOCK', 'RELEASE'
    ));

ALTER TABLE consent_events ADD COLUMN released_by_staff_id UUID NULL REFERENCES staff_users(id);
ALTER TABLE consent_events ADD COLUMN released_for_store_id UUID NULL REFERENCES stores(id);

ALTER TABLE consent_events ADD CONSTRAINT consent_events_release_shape CHECK (
    (event_type = 'RELEASE'
     AND purpose = 'TRANSACTIONAL'
     AND released_by_staff_id IS NOT NULL
     AND released_for_store_id IS NOT NULL)
    OR (event_type <> 'RELEASE'
        AND released_by_staff_id IS NULL
        AND released_for_store_id IS NULL)
);

ALTER TABLE suppression_entries DROP CONSTRAINT suppression_entries_purpose_check;
ALTER TABLE suppression_entries ADD CONSTRAINT suppression_entries_purpose_check
    CHECK (purpose IN ('MARKETING', 'TRANSACTIONAL'));

-- The backfill. `ON CONFLICT DO NOTHING` is belt and braces: no TRANSACTIONAL row could exist before
-- the CHECK above admitted one.
INSERT INTO suppression_entries (
    contact_binding_id, purpose, channel, state, source_consent_event_id, row_version, updated_at
)
SELECT contact_binding_id, 'TRANSACTIONAL', channel, state, source_consent_event_id, 1, updated_at
FROM suppression_entries
WHERE purpose = 'MARKETING' AND state IN ('SUPPRESSED', 'PENDING_REVIEW_BLOCKED')
ON CONFLICT (contact_binding_id, purpose, channel) DO NOTHING;

CREATE OR REPLACE FUNCTION enforce_suppression_projection_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    released BOOLEAN := FALSE;
BEGIN
    IF NEW.contact_binding_id IS DISTINCT FROM OLD.contact_binding_id
       OR NEW.purpose IS DISTINCT FROM OLD.purpose
       OR NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.row_version <> OLD.row_version + 1
       OR NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'invalid suppression projection update';
    END IF;

    -- A TRANSACTIONAL block is lifted only by a verified release. Checked for every blocked state,
    -- not only SUPPRESSED, so an ambiguous opt-out cannot be cleared by an ordinary CLEAR write.
    IF NEW.purpose = 'TRANSACTIONAL' AND OLD.state <> 'CLEAR' AND NEW.state = 'CLEAR' THEN
        SELECT EXISTS (
            SELECT 1
            FROM consent_events release
            JOIN webhook_events evidence ON evidence.id = release.evidence_webhook_id
            JOIN consent_events blocked ON blocked.id = OLD.source_consent_event_id
            WHERE release.id = NEW.source_consent_event_id
              AND release.id <> OLD.source_consent_event_id
              AND release.event_type = 'RELEASE'
              AND release.purpose = 'TRANSACTIONAL'
              AND release.contact_binding_id = NEW.contact_binding_id
              AND release.channel = NEW.channel
              AND evidence.contact_binding_id = NEW.contact_binding_id
              AND evidence.channel = NEW.channel
              AND evidence.received_at > blocked.occurred_at
        ) INTO released;
        IF NOT released THEN
            RAISE EXCEPTION 'invalid suppression projection update';
        END IF;
        RETURN NEW;
    END IF;

    -- Unchanged from 0008 for everything else: a withdrawal is never undone.
    IF OLD.state = 'SUPPRESSED' AND NEW.state <> 'SUPPRESSED' THEN
        RAISE EXCEPTION 'invalid suppression projection update';
    END IF;
    RETURN NEW;
END;
$$;
