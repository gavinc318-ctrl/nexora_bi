-- =====================================================================
-- OSS911 BI/DW  LD-02  03  core 事实表
--
-- CAD 的单据是一棵树，每个上级节点可指向多个下层节点：
--     接警单 → 派警单（每警种一张）→ 出警单（每警员一张）→ 反馈单（每警员多张）
-- 通话与接警单之间是多对多（掉线重拨、回拨、多人报警合并到同一张接警单）。
--
-- 两条刻意的取舍：
--  一、事实表之间不建外键约束。分区表的唯一约束必须包含分区键，跨层复合外键
--      会把日期强行绑在一起（子单据的日期未必等于父单据的日期）。参照完整性
--      改由模型构建阶段的测试保证，测试失败即阻断发布。
--  二、上级键在下层事实表中冗余（intake_sk 出现在出警单与反馈单上），避免三层
--      连接。冗余列一律由模型构建生成，任何情况下不得人工维护。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 通话（来源 ICP）
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_call (
    call_sk            uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    src_natural_key    text        NOT NULL,             -- 两侧统一的通话标识（AS-07）
    caller_number      text,                             -- 敏感字段，受字段可见性矩阵约束（DD-35）
    caller_number_hash text,                             -- 用于同一主叫的重复与失败呼叫分析，不暴露号码
    queue_code         text,
    agent_sk           uuid        REFERENCES core.dim_agent,
    call_result_sk     uuid        REFERENCES core.dim_call_result,
    offered_at         timestamptz NOT NULL,             -- 呼入
    answered_at        timestamptz,
    ended_at           timestamptz,
    wait_seconds       integer,
    talk_seconds       integer,
    ring_count         smallint,                         -- 1.6.2.20.1，依赖 IR-11
    released_by        text CHECK (released_by IN ('agent','caller','system')),  -- 1.6.2.20.3
    is_transferred     boolean     NOT NULL DEFAULT false,
    is_malicious       boolean     NOT NULL DEFAULT false,
    source_tz_offset   interval,                         -- 原始时区偏移，保真要求（REQ-MIG-08）
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    src_watermark      text,
    correction_seq     smallint    NOT NULL DEFAULT 0,
    last_change_at     timestamptz NOT NULL DEFAULT now(),
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (call_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON COLUMN core.fact_call.ring_count IS '应答前振铃次数。ICP 若仅提供当前状态快照则该字段不可得，五类话务指标随之无法交付（IR-11 · AS-06）';

-- ---------------------------------------------------------------------
-- 接警单 —— 案件粒度。多人报同一事件由 CAD 合并到同一张接警单
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_intake (
    intake_sk          uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    src_natural_key    text        NOT NULL,             -- 接警单号
    channel_sk         uuid        REFERENCES core.dim_channel,
    incident_type_sk   uuid        REFERENCES core.dim_incident_type,
    priority_sk        uuid        REFERENCES core.dim_priority,
    sector_sk          uuid        REFERENCES core.dim_sector,   -- 辖区归属以 CAD 字段为准（DD-32）
    taker_agent_sk     uuid        REFERENCES core.dim_agent,
    disposition_sk     uuid        REFERENCES core.dim_disposition,
    created_at         timestamptz NOT NULL,             -- 接警单建立
    closed_at          timestamptz,                      -- 结案；无独立字段时由下层派生，见 is_closed_derived
    is_closed_derived  boolean     NOT NULL DEFAULT false,
    is_cancelled       boolean     NOT NULL DEFAULT false,
    location           geometry(Point, 4326),
    location_source    text,                             -- cad_operator · caller_locate · geocoded · unknown
    location_accuracy_m numeric(8,1),
    address_text       text,                             -- 位置若仅为地址文本则需地理编码（IR-41）
    grid_key           text,                             -- 网格键，在 srid_analysis 投影下计算（DD-33）
    first_call_sk      uuid,                             -- 首呼，由桥表派生，禁止人工维护
    linked_call_count  smallint    NOT NULL DEFAULT 0,
    description        text,                             -- 高敏感，默认不可见（DD-35）
    source_tz_offset   interval,
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    src_watermark      text,
    correction_seq     smallint    NOT NULL DEFAULT 0,
    last_change_at     timestamptz NOT NULL DEFAULT now(),
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (intake_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON COLUMN core.fact_intake.first_call_sk IS '建立本接警单的那一通电话。CAD 若有 originating call 字段则取之，否则取时间最早的关联通话并在桥表标为推断';
COMMENT ON COLUMN core.fact_intake.is_closed_derived IS 'true 表示 closed_at 由「全部出警单结束的最晚时间」派生而非 CAD 直接给出。派生值不得不加标记地进入对外指标';

-- ---------------------------------------------------------------------
-- 通话 × 接警单（多对多）
-- ---------------------------------------------------------------------
CREATE TABLE core.bridge_call_intake (
    call_sk          uuid        NOT NULL,
    intake_sk        uuid        NOT NULL,
    date_key         integer     NOT NULL,               -- 接警单日期，与 fact_intake 对齐
    site_sk          uuid        NOT NULL,
    link_role        text        NOT NULL
                     CHECK (link_role IN ('first','callback','duplicate','transfer','followup')),
    link_source      text        NOT NULL
                     CHECK (link_source IN ('cad_field','inferred')),
    seq_no           smallint    NOT NULL,
    run_id           uuid        NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (call_sk, intake_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.bridge_call_intake IS 'R-10 重复来电与多人报警识别直接取本表中关联通话数大于一的接警单。link_source 为 inferred 的关联在报表上须可区分';

-- ---------------------------------------------------------------------
-- 派警单 —— 接警单 × 警种。行级安全的过滤落点（DD-41）
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_dispatch_order (
    dispatch_sk        uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    intake_sk          uuid        NOT NULL,
    intake_date_key    integer     NOT NULL,             -- 便于回溯父分区
    src_natural_key    text        NOT NULL,             -- 派警单号
    agency_type_sk     uuid        NOT NULL REFERENCES core.dim_agency_type,
    dispatcher_agent_sk uuid       REFERENCES core.dim_agent,
    parent_dispatch_sk uuid,                             -- 改派时指向原派警单
    order_kind         text        NOT NULL DEFAULT 'initial'
                       CHECK (order_kind IN ('initial','additional','redispatch')),
    issued_at          timestamptz NOT NULL,             -- 下发
    acknowledged_at    timestamptz,
    closed_at          timestamptz,                      -- CAD 上手动关闭，是点击时间而非实际结束时间
    closed_date_key    integer,                          -- 结案类指标按此归日，与事件日 date_key 并存
    is_cancelled       boolean     NOT NULL DEFAULT false,
    cancelled_at       timestamptz,
    source_tz_offset   interval,
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    src_watermark      text,
    correction_seq     smallint    NOT NULL DEFAULT 0,
    last_change_at     timestamptz NOT NULL DEFAULT now(),
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dispatch_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.fact_dispatch_order IS '派单时长（接警单建立→下发）的粒度在此。按警种各一个数，不可与接警单混为一谈';
COMMENT ON COLUMN core.fact_dispatch_order.order_kind IS '增派与改派均为新增节点而非修改原节点。R-16 派单及时性按 initial 计，additional 与 redispatch 单独统计，否则改派会抹掉原派单的超时';

-- ---------------------------------------------------------------------
-- 出警单 —— 派警单 × 警员。每张出警单自行管理其结束
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_turnout (
    turnout_sk         uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    dispatch_sk        uuid        NOT NULL,
    dispatch_date_key  integer     NOT NULL,
    intake_sk          uuid        NOT NULL,             -- 冗余，由模型构建生成
    intake_date_key    integer     NOT NULL,
    agency_type_sk     uuid        NOT NULL,             -- 冗余，行过滤下推用
    src_natural_key    text        NOT NULL,             -- 出警单号
    officer_sk         uuid        REFERENCES core.dim_officer,
    unit_sk            uuid        REFERENCES core.dim_unit,
    disposition_sk     uuid        REFERENCES core.dim_disposition,
    assigned_at        timestamptz NOT NULL,             -- 受领
    enroute_at         timestamptz,                      -- 出动
    arrived_at         timestamptz,                      -- 到场
    ended_at           timestamptz,                      -- 本出警单结束（CAD 上手动关闭）
    closed_date_key    integer,                          -- 结案类指标按此归日，与事件日 date_key 并存
    is_cancelled       boolean     NOT NULL DEFAULT false,
    arrival_location   geometry(Point, 4326),
    source_tz_offset   interval,
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    src_watermark      text,
    correction_seq     smallint    NOT NULL DEFAULT 0,
    last_change_at     timestamptz NOT NULL DEFAULT now(),
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (turnout_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.fact_turnout IS '到场时长与处置时长的粒度在此，按警员各一个数。M-R05 单位出动次数按本表计：一张派警单出三名警员即三次出动';
COMMENT ON COLUMN core.fact_turnout.ended_at IS '出警单在 CAD 上由人工关闭，故此值含操作员延迟。处置时长的主口径用中位数与 P90 而非均值——均值受长尾污染最重';
COMMENT ON COLUMN core.fact_turnout.closed_date_key IS '发生类指标（出动量）按 date_key 归日，结案类指标（结案数、处置时长）按本列归日。跨日关闭的单据若按事件日归，业务想看的当日结案数就取不到';

-- ---------------------------------------------------------------------
-- 反馈单 —— 出警单 × 第 N 次反馈（警员的处置反馈）
-- 注意与「客户反馈」（满意度回访，RFP 1.6.2.17）区分，二者中文同名
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_feedback (
    feedback_sk        uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    turnout_sk         uuid        NOT NULL,
    turnout_date_key   integer     NOT NULL,
    dispatch_sk        uuid        NOT NULL,             -- 冗余
    intake_sk          uuid        NOT NULL,             -- 冗余
    agency_type_sk     uuid        NOT NULL,             -- 冗余，行过滤下推用
    src_natural_key    text        NOT NULL,             -- 反馈单号
    officer_sk         uuid        REFERENCES core.dim_officer,
    seq_no             smallint    NOT NULL,
    feedback_kind      text,
    reported_at        timestamptz NOT NULL,
    content            text,                             -- 高敏感，默认不可见
    source_tz_offset   interval,
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feedback_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.fact_feedback IS '过程性记录，不决定时长终点——处置结束以出警单的 ended_at 为准。补录一张反馈不会改变已封账的处置时长';

-- ---------------------------------------------------------------------
-- 坐席状态（来源 ICP）
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_agent_state (
    agent_state_sk     uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    agent_sk           uuid        NOT NULL REFERENCES core.dim_agent,
    shift_sk           uuid        REFERENCES core.dim_shift,
    state_code         text        NOT NULL,             -- ready · talking · acw · away · logged_out
    state_from         timestamptz NOT NULL,
    state_to           timestamptz,
    duration_seconds   integer,
    measure_method     text        NOT NULL DEFAULT 'event'
                       CHECK (measure_method IN ('event','sampled')),
    sample_interval_s  smallint,
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_state_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON COLUMN core.fact_agent_state.measure_method IS 'ICP 仅提供当前状态快照时为 sampled，单次误差最大为一个轮询周期，日累计误差随状态切换次数放大。此类指标必须在报表上标注测量方式（HLD 4.6.3 · IR-09）';

-- ---------------------------------------------------------------------
-- AI 推断结果回流（IF-07）。与业务事实分表，不写入上述任何一张表（DD-28）
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_ai_inference (
    inference_sk       uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    entity_kind        text        NOT NULL,             -- intake · turnout · call ...
    entity_sk          uuid        NOT NULL,
    entity_date_key    integer     NOT NULL,
    model_id           text        NOT NULL,
    model_version      text        NOT NULL,
    inferred_at        timestamptz NOT NULL,
    output             jsonb       NOT NULL,
    confidence         numeric(5,4),
    input_snapshot_ref text,                             -- 指向 raw 或导出快照，供复算
    src_system         text        NOT NULL DEFAULT 'ai_pkg',
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (inference_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.fact_ai_inference IS 'AI 包的推断结果。分表保存使得任何时候都能回答某个数字是观测到的还是推断出来的，并可按模型版本复算（DD-28 · 1.3.8.9）';
