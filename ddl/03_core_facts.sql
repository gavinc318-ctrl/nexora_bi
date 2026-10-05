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
--
-- ICP 的话单是「呼叫每经过一个设备一行」，不是一通一行（LD-01 4.3）。
-- 因此通话拆成两张表：
--   fact_call_leg  腿级，与话单一行一一对应，是唯一的接入落点
--   fact_call      通话级，由腿级聚合构建，不独立接入（DD-66）
-- 通话级的任何一列都必须能从腿级重算出来。两条路各自算同一个数，
-- 是口径分裂最常见的来源——这里从结构上堵死。
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_call_leg (
    call_leg_sk        uuid        NOT NULL,
    date_key           integer     NOT NULL,             -- 按本腿 wait_begin 归日
    site_sk            uuid        NOT NULL,
    call_sk            uuid        NOT NULL,             -- 冗余上级键，由模型构建生成
    src_natural_key    text        NOT NULL,             -- CALLID + CALLIDNUM，话单的幂等键（LD-01 4.4）
    call_id            text        NOT NULL,             -- CALLID，同一通呼叫的各腿共用
    leg_seq            integer     NOT NULL,             -- CALLIDNUM：1 为首腿，-1 为末腿
    is_last_leg        boolean     NOT NULL,             -- leg_seq = -1
    device_type_code   smallint    NOT NULL,             -- DEVICETYPE：1 技能队列 · 2 坐席 · 3 IVR · 其余见接入契约
    device_no          text,                             -- DEVICENO：设备为坐席时即工号
    device_in_code     text,                             -- DEVICEIN：接入设备
    agent_sk           uuid        REFERENCES core.dim_agent,   -- 仅 device_type_code = 2 时填充
    queue_sk           uuid        REFERENCES core.dim_queue,   -- 仅 device_type_code = 1 时填充
    queue_code         text,                             -- 源端队列 ID，保留原值以便回溯
    call_type_code     smallint,                         -- CALLTYPE，呼入/呼出的取值集合登记在接入契约中
    direction          text        CHECK (direction IN ('inbound','outbound','internal')),
    caller_number      text,                             -- 敏感字段，受字段可见性矩阵约束（DD-35）
    caller_number_hash text,
    callee_number      text,
    wait_begin         timestamptz NOT NULL,             -- WAITBEGIN：进入本设备。接入水位字段
    wait_end           timestamptz,                      -- WAITEND
    ack_begin          timestamptz,                      -- ACKBEGIN，源端恒等于 wait_end
    ack_end            timestamptz,                      -- ACKEND
    call_begin         timestamptz,                      -- CALLBEGIN，源端恒等于 ack_end；为空即本腿未接通
    call_end           timestamptz,                      -- CALLEND
    wait_seconds       integer,                          -- wait_end − wait_begin
    talk_seconds       integer,                          -- call_end − call_begin
    wait_cause_code    text,                             -- WAITCAUSE
    release_cause_code text,                             -- RELEASECAUSE
    release_source     text        CHECK (release_source IN ('agent','caller','system')),
    enter_reason_code  text,                             -- UCENTERREASON
    leave_reason_code  text,                             -- UCLEAVEREASON
    source_tz_offset   interval,
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    src_watermark      text,
    correction_seq     smallint    NOT NULL DEFAULT 0,
    last_change_at     timestamptz NOT NULL DEFAULT now(),
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (call_leg_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.fact_call_leg IS '与 ICP 话单一行一一对应。幂等键为 CALLID + CALLIDNUM，窗口回看时按该键覆盖写（LD-01 4.4）。release_source 由 release_cause_code 归类而来，归类表须在联调时用真实数据核对，不能只按文档归类';
COMMENT ON COLUMN core.fact_call_leg.date_key IS '按本腿的 wait_begin 归日。跨日的长呼叫其各腿可能落在不同分区，通话级聚合因此会跨分区——这是话单结构的固有性质，不作归一';
COMMENT ON COLUMN core.fact_call_leg.call_begin IS '为空即本腿未接通。「呈现到坐席但未应答」（M-C12）正是 device_type_code = 2 且本列为空';

CREATE TABLE core.fact_call (
    call_sk            uuid        NOT NULL,
    date_key           integer     NOT NULL,             -- 按首腿 wait_begin 归日
    site_sk            uuid        NOT NULL,
    src_natural_key    text        NOT NULL,             -- CALLID（AS-07）
    call_type_code     smallint,
    direction          text        CHECK (direction IN ('inbound','outbound','internal')),
    caller_number      text,                             -- 敏感字段（DD-35）
    caller_number_hash text,                             -- 同一主叫的重复与失败呼叫分析，不暴露号码
    first_queue_sk     uuid        REFERENCES core.dim_queue,   -- 首个排队腿的技能队列
    first_queue_code   text,                             -- 源端队列 ID，保留原值以便回溯
    agent_sk           uuid        REFERENCES core.dim_agent,   -- 首个应答坐席。完整归属看腿级
    call_result_sk     uuid        REFERENCES core.dim_call_result,
    offered_at         timestamptz NOT NULL,             -- 首腿 wait_begin
    answered_at        timestamptz,                      -- 首个已接通坐席腿的 call_begin
    ended_at           timestamptz,                      -- 末腿 call_end
    wait_seconds       integer,                          -- 排队腿等待合计
    talk_seconds       integer,                          -- 坐席腿通话合计
    leg_count          smallint    NOT NULL,
    agent_leg_count    smallint    NOT NULL DEFAULT 0,   -- 呈现到几个坐席（M-C09 · M-C13）
    answered_leg_count smallint    NOT NULL DEFAULT 0,   -- 其中几个真正接通（M-C10 · M-C12）
    ring_count         smallint,                         -- 1.6.2.20.1，见下方注释
    released_by        text CHECK (released_by IN ('agent','caller','system')),  -- 1.6.2.20.3，取末腿
    is_transferred     boolean     NOT NULL DEFAULT false,   -- agent_leg_count >= 2 或原因码指示转接
    is_malicious       boolean     NOT NULL DEFAULT false,   -- 来源未定（IR-46），当前恒为假
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
COMMENT ON TABLE core.fact_call IS '通话级，由 fact_call_leg 聚合构建，不独立接入（DD-66）。每一列都必须能从腿级重算；新增派生列时一并补测试，使两边不会悄悄分叉';
COMMENT ON COLUMN core.fact_call.agent_sk IS '首个应答坐席，供通话级报表快速归属。一通呼叫经多个坐席时，完整归属只能看 fact_call_leg——按坐席的指标（M-C09 至 M-C13）一律从腿级算';
COMMENT ON COLUMN core.fact_call.wait_seconds IS '排队腿的等待合计。平台把「已应答呼叫的等待」与「全部排队呼叫的等待」分列为两个指标，对账前须先声明本列算的是哪一个（metrics/icp_source_map.yaml M-C06）';
COMMENT ON COLUMN core.fact_call.ring_count IS '应答前振铃次数（RFP 1.6.2.20.1）。CloudICP 话单无此字段，平台指标亦只有振铃呼叫数而非次数，故本列恒为空；M-C08 按原口径不可交付，须与 MOI 改口径后换指标编号重记。列保留以免口径改定后再改结构';

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
-- 坐席签入区间（来源 ICP 平台明细接口 IDX_04_03_001）
--
-- 平台这一条返回的是区间数组 {inTime, outTime, duration}，是明细不是累计。
-- 因此在岗时长与某时刻在岗人数可以自算，也可以按 DD-53 与班次时间窗口求交。
-- 状态时长则只有日累计，见下一张表——两者性质不同，分表存放。
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_agent_session (
    agent_session_sk   uuid        NOT NULL,
    date_key           integer     NOT NULL,             -- 按 signin_at 归日
    site_sk            uuid        NOT NULL,
    agent_sk           uuid        NOT NULL REFERENCES core.dim_agent,
    signin_at          timestamptz NOT NULL,             -- inTime
    signout_at         timestamptz,                      -- outTime，为空即仍在线或当日未签出
    duration_seconds   integer,                          -- duration，源端给出，不由本方相减
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    correction_seq     smallint    NOT NULL DEFAULT 0,
    last_change_at     timestamptz NOT NULL DEFAULT now(),
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_session_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.fact_agent_session IS '坐席签入签出区间，来源 IDX_04_03_001。一名坐席一天可有多段。跨日的区间按 signin_at 归日，与班次求交时须跨分区取数（DD-53）';
COMMENT ON COLUMN core.fact_agent_session.duration_seconds IS '取源端给出的 duration，不由本方用 signout_at 减 signin_at 重算——两者若不一致，以源端为准并记入对账差异，不在接入侧悄悄抹平';

-- ---------------------------------------------------------------------
-- 坐席状态日累计（来源 ICP 平台历史指标 IDX_04_02_*）
--
-- 话单只记呼叫，平台也只给到各状态的日累计，没有状态变化流水：
-- 「这名坐席 10 点 05 分从通话转为事后处理」这件事在任何接口里都不存在（DD-65）。
-- 表名直接写出粒度，避免被当成流水表连接使用。
-- ---------------------------------------------------------------------
CREATE TABLE core.fact_agent_day (
    agent_day_sk       uuid        NOT NULL,
    date_key           integer     NOT NULL,
    site_sk            uuid        NOT NULL,
    agent_sk           uuid        NOT NULL REFERENCES core.dim_agent,
    signin_count       integer,                          -- IDX_04_02_001，与 fact_agent_session 的段数对账
    signin_seconds     integer,                          -- IDX_04_02_002，与 fact_agent_session 的时长合计对账
    talk_seconds       integer,                          -- IDX_04_02_021 应答时长
    acw_seconds        integer,                          -- IDX_04_02_010 事后处理时长
    rest_seconds       integer,                          -- IDX_04_02_013 休息时长
    busy_seconds       integer,                          -- IDX_04_02_019 示忙时长
    hold_seconds       integer,                          -- IDX_04_02_016 保持时长
    idle_seconds       integer,                          -- IDX_04_02_022 空闲时长
    inbound_calls      integer,                          -- IDX_04_02_003
    inbound_seconds    integer,                          -- IDX_04_02_004
    outbound_calls     integer,                          -- IDX_04_02_006
    outbound_seconds   integer,                          -- IDX_04_02_007
    transfer_out_count integer,                          -- IDX_04_02_024
    internal_xfer_count integer,                         -- IDX_04_02_023
    source_kind        text        NOT NULL DEFAULT 'platform_index'
                       CHECK (source_kind IN ('platform_index','derived')),
    src_system         text        NOT NULL,
    run_id             uuid        NOT NULL,
    contract_version   integer     NOT NULL,
    built_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_day_sk, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE core.fact_agent_day IS '坐席状态日累计，来源为平台历史指标（DD-65）。不可下钻到单次状态变化，相应报表须标注数据来源为话务平台。若日后 ICP 开放状态流水，可改为自算并把 source_kind 置为 derived，同时递增相关指标版本';
COMMENT ON COLUMN core.fact_agent_day.talk_seconds IS '本表无班次维度：平台只给到坐席日粒度，拆不出班次。故 M-R03 状态时长与 M-R04 占用率不可按班次切分；而在岗人数与在岗时长有区间明细（fact_agent_session），仍可按 DD-53 与班次窗口求交。同一组报表里两类指标的可切分性不同，须在报表规格中写明';
COMMENT ON COLUMN core.fact_agent_day.signin_seconds IS '与 fact_agent_session 的区间合计构成一组对账：两者来自同一平台的两个接口，差异即平台内部的口径差，不得由本方调平';

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
