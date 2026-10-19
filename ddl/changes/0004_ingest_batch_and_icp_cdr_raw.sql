-- =====================================================================
-- OSS911 BI/DW  Structural change 0004  Batch registry, runtime parameters, raw table for ICP CDRs
-- Basis: DD-76 · DD-82 · DD-85 · DD-87 · DD-88 · DD-89 · LD-01 2.5 · LD-02 chapter 9
--
-- run_as = owner: new objects belong to oss911_owner and 0002's default privileges apply
-- (SELECT on new meta tables for ingest / transform / query_svc; SELECT, INSERT on new raw tables
-- for ingest; SELECT on new raw tables for transform). Below are only the privileges the default
-- privileges do not cover, and the ones taken away.
--
-- Contents:
--   1. The partition function also serves raw, whose partition key is a timestamptz
--   2. meta.ingest_batch -- one row per fetched batch
--   3. meta.quarantine_batch now references ingest_batch instead of run_log
--   4. meta.source_runtime_param -- hot-tuned values, kept apart from the contract text
--   5. raw.icp_cdr_voice -- the CDR raw table, partitioned by month on ingested_at
--   6. Privileges: added and narrowed
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Partition function: add raw
-- 0002 limited the SECURITY DEFINER function to core and audit, whose partition key is date_key
-- (YYYYMMDD integer). raw is partitioned on ingested_at (timestamptz, DD-88), so the bounds are
-- built from the type of the partition key. Month boundaries of timestamptz keys are UTC midnight,
-- written with an explicit offset: ingested_at is a system time with no business-day meaning, and
-- the bounds must not depend on the TimeZone setting of whichever session calls the function.
-- Signature and grants are unchanged (CREATE OR REPLACE keeps the EXECUTE grants).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION meta.ensure_monthly_partitions(
    p_table text, p_from date, p_to date) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    m       date    := date_trunc('month', p_from)::date;
    mend    date    := date_trunc('month', p_to)::date;
    n       integer := 0;
    sch     text    := split_part(p_table, '.', 1);
    tbl     text    := split_part(p_table, '.', 2);
    keytype regtype;
    part    text;
    b1      text;
    b2      text;
BEGIN
    SELECT a.atttypid::regtype INTO keytype
      FROM pg_partitioned_table pt
      JOIN pg_class c      ON c.oid = pt.partrelid
      JOIN pg_namespace ns ON ns.oid = c.relnamespace
      JOIN pg_attribute a  ON a.attrelid = c.oid AND a.attnum = pt.partattrs[0]
     WHERE ns.nspname = sch AND c.relname = tbl
       AND pt.partstrat = 'r' AND pt.partnatts = 1;
    IF sch NOT IN ('core', 'audit', 'raw') OR keytype IS NULL THEN
        RAISE EXCEPTION 'Partitions can only be created for range-partitioned tables in core, audit and raw: %', p_table;
    END IF;
    IF keytype NOT IN ('integer'::regtype, 'timestamptz'::regtype) THEN
        RAISE EXCEPTION 'Unsupported partition key type % on %', keytype, p_table;
    END IF;
    WHILE m <= mend LOOP
        part := tbl || '_p' || to_char(m, 'YYYYMM');
        IF keytype = 'integer'::regtype THEN
            b1 := to_char(m, 'YYYYMMDD');
            b2 := to_char((m + interval '1 month')::date, 'YYYYMMDD');
        ELSE
            b1 := quote_literal(to_char(m, 'YYYY-MM-DD') || ' 00:00:00+00');
            b2 := quote_literal(to_char((m + interval '1 month')::date, 'YYYY-MM-DD') || ' 00:00:00+00');
        END IF;
        IF to_regclass(format('%I.%I', sch, part)) IS NULL THEN
            EXECUTE format('CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%s) TO (%s)',
                           sch, part, sch, tbl, b1, b2);
            n := n + 1;
        END IF;
        m := (m + interval '1 month')::date;
    END LOOP;
    RETURN n;
END $$;
COMMENT ON FUNCTION meta.ensure_monthly_partitions(text, date, date) IS
    'Called by orchestration ahead of time to create future monthly partitions (DD-77; raw added in 0004, DD-88). '
    'A missing partition makes every write for that month fail, so it must be monitored';

-- ---------------------------------------------------------------------
-- 2. Batch registry (LD-01 2.5 · DD-88)
-- A run (run_id) cuts [window start - lookback, window end] into batches no longer than the interface
-- allows and fetches them one after another. Each batch commits in its own transaction: this row,
-- the raw rows, the quarantine registration and the watermark advance. meta.run_log gets one row
-- only when the run ends, so a run that crashes leaves batches without a run_log row; this table
-- is the only place where such orphan batches are visible (M-01).
--
-- Windows are kept as text in the source's own notation, like meta.watermark_state.watermark_value
-- and meta.run_log.watermark_from / _to: the watermark type differs per source (timestamp or sequence).
-- ---------------------------------------------------------------------
CREATE TABLE meta.ingest_batch (
    run_id           uuid        NOT NULL,
    batch_seq        integer     NOT NULL CHECK (batch_seq >= 1),
    source_id        text        NOT NULL REFERENCES meta.source_contract (source_id),
    mode             text        NOT NULL CHECK (mode IN ('incremental', 'backfill')),
    window_from      text        NOT NULL,
    window_to        text        NOT NULL,
    row_count        bigint      NOT NULL CHECK (row_count >= 0),   -- rows received, before de-duplication
    rows_written     bigint      NOT NULL CHECK (rows_written >= 0 AND rows_written <= row_count),
    received_header  text[],                                        -- column names in the order received
    file_sha256      bytea       CHECK (octet_length(file_sha256) = 32),
    file_bytes       bigint      CHECK (file_bytes >= 0),
    archive_key      text,                                          -- <source_id>/<run_id>/<batch_seq> (DD-87)
    contract_version integer     NOT NULL,
    effective_params jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(effective_params) = 'object'),
    committed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, batch_seq),
    -- A received file has both a checksum and a size, or neither (sources that are not files)
    CONSTRAINT ck_ingest_batch_file CHECK ((file_sha256 IS NULL) = (file_bytes IS NULL))
);
COMMENT ON TABLE meta.ingest_batch IS
    'One row per fetched batch, committed together with its raw rows, quarantine registration and watermark '
    '(LD-01 2.5 · DD-88). Parent of quarantine_batch and of every raw row';
COMMENT ON COLUMN meta.ingest_batch.archive_key IS
    'Object key of the archived original file (DD-87). May be empty during development only; '
    'archiving is a required step before go-live';
COMMENT ON COLUMN meta.ingest_batch.effective_params IS
    'Snapshot of the hot-tuned parameters resolved at the start of the run and used for all of it. Never holds secrets';
COMMENT ON COLUMN meta.ingest_batch.committed_at IS
    'Start time of the batch transaction (now()); the actual commit is a moment later';
CREATE INDEX ix_ingest_batch_source ON meta.ingest_batch (source_id, committed_at);

-- ---------------------------------------------------------------------
-- 3. Quarantine registry references the batch, not the run (DD-88 item five)
-- 0003 pointed the foreign key at meta.run_log, which held while a whole run was one transaction.
-- Now the batch commits first and run_log is written last, so the parent row would not exist yet
-- when the first batch needs quarantining. 0003's constraint name was generated, so it is looked
-- up rather than assumed, and exactly one must be found.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    fks text[];
BEGIN
    SELECT array_agg(conname) INTO fks
      FROM pg_constraint
     WHERE conrelid = 'meta.quarantine_batch'::regclass AND contype = 'f'
       AND confrelid = 'meta.run_log'::regclass;
    IF coalesce(array_length(fks, 1), 0) <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one foreign key from meta.quarantine_batch to meta.run_log, found: %', fks;
    END IF;
    EXECUTE format('ALTER TABLE meta.quarantine_batch DROP CONSTRAINT %I', fks[1]);
END $$;

ALTER TABLE meta.quarantine_batch
    ADD CONSTRAINT fk_quarantine_batch_ingest_batch
    FOREIGN KEY (run_id, batch_seq) REFERENCES meta.ingest_batch (run_id, batch_seq);
COMMENT ON TABLE meta.quarantine_batch IS
    'Quarantine registry. Bad batches stay in raw; only their state is kept here; discarding changes the state '
    'and deletes nothing from raw (DD-76 · DD-02). The source comes from meta.ingest_batch (0004)';

-- ---------------------------------------------------------------------
-- 4. Hot-tuned runtime parameters (DD-88 items two and six)
-- M-01 writes hot-tuned values here, never into meta.source_contract.definition: contract sync would
-- overwrite them, or they would leak into the deployed copy. The ingestion program reads the
-- definition, overlays these values once at the start of a run, and snapshots them into
-- meta.ingest_batch.effective_params. Every change is also logged in audit.runtime_param_change_log.
--
-- The whitelist (name, type, range) is this CHECK: adding a parameter is a structural change, i.e. a
-- release item. Not in it, by design: start / stop (only meta.source_contract.enabled) and the polling
-- interval (the DAG schedule, a release item in the first version, LD-06 2.4). Batch size and rate
-- limit are hot-tunable in principle (LD-06 2.4) but no contract defines them yet; they are added
-- by the change that introduces the first contract using them.
-- ---------------------------------------------------------------------
CREATE TABLE meta.source_runtime_param (
    source_id        text        NOT NULL REFERENCES meta.source_contract (source_id),
    param_name       text        NOT NULL,
    param_value      text        NOT NULL,
    updated_by       text        NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, param_name),
    -- Nested CASE, not AND: PostgreSQL does not promise left-to-right evaluation, and the cast
    -- must only run on a value that has already passed the pattern
    CONSTRAINT ck_source_runtime_param_whitelist CHECK (
        CASE param_name
            WHEN 'lookback_minutes' THEN
                CASE WHEN param_value ~ '^[0-9]{1,4}$'
                     THEN param_value::integer BETWEEN 1 AND 1440
                     ELSE false END
            ELSE false
        END)
);
COMMENT ON TABLE meta.source_runtime_param IS
    'Hot-tuned values per source, overlaid on the contract definition at the start of a run (DD-88). '
    'The whitelist is the CHECK constraint; start / stop is source_contract.enabled only';

-- ---------------------------------------------------------------------
-- 5. raw.icp_cdr_voice (contract if03a · LD-02 chapter 9 · DD-82 · DD-88)
-- One row per CDR line as received: the 18 contract columns verbatim as text (lower-case names),
-- plus the complete received record as jsonb (columns the contract does not know yet included).
-- No NOT NULL on source columns and no unique constraint: raw records whatever the source sent,
-- bad batches included (they are registered in meta.quarantine_batch, DD-76).
--
-- De-duplication (DD-82) is done by the ingestion program with INSERT ... SELECT ... WHERE NOT EXISTS
-- on (callid, callidnum, content_hash), ignoring rows of pending and discarded batches. content_hash
-- is SHA-256 over all received column names and values in the order received (LD-01 2.5); the
-- order itself is kept in meta.ingest_batch.received_header, as jsonb does not keep key order.
--
-- Partitioned by month on ingested_at (DD-88). Partitions are created by meta.ensure_monthly_partitions,
-- never by the ingestion program. Indexes per DD-78: append only -> BRIN on ingested_at; B-tree on
-- the batch key and on the de-duplication key. The NOT EXISTS carries no partition key and probes
-- every partition; its cost at real volume is to be measured (LD-10 TC-ING-01).
-- ---------------------------------------------------------------------
CREATE TABLE raw.icp_cdr_voice (
    callid           text,
    callidnum        text,
    callerno         text,
    calleeno         text,
    waitbegin        text,
    waitend          text,
    ackbegin         text,
    ackend           text,
    callbegin        text,
    callend          text,
    devicetype       text,
    deviceno         text,
    devicein         text,
    waitcause        text,
    releasecause     text,
    calltype         text,
    ucenterreason    text,
    ucleavereason    text,
    payload          jsonb       NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    run_id           uuid        NOT NULL,
    batch_seq        integer     NOT NULL,
    contract_version integer     NOT NULL,
    src_watermark    text,                       -- WAITBEGIN as received: the row's position on the watermark column
    src_tz_offset    text,                       -- the source sends no offset; kept for the LD-02 chapter 9 layout
    content_hash     bytea       NOT NULL CHECK (octet_length(content_hash) = 32),
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_icp_cdr_voice_batch
        FOREIGN KEY (run_id, batch_seq) REFERENCES meta.ingest_batch (run_id, batch_seq)
) PARTITION BY RANGE (ingested_at);
COMMENT ON TABLE raw.icp_cdr_voice IS
    'ICP CDRs as received, one row per call leg (contract if03a). Append only: ingest has SELECT and INSERT only (DD-02)';
COMMENT ON COLUMN raw.icp_cdr_voice.payload IS
    'The complete received record, all columns including ones the contract does not list yet';

CREATE INDEX ix_icp_cdr_voice_ingested ON raw.icp_cdr_voice USING brin (ingested_at)
    WITH (autosummarize = on);
CREATE INDEX ix_icp_cdr_voice_batch    ON raw.icp_cdr_voice (run_id, batch_seq);
CREATE INDEX ix_icp_cdr_voice_dedup    ON raw.icp_cdr_voice (callid, callidnum, content_hash);

-- ---------------------------------------------------------------------
-- 6. Privileges
-- ---------------------------------------------------------------------
-- Ingestion registers its batches and raises clarification items (CALLTYPE outside the value
-- table, LD-01 7.2; agreed by the Mac side 2026-10-14)
GRANT INSERT ON meta.ingest_batch        TO oss911_ingest;
GRANT INSERT ON meta.clarification_queue TO oss911_ingest;

-- run_log is now written once, at the end of a run (LD-01 2.5). 0002 granted UPDATE for the old
-- "insert at start, fill in at the end" pattern; nothing updates it any more, and run_log is
-- append-only (LD-01 10.1)
REVOKE UPDATE ON meta.run_log FROM oss911_ingest;

-- M-01: start / stop is the only column of the contract copy the admin path may change; the rest is
-- written by contract sync only (DD-88 item three). Hot-tuned values go to source_runtime_param;
-- DELETE there means "back to the contract default" and is logged like any other change
REVOKE UPDATE ON meta.source_contract FROM oss911_admin_svc;
GRANT UPDATE (enabled) ON meta.source_contract TO oss911_admin_svc;
GRANT INSERT, UPDATE, DELETE ON meta.source_runtime_param TO oss911_admin_svc;
