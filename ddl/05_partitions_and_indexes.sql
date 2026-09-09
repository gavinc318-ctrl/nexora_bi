-- =====================================================================
-- OSS911 BI/DW  LD-02  05  分区与索引
-- 分区：全部事实表按 date_key 逐日 RANGE 分区。
--   PostgreSQL 要求分区表的唯一约束包含分区键，故主键为 (xxx_sk, date_key)。
--   代理键本身在全局唯一（确定性 UUID），但该唯一性由模型构建的测试保证，
--   数据库层不跨分区强制——这是分区表的固有限制，不是设计取舍。
-- =====================================================================

CREATE OR REPLACE FUNCTION meta.ensure_daily_partitions(
    p_table text, p_from date, p_to date) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    d    date    := p_from;
    n    integer := 0;
    sch  text    := split_part(p_table, '.', 1);
    tbl  text    := split_part(p_table, '.', 2);
    part text;
    k1   integer;
    k2   integer;
BEGIN
    WHILE d <= p_to LOOP
        part := tbl || '_p' || to_char(d, 'YYYYMMDD');
        k1   := to_char(d,     'YYYYMMDD')::integer;
        k2   := to_char(d + 1, 'YYYYMMDD')::integer;
        IF to_regclass(sch || '.' || part) IS NULL THEN
            EXECUTE format('CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%s) TO (%s)',
                           sch, part, sch, tbl, k1, k2);
            n := n + 1;
        END IF;
        d := d + 1;
    END LOOP;
    RETURN n;
END $$;
COMMENT ON FUNCTION meta.ensure_daily_partitions IS '由批量编排每日提前创建未来分区。分区缺失会使当日写入失败，属必须监控项';

-- ---------------------------------------------------------------------
-- 索引策略
--  一、记录检索（DD-34）走业务字段索引，不走主键。meta.search_criteria 中
--      每一条启用的检索条件都必须在此处有对应索引。
--  二、时间范围一律先分区裁剪，索引只负责分区内的选择性。
--  三、外键列建索引，服务于模型构建阶段的连接与行级过滤的下推。
-- ---------------------------------------------------------------------

-- 通话
CREATE INDEX ix_call_natural   ON core.fact_call (src_natural_key);
CREATE INDEX ix_call_hash      ON core.fact_call (caller_number_hash, offered_at);   -- R-06 同一主叫失败呼叫历史
CREATE INDEX ix_call_agent     ON core.fact_call (agent_sk, offered_at);
CREATE INDEX ix_call_offered   ON core.fact_call USING brin (offered_at);

-- 接警单
CREATE INDEX ix_intake_natural ON core.fact_intake (src_natural_key);
CREATE INDEX ix_intake_type    ON core.fact_intake (incident_type_sk, created_at);
CREATE INDEX ix_intake_sector  ON core.fact_intake (sector_sk, created_at);
CREATE INDEX ix_intake_taker   ON core.fact_intake (taker_agent_sk, created_at);
CREATE INDEX ix_intake_grid    ON core.fact_intake (grid_key, created_at);           -- D-11 热点
CREATE INDEX ix_intake_loc     ON core.fact_intake USING gist (location);
CREATE INDEX ix_intake_created ON core.fact_intake USING brin (created_at);

-- 桥表
CREATE INDEX ix_bridge_intake  ON core.bridge_call_intake (intake_sk);
CREATE INDEX ix_bridge_call    ON core.bridge_call_intake (call_sk);

-- 派警单：agency_type_sk 在首位，因为行级过滤的下推条件永远带它（DD-41）
CREATE INDEX ix_disp_agency    ON core.fact_dispatch_order (agency_type_sk, issued_at);
CREATE INDEX ix_disp_intake    ON core.fact_dispatch_order (intake_sk);
CREATE INDEX ix_disp_natural   ON core.fact_dispatch_order (src_natural_key);
CREATE INDEX ix_disp_dispatcher ON core.fact_dispatch_order (dispatcher_agent_sk, issued_at);

-- 出警单
CREATE INDEX ix_turnout_agency ON core.fact_turnout (agency_type_sk, assigned_at);
CREATE INDEX ix_turnout_disp   ON core.fact_turnout (dispatch_sk);
CREATE INDEX ix_turnout_intake ON core.fact_turnout (intake_sk);
CREATE INDEX ix_turnout_officer ON core.fact_turnout (officer_sk, assigned_at);
CREATE INDEX ix_turnout_unit   ON core.fact_turnout (unit_sk, assigned_at);
CREATE INDEX ix_turnout_natural ON core.fact_turnout (src_natural_key);

-- 反馈单
CREATE INDEX ix_fb_turnout     ON core.fact_feedback (turnout_sk, seq_no);
CREATE INDEX ix_fb_agency      ON core.fact_feedback (agency_type_sk, reported_at);
CREATE INDEX ix_fb_officer     ON core.fact_feedback (officer_sk, reported_at);

-- 坐席状态
CREATE INDEX ix_agentstate     ON core.fact_agent_state (agent_sk, state_from);

-- AI 回流
CREATE INDEX ix_ai_entity      ON core.fact_ai_inference (entity_kind, entity_sk);
CREATE INDEX ix_ai_model       ON core.fact_ai_inference (model_id, model_version, inferred_at);

-- 审计
CREATE INDEX ix_audit_user     ON audit.access_log (user_principal, occurred_at);
CREATE INDEX ix_audit_object   ON audit.access_log (object_ref, occurred_at);

-- 维度
CREATE INDEX ix_dim_agency_cur ON core.dim_agency_type (site_sk, src_natural_key) WHERE is_current;
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
