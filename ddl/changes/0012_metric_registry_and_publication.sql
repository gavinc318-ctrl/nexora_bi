-- =====================================================================
-- OSS911 BI/DW  Structural change 0012  Metric registry by version; mart publications
-- Basis: DD-97 (2026-11-16 nine items, review supplement) · DD-81 · DD-99 · LD-03 chapter 5 · LD-02 chapters 10, 11
--
-- run_as = owner.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. meta.metric_registry: one row per (metric, version) (DD-97 items one, two, three, four)
-- The read-only copy of metrics/metrics.yaml, written only by 46-metrics (infra/files/metric_sync.sql) as the owner.
-- Nothing has written it yet; it must be empty.
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM meta.metric_registry) THEN
        RAISE EXCEPTION 'meta.metric_registry must be empty: nothing wrote it before 0012';
    END IF;
END $$;

ALTER TABLE meta.metric_registry DROP CONSTRAINT metric_registry_pkey;
ALTER TABLE meta.metric_registry ADD PRIMARY KEY (metric_code, metric_version);

-- The registry does not hold SQL (DD-97 item one): the definition's measure is a text description
ALTER TABLE meta.metric_registry RENAME COLUMN sql_expression TO measure;
ALTER TABLE meta.metric_registry ALTER COLUMN measure DROP NOT NULL;
ALTER TABLE meta.metric_registry ALTER COLUMN metric_version DROP DEFAULT;

ALTER TABLE meta.metric_registry
    ADD COLUMN name_zh          text    NOT NULL,
    ADD COLUMN name_ar_state    text    NOT NULL,
    ADD COLUMN aggregation      text    NOT NULL,
    ADD COLUMN entity_date      text    NOT NULL,
    ADD COLUMN retrospective    boolean NOT NULL,
    ADD COLUMN params           jsonb   NOT NULL DEFAULT '{}',
    ADD COLUMN mart_table       text,
    ADD COLUMN mart_column      text,
    ADD COLUMN mart_numerator   text,
    ADD COLUMN mart_denominator text,
    ADD COLUMN effective_from   date    NOT NULL,
    ADD COLUMN definition_sha256 text   NOT NULL,
    ADD CONSTRAINT ck_metric_registry_version CHECK (metric_version >= 1),
    ADD CONSTRAINT ck_metric_registry_name_ar_state CHECK (name_ar_state IN ('draft', 'confirmed')),
    ADD CONSTRAINT ck_metric_registry_entity_date CHECK (entity_date IN ('date_key', 'closed_date_key')),
    -- What the service layer can compute from additive mart columns (DD-97 item three): count and sum read one
    -- column, ratio and avg divide two. Other aggregations (median, p90, min, max) cannot be summed over days or
    -- members; they are registered as not implemented until a mart for them is designed.
    ADD CONSTRAINT ck_metric_registry_mart CHECK (
        (mart_table IS NULL AND mart_column IS NULL AND mart_numerator IS NULL AND mart_denominator IS NULL)
        OR (mart_table IS NOT NULL AND aggregation IN ('count', 'sum')
            AND mart_column IS NOT NULL AND mart_numerator IS NULL AND mart_denominator IS NULL)
        OR (mart_table IS NOT NULL AND aggregation IN ('ratio', 'avg')
            AND mart_column IS NULL AND mart_numerator IS NOT NULL AND mart_denominator IS NOT NULL));

COMMENT ON TABLE meta.metric_registry IS
    'Read-only copy of metrics/metrics.yaml, one row per (metric, version); written by 46-metrics only. Versions only '
    'go forward and older version rows are kept (DD-97 · LD-03 chapters 5, 6)';
COMMENT ON COLUMN meta.metric_registry.measure IS 'Text description of the measure; the registry holds no SQL (DD-97)';
COMMENT ON COLUMN meta.metric_registry.name_ar_state IS
    'draft until MOI confirms the Arabic name through M-10 (DD-56); shown either way, marked in M-04';
COMMENT ON COLUMN meta.metric_registry.mart_table IS
    'Implementing mart table; NULL = not implemented (the service layer answers metric_not_implemented, DD-97 item four)';
COMMENT ON COLUMN meta.metric_registry.mart_column IS 'count / sum: the additive mart column summed';
COMMENT ON COLUMN meta.metric_registry.mart_numerator IS 'ratio / avg: summed, then divided by the summed denominator';
COMMENT ON COLUMN meta.metric_registry.effective_from IS
    'First day this version applies to (Asia/Riyadh). -infinity for the first version of a metric: it covers all '
    'history. When a request omits the version, each day uses the version in effect on it (DD-99)';
COMMENT ON COLUMN meta.metric_registry.definition_sha256 IS
    'Hash of the fields that define the metric (definition, grain, aggregation, measure, entity_date, params, '
    'retrospective). After go-live a change without a new version fails deployment (LD-03 chapter 6)';

-- ---------------------------------------------------------------------
-- 2. meta.data_publication: one row per write of mart (DD-81 · DD-97 item nine)
-- publication_seq is the number cache keys use. kind: intraday (the hourly provisional rows, close_version 0),
-- nightly (closing days, close_version 1), correction (a closed day rewritten, close_version + 1).
-- ---------------------------------------------------------------------
CREATE TABLE meta.data_publication (
    publication_seq bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            text        NOT NULL CHECK (kind IN ('intraday', 'nightly', 'correction')),
    date_from       integer     NOT NULL,            -- date_key range written, inclusive
    date_to         integer     NOT NULL,
    run_id          text        NOT NULL,            -- the Airflow run, or the operator's label for a manual run
    published_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_data_publication_range CHECK (date_from <= date_to)
);
COMMENT ON TABLE meta.data_publication IS
    'One row per write of mart (DD-81). Every mart row carries the publication_seq that wrote it; the per-day '
    'versions are in meta.mart_day_publication';

-- ---------------------------------------------------------------------
-- 3. meta.mart_day_publication: which version of a day a publication wrote, zero rows included (DD-97 review)
-- The service layer picks each day's version from here before it reads, filters and sums (DD-99): a day whose
-- rows all moved to other days in a correction has an empty new version, which mart rows alone cannot show.
-- Provisional days (close_version 0) get a row per hourly write; the latest publication_seq is current.
-- ---------------------------------------------------------------------
CREATE TABLE meta.mart_day_publication (
    mart_table      text        NOT NULL,            -- schema-qualified, e.g. mart.calls__day__volume
    date_key        integer     NOT NULL,
    close_version   integer     NOT NULL CHECK (close_version >= 0),
    publication_seq bigint      NOT NULL REFERENCES meta.data_publication (publication_seq),
    row_count       integer     NOT NULL CHECK (row_count >= 0),
    published_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mart_table, date_key, publication_seq)
);
-- A closed version of a day is written once
CREATE UNIQUE INDEX ux_mart_day_closed ON meta.mart_day_publication (mart_table, date_key, close_version)
    WHERE close_version >= 1;
COMMENT ON TABLE meta.mart_day_publication IS
    'Per (mart table, day): the versions written, zero-row ones included. Day state for a query: published, '
    'not_built (no row), not_closed (only close_version 0) (DD-99)';

-- dbt writes both (the mart publishing macros); nobody updates or deletes: they are the history of what was served
GRANT INSERT ON meta.data_publication, meta.mart_day_publication TO oss911_transform;

-- ---------------------------------------------------------------------
-- 4. Which calls the consistency tests look at (macros/fact_call.sql, calls_to_check): the calls fact_call wrote
-- in the last day. B-tree: every rewritten row changes it (DD-78).
-- ---------------------------------------------------------------------
CREATE INDEX ix_call_built ON core.fact_call (built_at);
