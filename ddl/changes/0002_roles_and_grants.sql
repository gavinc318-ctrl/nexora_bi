-- =====================================================================
-- OSS911 BI/DW  结构变更 0002  库角色、属主与授权
-- 依据：DD-02 · DD-03 · DD-17 · LD-04 第 1 章 · LD-06 2.1 · NOTES.md「数据库角色划分」
--       （待登记为 DD）
--
-- 本文件只建五个 NOLOGIN 的权限角色。可登录的用户与口令按环境由剧本建立并
-- 加入相应角色——本文件随发布制品进入生产，不得含口令或环境相关的用户名。
--
--   oss911_owner      持有 raw · core · meta · audit 的全部对象与七个 schema
--   oss911_ingest     接入。对 raw 只有 SELECT 与 INSERT ——raw 只追加由权限执行（DD-02）
--   oss911_transform  dbt。对 raw 只读；对 core 增删改但不能 DROP；
--                     staging · mart · ai 由它在运行时建，属主是它
--   oss911_query_svc  服务层查询路径。读 core · mart · ai · meta；对 audit 只 INSERT
--   oss911_admin_svc  服务层管理路径（M-* 界面，LD-06 2.1）。继承 query_svc，
--                     另对权限与参照配置表有写权限
--
-- 写权限只授予设计文档中有明确依据的；依据不明的一律不授。少授会当场报错，
-- 补一个变更即可；多授不会报错，只会静默留着。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 一、五个权限角色
-- 角色是集群级对象，不随库走。已存在时不跳过，而是断言其属性——一个同名但
-- 可登录或带超级权限的角色，比没有这个角色更危险。
-- ---------------------------------------------------------------------
DO $$
DECLARE
    r   text;
    rec pg_roles%ROWTYPE;
BEGIN
    FOREACH r IN ARRAY ARRAY['oss911_owner','oss911_ingest','oss911_transform',
                             'oss911_query_svc','oss911_admin_svc'] LOOP
        SELECT * INTO rec FROM pg_roles WHERE rolname = r;
        IF NOT FOUND THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        ELSIF rec.rolcanlogin OR rec.rolsuper OR rec.rolcreaterole OR rec.rolcreatedb
              OR rec.rolreplication OR rec.rolbypassrls THEN
            RAISE EXCEPTION '角色 % 已存在但属性不符：权限角色必须 NOLOGIN 且不带任何特权属性', r;
        END IF;
    END LOOP;
END $$;

GRANT oss911_query_svc TO oss911_admin_svc;

-- ---------------------------------------------------------------------
-- 二、分区函数改为 SECURITY DEFINER
-- CREATE TABLE ... PARTITION OF 要求调用者是父表属主。编排每日调用本函数时
-- 不是属主身份，不改则必然失败。改为以属主身份执行，并收窄到 core 与 audit
-- 的分区表——SECURITY DEFINER 函数接受表名参数，不收窄就是一个以属主身份
-- 对任意表建分区的入口。
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION meta.ensure_daily_partitions(
    p_table text, p_from date, p_to date) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    d    date    := p_from;
    n    integer := 0;
    sch  text    := split_part(p_table, '.', 1);
    tbl  text    := split_part(p_table, '.', 2);
    part text;
    k1   integer;
    k2   integer;
BEGIN
    IF sch NOT IN ('core', 'audit') OR NOT EXISTS (
           SELECT 1 FROM pg_partitioned_table pt
           JOIN pg_class c ON c.oid = pt.partrelid
           JOIN pg_namespace ns ON ns.oid = c.relnamespace
           WHERE ns.nspname = sch AND c.relname = tbl) THEN
        RAISE EXCEPTION '只能为 core 与 audit 下的分区表建分区：%', p_table;
    END IF;
    WHILE d <= p_to LOOP
        part := tbl || '_p' || to_char(d, 'YYYYMMDD');
        k1   := to_char(d,     'YYYYMMDD')::integer;
        k2   := to_char(d + 1, 'YYYYMMDD')::integer;
        IF to_regclass(format('%I.%I', sch, part)) IS NULL THEN
            EXECUTE format('CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%s) TO (%s)',
                           sch, part, sch, tbl, k1, k2);
            n := n + 1;
        END IF;
        d := d + 1;
    END LOOP;
    RETURN n;
END $$;

-- ---------------------------------------------------------------------
-- 三、属主归一
-- 0000 与 0001 由执行者（postgres）执行，对象属主是超级用户。全部改归 oss911_owner。
-- 扩展自带的对象不动（它们在 public，且随扩展升级）。
-- ALTER TABLE ... OWNER 不会级联到分区，故分区逐个改；标识列的序列随表一起改。
-- ---------------------------------------------------------------------
DO $$
DECLARE o record;
BEGIN
    FOR o IN SELECT n.nspname FROM pg_namespace n
             WHERE n.nspname IN ('raw','staging','core','mart','ai','meta','audit') LOOP
        EXECUTE format('ALTER SCHEMA %I OWNER TO oss911_owner', o.nspname);
    END LOOP;

    FOR o IN SELECT n.nspname, c.relname, c.relkind
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname IN ('raw','staging','core','mart','ai','meta','audit')
               AND c.relkind IN ('r','p','v','m','f') LOOP
        EXECUTE format('ALTER %s %I.%I OWNER TO oss911_owner',
                       CASE o.relkind WHEN 'v' THEN 'VIEW'
                                      WHEN 'm' THEN 'MATERIALIZED VIEW'
                                      WHEN 'f' THEN 'FOREIGN TABLE'
                                      ELSE 'TABLE' END,
                       o.nspname, o.relname);
    END LOOP;

    FOR o IN SELECT p.oid::regprocedure AS fn
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('core','meta')
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                               WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                                 AND d.deptype = 'e') LOOP
        EXECUTE format('ALTER ROUTINE %s OWNER TO oss911_owner', o.fn);
    END LOOP;
END $$;

ALTER TABLE meta.ddl_change
    ADD CONSTRAINT fk_ddl_change_release
    FOREIGN KEY (release_version) REFERENCES meta.release_registry (release_version);

-- ---------------------------------------------------------------------
-- 四、schema 权限
-- ---------------------------------------------------------------------
REVOKE ALL ON SCHEMA raw, staging, core, mart, ai, meta, audit FROM PUBLIC;

GRANT USAGE          ON SCHEMA raw                 TO oss911_ingest, oss911_transform;
GRANT USAGE, CREATE  ON SCHEMA staging, mart, ai   TO oss911_transform;
GRANT USAGE          ON SCHEMA mart, ai, core      TO oss911_query_svc;
GRANT USAGE          ON SCHEMA core                TO oss911_transform;
GRANT USAGE          ON SCHEMA meta                TO oss911_ingest, oss911_transform, oss911_query_svc;
GRANT USAGE          ON SCHEMA audit               TO oss911_query_svc;
-- staging 不对服务层开放：服务层只从 core · mart · ai 取数（DD-03）

-- ---------------------------------------------------------------------
-- 五、表权限：现有对象一次授全，将来的对象靠默认权限
-- 默认权限只作用于「由指定角色建的」对象：
--   FOR ROLE oss911_owner      —— 结构变更（run_as=owner）与分区函数建出的对象
--   FOR ROLE oss911_transform  —— dbt 建出的对象。dbt 的 profile 必须设
--                                 role: oss911_transform，否则属主是登录用户，
--                                 这里的默认权限不生效
-- ---------------------------------------------------------------------

-- raw：接入只追加，dbt 只读（DD-02）。不授 UPDATE · DELETE · TRUNCATE
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA raw TO oss911_ingest;
GRANT SELECT         ON ALL TABLES IN SCHEMA raw TO oss911_transform;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_owner IN SCHEMA raw
    GRANT SELECT, INSERT ON TABLES TO oss911_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_owner IN SCHEMA raw
    GRANT SELECT ON TABLES TO oss911_transform;

-- core：dbt 增量写入既有表，不能 DROP（不是属主）。core 表由 DDL 定义且为分区表，
-- 不该被 dbt 重建——--full-refresh 会因此失败，这是期望的行为
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO oss911_transform;
GRANT SELECT                         ON ALL TABLES IN SCHEMA core TO oss911_query_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_owner IN SCHEMA core
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oss911_transform;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_owner IN SCHEMA core
    GRANT SELECT ON TABLES TO oss911_query_svc;

-- mart · ai：dbt 建，服务层读
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_transform IN SCHEMA mart
    GRANT SELECT ON TABLES TO oss911_query_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_transform IN SCHEMA ai
    GRANT SELECT ON TABLES TO oss911_query_svc;

-- meta：都可读；写权限逐表列出，依据写在行尾
GRANT SELECT ON ALL TABLES IN SCHEMA meta TO oss911_ingest, oss911_transform, oss911_query_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_owner IN SCHEMA meta
    GRANT SELECT ON TABLES TO oss911_ingest, oss911_transform, oss911_query_svc;

GRANT INSERT, UPDATE ON meta.watermark_state  TO oss911_ingest;     -- 水位推进
GRANT INSERT, UPDATE ON meta.run_log          TO oss911_ingest;     -- 开始时插入，结束时回填 finished_at 与 outcome
GRANT INSERT, UPDATE ON meta.value_profile    TO oss911_ingest;     -- LD-01 7.1：值域外的取值写入 value_profile
GRANT INSERT         ON meta.mapping_registry TO oss911_ingest;     -- 结构漂移发现的新列落 unknown（DD-07）

GRANT INSERT, UPDATE ON meta.field_visibility       TO oss911_admin_svc;  -- M-06
GRANT INSERT, UPDATE ON meta.user_department_map    TO oss911_admin_svc;  -- M-06
GRANT INSERT, UPDATE ON meta.sql_grant              TO oss911_admin_svc;  -- M-06。撤销走 is_revoked，不删行
GRANT INSERT, UPDATE ON meta.label_override         TO oss911_admin_svc;  -- M-10
GRANT INSERT, UPDATE ON meta.reference_value        TO oss911_admin_svc;  -- M-10。源端删除置 is_current 为假，不删行
GRANT INSERT, UPDATE ON meta.search_criteria        TO oss911_admin_svc;  -- DD-34：条件集在管理界面可配置
GRANT INSERT, UPDATE ON meta.availability_threshold TO oss911_admin_svc;  -- DD-57：阈值是业务口径，可调
GRANT UPDATE         ON meta.source_contract        TO oss911_admin_svc;  -- M-01 热调运行参数（LD-06 2.4）

-- audit：只增不改。查询路径只写访问与投递留痕；管理路径另写变更留痕并可查询（M-07）
GRANT INSERT ON audit.access_log, audit.delivery_log TO oss911_query_svc;
GRANT INSERT ON audit.permission_change_log, audit.reference_data_change_log TO oss911_admin_svc;
GRANT SELECT ON ALL TABLES IN SCHEMA audit TO oss911_admin_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_owner IN SCHEMA audit
    GRANT SELECT ON TABLES TO oss911_admin_svc;

-- ---------------------------------------------------------------------
-- 六、函数权限
-- PostgreSQL 默认把新函数的 EXECUTE 授予 PUBLIC。对 SECURITY DEFINER 函数
-- 这等于人人可以借属主身份执行，故默认收回，逐个授予。
-- ---------------------------------------------------------------------
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA core, meta FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE oss911_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core TO oss911_transform;          -- 代理键与坐标系函数
GRANT EXECUTE ON FUNCTION meta.ensure_daily_partitions(text, date, date)
    TO oss911_transform;                                                    -- 由编排每日调用

-- ---------------------------------------------------------------------
-- 七、自检：本变更的结果用查询复核，不用肉眼复核
-- ---------------------------------------------------------------------
DO $$
DECLARE bad text;
BEGIN
    SELECT string_agg(n.nspname || '.' || c.relname || '(' || pg_get_userbyid(c.relowner) || ')', ', ')
      INTO bad
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname IN ('raw','core','meta','audit')
       AND c.relkind IN ('r','p','v','m','f','S','i','I')
       AND c.relowner <> 'oss911_owner'::regrole;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION '以下对象的属主不是 oss911_owner：%', bad;
    END IF;

    SELECT string_agg(p.oid::regprocedure::text, ', ')
      INTO bad
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname IN ('core','meta')
       AND has_function_privilege('public', p.oid, 'EXECUTE');
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION '以下函数仍对 PUBLIC 开放 EXECUTE：%', bad;
    END IF;
END $$;
