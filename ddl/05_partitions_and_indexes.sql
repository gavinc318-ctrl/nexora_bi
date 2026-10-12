-- =====================================================================
-- OSS911 BI/DW  LD-02  05  分区与索引
-- 分区：13 张事实表与审计表按 date_key 按月 RANGE 分区（DD-77）。
--   分区键仍为 date_key（YYYYMMDD 整数），一个分区覆盖 [当月 1 日, 次月 1 日)，
--   月内按日定位靠 BRIN 索引。不按天分：八年加归档约 8.5 万个分区，裁不掉分区的
--   查询会撞 max_locks_per_transaction，而单日十余万行换不来查询收益（DD-77）。
--   封账是逻辑冻结（close_version 与修正行），不依赖分区物理只读——
--   一个月的分区里同时有已封账与未封账的日子。
--   PostgreSQL 要求分区表的唯一约束包含分区键，故主键为 (xxx_sk, date_key)。
--   代理键本身在全局唯一（确定性 UUID），但该唯一性由模型构建的测试保证，
--   数据库层不跨分区强制——这是分区表的固有限制，不是设计取舍。
-- =====================================================================

-- 为 [p_from, p_to] 覆盖到的每个月建一个分区（两端按所在月取整），已有的跳过。
-- 分区名 <表名>_pYYYYMM。
CREATE OR REPLACE FUNCTION meta.ensure_monthly_partitions(
    p_table text, p_from date, p_to date) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    m    date    := date_trunc('month', p_from)::date;
    mend date    := date_trunc('month', p_to)::date;
    n    integer := 0;
    sch  text    := split_part(p_table, '.', 1);
    tbl  text    := split_part(p_table, '.', 2);
    part text;
    k1   integer;
    k2   integer;
BEGIN
    WHILE m <= mend LOOP
        part := tbl || '_p' || to_char(m, 'YYYYMM');
        k1   := to_char(m, 'YYYYMMDD')::integer;
        k2   := to_char((m + interval '1 month')::date, 'YYYYMMDD')::integer;
        IF to_regclass(format('%I.%I', sch, part)) IS NULL THEN
            EXECUTE format('CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%s) TO (%s)',
                           sch, part, sch, tbl, k1, k2);
            n := n + 1;
        END IF;
        m := (m + interval '1 month')::date;
    END LOOP;
    RETURN n;
END $$;
COMMENT ON FUNCTION meta.ensure_monthly_partitions IS '由编排提前建出未来若干个月的分区（DD-77）。分区缺失会使该月写入失败，属必须监控项';

-- ---------------------------------------------------------------------
-- 索引策略
--  一、记录检索（DD-34）走业务字段索引，不走主键。meta.search_criteria 中
--      每一条启用的检索条件都必须在此处有对应索引。
--  二、时间范围一律先分区裁剪，索引只负责分区内的选择性。按时间范围访问分区表的
--      查询必须同时带 date_key 条件，否则裁不掉任何分区（DD-78 · LD-02 12.2）。
--  三、外键列建索引，服务于模型构建阶段的连接与行级过滤的下推。
--  四、时间列索引按写入模式选（DD-78 · LD-02 12.1）：行写入后不再改写、按时间
--      顺序追加 → BRIN；写入后会被改写（状态推进、结案回填、源端原地更新后重取）
--      → B-tree。BRIN 依赖物理顺序与时间顺序一致，被 UPDATE 的行新版本落到别处，
--      几行错位就撑宽整段摘要，且退化随时间累积。
--      量很小的坐席会话、坐席日累计与桥表不单建时间索引。
--  五、BRIN 一律 autosummarize = on：否则新写的页在下次 VACUUM 前不进摘要，每次
--      查询都要扫它们。
-- ---------------------------------------------------------------------

-- 通话（腿级）
CREATE INDEX ix_leg_call       ON core.fact_call_leg (call_id, leg_seq);             -- 按 CALLID 聚回通话
CREATE INDEX ix_leg_parent     ON core.fact_call_leg (call_sk);
CREATE INDEX ix_leg_natural    ON core.fact_call_leg (src_natural_key);              -- 回看窗口覆盖写按此键定位
CREATE INDEX ix_leg_agent      ON core.fact_call_leg (agent_sk, wait_begin)
                                   WHERE device_type_code = 2;                       -- M-C09 至 M-C13 一律从腿级算
CREATE INDEX ix_leg_queue      ON core.fact_call_leg (queue_sk, wait_begin)
                                   WHERE device_type_code = 1;                       -- M-C05 · M-C06 按技能队列
CREATE INDEX ix_leg_wait       ON core.fact_call_leg USING brin (wait_begin)
                                   WITH (autosummarize = on);                        -- 只在 15 分钟回看窗口内覆盖写

-- 通话（通话级）
CREATE INDEX ix_call_natural   ON core.fact_call (src_natural_key);
CREATE INDEX ix_call_hash      ON core.fact_call (caller_number_hash, offered_at);   -- R-06 同一主叫失败呼叫历史
CREATE INDEX ix_call_agent     ON core.fact_call (agent_sk, offered_at);
CREATE INDEX ix_call_offered   ON core.fact_call USING brin (offered_at)
                                   WITH (autosummarize = on);

-- 接警单
CREATE INDEX ix_intake_natural ON core.fact_intake (src_natural_key);
CREATE INDEX ix_intake_type    ON core.fact_intake (incident_type_sk, created_at);
CREATE INDEX ix_intake_sector  ON core.fact_intake (sector_sk, created_at);
CREATE INDEX ix_intake_taker   ON core.fact_intake (taker_agent_sk, created_at);
CREATE INDEX ix_intake_grid    ON core.fact_intake (grid_key, created_at);           -- D-11 热点
CREATE INDEX ix_intake_loc     ON core.fact_intake USING gist (location);
CREATE INDEX ix_intake_created ON core.fact_intake (created_at);                    -- B-tree：结案前持续更新，不宜 BRIN（DD-78）

-- 桥表
CREATE INDEX ix_bridge_intake  ON core.bridge_call_intake (intake_sk);
CREATE INDEX ix_bridge_call    ON core.bridge_call_intake (call_sk);

-- 派警单：agency_type_sk 在首位，因为行级过滤的下推条件永远带它（DD-41）
CREATE INDEX ix_disp_agency    ON core.fact_dispatch_order (agency_type_sk, issued_at);
CREATE INDEX ix_disp_intake    ON core.fact_dispatch_order (intake_sk);
CREATE INDEX ix_disp_natural   ON core.fact_dispatch_order (src_natural_key);
CREATE INDEX ix_disp_dispatcher ON core.fact_dispatch_order (dispatcher_agent_sk, issued_at);
CREATE INDEX ix_disp_issued    ON core.fact_dispatch_order (issued_at);             -- B-tree：确认、结案、取消陆续回填。
                                                                                     -- 用户查询走 ix_disp_agency；本索引服务不带行过滤的管理与导出路径

-- 出警单
CREATE INDEX ix_turnout_agency ON core.fact_turnout (agency_type_sk, assigned_at);
CREATE INDEX ix_turnout_disp   ON core.fact_turnout (dispatch_sk);
CREATE INDEX ix_turnout_intake ON core.fact_turnout (intake_sk);
CREATE INDEX ix_turnout_officer ON core.fact_turnout (officer_sk, assigned_at);
CREATE INDEX ix_turnout_unit   ON core.fact_turnout (unit_sk, assigned_at);
CREATE INDEX ix_turnout_natural ON core.fact_turnout (src_natural_key);
CREATE INDEX ix_turnout_assigned ON core.fact_turnout (assigned_at);               -- B-tree：出动、到达、结束陆续回填（LD-02 12.1）

-- 反馈单
CREATE INDEX ix_fb_turnout     ON core.fact_feedback (turnout_sk, seq_no);
CREATE INDEX ix_fb_agency      ON core.fact_feedback (agency_type_sk, reported_at);
CREATE INDEX ix_fb_officer     ON core.fact_feedback (officer_sk, reported_at);
CREATE INDEX ix_fb_reported    ON core.fact_feedback USING brin (reported_at)
                                   WITH (autosummarize = on);                        -- 一条反馈一行，写后不改

-- 坐席签入区间与日累计：量很小，不单建时间索引（DD-78）。
-- 签出时回填 signout_at，故原有的 BRIN (signin_at) 去掉。
-- fact_agent_day 的 (agent_sk, date_key) 即下面这条唯一索引，不另建。
CREATE INDEX ix_agentsess      ON core.fact_agent_session (agent_sk, signin_at);
CREATE UNIQUE INDEX ux_agentday ON core.fact_agent_day (agent_sk, date_key);

-- AI 回流
CREATE INDEX ix_ai_entity      ON core.fact_ai_inference (entity_kind, entity_sk);
CREATE INDEX ix_ai_model       ON core.fact_ai_inference (model_id, model_version, inferred_at);

-- 审计：三张都只插不改，时间列用 BRIN（DD-78）
CREATE INDEX ix_audit_user     ON audit.access_log (user_principal, occurred_at);
CREATE INDEX ix_audit_object   ON audit.access_log (object_ref, occurred_at);
CREATE INDEX ix_audit_occurred ON audit.access_log USING brin (occurred_at)
                                   WITH (autosummarize = on);
CREATE INDEX ix_delivery_handed ON audit.delivery_log USING brin (handed_over_at)
                                   WITH (autosummarize = on);
CREATE INDEX ix_probe_probed   ON audit.sla_probe USING brin (probed_at)
                                   WITH (autosummarize = on);

-- 维度
CREATE INDEX ix_dim_agency_cur ON core.dim_agency_type (site_sk, src_natural_key) WHERE is_current;
CREATE INDEX ix_dim_queue_cur  ON core.dim_queue       (site_sk, src_natural_key) WHERE is_current;
CREATE INDEX ix_dim_unit_cur   ON core.dim_unit        (site_sk, src_natural_key) WHERE is_current;
CREATE INDEX ix_dim_officer_cur ON core.dim_officer    (site_sk, src_natural_key) WHERE is_current;
CREATE INDEX ix_dim_agent_cur  ON core.dim_agent       (site_sk, src_natural_key) WHERE is_current;
CREATE INDEX ix_dim_type_cur   ON core.dim_incident_type (site_sk, src_natural_key) WHERE is_current;
CREATE INDEX ix_dim_sector_cur ON core.dim_sector      (site_sk, src_natural_key) WHERE is_current;

-- 结案类指标按 closed_date_key 归日，需各自的索引（LD-03）
CREATE INDEX ix_disp_closed    ON core.fact_dispatch_order (closed_date_key) WHERE closed_date_key IS NOT NULL;
CREATE INDEX ix_turnout_closed ON core.fact_turnout (closed_date_key) WHERE closed_date_key IS NOT NULL;
-- 未关闭单据的数据质量监控：ended_at 为空且已超期的出警单
CREATE INDEX ix_turnout_open   ON core.fact_turnout (assigned_at) WHERE ended_at IS NULL AND NOT is_cancelled;
