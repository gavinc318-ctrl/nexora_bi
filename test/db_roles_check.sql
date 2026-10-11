-- =====================================================================
-- 库角色与授权的验证（结构变更 0002）
--
-- 以【每个真实登录用户】的身份去验它自己，不用超级用户的身份代验
-- （CLAUDE.md 第 5 节第二条）。做法是超级用户会话里 SET ROLE 到被测登录用户：
-- 权限判定看 current_user，与该用户直连时一致；好处是全程一个事务，结尾回滚，
-- 在真集群上跑也不留任何痕迹。
-- 验不到的：登录用户能否经 pgBouncer 认证——那是 21-connection 的事。
--
-- 用法（四个变量为各环境的登录用户名）：
--   sudo -u postgres psql -d oss911 -X -v ON_ERROR_STOP=1 \
--     -v ingest_user=... -v transform_user=... -v query_user=... -v admin_user=... \
--     < test/db_roles_check.sql
-- 用重定向而非 -f：postgres 读不到 /home 下的文件，重定向由调用者的 shell 完成
-- （troubleshooting 第 23 条）。
--
-- 任一检查失败即 RAISE，ON_ERROR_STOP 使脚本以非零退出。
-- =====================================================================
\set ON_ERROR_STOP 1
\set QUIET on
\pset tuples_only on
\pset format unaligned
BEGIN;

CREATE FUNCTION pg_temp.expect_ok(p_sql text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE p_sql;
    RAISE NOTICE 'PASS 允许 [%] %', current_user, p_sql;
EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'FAIL 应允许却失败 [%] % —— %', current_user, p_sql, SQLERRM;
END $$;

CREATE FUNCTION pg_temp.expect_denied(p_sql text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS 拒绝 [%] %', current_user, p_sql;
        RETURN;
    END;
    RAISE EXCEPTION 'FAIL 应拒绝却成功 [%] %', current_user, p_sql;
END $$;

CREATE FUNCTION pg_temp.expect_error(p_sql text, p_like text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM LIKE p_like THEN
            RAISE NOTICE 'PASS 报错 [%] %', current_user, p_sql;
            RETURN;
        END IF;
        RAISE EXCEPTION 'FAIL 报错内容不符 [%] % —— %', current_user, p_sql, SQLERRM;
    END;
    RAISE EXCEPTION 'FAIL 应报错却成功 [%] %', current_user, p_sql;
END $$;

-- ---------- 成员关系：登录用户确实在相应的权限角色里 ----------
-- psql 变量不会在 $$ 内展开，先经会话设置传进去
SELECT set_config('roles_check.ingest',    :'ingest_user',    true),
       set_config('roles_check.transform', :'transform_user', true),
       set_config('roles_check.query',     :'query_user',     true),
       set_config('roles_check.admin',     :'admin_user',     true) \gset
DO $$
DECLARE
    pairs text[][] := ARRAY[[current_setting('roles_check.ingest'),    'oss911_ingest'],
                            [current_setting('roles_check.transform'), 'oss911_transform'],
                            [current_setting('roles_check.query'),     'oss911_query_svc'],
                            [current_setting('roles_check.admin'),     'oss911_admin_svc']];
    i int;
BEGIN
    FOR i IN 1 .. array_length(pairs, 1) LOOP
        IF NOT pg_has_role(pairs[i][1], pairs[i][2], 'USAGE') THEN
            RAISE EXCEPTION 'FAIL % 不是 % 的成员（或未继承其权限）', pairs[i][1], pairs[i][2];
        END IF;
    END LOOP;
    -- dbt 以 role: oss911_transform 运行，须能 SET ROLE 过去
    IF NOT pg_has_role(current_setting('roles_check.transform'), 'oss911_transform', 'SET') THEN
        RAISE EXCEPTION 'FAIL % 不能 SET ROLE oss911_transform', current_setting('roles_check.transform');
    END IF;
    RAISE NOTICE 'PASS 成员关系';
END $$;

-- ---------- 夹具：以属主身份建一张 raw 表，走默认权限 ----------
SET ROLE oss911_owner;
CREATE TABLE raw.zz_roles_check (id integer, v text);
RESET ROLE;

-- ---------- 编排（transform）：建分区 ----------
SET ROLE :"transform_user";
SELECT pg_temp.expect_ok($$SELECT meta.ensure_daily_partitions('core.fact_call', current_date, current_date)$$);
SELECT pg_temp.expect_ok($$SELECT meta.ensure_daily_partitions('audit.access_log', current_date, current_date)$$);
SELECT pg_temp.expect_error($$SELECT meta.ensure_daily_partitions('meta.srid_config', current_date, current_date)$$,
                            '只能为 core 与 audit 下的分区表建分区%');
RESET ROLE;

-- 分区函数建出的分区归属主，且继承默认权限
DO $$
DECLARE p regclass := to_regclass('core.fact_call_p' || to_char(current_date, 'YYYYMMDD'));
BEGIN
    IF pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = p)) <> 'oss911_owner' THEN
        RAISE EXCEPTION 'FAIL 新分区 % 的属主不是 oss911_owner', p;
    END IF;
    RAISE NOTICE 'PASS 新分区属主';
END $$;

-- ---------- 接入：raw 只追加（DD-02） ----------
SET ROLE :"ingest_user";
SELECT pg_temp.expect_ok    ($$INSERT INTO raw.zz_roles_check VALUES (1, 'a')$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_denied($$UPDATE raw.zz_roles_check SET v = 'b'$$);
SELECT pg_temp.expect_denied($$DELETE FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_denied($$TRUNCATE raw.zz_roles_check$$);
SELECT pg_temp.expect_denied($$CREATE TABLE raw.zz_own (x int)$$);       -- 自建即成属主，只追加随之失效
SELECT pg_temp.expect_denied($$INSERT INTO core.dim_shift (shift_sk) VALUES (gen_random_uuid())$$);
SELECT pg_temp.expect_denied($$SELECT meta.ensure_daily_partitions('core.fact_call', current_date, current_date)$$);
SELECT pg_temp.expect_ok    ($$UPDATE meta.watermark_state SET updated_at = now() WHERE false$$);
SELECT pg_temp.expect_denied($$DELETE FROM meta.run_log WHERE false$$);
SELECT pg_temp.expect_denied($$UPDATE meta.field_visibility SET is_visible = true WHERE false$$);
RESET ROLE;

-- ---------- dbt：raw 只读；core 增删改不可删表；自建 staging · mart ----------
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
-- dbt 以 role: oss911_transform 建表（profile 中设定），新表属主是该角色
SET ROLE oss911_transform;
SELECT pg_temp.expect_ok    ($$CREATE TABLE staging.zz_check (x int)$$);
SELECT pg_temp.expect_ok    ($$CREATE TABLE mart.zz_check (x int)$$);
SELECT pg_temp.expect_ok    ($$CREATE TABLE ai.zz_check (x int)$$);
RESET ROLE;

-- ---------- 服务层查询路径 ----------
SET ROLE :"query_user";
SELECT pg_temp.expect_ok    ($$SELECT * FROM core.fact_call LIMIT 1$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM mart.zz_check$$);          -- 默认权限 FOR ROLE oss911_transform 生效
SELECT pg_temp.expect_ok    ($$SELECT * FROM ai.zz_check$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM meta.field_visibility$$);
SELECT pg_temp.expect_denied($$SELECT * FROM staging.zz_check$$);       -- 服务层不读 staging（DD-03）
SELECT pg_temp.expect_denied($$SELECT * FROM raw.zz_roles_check$$);
SELECT pg_temp.expect_ok    ($$INSERT INTO audit.access_log (date_key, user_principal, role_code, action)
                               VALUES (to_char(current_date, 'YYYYMMDD')::int, 'x', 'x', 'query')$$);
SELECT pg_temp.expect_denied($$UPDATE audit.access_log SET row_count = 0$$);
SELECT pg_temp.expect_denied($$DELETE FROM audit.access_log$$);
SELECT pg_temp.expect_denied($$SELECT * FROM audit.access_log$$);       -- 审计查询是管理路径（M-07）
SELECT pg_temp.expect_denied($$INSERT INTO meta.field_visibility (role_code, table_ref, column_ref)
                               VALUES ('x', 'x', 'x')$$);
SELECT pg_temp.expect_denied($$DELETE FROM core.fact_call WHERE false$$);
RESET ROLE;

-- ---------- 服务层管理路径 ----------
SET ROLE :"admin_user";
SELECT pg_temp.expect_ok    ($$INSERT INTO meta.field_visibility (role_code, table_ref, column_ref)
                               VALUES ('x', 'x', 'x')$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM audit.access_log$$);
SELECT pg_temp.expect_ok    ($$SELECT * FROM core.fact_call LIMIT 1$$);   -- 继承查询路径
SELECT pg_temp.expect_ok    ($$INSERT INTO audit.permission_change_log (changed_by, object_kind, object_ref)
                               VALUES ('x', 'field_visibility', 'x')$$);
SELECT pg_temp.expect_denied($$UPDATE audit.access_log SET row_count = 0$$);
SELECT pg_temp.expect_denied($$DELETE FROM meta.sql_grant WHERE false$$); -- 撤销走 is_revoked
SELECT pg_temp.expect_denied($$DELETE FROM meta.field_visibility WHERE false$$);
SELECT pg_temp.expect_denied($$UPDATE meta.watermark_state SET updated_at = now() WHERE false$$);
RESET ROLE;

-- ---------- 属主与 PUBLIC ----------
DO $$
DECLARE bad text;
BEGIN
    SELECT string_agg(n.nspname || '.' || c.relname, ', ') INTO bad
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname IN ('raw','core','meta','audit') AND c.relkind IN ('r','p')
       AND c.relowner <> 'oss911_owner'::regrole;
    IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL 属主不是 oss911_owner：%', bad; END IF;

    SELECT string_agg(n.nspname, ', ') INTO bad
      FROM pg_namespace n
     WHERE n.nspname IN ('raw','staging','core','mart','ai','meta','audit')
       AND has_schema_privilege('public', n.oid, 'USAGE');
    IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL PUBLIC 对以下 schema 有 USAGE：%', bad; END IF;
    RAISE NOTICE 'PASS 属主与 PUBLIC';
END $$;

ROLLBACK;
\echo 'db_roles_check: 全部通过（已回滚，未留痕）'
