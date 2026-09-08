-- =====================================================================
-- OSS911 BI/DW  LD-02  01  schema 与公共函数
-- 依据：HLD v0.3 第 5 章、DD-01/02/03/12/23/32/33/38
-- 说明：本文件在 Git 中受控，是结构性配置（HLD 9.2），变更须走发布流程。
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS postgis;          -- PG 扩展，非第二个数据库产品（CN-01）

CREATE SCHEMA IF NOT EXISTS raw;       COMMENT ON SCHEMA raw     IS '原始落地：与源系统逐字一致，只追加，永不覆盖（DD-02）';
CREATE SCHEMA IF NOT EXISTS staging;   COMMENT ON SCHEMA staging IS '清洗：类型规范、去重、软删除与迟到处理、语义映射。一源一模型，不跨源关联';
CREATE SCHEMA IF NOT EXISTS core;      COMMENT ON SCHEMA core    IS '核心模型：唯一事实来源。mart 与 ai 均为其投影（DD-03）';
CREATE SCHEMA IF NOT EXISTS mart;      COMMENT ON SCHEMA mart    IS '主题层：面向报表与仪表板的聚合，按五个主题域分组';
CREATE SCHEMA IF NOT EXISTS ai;        COMMENT ON SCHEMA ai      IS 'AI 数据层：实体级宽表、特征表、媒体索引、导出快照';
CREATE SCHEMA IF NOT EXISTS meta;      COMMENT ON SCHEMA meta    IS '元数据：契约、映射登记、指标注册、血缘、水位、值剖析、权限矩阵';
CREATE SCHEMA IF NOT EXISTS audit;     COMMENT ON SCHEMA audit   IS '审计：访问与导出留痕，只增不改';

-- ---------------------------------------------------------------------
-- 代理键：确定性 UUID v5
-- 同一条源记录无论重建多少次、在哪个环境，得到同一个主键。
-- 这是 raw 只追加、向上重建（DD-02）能够成立的前提：core 重建后，
-- mart / ai / audit / fact_ai_inference 中已有的引用仍然有效。
-- 自然键取自接入契约的 idempotency_key（HLD 4.2），两处口径天然一致。
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.site_namespace(p_site_code text)
RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
    -- 站点命名空间：跨站合并不冲突（REQ-MOD-05 · DD-23）
    SELECT uuid_generate_v5('6ba7b810-9dad-11d1-80b4-00c04fd430c8'::uuid,
                            'oss911:site:' || p_site_code)
$$;

-- ---------------------------------------------------------------------
-- 自然键的规范化拼接
-- idempotency_key 在接入契约中声明的是「哪几列构成这条记录的身份」，
-- 不是一个算出来的值。本函数把这几列的值拼成唯一的一个字符串。
--
-- 必须转义：若只用分隔符简单相连，('A|B') 与 ('A','B') 会得到同一个字符串，
-- 两条不同的记录撞成同一个主键，且不会报错，是静默合并。
-- 必须拒绝 NULL：把 NULL 当成空串处理，会让缺列的记录与真正的空值撞键。
-- 大小写、去空格、补零等规范化不在此处做——那是语义决定，按源在契约的
-- idempotency_key.normalize 中声明，由 staging 模型执行。本函数只负责转义与拼接。
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.nk(VARIADIC p_parts text[])
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE x text; acc text[] := '{}';
BEGIN
    IF p_parts IS NULL OR array_length(p_parts, 1) IS NULL THEN
        RAISE EXCEPTION '自然键不能为空：接入契约中未声明 idempotency_key 的源不得进入 core';
    END IF;
    FOREACH x IN ARRAY p_parts LOOP
        IF x IS NULL THEN
            RAISE EXCEPTION '自然键的组成部分不允许为 NULL（%）', p_parts;
        END IF;
        acc := acc || replace(replace(x, E'\\', E'\\\\'), '|', E'\\|');
    END LOOP;
    RETURN array_to_string(acc, '|');
END $$;
COMMENT ON FUNCTION core.nk IS '把契约声明的幂等键各列的值拼成规范形式。转义反斜杠与竖线，拒绝 NULL';

CREATE OR REPLACE FUNCTION core.sk(p_site_code text, p_src_system text, p_natural_key text)
RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
    SELECT uuid_generate_v5(core.site_namespace(p_site_code),
                            p_src_system || ':' || p_natural_key)
$$;
-- p_natural_key 一律由 core.nk() 产生，不得直接传原始列值
COMMENT ON FUNCTION core.sk IS '事实与维度的代理键。确定性、幂等、跨站唯一。禁止使用自增序列作为代理键';

CREATE OR REPLACE FUNCTION core.sk_scd(p_site_code text, p_src_system text,
                                       p_natural_key text, p_valid_from timestamptz)
RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
    -- 缓变维 Type 2：同一业务实体的每个版本一个键（DD-12）
    SELECT uuid_generate_v5(core.site_namespace(p_site_code),
             p_src_system || ':' || p_natural_key || '@' ||
             to_char(p_valid_from AT TIME ZONE 'UTC', 'YYYYMMDDHH24MISS'))
$$;

-- ---------------------------------------------------------------------
-- 坐标系：三项参数化（DD-33）
--   srid_source   各源的坐标系，写在接入契约中，一源一值
--   srid_storage  内部统一存储，默认 WGS84
--   srid_analysis 网格聚合与距离计算，默认 UTM 37N（麦加）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta.srid_config (
    config_key   text PRIMARY KEY,
    srid         integer     NOT NULL,
    description  text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
INSERT INTO meta.srid_config(config_key, srid, description) VALUES
  ('srid_storage' , 4326 , 'WGS84。core 与 mart 中全部几何字段的存储坐标系'),
  ('srid_analysis', 32637, 'UTM Zone 37N。网格聚合、面积与距离计算必须在此投影下进行')
ON CONFLICT (config_key) DO NOTHING;

CREATE OR REPLACE FUNCTION core.srid(p_key text)
RETURNS integer LANGUAGE sql STABLE AS $$
    SELECT srid FROM meta.srid_config WHERE config_key = p_key
$$;
