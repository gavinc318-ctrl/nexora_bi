-- =====================================================================
-- OSS911 BI/DW  Structural change 0005  Staging batch registry; re-sweep as a batch mode of its own
-- Basis: DD-89 (with its 2026-10-19 correction and 2026-10-26 note) · LD-02 chapter 9 · DD-76
--
-- run_as = owner. Default privileges of 0002 give SELECT on the new meta table to ingest, transform and query_svc;
-- below are only the privileges they do not cover.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Which batches each staging model has processed (DD-89)
-- Staging consumes raw by batch. Correctness rests on this registry, not on a time watermark: a model processes the
-- batches that are processable (not quarantined, or quarantined and released) and not yet registered for it, and
-- registers them in the same transaction as its own write (a dbt post-hook runs in the model's transaction). A batch
-- released on Thursday that was ingested on Monday is therefore picked up by the next run, which no watermark on
-- ingested_at or on the release time can guarantee. Time columns only narrow the scan.
--
-- The foreign key makes "registered" mean "a batch that exists"; ingest_batch rows are never deleted.
-- ---------------------------------------------------------------------
CREATE TABLE meta.staging_batch_state (
    model            text        NOT NULL CHECK (model ~ '^[a-z][a-z0-9_]*$'),   -- the dbt model name
    run_id           uuid        NOT NULL,
    batch_seq        integer     NOT NULL,
    staged_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model, run_id, batch_seq),
    CONSTRAINT fk_staging_batch_state_batch
        FOREIGN KEY (run_id, batch_seq) REFERENCES meta.ingest_batch (run_id, batch_seq)
);
COMMENT ON TABLE meta.staging_batch_state IS
    'Batches each staging model has processed, registered in the same transaction as the model''s write (DD-89). '
    'The basis for "processed or not"; time watermarks only narrow the scan';
-- The batches of one run, for "which models have taken this batch" (M-03 release screen, LD-06 chapter 3)
CREATE INDEX ix_staging_batch_state_batch ON meta.staging_batch_state (run_id, batch_seq);

-- dbt (oss911_transform) registers the batches. DELETE as well: staging is rebuildable (LD-02 chapter 9) -- rebuilding
-- a staging model from raw must clear its registrations in the same transaction, or the rebuilt model would skip every
-- batch it had processed before. Nothing else writes here
GRANT INSERT, DELETE ON meta.staging_batch_state TO oss911_transform;

-- ---------------------------------------------------------------------
-- 2. The re-sweep is a batch mode of its own (DD-89 note, 2026-10-26)
-- 0004 allowed incremental and backfill; the daily re-sweep has been recorded as backfill since. It is a third mode:
-- its window comes from the DAG's logical date, and M-01 must be able to tell a scheduled re-sweep from a manual
-- backfill. 0004's constraint name was generated, so it is looked up rather than assumed.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    found text[];
BEGIN
    SELECT array_agg(conname) INTO found
      FROM pg_constraint
     WHERE conrelid = 'meta.ingest_batch'::regclass AND contype = 'c'
       AND pg_get_constraintdef(oid) LIKE '%mode%incremental%backfill%';
    IF coalesce(array_length(found, 1), 0) <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one CHECK on meta.ingest_batch.mode, found: %', found;
    END IF;
    EXECUTE format('ALTER TABLE meta.ingest_batch DROP CONSTRAINT %I', found[1]);
END $$;

ALTER TABLE meta.ingest_batch
    ADD CONSTRAINT ck_ingest_batch_mode CHECK (mode IN ('incremental', 'backfill', 'resweep'));
COMMENT ON COLUMN meta.ingest_batch.mode IS
    'incremental: from the watermark, moves it. backfill: an explicit window, by hand. resweep: the daily re-sweep of one '
    'day, from the DAG''s logical date. Only incremental moves the watermark (DD-89)';
