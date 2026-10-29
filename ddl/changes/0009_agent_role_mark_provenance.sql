-- =====================================================================
-- OSS911 BI/DW  Structural change 0009  Who and what behind a supervisor mark
-- Basis: DD-94 item eighteen (2026-10-29 review) · change 0008
-- =====================================================================

-- ---------------------------------------------------------------------
-- A mark entered for a time before the agent's latest dimension version cannot take effect then: the dimension only
-- gains and closes versions (DD-12). M-10 shows "takes effect from X" (X = that version + 1 second), asks to confirm,
-- and stores X in valid_from; the time asked for is kept here. NULL when the mark took effect as asked.
-- ---------------------------------------------------------------------
ALTER TABLE meta.agent_role_override
    ADD COLUMN requested_from timestamptz,
    ADD CONSTRAINT ck_agent_role_override_requested CHECK (requested_from < valid_from);
COMMENT ON COLUMN meta.agent_role_override.requested_from IS
    'The start asked for in M-10 when it was before the agent''s latest version; valid_from is when it took effect';

-- ---------------------------------------------------------------------
-- The audit row of a version caused by a mark names who caused it (DD-94 item eighteen). Clearing a mark is a second
-- act, often by someone else than who set it: changed_by is who set it, cleared_by who closed valid_to. NULL while
-- open, and for a mark entered with its end already known (its changed_by stands for both).
-- ---------------------------------------------------------------------
ALTER TABLE meta.agent_role_override
    ADD COLUMN cleared_by text,
    ADD CONSTRAINT ck_agent_role_override_cleared
        CHECK (cleared_by IS NULL OR (length(btrim(cleared_by)) > 0 AND valid_to <> 'infinity'));
COMMENT ON COLUMN meta.agent_role_override.cleared_by IS
    'Who cleared the mark (closed valid_to) in M-10; NULL while open or when entered with its end';

GRANT UPDATE (cleared_by) ON meta.agent_role_override TO oss911_admin_svc;
