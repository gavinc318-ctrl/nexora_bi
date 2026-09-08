-- =====================================================================
-- OSS911 BI/DW  LD-02  04  meta 与 audit
-- meta 与 audit 是仅有的两个不可重建的 schema（raw 为第三个）
-- =====================================================================

-- ---- 接入与运行 -----------------------------------------------------
CREATE TABLE meta.source_contract (          -- Git 为权威，库内为部署时同步的只读副本
    source_id        text        PRIMARY KEY,
    connector        text        NOT NULL CHECK (connector IN ('pg_read','rest_api','kafka_consume','file_drop')),
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
    layer            text        PRIMARY KEY CHECK (layer IN ('hot','warm','backup_offline','backup_extended')),
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
