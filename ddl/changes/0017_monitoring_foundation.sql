-- =====================================================================
-- OSS911 BI/DW  Structural change 0017  Monitoring foundation: check results, the monitoring role, unlinked legs
-- Basis: DD-100 (monitoring and alerting channel, block one) and its review note (2026-11-22) · LD-01 10.2
--
-- run_as = owner. The privilege role oss911_monitor itself is created before the changes by 40-schema.yml (as
-- 0002 does for the first five, with the same attribute check): from 0003 on changes run as the owner role, which
-- cannot create roles (DD-74). This change only grants to it. Its login user and env file come with the exporter
-- (block two).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Partition function: add meta (meta.check_result is the first partitioned table there)
-- As 0004, with meta in the allowed schemas; a meta table that is not range-partitioned is still refused.
-- Signature and grants unchanged.
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
    IF sch NOT IN ('core', 'audit', 'raw', 'meta') OR keytype IS NULL THEN
        RAISE EXCEPTION 'Partitions can only be created for range-partitioned tables in core, audit, raw and meta: %',
                        p_table;
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
    'Called by orchestration ahead of time to create future monthly partitions (DD-77; raw added in 0004, DD-88; '
    'meta in 0017, DD-100). A missing partition makes every write for that month fail, so it must be monitored';

-- ---------------------------------------------------------------------
-- 2. meta.check_result: one row per dbt node per invocation (DD-100 item three)
-- Written by the on-run-end hook of the dbt project (macros/check_results.sql) as oss911_transform: models and
-- tests, with dbt's own status -- success / error / skipped for models, pass / warn / fail / error / skipped for
-- tests. failures is the number of rows a test returned (NULL for models, and for a test that errored: an error is
-- not 0). When dbt itself crashes the hook does not run and nothing is written: the exporter judges each expected
-- check by the age of its last row, not by the absence of failures (DD-100 review note, item three).
-- Append only. Monthly partitions on checked_at (UTC month boundaries, as raw); partitions older than the retention
-- (90 days, DD-100 item 7) are dropped by meta.drop_expired_check_partitions, nightly.
-- No data values: the message is dbt's, cut to 500 characters -- a test's message is its row count, a model's
-- error is the database error (which may quote a value; the table is read by operators only, not alerted).
-- ---------------------------------------------------------------------
CREATE TABLE meta.check_result (
    invocation_id     uuid        NOT NULL,     -- dbt's invocation_id: the rows of one dbt command
    command           text        NOT NULL,     -- build, run, test, ...
    unique_id         text        NOT NULL,     -- dbt's node id, e.g. test.oss911.agent_day_totals_decrease
    resource_type     text        NOT NULL CHECK (resource_type IN ('model', 'test', 'seed', 'snapshot')),
    node_name         text        NOT NULL,
    status            text        NOT NULL
                      CHECK (status IN ('success', 'pass', 'warn', 'fail', 'error', 'skipped')),
    severity          text        CHECK (severity IN ('error', 'warn')),   -- tests only
    failures          bigint      CHECK (failures >= 0),
    rows_affected     bigint,                   -- models: what the adapter reported
    execution_seconds numeric(10, 3),
    message           text        CHECK (length(message) <= 500),
    dag_id            text,                     -- Airflow context of the task that ran dbt; NULL when run by hand
    task_id           text,
    dag_run_id        text,
    try_number        integer,
    checked_at        timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (checked_at);
COMMENT ON TABLE meta.check_result IS
    'Results of every dbt model and test, per invocation (DD-100). Written by the dbt on-run-end hook; read by the '
    'monitoring exporter (freshness and outcome per expected check) and later by M-08. Append only, 90 days';
COMMENT ON COLUMN meta.check_result.failures IS
    'Rows the test returned; NULL for models and for a test that errored or was skipped (never counted as 0)';

CREATE INDEX ix_check_result_node ON meta.check_result (unique_id, checked_at);
CREATE INDEX ix_check_result_dag  ON meta.check_result (dag_id, checked_at);

-- dbt appends; nobody updates or deletes rows (old months go as whole partitions)
GRANT INSERT ON meta.check_result TO oss911_transform;

-- ---------------------------------------------------------------------
-- 3. Retention of meta.check_result: drop whole monthly partitions past the retention
-- SECURITY DEFINER like the partition function, and limited to this one table: other tables' retention is their
-- own decision (DD-38), and a generic "drop old partitions" in the hands of orchestration would be a way to lose
-- raw or core. A partition goes when its whole month ended more than p_keep_days ago, so at least p_keep_days are
-- always kept. Returns the number of partitions dropped.
-- ---------------------------------------------------------------------
CREATE FUNCTION meta.drop_expired_check_partitions(p_keep_days integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    p   record;
    n   integer := 0;
    hi  date;
BEGIN
    IF p_keep_days IS NULL OR p_keep_days < 90 THEN
        RAISE EXCEPTION 'meta.check_result is kept at least 90 days (DD-100), not %', p_keep_days;
    END IF;
    FOR p IN SELECT c.relname
               FROM pg_inherits i
               JOIN pg_class c ON c.oid = i.inhrelid
              WHERE i.inhparent = 'meta.check_result'::regclass
                AND c.relname ~ '^check_result_p[0-9]{6}$' LOOP
        hi := (to_date(right(p.relname, 6), 'YYYYMM') + interval '1 month')::date;
        IF hi < current_date - p_keep_days THEN
            EXECUTE format('DROP TABLE meta.%I', p.relname);
            n := n + 1;
        END IF;
    END LOOP;
    RETURN n;
END $$;
COMMENT ON FUNCTION meta.drop_expired_check_partitions(integer) IS
    'Drops the monthly partitions of meta.check_result whose month ended more than p_keep_days (>= 90) ago (DD-100). '
    'Called nightly by maintenance_partitions';
REVOKE EXECUTE ON FUNCTION meta.drop_expired_check_partitions(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION meta.drop_expired_check_partitions(integer) TO oss911_transform;

-- ---------------------------------------------------------------------
-- 4. Unlinked call legs: agent legs no dimension version covers (DD-100 item two, oss911_unlinked_agents)
-- As the partial indexes on the agent facts (0015): the exporter counts the distinct IDs every minute, and the set
-- is small next to the table.
-- ---------------------------------------------------------------------
CREATE INDEX ix_leg_agent_unlinked ON core.fact_call_leg (device_no, date_key)
    WHERE device_type_code = 2 AND agent_sk IS NULL;

-- ---------------------------------------------------------------------
-- 5. The monitoring role: read-only, only what the exporter reads (DD-100 item one)
-- No raw payloads, no call legs' numbers: it reads registries and counts. core.fact_call_leg is read only through
-- the partial index above (device_no, date_key); column privileges keep it to those columns.
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA meta, core, raw TO oss911_monitor;
GRANT SELECT ON meta.check_result, meta.mart_day_publication, meta.ingest_batch, meta.staging_batch_state,
                meta.quarantine_batch, meta.run_log, meta.clarification_queue, meta.source_contract,
                meta.source_runtime_param, meta.ddl_change
             TO oss911_monitor;
GRANT SELECT (source_id, date_key, observed_at, requested, returned, missing_count, run_id, batch_seq)
          ON raw.icp_agent_coverage TO oss911_monitor;
GRANT SELECT (agent_no, date_key, agent_sk, src_confirmed_at) ON core.fact_agent_session TO oss911_monitor;
GRANT SELECT (agent_no, date_key, agent_sk, src_confirmed_at) ON core.fact_agent_day     TO oss911_monitor;
GRANT SELECT (device_no, date_key, device_type_code, agent_sk) ON core.fact_call_leg       TO oss911_monitor;
GRANT SELECT (src_natural_key, valid_from, valid_to, is_current, site_sk) ON core.dim_agent      TO oss911_monitor;
