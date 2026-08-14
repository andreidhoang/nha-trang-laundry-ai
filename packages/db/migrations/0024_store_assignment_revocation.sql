-- STORE-ASSIGNMENT-001: give store membership a revocation, the way roles already have one.
--
-- `staff_store_assignments` had no way to end an assignment. There is no `revoked_at` column, and
-- `staff_store_assignments_no_hard_delete` rejects a DELETE, so once a staff member was assigned to
-- a store there was no supported way to un-assign them: not a route, not a repository method, not
-- even a direct DELETE. The only instrument was disabling the whole account, which is blunter than
-- the situation usually needs and loses the person's history.
--
-- The shape here is copied from `staff_role_assignments`, which solved the same problem in
-- migration 0003: a nullable `revoked_at`/`revoked_by` pair, with every read filtering
-- `revoked_at IS NULL`. Soft revocation rather than deletion keeps who granted what and when, which
-- is the record an authorization table exists to hold, and it leaves the no-hard-delete guard in
-- place rather than removing a protection this repository added deliberately.
--
-- The partial index is what the membership check actually uses; an assignment is read on nearly
-- every request in the system, and a revoked row must never be reachable through it.

ALTER TABLE staff_store_assignments
    ADD COLUMN revoked_at TIMESTAMPTZ NULL,
    ADD COLUMN revoked_by_staff_id UUID NULL REFERENCES staff_users(id),
    ADD CONSTRAINT staff_store_assignments_revocation_is_attributed
        CHECK ((revoked_at IS NULL) = (revoked_by_staff_id IS NULL));

CREATE INDEX staff_store_assignments_active_idx
    ON staff_store_assignments (staff_user_id, store_id)
    WHERE revoked_at IS NULL;
