-- =====================================================================
-- OSS911 BI/DW  LD-02  02  core 维度表
-- 缓变维一律 Type 2「按当时」记录（DD-12）：维度存生效与失效时间，
-- 事实关联当时那一版。目的只有一个——去年打印的报表今年重跑数值不变。
-- 双语名称：源系统仅提供单语时由 M-10 维护对照（REQ-RPT-09）
--
-- 排序规则策略（DD-63）
--   集群默认 LC_COLLATE=C —— 字节序，与 OS 的 libc 解耦。glibc 大版本升级会改变
--   排序规则，而绑在 glibc 上的文本索引会【静默】失效（索引按旧规则排、查询按新规则
--   找，查不到本该查到的行且不报错）。八年合同期内必然发生一次 OS 大版本升级，
--   故集群默认必须是 C。
--
--   语言相关的排序显式用 ICU，并声明在【列】上而非写在查询里：
--     name_ar  COLLATE "ar-x-icu"    面向用户的阿拉伯语名称
--     name_en  COLLATE "en-x-icu"    面向用户的英文名称
--   声明在列上，ORDER BY name_ar 自动使用该规则——不依赖每条查询都记得写 COLLATE，
--   而漏写 COLLATE 不会报错，只是排序不对，阿语母语者一眼看得出、我们看不出。
--
--   编码、代码值、自然键、英阿混排的自由文本（不参与自然语言排序的）一律保持 C。
--
--   ICU 版本变化由 PostgreSQL 的 collation version 管理：版本不符时会告警，
--   按 infra/runbook 的检查脚本找出受影响的索引并 REINDEX。这比 glibc 的静默失效
--   好在「它会告诉你」。
-- =====================================================================

-- 通用的 Type 2 列约定（每张 SCD 维度都有）：
--   valid_from timestamptz NOT NULL
--   valid_to   timestamptz NOT NULL DEFAULT 'infinity'
--   is_current boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED
--   src_natural_key text   NOT NULL     -- 业务键，跨版本不变
-- 通用的血缘列（每张 core 表都有）：
--   src_system, run_id, contract_version, src_watermark, ingested_at, built_at

CREATE TABLE core.dim_site (
    site_sk          uuid        PRIMARY KEY,
    site_code        text        NOT NULL UNIQUE,        -- 'MKK' 麦加 · 'MDN' 麦地那（预留）
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    is_active        boolean     NOT NULL DEFAULT true,
    built_at         timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.dim_site IS '站点。本期仅麦加；全部事实表带 site_sk 为多站演进预留（DD-23 · REQ-MSI-01）';

CREATE TABLE core.dim_date (
    date_key         integer     PRIMARY KEY,            -- YYYYMMDD
    full_date        date        NOT NULL UNIQUE,
    year             smallint    NOT NULL,
    quarter          smallint    NOT NULL,
    month            smallint    NOT NULL,
    day              smallint    NOT NULL,
    day_of_week      smallint    NOT NULL,
    week_of_year     smallint    NOT NULL,
    is_weekend       boolean     NOT NULL,               -- 沙特周末为周五、周六
    hijri_year       smallint,                           -- 回历，备朝觐相关分析（HLD 6.3）
    hijri_month      smallint,
    hijri_day        smallint,
    hijri_label_ar   text,
    is_hajj_season   boolean     NOT NULL DEFAULT false, -- 由 M-10 人工维护
    duty_level_code  text                                -- 当日勤务级别。客户现未使用，接口保留、非必选（DD-54）；启用时由 CAD 提供，不做人工维护
);
COMMENT ON COLUMN core.dim_date.is_hajj_season IS '朝觐期标识。人工维护，用于分组对比而非直接比较（M-R07）';

CREATE TABLE core.dim_agency_type (                      -- 警种
    agency_type_sk   uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,               -- CAD 警种编码
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    src_system       text        NOT NULL,
    run_id           uuid        NOT NULL,
    contract_version integer     NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON TABLE core.dim_agency_type IS '警种（警察 · 民防 · 救护 · 交警……）。派警单按警种拆分，调度员按警种归口，是行级安全的过滤维度（DD-41）';

CREATE TABLE core.dim_unit (                             -- 处置单位，隶属警种
    unit_sk          uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    agency_type_sk   uuid        NOT NULL REFERENCES core.dim_agency_type,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    sector_sk        uuid,                               -- 驻地辖区，见 dim_sector
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    src_system       text        NOT NULL,
    run_id           uuid        NOT NULL,
    contract_version integer     NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_sk, src_natural_key, valid_from)
);

CREATE TABLE core.dim_officer (                          -- 警员（出警）
    officer_sk       uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    unit_sk          uuid        NOT NULL REFERENCES core.dim_unit,
    agency_type_sk   uuid        NOT NULL REFERENCES core.dim_agency_type,
    src_natural_key  text        NOT NULL,               -- 警号
    name_ar          text COLLATE "ar-x-icu",
    name_en          text COLLATE "en-x-icu",
    rank_code        text,
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    src_system       text        NOT NULL,
    run_id           uuid        NOT NULL,
    contract_version integer     NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON TABLE core.dim_officer IS '警员。出警单的粒度是警员而非车组，故 M-R05 单位出动次数按出警单计（一张派警单出三名警员即三次）';

CREATE TABLE core.dim_agent (                            -- 坐席（接警员 / 调度员）
    agent_sk         uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,               -- 工号
    login_id         text,
    name_ar          text COLLATE "ar-x-icu",
    name_en          text COLLATE "en-x-icu",
    role_code        text        NOT NULL,               -- call_taker · dispatcher · supervisor
    agency_type_sk   uuid        REFERENCES core.dim_agency_type,  -- 调度员与主管的派驻警种；接警员为空
    team_code        text,                               -- 班组
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    src_system       text        NOT NULL,
    run_id           uuid        NOT NULL,
    contract_version integer     NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON COLUMN core.dim_agent.agency_type_sk IS '统计口径用的派驻警种（按当时）。权限判定用的是当前警种，取自身份系统，两者正交、不得混用（DD-41）';

CREATE TABLE core.dim_incident_type (
    incident_type_sk uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    parent_sk        uuid,                               -- 分类层级
    level_no         smallint    NOT NULL DEFAULT 1,
    mapping_state    text        NOT NULL DEFAULT 'mapped_unverified'
                     CHECK (mapping_state IN ('verified','mapped_unverified','unknown')),
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    src_system       text        NOT NULL,
    run_id           uuid        NOT NULL,
    contract_version integer     NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON COLUMN core.dim_incident_type.mapping_state IS '映射三态（HLD 4.3）。未验证的映射可进探索性分析，进入对外指标时必须带标记';

CREATE TABLE core.dim_sector (                           -- 辖区
    sector_sk        uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    parent_sk        uuid,
    boundary         geometry(MultiPolygon, 4326),       -- 仅用于网格聚合与呈现，辖区归属以 CAD 字段为准（DD-32）
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    src_system       text        NOT NULL,
    run_id           uuid        NOT NULL,
    contract_version integer     NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON COLUMN core.dim_sector.boundary IS '区划面。警情的辖区归属不由此重算——CAD 在受理当时用当时的区划面算出，重算会得到「按现在」的口径（DD-32 · DD-12）';
CREATE INDEX ix_dim_sector_boundary ON core.dim_sector USING gist (boundary);

CREATE TABLE core.dim_channel (                          -- 警情来源渠道
    channel_sk       uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,               -- phone · self_initiated · patrol · vms · mds · sms
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    has_call         boolean     NOT NULL,               -- 决定该渠道的接警单是否应有关联通话
    UNIQUE (site_sk, src_natural_key)
);
COMMENT ON COLUMN core.dim_channel.has_call IS '非电话渠道的接警单没有首呼，须从全过程时长的分母中显式排除（M-I08）';

CREATE TABLE core.dim_queue (                            -- 技能队列（来源 ICP）
    queue_sk         uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,               -- ICP 的技能队列 ID
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    skill_group_code text,
    media_type_code  smallint,                           -- 语音 · 文字等，见 ICP 媒体类型
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    src_system       text        NOT NULL,
    run_id           uuid        NOT NULL,
    contract_version integer     NOT NULL,
    built_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON TABLE core.dim_queue IS '技能队列是路由能力标签（阿语、英语、医疗…），不是组织结构，也不是 CAD 的警种——技能队列决定这通电话由谁接，警种决定接完之后派谁去，禁止一对一映射。走 SCD2：队列改名或合并后，历史报表仍按当时的名字呈现';
COMMENT ON COLUMN core.dim_queue.media_type_code IS '本期只用语音。ICP 的技能队列可承载文字等其它媒体，取值保留以免日后启用时改结构';

CREATE TABLE core.dim_priority (
    priority_sk      uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    rank_order       smallint    NOT NULL,
    UNIQUE (site_sk, src_natural_key)
);

CREATE TABLE core.dim_disposition (                      -- 处置结果 / 结案原因
    disposition_sk   uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    is_cancelled     boolean     NOT NULL DEFAULT false, -- 撤案类
    mapping_state    text        NOT NULL DEFAULT 'mapped_unverified'
                     CHECK (mapping_state IN ('verified','mapped_unverified','unknown')),
    UNIQUE (site_sk, src_natural_key)
);

CREATE TABLE core.dim_call_result (
    call_result_sk   uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,               -- answered · abandoned · missed · transferred · malicious
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    UNIQUE (site_sk, src_natural_key)
);

CREATE TABLE core.dim_shift (
    shift_sk         uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text COLLATE "ar-x-icu"        NOT NULL,
    name_en          text COLLATE "en-x-icu"        NOT NULL,
    start_time       time        NOT NULL,
    end_time         time        NOT NULL,
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON TABLE core.dim_shift IS '班次为派生时间带，不维护排班计划（DD-53）。本表只登记时间窗口定义（start_time/end_time），由 M-10 人工维护、行数极少、走 SCD2 以免改窗口时改写历史。事件按自身时间戳归班；在岗时长由 fact_agent_session 的签入区间与窗口求交得出';
