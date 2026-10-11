-- =====================================================================
-- OSS911 BI/DW  结构变更 0000  结构变更登记表
--
-- 一个变更一行。与 meta.release_registry（DD-49，一次发布一行）并列，一对多：
-- 一次发布可含多个变更，也可一个都不含（只改了 dbt 模型或指标）。
--
-- 不得与 meta.schema_version_registry 混用：那张表记的是归档【数据源】在时间轴上
-- 的多版本结构（RFP 2.7.7.9 · DD-37），与我方自己的 DDL 版本是两件事。
--
-- 本表必须先于 0001 存在，故单独成变更。执行器以「本表不存在」判定为空库。
-- meta 用 IF NOT EXISTS：schema 没有「定义」可言，0001 中同样如此。
-- 登记行由执行器在同一事务末尾写入，本文件不自行登记。
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE meta.ddl_change (
    change_id        text        PRIMARY KEY CHECK (change_id ~ '^[0-9]{4}$'),
    files            text[]      NOT NULL,
    content_sha256   text        NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    summary          text        NOT NULL,
    applied_at       timestamptz NOT NULL DEFAULT now(),
    applied_by       text        NOT NULL DEFAULT session_user,
    release_version  text        -- 开发环境无发布，为空。外键在 0002 补（release_registry 建于 0001）
);
COMMENT ON TABLE meta.ddl_change IS '已执行的结构变更，一个变更一行。已执行变更的文件校验和一旦与仓库不符，执行器即失败——结构只能新增变更来改';
COMMENT ON COLUMN meta.ddl_change.content_sha256 IS '按 files 顺序拼接各文件字节后的 SHA-256';
COMMENT ON COLUMN meta.ddl_change.applied_by IS 'session_user 而非 current_user：run_as=owner 时 current_user 是 oss911_owner，记它等于没记';
