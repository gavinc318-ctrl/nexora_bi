-- =====================================================================
-- Verification of database roles and grants (structural changes 0002 to 0006 · DD-74 · DD-75 · DD-76 · DD-88 · DD-89)
--
-- It verifies the five privilege roles themselves: inside a transaction the script creates one temporary
-- login user per role, the superuser session does SET ROLE to that login user, and then runs statements
-- that "must be allowed / must be denied / must raise an error" one by one. Privilege checks use
-- current_user, exactly as when that user connects directly, rather than verifying as superuser
-- (CLAUDE.md section 5, item two). Everything is one transaction rolled back at the end -- the temporary
-- users, fixture tables, new partitions and inserted rows all disappear, so it leaves no trace even on a real cluster.
--
-- Not verified here: the real login users' passwords, authentication through pgBouncer, and whether they
-- really are members of the privilege roles. 40-schema.yml verifies those three by logging in through
-- pgBouncer as the consumer.
--
-- Usage (40-schema.yml runs it at the end automatically; to run it by hand):
--   sudo -u postgres psql -d oss911 -X < ~/src/nexora_bi/test/db_roles_check.sql
-- Redirect rather than -f: postgres cannot read files under /home; the redirection is done by the caller's
-- shell (troubleshooting entry 23).
--
-- Any failing check RAISEs, and ON_ERROR_STOP makes the script exit non-zero.
-- =====================================================================
\set ON_ERROR_STOP 1
\set QUIET on
\pset tuples_only on
\pset format unaligned
BEGIN;

-- Functional Chinese (allowed by tools/check_no_cjk.py with the cjk-ok marker): the exception message raised
-- by the already-executed change 0003 is in Chinese, and that file must not be edited (DD-73), so the pattern
-- that matches it must be Chinese too. Kept together here and referenced as psql variables.
-- (The partition function's message became English when 0004 redefined the function.)
SELECT 'Partitions can only be created for%' AS err_partition_scope,
       '%处理结论不可改%'                     AS err_quarantine_final  -- cjk-ok: raised by meta.quarantine_batch_guard (change 0003)
\gset

CREATE FUNCTION pg_temp.expect_ok(p_sql text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE p_sql;
    RAISE NOTICE 'PASS allowed [%] %', current_user, p_sql;
EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'FAIL should be allowed but failed [%] % -- %', current_user, p_sql, SQLERRM;
END $$;

CREATE FUNCTION pg_temp.expect_denied(p_sql text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS denied [%] %', current_user, p_sql;
        RETURN;
    END;
    RAISE EXCEPTION 'FAIL should be denied but succeeded [%] %', current_user, p_sql;
END $$;

CREATE FUNCTION pg_temp.expect_error(p_sql text, p_like text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM LIKE p_like THEN
            RAISE NOTICE 'PASS raised [%] %', current_user, p_sql;
            RETURN;
        END IF;
        RAISE EXCEPTION 'FAIL raised a different error [%] % -- %', current_user, p_sql, SQLERRM;
    END;
    RAISE EXCEPTION 'FAIL should raise an error but succeeded [%] %', current_user, p_sql;
END $$;

-- ---------- Temporary login users: one per privilege role, gone with the rollback ----------
\set ingest_user    zz_chk_ingest
\set transform_user zz_chk_transform
\set query_user     zz_chk_query
\set admin_user     zz_chk_admin
CREATE ROLE :"ingest_user"    LOGIN IN ROLE oss911_ingest;
CREATE ROLE :"transform_user" LOGIN IN ROLE oss911_transform;
CREATE ROLE :"query_user"     LOGIN IN ROLE oss911_query_svc;
CREATE ROLE :"admin_user"     LOGIN IN ROLE oss911_admin_svc;

-- ---------- Fixture: a raw table created as the owner role, so default privileges apply ----------
SET ROLE oss911_owner;
CREATE TABLE raw.zz_roles_check (id integer, v text);
RESET ROLE;
-- A contract row for the foreign keys of ingest_batch and source_runtime_param (contract sync writes these in
-- real life, as the owner role -- not tested here)
INSERT INTO meta.source_contract (source_id, connector, target_raw, contract_version, definition)
VALUES ('zz_check', 'rest_api', 'raw.icp_cdr_voice', 1, '{}');

-- ---------- Orchestration (transform): create partitions ----------
SET ROLE :"transform_user";
SELECT pg_temp.expect_ok($$SELECT meta.ensure_monthly_partitions('core.fact_call', current_date, current_date)$$);
SELECT pg_temp.expect_ok($$SELECT meta.ensure_monthly_partitions('core.fact_call_leg', current_date, current_date)$$);
SELECT pg_temp.expect_ok($$SELECT meta.ensure_monthly_partitions('audit.access_log', current_date, current_date)$$);
SELECT pg_temp.expect_error($$SELECT meta.ensure_monthly_partitions('meta.srid_config', current_date, current_date)$$,
                            :'err_partition_scope');
-- raw is partitioned on a timestamptz (0004); the bounds must be UTC month boundaries whatever the session TimeZone
SET LOCAL TimeZone = 'Asia/Riyadh';
SELECT pg_temp.expect_ok($$SELECT meta.ensure_monthly_partitions('raw.icp_cdr_voice', current_date, current_date)$$);
SELECT pg_temp.expect_error($$SELECT meta.ensure_monthly_partitions('raw.zz_roles_check', current_date, current_date)$$,
                            :'err_partition_scope');                  -- not a partitioned table
RESET TimeZone;
RESET ROLE;

-- Partitions created by the partition function belong to the owner role and inherit the default privileges
DO $$
DECLARE p regclass := to_regclass('core.fact_call_p' || to_char(current_date, 'YYYYMM'));
BEGIN
    IF pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = p)) <> 'oss911_owner' THEN
        RAISE EXCEPTION 'FAIL the owner of new partition % is not oss911_owner', p;
    END IF;
    RAISE NOTICE 'PASS owner of new partitions';
END $$;

DO $$
DECLARE
    p     regclass := to_regclass('raw.icp_cdr_voice_p' || to_char(current_date, 'YYYYMM'));
    bound text;
    want  text := format('FOR VALUES FROM (%L) TO (%L)',
                         to_char(date_trunc('month', current_date), 'YYYY-MM-DD') || ' 00:00:00+00',
                         to_char(date_trunc('month', current_date) + interval '1 month', 'YYYY-MM-DD') || ' 00:00:00+00');
BEGIN
    IF p IS NULL THEN RAISE EXCEPTION 'FAIL raw partition for the current month was not created'; END IF;
    SET LOCAL TimeZone = 'UTC';                                       -- pg_get_expr prints the bounds in the session zone
    bound := pg_get_expr((SELECT relpartbound FROM pg_class WHERE oid = p), p);
    RESET TimeZone;
    IF bound <> want THEN
        RAISE EXCEPTION 'FAIL raw partition bounds: got %, expected %', bound, want;
    END IF;
    IF pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = p)) <> 'oss911_owner' THEN
        RAISE EXCEPTION 'FAIL the owner of new partition % is not oss911_owner', p;
    END IF;
    RAISE NOTICE 'PASS raw partition bounds are UTC month boundaries and belong to the owner';
END $$;

-- ---------- Ingestion: raw is append-only (DD-02) ----------
SET ROLE :"ingest_user";
SELECT pg_temp.expect_ok    ($$INSERT INTO raw.zz_roles_check VALUES (1, 'a')$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_denied($$UPDATE raw.zz_roles_check SET v = 'b'$$);
SELECT pg_temp.expect_denied($$DELETE FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_denied($$TRUNCATE raw.zz_roles_check$$);
SELECT pg_temp.expect_denied($$CREATE TABLE raw.zz_own (x int)$$);       -- creating it would make it the owner, ending append-only
SELECT pg_temp.expect_denied($$INSERT INTO core.dim_shift (shift_sk) VALUES (gen_random_uuid())$$);
SELECT pg_temp.expect_denied($$SELECT meta.ensure_monthly_partitions('core.fact_call', current_date, current_date)$$);
SELECT pg_temp.expect_ok    ($$UPDATE meta.watermark_state SET updated_at = now() WHERE false$$);
SELECT pg_temp.expect_denied($$DELETE FROM meta.run_log WHERE false$$);
SELECT pg_temp.expect_denied($$UPDATE meta.run_log SET message = 'x' WHERE false$$);   -- written once at the end (0004)
SELECT pg_temp.expect_denied($$UPDATE meta.field_visibility SET is_visible = true WHERE false$$);
-- Batch registry (0004): one row per batch, committed with its raw rows; never changed afterwards
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                                   row_count, rows_written, contract_version)
                               SELECT '00000000-0000-0000-0000-0000000000c1', s, 'zz_check', 'incremental',
                                      '2026-10-14 00:00:00', '2026-10-14 00:05:00', 1, 1, 1
                               FROM generate_series(1, 3) s$$);
SELECT pg_temp.expect_error ($$INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                                   row_count, rows_written, contract_version, file_sha256)
                               VALUES (gen_random_uuid(), 1, 'zz_check', 'incremental', 'a', 'b', 1, 1, 1,
                                       sha256('x'))$$, '%ck_ingest_batch_file%');   -- checksum without size
SELECT pg_temp.expect_denied($$UPDATE meta.ingest_batch SET row_count = 0 WHERE false$$);
-- The re-sweep is a batch mode of its own (0005); anything else is refused
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                                   row_count, rows_written, contract_version)
                               VALUES ('00000000-0000-0000-0000-0000000000c2', 1, 'zz_check', 'resweep', 'a', 'b', 0, 0, 1)$$);
SELECT pg_temp.expect_error ($$INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                                   row_count, rows_written, contract_version)
                               VALUES (gen_random_uuid(), 1, 'zz_check', 'manual', 'a', 'b', 0, 0, 1)$$, '%ck_ingest_batch_mode%');
-- The staging batch registry is dbt's (0005), not ingestion's
SELECT pg_temp.expect_denied($$INSERT INTO meta.staging_batch_state (model, run_id, batch_seq)
                               VALUES ('stg_x', '00000000-0000-0000-0000-0000000000c1', 1)$$);
SELECT pg_temp.expect_denied($$DELETE FROM meta.ingest_batch WHERE false$$);
-- raw.icp_cdr_voice: append only, every row belongs to a registered batch
SELECT pg_temp.expect_ok    ($$INSERT INTO raw.icp_cdr_voice (callid, callidnum, payload, run_id, batch_seq,
                                   contract_version, content_hash)
                               VALUES ('1-1', '-1', '{"CALLID": "1-1"}', '00000000-0000-0000-0000-0000000000c1', 1, 1,
                                       sha256('x'))$$);
SELECT pg_temp.expect_error ($$INSERT INTO raw.icp_cdr_voice (callid, callidnum, payload, run_id, batch_seq,
                                   contract_version, content_hash)
                               VALUES ('1-1', '-1', '{}', gen_random_uuid(), 1, 1, sha256('x'))$$,
                             '%fk_icp_cdr_voice_batch%');                  -- no registered batch
SELECT pg_temp.expect_ok    ($$SELECT count(*) FROM raw.icp_cdr_voice r
                               WHERE NOT EXISTS (SELECT 1 FROM meta.quarantine_batch q
                                                 WHERE q.run_id = r.run_id AND q.batch_seq = r.batch_seq)$$);
SELECT pg_temp.expect_denied($$UPDATE raw.icp_cdr_voice SET callerno = 'x'$$);
SELECT pg_temp.expect_denied($$DELETE FROM raw.icp_cdr_voice$$);
SELECT pg_temp.expect_denied($$TRUNCATE raw.icp_cdr_voice$$);
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
                               VALUES ('zz_check', 'CALLTYPE', '9999', 1)$$);
SELECT pg_temp.expect_denied($$UPDATE meta.clarification_queue SET status = 'closed' WHERE false$$);
-- Hot-tuned parameters: ingestion reads them, never writes them
SELECT pg_temp.expect_ok    ($$SELECT * FROM meta.source_runtime_param$$);
SELECT pg_temp.expect_denied($$INSERT INTO meta.source_runtime_param (source_id, param_name, param_value, updated_by)
                               VALUES ('zz_check', 'lookback_minutes', '30', 'x')$$);
-- Advisory lock per source (DD-85): needs no privilege, but it must work for this identity
SELECT pg_temp.expect_ok    ($$SELECT pg_advisory_unlock(hashtextextended('zz_check', 0))
                               FROM (SELECT pg_try_advisory_lock(hashtextextended('zz_check', 0)) AS got) t
                               WHERE got$$);
-- Quarantine registry (DD-76): ingestion registers, it cannot decide. Its parent is now the batch (0004)
SELECT pg_temp.expect_error ($$INSERT INTO meta.quarantine_batch (run_id, batch_seq, failure_reason)
                               VALUES (gen_random_uuid(), 1, 'zz')$$, '%fk_quarantine_batch_ingest_batch%');
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.run_log (run_id, source_id, contract_version, started_at, outcome)
                               VALUES ('00000000-0000-0000-0000-0000000000c1', 'zz_check', 1, now(), 'success')$$);
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.quarantine_batch (run_id, batch_seq, failure_reason)
                               VALUES ('00000000-0000-0000-0000-0000000000c1', 1, 'zz')$$);
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.quarantine_batch (run_id, batch_seq, failure_reason)
                               VALUES ('00000000-0000-0000-0000-0000000000c1', 2, 'zz')$$);
-- De-duplication must exclude rows of pending and discarded batches, so ingestion must be able to read the
-- quarantine registry (DD-82 items two and six). The privilege comes from 0002's default privileges on meta,
-- not from a separate structural change; this check guards it
SELECT pg_temp.expect_ok    ($$SELECT run_id, batch_seq FROM meta.quarantine_batch WHERE state IN ('pending', 'discarded')$$);
SELECT pg_temp.expect_denied($$UPDATE meta.quarantine_batch SET state = 'discarded' WHERE false$$);
SELECT pg_temp.expect_denied($$DELETE FROM meta.quarantine_batch WHERE false$$);
SELECT pg_temp.expect_denied($$INSERT INTO audit.runtime_param_change_log (source_id, param_name, changed_by, reason)
                               VALUES ('x', 'x', 'x', 'x')$$);
RESET ROLE;

-- ---------- dbt: raw read-only; core insert/update/delete but no DROP; creates staging and mart itself ----------
SET ROLE :"transform_user";
SELECT pg_temp.expect_ok    ($$SELECT * FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM raw.icp_cdr_voice$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM meta.ingest_batch$$);
-- Staging batch registry (0005, DD-89): dbt registers and, when rebuilding a model, clears; only existing batches
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.staging_batch_state (model, run_id, batch_seq)
                               VALUES ('stg_icp_cdr_voice', '00000000-0000-0000-0000-0000000000c1', 1)$$);
SELECT pg_temp.expect_error ($$INSERT INTO meta.staging_batch_state (model, run_id, batch_seq)
                               VALUES ('stg_icp_cdr_voice', gen_random_uuid(), 1)$$, '%fk_staging_batch_state_batch%');
SELECT pg_temp.expect_error ($$INSERT INTO meta.staging_batch_state (model, run_id, batch_seq)
                               VALUES ('Bad Name', '00000000-0000-0000-0000-0000000000c1', 2)$$, '%staging_batch_state_model_check%');
SELECT pg_temp.expect_denied($$UPDATE meta.staging_batch_state SET staged_at = now()$$);
SELECT pg_temp.expect_ok    ($$DELETE FROM meta.staging_batch_state WHERE model = 'stg_icp_cdr_voice'$$);
-- dbt raises clarification items (0006, the 24-hour guard of DD-91 item 9), but does not decide them
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
                               VALUES ('zz_check', 'CALLID', 'x', 1)$$);
SELECT pg_temp.expect_denied($$UPDATE meta.clarification_queue SET status = 'closed' WHERE false$$);
SELECT pg_temp.expect_denied($$INSERT INTO raw.zz_roles_check VALUES (2, 'x')$$);
SELECT pg_temp.expect_denied($$INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                                   row_count, rows_written, contract_version)
                               VALUES (gen_random_uuid(), 1, 'zz_check', 'backfill', 'a', 'b', 0, 0, 1)$$);
SELECT pg_temp.expect_denied($$DELETE FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_ok    ($$DELETE FROM core.fact_call WHERE false$$);
SELECT pg_temp.expect_ok    ($$SELECT core.sk('MAK', 'icp', core.nk('1', '2'))$$);
SELECT pg_temp.expect_denied($$DROP TABLE core.fact_call$$);
SELECT pg_temp.expect_denied($$TRUNCATE core.fact_call$$);
SELECT pg_temp.expect_denied($$ALTER TABLE core.dim_shift ADD COLUMN zz int$$);
SELECT pg_temp.expect_denied($$CREATE TABLE core.zz_own (x int)$$);
SELECT pg_temp.expect_denied($$INSERT INTO audit.access_log (date_key, user_principal, role_code, action)
                               VALUES (to_char(current_date, 'YYYYMMDD')::int, 'x', 'x', 'query')$$);
-- dbt creates tables with role: oss911_transform (set in the profile), so the new tables belong to that role
SET ROLE oss911_transform;
SELECT pg_temp.expect_ok    ($$CREATE TABLE staging.zz_check (x int)$$);
SELECT pg_temp.expect_ok    ($$CREATE TABLE mart.zz_check (x int)$$);
SELECT pg_temp.expect_ok    ($$CREATE TABLE ai.zz_check (x int)$$);
RESET ROLE;

-- ---------- Service layer, query path ----------
SET ROLE :"query_user";
SELECT pg_temp.expect_ok    ($$SELECT * FROM core.fact_call LIMIT 1$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM mart.zz_check$$);          -- default privileges FOR ROLE oss911_transform in effect
SELECT pg_temp.expect_ok    ($$SELECT * FROM ai.zz_check$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM meta.field_visibility$$);
SELECT pg_temp.expect_denied($$SELECT * FROM staging.zz_check$$);       -- the service layer does not read staging (DD-03)
SELECT pg_temp.expect_denied($$SELECT * FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_denied($$SELECT * FROM raw.icp_cdr_voice$$);
SELECT pg_temp.expect_ok    ($$INSERT INTO audit.access_log (date_key, user_principal, role_code, action)
                               VALUES (to_char(current_date, 'YYYYMMDD')::int, 'x', 'x', 'query')$$);
SELECT pg_temp.expect_denied($$UPDATE audit.access_log SET row_count = 0$$);
SELECT pg_temp.expect_denied($$DELETE FROM audit.access_log$$);
SELECT pg_temp.expect_denied($$SELECT * FROM audit.access_log$$);       -- audit queries belong to the admin path (M-07)
SELECT pg_temp.expect_denied($$INSERT INTO meta.field_visibility (role_code, table_ref, column_ref)
                               VALUES ('x', 'x', 'x')$$);
SELECT pg_temp.expect_denied($$DELETE FROM core.fact_call WHERE false$$);
RESET ROLE;

-- ---------- Service layer, admin path ----------
SET ROLE :"admin_user";
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.field_visibility (role_code, table_ref, column_ref)
                               VALUES ('x', 'x', 'x')$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM audit.access_log$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM core.fact_call LIMIT 1$$);   -- inherits the query path
SELECT pg_temp.expect_ok    ($$INSERT INTO audit.permission_change_log (changed_by, object_kind, object_ref)
                               VALUES ('x', 'field_visibility', 'x')$$);
SELECT pg_temp.expect_denied($$UPDATE audit.access_log SET row_count = 0$$);
SELECT pg_temp.expect_denied($$DELETE FROM meta.sql_grant WHERE false$$); -- revocation goes through is_revoked
SELECT pg_temp.expect_denied($$DELETE FROM meta.field_visibility WHERE false$$);
SELECT pg_temp.expect_denied($$UPDATE meta.watermark_state SET updated_at = now() WHERE false$$);
-- Quarantine decisions (DD-76): only the four decision columns can change; a reason is required; the decision is written once
SELECT pg_temp.expect_error ($$UPDATE meta.quarantine_batch SET state = 'released', handled_by = 'x', handled_at = now()
                               WHERE batch_seq = 1$$, '%ck_quarantine_handled%');
SELECT pg_temp.expect_ok    ($$UPDATE meta.quarantine_batch SET state = 'released', handled_by = 'x', handled_at = now(),
                               handling_reason = 'confirmed with the source' WHERE batch_seq = 1$$);
SELECT pg_temp.expect_error ($$UPDATE meta.quarantine_batch SET state = 'discarded', handled_by = 'x', handled_at = now(),
                               handling_reason = 'changed my mind' WHERE batch_seq = 1$$, :'err_quarantine_final');
SELECT pg_temp.expect_denied($$UPDATE meta.quarantine_batch SET failure_reason = 'x' WHERE batch_seq = 2$$);
SELECT pg_temp.expect_denied($$DELETE FROM meta.quarantine_batch WHERE batch_seq = 2$$);
-- Runtime parameter change log (DD-75): insert only, reason required
SELECT pg_temp.expect_ok    ($$INSERT INTO audit.runtime_param_change_log (source_id, param_name, old_value, new_value, changed_by, reason)
                               VALUES ('x', 'interval_minutes', '5', '10', 'x', 'source rate limit')$$);
SELECT pg_temp.expect_error ($$INSERT INTO audit.runtime_param_change_log (source_id, param_name, changed_by, reason)
                               VALUES ('x', 'x', 'x', '  ')$$, '%runtime_param_change_log_reason_check%');
SELECT pg_temp.expect_ok    ($$SELECT * FROM audit.runtime_param_change_log$$);
SELECT pg_temp.expect_denied($$UPDATE audit.runtime_param_change_log SET reason = 'x'$$);
SELECT pg_temp.expect_denied($$DELETE FROM audit.runtime_param_change_log$$);
-- Contract copy (DD-88 item three): only start / stop is changed on the admin path
SELECT pg_temp.expect_ok    ($$UPDATE meta.source_contract SET enabled = false WHERE source_id = 'zz_check'$$);
SELECT pg_temp.expect_denied($$UPDATE meta.source_contract SET definition = '{}' WHERE source_id = 'zz_check'$$);
SELECT pg_temp.expect_denied($$UPDATE meta.source_contract SET contract_version = 2 WHERE source_id = 'zz_check'$$);
-- Hot-tuned parameters (DD-88 item six): whitelist in the CHECK; start / stop and the interval are not in it
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.source_runtime_param (source_id, param_name, param_value, updated_by)
                               VALUES ('zz_check', 'lookback_minutes', '30', 'x')$$);
SELECT pg_temp.expect_ok    ($$UPDATE meta.source_runtime_param SET param_value = '20', updated_at = now()
                               WHERE source_id = 'zz_check' AND param_name = 'lookback_minutes'$$);
SELECT pg_temp.expect_error ($$UPDATE meta.source_runtime_param SET param_value = '0'
                               WHERE source_id = 'zz_check'$$, '%ck_source_runtime_param_whitelist%');
SELECT pg_temp.expect_error ($$UPDATE meta.source_runtime_param SET param_value = '15m'
                               WHERE source_id = 'zz_check'$$, '%ck_source_runtime_param_whitelist%');
SELECT pg_temp.expect_error ($$INSERT INTO meta.source_runtime_param (source_id, param_name, param_value, updated_by)
                               VALUES ('zz_check', 'enabled', 'false', 'x')$$, '%ck_source_runtime_param_whitelist%');
SELECT pg_temp.expect_error ($$INSERT INTO meta.source_runtime_param (source_id, param_name, param_value, updated_by)
                               VALUES ('zz_check', 'interval_minutes', '10', 'x')$$, '%ck_source_runtime_param_whitelist%');
SELECT pg_temp.expect_ok    ($$DELETE FROM meta.source_runtime_param WHERE source_id = 'zz_check'$$);
SELECT pg_temp.expect_denied($$INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                                   row_count, rows_written, contract_version)
                               VALUES (gen_random_uuid(), 1, 'zz_check', 'backfill', 'a', 'b', 0, 0, 1)$$);
RESET ROLE;

-- ---------- Core load rules (0006, DD-91) ----------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM core.dim_site WHERE site_code = 'MKK' AND site_sk = core.site_namespace('MKK')) THEN
        RAISE EXCEPTION 'FAIL dim_site has no MKK row keyed by its namespace';
    END IF;
    RAISE NOTICE 'PASS site MKK';
END $$;
SELECT pg_temp.expect_error($$INSERT INTO core.fact_call_leg (call_leg_sk, date_key, site_sk, call_sk, src_natural_key, call_id,
                               leg_seq, is_last_leg, device_type_code, wait_begin, src_system, run_id, contract_version,
                               src_observed_at, direction)
                              VALUES (gen_random_uuid(), to_char(current_date, 'YYYYMMDD')::int, gen_random_uuid(),
                                      gen_random_uuid(), 'x', 'x', 1, false, 1, now(), 'icp', gen_random_uuid(), 1, now(),
                                      'other')$$, '%ck_fact_call_leg_direction%');
SELECT pg_temp.expect_ok   ($$INSERT INTO core.fact_call_leg (call_leg_sk, date_key, site_sk, call_sk, src_natural_key, call_id,
                               leg_seq, is_last_leg, device_type_code, wait_begin, src_system, run_id, contract_version,
                               src_observed_at, direction, release_source)
                              VALUES (gen_random_uuid(), to_char(current_date, 'YYYYMMDD')::int, gen_random_uuid(),
                                      gen_random_uuid(), 'x', 'x', 1, false, 1, now(), 'icp', gen_random_uuid(), 1, now(),
                                      'unmapped', 'unmapped')$$);
SELECT pg_temp.expect_error($$INSERT INTO core.fact_call_leg (call_leg_sk, date_key, site_sk, call_sk, src_natural_key, call_id,
                               leg_seq, is_last_leg, device_type_code, wait_begin, src_system, run_id, contract_version)
                              VALUES (gen_random_uuid(), to_char(current_date, 'YYYYMMDD')::int, gen_random_uuid(),
                                      gen_random_uuid(), 'x', 'x', 1, false, 1, now(), 'icp', gen_random_uuid(), 1)$$,
                            '%src_observed_at%');

-- ---------- Ownership and PUBLIC ----------
DO $$
DECLARE bad text;
BEGIN
    SELECT string_agg(n.nspname || '.' || c.relname, ', ') INTO bad
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname IN ('raw','core','meta','audit') AND c.relkind IN ('r','p')
       AND c.relowner <> 'oss911_owner'::regrole;
    IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL owner is not oss911_owner: %', bad; END IF;

    SELECT string_agg(n.nspname, ', ') INTO bad
      FROM pg_namespace n
     WHERE n.nspname IN ('raw','staging','core','mart','ai','meta','audit')
       AND has_schema_privilege('public', n.oid, 'USAGE');
    IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL PUBLIC has USAGE on these schemas: %', bad; END IF;
    RAISE NOTICE 'PASS ownership and PUBLIC';
END $$;

ROLLBACK;
\echo 'db_roles_check: all checks passed (rolled back, no trace left)'
