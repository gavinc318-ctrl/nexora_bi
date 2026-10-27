-- =====================================================================
-- OSS911 BI/DW  Structural change 0007  ICP reference data: raw table, snapshot batches, dbt writes the dimensions
-- Basis: DD-94 · DD-82 note (2026-10-27) · DD-56 · LD-01 4.6 / 9.3 · LD-02 chapters 4, 7, 9 · contract if03d v2
--
-- run_as = owner. Default privileges of 0002 give ingest SELECT and INSERT on the new raw table and transform
-- SELECT on it; below are only the privileges they do not cover, and the ones narrowed.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. raw.icp_reference (DD-94 items one, two)
-- Eight datasets of eight different shapes (synwas, interface reference p.907-933), all into one table: a few
-- generic columns and the received record verbatim in payload. Not widened into one column per field: most columns
-- would be empty for most datasets, and a new field at the source would need a structural change.
--
-- Written as whole snapshots (DD-94 item two; contract raw_write.mode snapshot_on_version): when a dataset's version
-- changes, every record of the dataset is written, one batch per dataset -- so "missing from the latest snapshot"
-- means "gone at the source", which core needs to close the row (SCD2). The same version fetched again is not
-- written again (the ingestion program checks meta.ingest_batch first).
--
-- Partitioned by month on ingested_at like every raw table (DD-88); small, but one rule for all raw tables.
-- ---------------------------------------------------------------------
CREATE TABLE raw.icp_reference (
    dataset          text        NOT NULL CHECK (dataset IN ('vdns', 'agents', 'groups', 'skills', 'skillgroups',
                                                             'calledconfigs', 'restreasons', 'servertypes')),
    cc_id            text        NOT NULL,     -- as received (the interface types it int, sends a string)
    vdn_id           text,                     -- NULL for dataset vdns: that list is per ccId
    src_natural_key  text,                     -- the record's id within its dataset (agentNo, skillId ...), as received
    snapshot_version bigint      NOT NULL,     -- CTI version for vdns, the VDN's version for the other seven (DD-94 item three)
    payload          jsonb       NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    run_id           uuid        NOT NULL,
    batch_seq        integer     NOT NULL,
    contract_version integer     NOT NULL,
    content_hash     bytea       NOT NULL CHECK (octet_length(content_hash) = 32),
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_icp_reference_batch
        FOREIGN KEY (run_id, batch_seq) REFERENCES meta.ingest_batch (run_id, batch_seq)
) PARTITION BY RANGE (ingested_at);
COMMENT ON TABLE raw.icp_reference IS
    'ICP reference data as received (contract if03d): whole snapshots, one batch per dataset and version. '
    'Append only: ingest has SELECT and INSERT only (DD-02 · DD-94)';
COMMENT ON COLUMN raw.icp_reference.src_natural_key IS
    'The record''s id within its dataset as received. NULL when the source sent none: such a record cannot become a '
    'dimension row, and staging counts it rather than inventing a key';

CREATE INDEX ix_icp_reference_ingested ON raw.icp_reference USING brin (ingested_at) WITH (autosummarize = on);
CREATE INDEX ix_icp_reference_batch    ON raw.icp_reference (run_id, batch_seq);

-- ---------------------------------------------------------------------
-- 2. Snapshot batches (DD-94 items two, three, twelve)
-- A snapshot batch has no time window: it is one dataset at one version. The ingestion program decides from these
-- two columns whether a version has been taken already (equal: nothing to do), is new (greater: fetch), or went
-- backwards (smaller: clarification item, not applied). Every batch counts, quarantined ones too: a version whose
-- snapshot is held in quarantine is not fetched again every 30 minutes; it waits for its release or a new version.
-- window_from / window_to, NOT NULL for every batch, hold the version taken before and this one.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    found text[];
BEGIN
    SELECT array_agg(conname) INTO found
      FROM pg_constraint
     WHERE conrelid = 'meta.ingest_batch'::regclass AND contype = 'c'
       AND pg_get_constraintdef(oid) LIKE '%mode%incremental%backfill%resweep%';
    IF coalesce(array_length(found, 1), 0) <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one CHECK on meta.ingest_batch.mode, found: %', found;
    END IF;
    EXECUTE format('ALTER TABLE meta.ingest_batch DROP CONSTRAINT %I', found[1]);
END $$;

ALTER TABLE meta.ingest_batch
    ADD COLUMN snapshot_scope   text,
    ADD COLUMN snapshot_version bigint,
    ADD COLUMN snapshot_synced_at timestamptz,
    ADD CONSTRAINT ck_ingest_batch_mode CHECK (mode IN ('incremental', 'backfill', 'resweep', 'snapshot')),
    ADD CONSTRAINT ck_ingest_batch_snapshot CHECK (
        (mode = 'snapshot') = (snapshot_scope IS NOT NULL AND snapshot_version IS NOT NULL));
COMMENT ON COLUMN meta.ingest_batch.snapshot_scope IS
    'Snapshot batches only: <dataset>@<ccId>/<vdnId> (vdns: vdns@<ccId>) -- what one version number covers';
COMMENT ON COLUMN meta.ingest_batch.snapshot_version IS
    'Snapshot batches only: the source''s version of that scope when fetched (DD-94 item three)';
COMMENT ON COLUMN meta.ingest_batch.snapshot_synced_at IS
    'Snapshot batches only: endTime of the gateway sync that produced this version, as the source reports it; NULL '
    'when unreadable. A dimension version built from the snapshot is valid from this time (DD-94 item five)';
CREATE INDEX ix_ingest_batch_snapshot ON meta.ingest_batch (source_id, snapshot_scope, snapshot_version)
    WHERE mode = 'snapshot';

-- ---------------------------------------------------------------------
-- 3. Hot-tuned: how many days the gateway's copy may go without a sync before it is reported (DD-94 item four)
-- The synwas GETs read the gateway's own copy; if nothing syncs it, the data stays old with no error. The first
-- value is 7 days (contract); the range is a type guard, the rules live in contract.py
-- ---------------------------------------------------------------------
ALTER TABLE meta.source_runtime_param DROP CONSTRAINT ck_source_runtime_param_whitelist;
ALTER TABLE meta.source_runtime_param ADD CONSTRAINT ck_source_runtime_param_whitelist CHECK (
    CASE param_name
        WHEN 'lookback_minutes' THEN
            CASE WHEN param_value ~ '^[0-9]{1,4}$'
                 THEN param_value::integer BETWEEN 1 AND 1440
                 ELSE false END
        WHEN 'version_stale_days' THEN
            CASE WHEN param_value ~ '^[0-9]{1,3}$'
                 THEN param_value::integer BETWEEN 1 AND 365
                 ELSE false END
        ELSE false
    END);

-- ---------------------------------------------------------------------
-- 4. Supervisors are marked in M-10 (DD-94 item six, corrected 2026-10-27; AS-36)
-- ICP agent IDs belong to call takers and their supervisors only; ICP does not say which is which. Every agent from
-- ICP is a call_taker unless M-10 has marked it. The mark is kept here, not written into core.dim_agent: the
-- dimension is rebuilt from the source by dbt, and a value typed into it would be lost at the next version. dbt
-- reads this table; a mark set (or cleared) makes a new version of the agent, valid from set_at.
-- ---------------------------------------------------------------------
CREATE TABLE meta.agent_role_override (
    site_code        text        NOT NULL,
    src_system       text        NOT NULL,
    src_natural_key  text        NOT NULL,                     -- the agent's ID (agentNo)
    role_code        text        NOT NULL CHECK (role_code IN ('call_taker', 'supervisor')),
    set_by           text        NOT NULL,
    set_at           timestamptz NOT NULL DEFAULT now(),
    reason           text        NOT NULL CHECK (length(btrim(reason)) > 0),
    PRIMARY KEY (site_code, src_system, src_natural_key)
);
COMMENT ON TABLE meta.agent_role_override IS
    'Business role of an agent where it is not call_taker, set in M-10 (DD-94 item six). Read by dbt; changes are '
    'logged in audit.reference_data_change_log by the admin path like every M-10 change (LD-01 9.3)';
GRANT INSERT, UPDATE ON meta.agent_role_override TO oss911_admin_svc;

-- ---------------------------------------------------------------------
-- 5. Small code tables keep the source's own text (DD-94 items seven, eight)
-- meta.reference_value had only the two translated names, maintained in M-10. ICP sends one text per code
-- (reasonInfo, serviceName, groupName) in a language it does not state; it goes here, beside the translations, so
-- that M-10 sees what it is translating and dbt never overwrites a translation.
-- ---------------------------------------------------------------------
ALTER TABLE meta.reference_value ADD COLUMN src_label text;
COMMENT ON COLUMN meta.reference_value.src_label IS
    'The source''s own text for the code, as last received; written by dbt. name_ar / name_en are M-10''s translations';

-- ---------------------------------------------------------------------
-- 6. The first version of a dimension row (DD-94 item five)
-- valid_from of a version is the gateway's sync endTime. The first version of an entity covers everything before it
-- was first observed -- otherwise no call before the first snapshot could find its agent. Its valid_from is a
-- sentinel: 1970-01-01 00:00 UTC, earlier than any fact. Not -infinity: core.sk_scd() formats valid_from with
-- to_char, which returns NULL for infinity, and the key would be NULL (tested on PostgreSQL 16).
-- ---------------------------------------------------------------------
COMMENT ON COLUMN core.dim_agent.valid_from IS
    'Gateway sync endTime of the snapshot this version came from (DD-94 item five). The first version of an agent is '
    'valid from the sentinel 1970-01-01 00:00 UTC: it is assumed to cover the time before it was first observed -- an '
    'inference accepted in DD-94, not a fact from the source';
COMMENT ON COLUMN core.dim_queue.valid_from IS
    'As core.dim_agent.valid_from: sync endTime; the first version from the sentinel 1970-01-01 00:00 UTC (DD-94 item five)';
COMMENT ON COLUMN core.dim_queue.name_ar IS
    'ICP sends one name (skillName): written here as received until M-10 translates it; translations go to '
    'meta.label_override (DD-94 item eight · DD-56)';
COMMENT ON COLUMN core.dim_queue.name_en IS
    'As name_ar: the source''s single name until translated through meta.label_override (DD-94 item eight)';

-- ---------------------------------------------------------------------
-- 7. What dbt may write for reference data (DD-94 item eleven)
-- Dimension rows are never deleted or rewritten: a change is a new version, and closing the old one sets valid_to.
-- 0002 gave transform full DML on core; for the two dimensions it is narrowed to exactly that.
-- ---------------------------------------------------------------------
REVOKE UPDATE, DELETE ON core.dim_agent, core.dim_queue FROM oss911_transform;
GRANT UPDATE (valid_to, built_at) ON core.dim_agent, core.dim_queue TO oss911_transform;

GRANT INSERT ON meta.reference_value TO oss911_transform;
GRANT UPDATE (src_label, is_current, last_seen_at, contract_version) ON meta.reference_value TO oss911_transform;
GRANT USAGE ON SCHEMA audit TO oss911_transform;                            -- for the next line only
GRANT INSERT ON audit.reference_data_change_log TO oss911_transform;        -- changed_by = icp_sync
GRANT UPDATE (state, flagged_at) ON meta.label_override TO oss911_transform; -- source renamed -> pending_reconfirm

COMMENT ON TABLE audit.reference_data_change_log IS
    'Field-level log of reference data changes: M-10''s manual maintenance (LD-01 9.3), and the changes dbt applies from '
    'ICP''s synced reference data (changed_by = icp_sync, DD-94). Manual maintenance does not pass through raw and has '
    'no run_log lineage, so for it this table is the only trace; synced data also has raw.icp_reference';
