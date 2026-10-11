-- =====================================================================
-- OSS911 BI/DW  结构变更 0003  隔离登记表与热调留痕表
-- 依据：DD-75 · DD-76 · LD-01 7.2 · LD-02 第 9 章 · LD-06 2.2
--
-- 本变更起 run_as=owner：执行器在事务开头 SET LOCAL ROLE oss911_owner，
-- 新对象归属主，0002 中 FOR ROLE oss911_owner 的默认权限随之生效——
-- 下面只写默认权限覆盖不到的写权限。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 隔离登记（DD-76）
-- 隔离以「登记」实现，不以「搬移」实现：未通过校验的批次照常落入 raw，
-- 在本表登记一行；staging 只读取未登记、或登记为 released 的批次。
--
-- 批次 = 一次运行中的一个取数窗口，故键为 (run_id, batch_seq)。一次补数
-- 运行可含多个窗口，其中一个不合格只隔离那一个，不连累整次运行。
-- 由此 raw 表须带 run_id 与 batch_seq 两列，staging 才能按批次过滤。
--
-- 来源不在本表冗余：经 run_id 关联 meta.run_log.source_id 即得。
-- ---------------------------------------------------------------------
CREATE TABLE meta.quarantine_batch (
    run_id           uuid        NOT NULL REFERENCES meta.run_log (run_id),
    batch_seq        integer     NOT NULL CHECK (batch_seq >= 1),
    failure_reason   text        NOT NULL,      -- 未通过的契约期望，逐条
    row_count        bigint,
    quarantined_at   timestamptz NOT NULL DEFAULT now(),
    state            text        NOT NULL DEFAULT 'pending'
                                 CHECK (state IN ('pending','released','discarded')),
    handled_by       text,
    handled_at       timestamptz,
    handling_reason  text,
    PRIMARY KEY (run_id, batch_seq),
    -- 待处理时处理三列必须为空；一经处理三列必须齐全，理由不得为空白。
    -- 不允许无理由放行：三个月后没人说得清当时为什么放（LD-01 7.2）
    -- handling_reason IS NOT NULL 不可省：CHECK 把 NULL 当作通过，省掉它则
    -- length(btrim(NULL)) > 0 得 NULL，无理由放行会被静默放过（实测踩到过）
    CONSTRAINT ck_quarantine_handled CHECK (
        (state = 'pending'  AND handled_by IS NULL AND handled_at IS NULL AND handling_reason IS NULL)
     OR (state <> 'pending' AND handled_by IS NOT NULL AND handled_at IS NOT NULL
                            AND handling_reason IS NOT NULL AND length(btrim(handling_reason)) > 0))
);
COMMENT ON TABLE meta.quarantine_batch IS '隔离登记。坏批次照常在 raw，这里只记状态；丢弃只改状态，不删 raw（DD-76 · DD-02）';
COMMENT ON COLUMN meta.quarantine_batch.state IS 'pending → released 或 discarded，处理结论一经写入不可再改';
CREATE INDEX ix_quarantine_pending ON meta.quarantine_batch (quarantined_at) WHERE state = 'pending';

-- 处理结论只能从 pending 写一次。本表的处理三列就是这次处理的留痕（LD-01 7.2
-- 「两者都留审计」）；若允许改写，留痕即被覆盖。
CREATE FUNCTION meta.quarantine_batch_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF OLD.state <> 'pending' THEN
        RAISE EXCEPTION '批次 %/% 已处理为 %，处理结论不可改', OLD.run_id, OLD.batch_seq, OLD.state;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_quarantine_batch_guard
    BEFORE UPDATE ON meta.quarantine_batch
    FOR EACH ROW EXECUTE FUNCTION meta.quarantine_batch_guard();

-- 登记由接入；放行与丢弃经服务层管理路径。列级授权：管理路径只能改处理四列，
-- 改不了批次身份与失败原因
GRANT INSERT ON meta.quarantine_batch TO oss911_ingest;
GRANT UPDATE (state, handled_by, handled_at, handling_reason)
    ON meta.quarantine_batch TO oss911_admin_svc;

-- ---------------------------------------------------------------------
-- 运行参数热调留痕（DD-75）
-- 与 audit.reference_data_change_log、audit.permission_change_log 并列，
-- 字段语义对齐，以便合并为一条时间线（LD-06 2.2）。值用 text 与参照数据
-- 留痕一致。不写 meta.run_log：后者是接入运行记录，热调在结构上写不进去。
-- 理由必填：热调改变速率与资源占用，「那天为什么慢了」要有记录可查。
-- ---------------------------------------------------------------------
CREATE TABLE audit.runtime_param_change_log (
    change_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id        text        NOT NULL,      -- 热调的对象是某个数据源（M-01）
    param_name       text        NOT NULL,      -- 启停 · 间隔 · 批量 · 限流 ……
    old_value        text,
    new_value        text,
    changed_by       text        NOT NULL,
    changed_at       timestamptz NOT NULL DEFAULT now(),
    reason           text        NOT NULL CHECK (length(btrim(reason)) > 0)
);
COMMENT ON TABLE audit.runtime_param_change_log IS 'M-01 运行参数热调的逐项留痕，只增不改（DD-75）';
CREATE INDEX ix_paramchg_source ON audit.runtime_param_change_log (source_id, changed_at DESC);
REVOKE UPDATE, DELETE ON audit.runtime_param_change_log FROM PUBLIC;

GRANT INSERT ON audit.runtime_param_change_log TO oss911_admin_svc;
