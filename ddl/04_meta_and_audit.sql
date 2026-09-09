-- =====================================================================
-- OSS911 BI/DW  LD-02  04  meta 与 audit
-- meta 与 audit 是仅有的两个不可重建的 schema（raw 为第三个）
-- =====================================================================

-- ---- 接入与运行 -----------------------------------------------------
CREATE TABLE meta.source_contract (          -- Git 为权威，库内为部署时同步的只读副本
    source_id        text        PRIMARY KEY,
    connector        text        NOT NULL CHECK (connector IN ('pg_read','mssql_read','rest_api','kafka_consume','file_drop')),
    target_raw       text        NOT NULL,
    contract_version integer     NOT NULL,
    definition       jsonb       NOT NULL,
    enabled          boolean     NOT NULL DEFAULT true,
    synced_at        timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN meta.source_contract.definition IS '契约全文。结构性字段变更走发布流程；运行参数可在 M-01 热调并写审计（HLD 9.2）';

CREATE TABLE meta.watermark_state (
    source_id        text        PRIMARY KEY REFERENCES meta.source_contract,
    watermark_value  text,
    watermark_type   text        NOT NULL CHECK (watermark_type IN ('timestamptz','sequence')),
    last_success_at  timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE meta.run_log (
    run_id           uuid        PRIMARY KEY,
    source_id        text        NOT NULL,
    contract_version integer     NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz,
    watermark_from   text,
    watermark_to     text,
    row_count        bigint,
    outcome          text        NOT NULL CHECK (outcome IN ('success','failed','quarantined','partial')),
    message          text
);
COMMENT ON TABLE meta.run_log IS '重放的依据。配合 raw 中的 run_id 与 contract_version，可回答某条数据是哪一次运行、按第几版契约、从哪个水位进来的';

-- ---- 语义映射与值剖析 ----------------------------------------------
CREATE TABLE meta.mapping_registry (
    source_id        text        NOT NULL,
    src_column       text        NOT NULL,
    target_ref       text,
    mapping_state    text        NOT NULL CHECK (mapping_state IN ('verified','mapped_unverified','unknown')),
    verified_sample  text,
    note             text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, src_column)
);
COMMENT ON TABLE meta.mapping_registry IS '范围内的每一列都必须有一行登记。结构漂移检测发现的新列默认落 unknown——空白会被遗忘，未知会被统计并排期';

CREATE TABLE meta.value_profile (
    source_id        text        NOT NULL,
    src_column       text        NOT NULL,
    src_value        text        NOT NULL,
    occurrences      bigint      NOT NULL,
    share_pct        numeric(6,3),
    first_seen_at    timestamptz NOT NULL,
    last_seen_at     timestamptz NOT NULL,
    is_mapped        boolean     NOT NULL DEFAULT false,
    profiled_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, src_column, src_value)
);
COMMENT ON COLUMN meta.value_profile.first_seen_at IS '取值的首次与最后出现时点。分布突变点即为归档数据中 schema 版本边界的候选，用于在拿不到升级记录时反推（DD-37）';

CREATE TABLE meta.schema_version_registry (
    source_id        text        NOT NULL,
    version_no       integer     NOT NULL,
    effective_from   timestamptz NOT NULL,
    effective_to     timestamptz NOT NULL DEFAULT 'infinity',
    structure_diff   jsonb,
    codeset_diff     jsonb,
    evidence         text        NOT NULL CHECK (evidence IN ('upgrade_record','profiled','confirmed')),
    note             text,
    PRIMARY KEY (source_id, version_no)
);
COMMENT ON TABLE meta.schema_version_registry IS '归档数据在时间轴上的多版本登记（RFP 2.7.7.9 · DD-37）。evidence 为 profiled 表示版本边界由值剖析反推而非来自升级记录';

CREATE TABLE meta.clarification_queue (
    item_id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id        text        NOT NULL,
    src_column       text        NOT NULL,
    src_value        text,
    impacted_rows    bigint      NOT NULL,
    impacted_metrics text[],
    status           text        NOT NULL DEFAULT 'open' CHECK (status IN ('open','asked','answered','closed')),
    raised_at        timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz
);

-- ---- 指标与检索 -----------------------------------------------------
CREATE TABLE meta.metric_registry (
    metric_code      text        PRIMARY KEY,            -- M-C01 · M-I05 ...
    subject_domain   text        NOT NULL,
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
    definition       text        NOT NULL,
    grain            text        NOT NULL,               -- intake · dispatch_order · turnout · call · agent-day ...
    sql_expression   text        NOT NULL,
    dimensions       text[]      NOT NULL,
    source_tables    text[]      NOT NULL,
    metric_version   integer     NOT NULL DEFAULT 1,
    depends_unverified boolean   NOT NULL DEFAULT false,
    is_published     boolean     NOT NULL DEFAULT false,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN meta.metric_registry.grain IS '粒度必须显式声明。派单时长在派警单粒度、到场与处置时长在出警单粒度——按派单平均与按接警单平均是两个不同的数，须分别命名';

CREATE TABLE meta.search_criteria (          -- 记录检索的可配置条件集（DD-34）
    criteria_code    text        PRIMARY KEY,
    target_entity    text        NOT NULL,
    column_ref       text        NOT NULL,
    operator_set     text[]      NOT NULL,
    index_name       text        NOT NULL,               -- 无索引支撑的条件不得启用
    is_enabled       boolean     NOT NULL DEFAULT false,
    max_range_days   integer,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN meta.search_criteria.index_name IS '每条检索条件必须指向一个实际存在的索引。新增条件须伴随索引评审，否则将在千万行事实表上产生全表扫描';

-- ---- 权限（结构在此，默认取值待数据分类政策批准）--------------------
CREATE TABLE meta.field_visibility (         -- 字段级可见性矩阵（DD-35）
    role_code        text        NOT NULL,
    table_ref        text        NOT NULL,
    column_ref       text        NOT NULL,
    is_visible       boolean     NOT NULL DEFAULT false,
    classification   text,                               -- 由 M-11 扫描与人工确认得出
    changed_by       text,
    changed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_code, table_ref, column_ref)
);
COMMENT ON TABLE meta.field_visibility IS '服务端按角色的字段白名单选列，不可见字段不进入结果集。查询缓存的缓存键必须包含白名单指纹，否则跨角色串号';

CREATE TABLE meta.user_department_map (      -- 部门归属（DD-41 过渡期方案）
    user_principal   text        PRIMARY KEY,
    agency_type_code text        NOT NULL,               -- 必须映射到有效的 CAD 警种编码
    source           text        NOT NULL DEFAULT 'local' CHECK (source IN ('directory','local')),
    changed_by       text,
    changed_at       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE meta.user_department_map IS 'AD 增设部门组之前的过渡（IR-39）。映射不到有效警种编码时拒绝放行并告警，不得静默返回空结果';

CREATE TABLE meta.retention_policy (         -- 三层保留（DD-38）
    layer            text        PRIMARY KEY CHECK (layer IN ('hot','warm','backup_offline','backup_extended','artifact')),
    scope            text        NOT NULL,
    duration         interval,
    rfp_clause       text,
    note             text
);
INSERT INTO meta.retention_policy VALUES
 ('hot'             ,'mart 预聚合与缓存覆盖的近一年','1 year' ,'2.7.10.4.1','承担 95% 亚秒的快速查询路径'),
 ('warm'            ,'core 中一年以上的全部历史'    ,NULL     ,'1.6.2.22.2 · 2.7.7.13','在线但走历史查询路径，秒级或转异步'),
 ('backup_offline'  ,'关键数据离线备份'             ,'5 years','2.7.10.4.2',NULL),
 ('backup_extended' ,'已识别警情的扩展离线备份'      ,'8 years','2.7.10.4.3','原「业务数据在线保留八年」为对本条的口径误读')
ON CONFLICT DO NOTHING;

-- ---- 审计（只增不改）-----------------------------------------------
CREATE TABLE audit.access_log (
    access_id        bigint      GENERATED ALWAYS AS IDENTITY,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    date_key         integer     NOT NULL,
    user_principal   text        NOT NULL,
    role_code        text        NOT NULL,
    agency_type_code text,
    action           text        NOT NULL CHECK (action IN ('query','record_search','export','render','api','mcp')),
    object_ref       text,
    metric_codes     text[],
    sensitive_fields text[],                             -- 记字段名，不记值
    row_count        bigint,
    duration_ms      integer,
    client_kind      text,                               -- portal · wall · mobile · esb · ai_pkg · mcp
    request_digest   text,
    PRIMARY KEY (access_id, date_key)
) PARTITION BY RANGE (date_key);
REVOKE UPDATE, DELETE ON audit.access_log FROM PUBLIC;
COMMENT ON COLUMN audit.access_log.sensitive_fields IS '本次返回的敏感字段名清单。记名不记值——审计表本身不得成为系统内最集中的泄露点';

CREATE TABLE audit.delivery_log (            -- 投递交接点证据（CN-30）
    delivery_id      bigint      GENERATED ALWAYS AS IDENTITY,
    date_key         integer     NOT NULL,
    content_kind     text        NOT NULL CHECK (content_kind IN ('report','alert')),
    content_ref      text        NOT NULL,
    channel          text        NOT NULL CHECK (channel IN ('email','sms','in_portal')),
    recipient_ref    text        NOT NULL,
    handed_over_at   timestamptz NOT NULL,
    gateway_status   text,
    retry_count      smallint    NOT NULL DEFAULT 0,
    PRIMARY KEY (delivery_id, date_key)
) PARTITION BY RANGE (date_key);
COMMENT ON TABLE audit.delivery_log IS '短信与邮件网关由总包商提供，其故障不计入本方服务水平；但本方必须能证明消息确已交给网关，否则争议时无法自证';

CREATE TABLE audit.permission_change_log (
    change_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_at       timestamptz NOT NULL DEFAULT now(),
    changed_by       text        NOT NULL,
    object_kind      text        NOT NULL,               -- field_visibility · department_map · role
    object_ref       text        NOT NULL,
    old_value        jsonb,
    new_value        jsonb,
    reason           text
);

-- ---------------------------------------------------------------------
-- 服务水平举证（DD-45）
-- 落 audit 而非新增第八个 schema：REQ-MOD-01 的验收标准是「库中存在且
-- 仅存在这七个业务 schema」，且 audit 的只增不改语义与举证要求天然一致。
-- Prometheus 只保存数周的运行态供排障，按日聚合的证据在此长期留存。
-- ---------------------------------------------------------------------
CREATE TABLE audit.sla_probe (               -- 探测原始记录，短期保留
    probe_id       bigint      GENERATED ALWAYS AS IDENTITY,
    date_key       integer     NOT NULL,
    probed_at      timestamptz NOT NULL,
    component      text        NOT NULL,     -- database · bi_portal · query_api · wall · mobile
    probe_point    text        NOT NULL,     -- 探测发起侧，须与总包商书面认可一致（IR-21）
    is_up          boolean     NOT NULL,
    latency_ms     integer,
    detail         text,
    PRIMARY KEY (probe_id, date_key)
) PARTITION BY RANGE (date_key);

CREATE TABLE audit.sla_daily (               -- 按日考核结果，八年只增不改
    date_key            integer     NOT NULL,
    component           text        NOT NULL,
    service_level       smallint    NOT NULL CHECK (service_level IN (1,2)),
    target_availability numeric(7,5) NOT NULL,   -- 0.99999 / 0.9999
    down_seconds        numeric(10,3) NOT NULL,
    daily_allowance_s   numeric(10,3) NOT NULL,  -- 0.86 / 8.64
    is_met              boolean     NOT NULL,
    probe_count         integer     NOT NULL,
    failure_rule        text        NOT NULL,    -- 失败判定口径，随记录固化
    excluded_reason     text,                    -- DMZ、短信与邮件网关、AI 包、HudHud 的故障不计入
    sealed_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (date_key, component)
);
REVOKE UPDATE, DELETE ON audit.sla_daily FROM PUBLIC;
COMMENT ON COLUMN audit.sla_daily.failure_rule IS '判定口径随每一条记录固化保存。事后修改口径不能改变已封存的历史判定——未事先谈定判定口径的自证等同于没有证据（HLD 11.6）';
COMMENT ON COLUMN audit.sla_daily.excluded_reason IS '按 CN-30、CN-31、DD-28 与 CN-32，总包商运维的 DMZ 与两个网关、AI 包、HudHud 地图服务的故障均不计入本方服务水平';

-- ---------------------------------------------------------------------
-- 发布制品登记（DD-49）
-- 开发侧 Git 为唯一权威；生产网内不部署代码仓库，也不自研版本管理工具。
-- 生产侧靠三样东西回答「现在跑的是哪一版、上个月是哪一版、怎么回上一版」：
-- 不可变制品、对象存储中永不删除的历史版本、以及本表。
-- ---------------------------------------------------------------------
CREATE TABLE meta.release_registry (
    release_version   text        PRIMARY KEY,
    released_at       timestamptz NOT NULL,
    imported_at       timestamptz NOT NULL DEFAULT now(),
    artifact_object   text        NOT NULL,   -- 对象存储中的键，历史版本原则上不删除
    artifact_sha256   text        NOT NULL,
    manifest          jsonb       NOT NULL,   -- 本次包含的契约、模型、指标各自的版本清单
    change_summary    text        NOT NULL,
    reviewed_by       text        NOT NULL,   -- 评审在开发侧发生，证据须随制品进入生产
    imported_by       text        NOT NULL,
    is_current        boolean     NOT NULL DEFAULT false,
    superseded_by     text        REFERENCES meta.release_registry(release_version)
);
CREATE UNIQUE INDEX ux_release_current ON meta.release_registry (is_current) WHERE is_current;
COMMENT ON TABLE meta.release_registry IS '每次发布一行。回滚即从对象存储取回上一制品重新导入，不依赖联网';
COMMENT ON COLUMN meta.release_registry.change_summary IS '没有这一列，运维侧只看得到版本号从 12 变成 13，看不到为什么';

INSERT INTO meta.retention_policy VALUES
 ('artifact','发布制品及其全部历史版本',NULL,'—','原则上不删除。可追溯、可回滚，不适用 DD-38 的三层期限')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- 受控 SQL 的主体授权（DD-51）
-- 只授予自然人，独立于四角色。RFP 1.6.2.15 的措辞是 based on operator role
-- and privileges，按人授予正是 privileges 所指。
-- 系统一律经服务层的查询 API、记录检索、对外数据服务与 MCP 绑定访问——那些
-- 路径自带字段矩阵、部门过滤与全程审计。给系统开自由 SQL 等于在唯一出口旁边
-- 另开一扇门，无收益只有风险（DD-17）。
-- 被授权人在其范围内可见全量数据，该范围内的数据安全由调用方负责，故本名单
-- 须写入数据分类政策输入稿由 MOI 批准。
-- ---------------------------------------------------------------------
CREATE TABLE meta.sql_grant (
    principal            text        PRIMARY KEY,   -- MOI 目录中的自然人账号
    directory_verified   boolean     NOT NULL,
    responsible_party    text        NOT NULL,      -- 责任方（所属部门或主管）
    granted_by           text        NOT NULL,
    granted_at           timestamptz NOT NULL DEFAULT now(),
    expires_at           timestamptz,
    reason               text        NOT NULL,
    allow_export         boolean     NOT NULL DEFAULT false,
    daily_query_quota    integer,
    max_rows             integer     NOT NULL DEFAULT 10000,
    statement_timeout_ms integer     NOT NULL DEFAULT 30000,
    is_revoked           boolean     NOT NULL DEFAULT false,
    revoked_at           timestamptz,
    -- 授权前必须已核实该主体在目录中存在且为自然人账号。默认值缺省即无法插入，
    -- 迫使授权动作显式声明这件事做过了
    CONSTRAINT ck_directory_verified CHECK (directory_verified)
);
COMMENT ON TABLE meta.sql_grant IS '受控 SQL 的授权名单。只列自然人；服务账号不得出现在本表中';
COMMENT ON COLUMN meta.sql_grant.responsible_party IS '「数据安全由调用方负责」须有对象，否则出事无从追责';
COMMENT ON COLUMN meta.sql_grant.allow_export IS '导出单独控制、单独记审计、可单独关闭——一旦导出，责任边界就延伸到了个人电脑上';

-- ---------------------------------------------------------------------------
-- 双语标签覆盖（DD-56）
-- CAD 多数维度只给单语名称，缺的一半由 M-10 人工补。补的是他方系统里的对象，
-- 因此必须记住「确认译名时源端长什么样」——源端一改名，译名即待重新确认。
-- 覆盖挂在自然键上而非代理键上：译的是实体，不是 SCD2 的某一个版本。
-- ---------------------------------------------------------------------------
CREATE TABLE meta.label_override (
    site_code            text        NOT NULL,
    dim_name             text        NOT NULL,      -- 如 dim_unit · dim_incident_type
    src_natural_key      text        NOT NULL,
    lang                 text        NOT NULL CHECK (lang IN ('ar','en')),
    label_text           text        NOT NULL,      -- 人工维护的该语种名称
    -- 确认当时源端另一语种的原文。源端现值与此不符即触发待重新确认，
    -- 不做任何规范化比较：改名频率为数年一次，为降误报而引入的规则得不偿失
    source_label_at_confirm text     NOT NULL,
    state                text        NOT NULL DEFAULT 'confirmed'
                                     CHECK (state IN ('confirmed','pending_reconfirm')),
    confirmed_by         text        NOT NULL,
    confirmed_at         timestamptz NOT NULL DEFAULT now(),
    flagged_at           timestamptz,               -- 转入 pending_reconfirm 的时点
    PRIMARY KEY (site_code, dim_name, src_natural_key, lang)
);
COMMENT ON TABLE meta.label_override IS '双语对照的人工覆盖（REQ-RPT-09）。state=pending_reconfirm 时展现层回落显示源端原文，不显示可能已失效的旧译名';
COMMENT ON COLUMN meta.label_override.source_label_at_confirm IS '存原文而非哈希：M-10 待办界面要能并排显示「确认时源端是这个，现在是这个」，让确认者判断是改名还是实体变更';

CREATE INDEX ix_label_override_pending ON meta.label_override (dim_name, flagged_at)
    WHERE state = 'pending_reconfirm';

-- ---------------------------------------------------------------------------
-- 参照数据变更审计
-- M-10 维护的全部内容——双语覆盖、班次窗口、朝觐期标识——都是人手改的配置，
-- 改错会直接改变报表数字而不留痕。逐字段留痕，只增不改。
-- ---------------------------------------------------------------------------
CREATE TABLE audit.reference_data_change_log (
    change_id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_table         text        NOT NULL,
    target_key           text        NOT NULL,      -- 业务键的规范形式，core.nk 拼成
    field_name           text        NOT NULL,
    old_value            text,                      -- 新增时为 NULL
    new_value            text,                      -- 删除时为 NULL
    changed_by           text        NOT NULL,
    changed_at           timestamptz NOT NULL DEFAULT now(),
    reason               text
);
COMMENT ON TABLE audit.reference_data_change_log IS 'M-10 参照数据维护的逐字段留痕。参照数据不经 raw 层，没有 run_log 血缘，本表是唯一的追溯来源';
CREATE INDEX ix_refchg_target ON audit.reference_data_change_log (target_table, target_key, changed_at DESC);
CREATE INDEX ix_refchg_time   ON audit.reference_data_change_log (changed_at DESC);

-- ---------------------------------------------------------------------------
-- 归档字段可用性剖面与指标起始可用年份（DD-57）
-- 归档数据自 2016 年起，中间经历数次系统升级，早期表结构里可能根本不存在
-- 某些字段。这不是实现缺陷，是源端信息不存在。必须在设计阶段量化并交付，
-- 而不是等验收演示时客户拉一张 2016 年的报表发现是空的。
-- ---------------------------------------------------------------------------
CREATE TABLE meta.field_availability (
    source_id        text        NOT NULL,
    src_table        text        NOT NULL,
    src_column       text        NOT NULL,
    year             smallint    NOT NULL,
    rows_total       bigint      NOT NULL,
    rows_nonnull     bigint      NOT NULL,
    -- 列在该年份的源结构中是否存在。不存在与存在但全空是两回事：
    -- 前者是结构变更的证据（喂给 DD-37 的版本边界反推），后者是采集习惯问题
    column_present   boolean     NOT NULL,
    nonnull_pct      numeric(6,3) GENERATED ALWAYS AS
                     (CASE WHEN rows_total = 0 THEN NULL
                           ELSE round(100.0 * rows_nonnull / rows_total, 3) END) STORED,
    profiled_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, src_table, src_column, year)
);
COMMENT ON TABLE meta.field_availability IS '迁移期一次性全量剖面，按（字段, 年份）统计。既是指标可用年份的依据，也是 schema 版本边界反推的输入（DD-37）';

CREATE TABLE meta.availability_threshold (
    state            text        PRIMARY KEY CHECK (state IN ('available','partial','unavailable')),
    min_pct          numeric(6,3) NOT NULL,
    note             text        NOT NULL
);
COMMENT ON TABLE meta.availability_threshold IS '阈值是业务口径而非常数，故入表不入代码：客户可能认为 80% 的填充率已足够支撑趋势分析';
INSERT INTO meta.availability_threshold(state, min_pct, note) VALUES
  ('available',   95.0, '可用：可直接出数，报表不加提示'),
  ('partial',      5.0, '部分可用：出数但报表须显示填充率与样本量，禁止用于同比'),
  ('unavailable',  0.0, '不可用：报表显示「该时段源端无此数据」，不显示空白也不显示零');

CREATE TABLE meta.metric_availability (
    metric_code      text        NOT NULL,
    site_code        text        NOT NULL,
    year             smallint    NOT NULL,
    -- 指标继承其全部输入字段中最差的一个状态：缺一列即算不出
    state            text        NOT NULL REFERENCES meta.availability_threshold(state),
    limiting_column  text,                      -- 造成该状态的字段，报表提示文案要指名道姓
    computed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (metric_code, site_code, year)
);
COMMENT ON TABLE meta.metric_availability IS '「指标 × 起始可用年份」对照表的机器可读形态。服务层在每次查询时读它——若只作为 Word 交付物，报表就无从提示，客户仍会看到空白';
CREATE INDEX ix_metric_avail_state ON meta.metric_availability (state, year) WHERE state <> 'available';
