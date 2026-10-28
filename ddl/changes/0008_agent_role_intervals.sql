-- =====================================================================
-- OSS911 BI/DW  Structural change 0008  Supervisor marks with validity intervals
-- Basis: DD-94 item sixteen (2026-10-28 review) · LD-02 chapter 7, third rule · DD-12
--
-- run_as = owner. Needs the extension btree_gist, created by 20-database.yml (pg_extensions in the inventory): the
-- owner role may not create extensions, and the exclusion constraint below cannot be written without it.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'btree_gist') THEN
        RAISE EXCEPTION 'Extension btree_gist is missing: run 20-database.yml first (pg_extensions in the inventory)';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- meta.agent_role_override of 0007 held one current mark per agent (set_at, no end). Such a "current flag" cannot
-- take part in cutting versions: a mark entered at 09:30 for 09:00, while a rename synced at 08:00 is still to be
-- applied, would be merged into one version from 08:00 -- the calls of 08:00-09:00 linked to a supervisor. The
-- dimension is now cut at the union of the change points of the source snapshots and of the marks, one version per
-- point (LD-02 chapter 7, third rule), and a mark is a record of an interval:
--   set       INSERT with valid_from (valid_to infinity)
--   cleared   UPDATE valid_to; the row stays, so the history of marks can be read back
-- Intervals of one agent never overlap (exclusion constraint): at any time an agent has one role.
-- Rows of 0007 are carried over: valid from set_at, still open, changed_by = set_by.
-- ---------------------------------------------------------------------
ALTER TABLE meta.agent_role_override RENAME TO agent_role_override_0007;
ALTER TABLE meta.agent_role_override_0007 RENAME CONSTRAINT agent_role_override_pkey TO agent_role_override_0007_pkey;

CREATE TABLE meta.agent_role_override (
    override_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_code        text        NOT NULL,
    src_system       text        NOT NULL,
    agent_no         text        NOT NULL,                     -- the agent's ID at the source (ICP agentNo)
    role_code        text        NOT NULL CHECK (role_code IN ('call_taker', 'supervisor')),
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    changed_by       text        NOT NULL CHECK (length(btrim(changed_by)) > 0),
    reason           text        NOT NULL CHECK (length(btrim(reason)) > 0),
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_agent_role_override_interval CHECK (valid_to > valid_from),
    CONSTRAINT ex_agent_role_override_overlap
        EXCLUDE USING gist (site_code WITH =, src_system WITH =, agent_no WITH =,
                            tstzrange(valid_from, valid_to) WITH &&)
);
COMMENT ON TABLE meta.agent_role_override IS
    'Business role of an agent over an interval, where it is not call_taker; set in M-10 (DD-94 items six, sixteen). '
    'Clearing a mark closes valid_to, never deletes. dbt cuts a new version of core.dim_agent at every valid_from and '
    'valid_to, as at every source snapshot (LD-02 chapter 7)';
COMMENT ON COLUMN meta.agent_role_override.valid_from IS
    'When the role starts to apply. A time before the agent''s latest version in core.dim_agent cannot rewrite the '
    'versions already written: dbt applies the mark from just after that version';
COMMENT ON COLUMN meta.agent_role_override.recorded_at IS 'When the mark was entered, as against when it applies';

INSERT INTO meta.agent_role_override (site_code, src_system, agent_no, role_code, valid_from, changed_by, reason, recorded_at)
SELECT site_code, src_system, src_natural_key, role_code, set_at, set_by, reason, set_at
  FROM meta.agent_role_override_0007;
DROP TABLE meta.agent_role_override_0007;

-- M-10 sets and clears; nothing else changes a mark. dbt reads it (default privileges of 0002 give transform SELECT)
GRANT INSERT ON meta.agent_role_override TO oss911_admin_svc;
GRANT UPDATE (valid_to) ON meta.agent_role_override TO oss911_admin_svc;
