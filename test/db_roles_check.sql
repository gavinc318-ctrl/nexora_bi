-- =====================================================================
-- Verification of database roles and grants (structural changes 0002 · 0003 · DD-74 · DD-75 · DD-76)
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

-- Functional Chinese (allowed by tools/check_no_cjk.py with the cjk-ok marker): the exception messages raised
-- by the already-executed changes 0002 and 0003 are in Chinese, and those files must not be edited (DD-73),
-- so the patterns that match them must be Chinese too. Kept together here and referenced as psql variables.
SELECT '只能为 core 与 audit 下的分区表建分区%' AS err_partition_scope,  -- cjk-ok: raised by meta.ensure_monthly_partitions (change 0002)
       '%处理结论不可改%'                       AS err_quarantine_final  -- cjk-ok: raised by meta.quarantine_batch_guard (change 0003)
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

-- ---------- Orchestration (transform): create partitions ----------
SET ROLE :"transform_user";
SELECT pg_temp.expect_ok($$SELECT meta.ensure_monthly_partitions('core.fact_call', current_date, current_date)$$);
SELECT pg_temp.expect_ok($$SELECT meta.ensure_monthly_partitions('audit.access_log', current_date, current_date)$$);
SELECT pg_temp.expect_error($$SELECT meta.ensure_monthly_partitions('meta.srid_config', current_date, current_date)$$,
                            :'err_partition_scope');
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
SELECT pg_temp.expect_denied($$UPDATE meta.field_visibility SET is_visible = true WHERE false$$);
-- Quarantine registry (DD-76): ingestion registers, it cannot decide
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.run_log (run_id, source_id, contract_version, started_at, outcome)
                               VALUES ('00000000-0000-0000-0000-0000000000c1', 'zz_check', 1, now(), 'quarantined')$$);
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
SELECT pg_temp.expect_denied($$INSERT INTO raw.zz_roles_check VALUES (2, 'x')$$);
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
RESET ROLE;

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
