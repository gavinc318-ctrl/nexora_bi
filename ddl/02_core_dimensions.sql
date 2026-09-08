-- =====================================================================
-- OSS911 BI/DW  LD-02  02  core 维度表
-- 缓变维一律 Type 2「按当时」记录（DD-12）：维度存生效与失效时间，
-- 事实关联当时那一版。目的只有一个——去年打印的报表今年重跑数值不变。
-- 双语名称：源系统仅提供单语时由 M-10 维护对照（REQ-RPT-09）
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
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
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
    duty_level_code  text                                -- 当日勤务级别，M-10 人工维护
);
COMMENT ON COLUMN core.dim_date.is_hajj_season IS '朝觐期标识。人工维护，用于分组对比而非直接比较（M-R07）';

CREATE TABLE core.dim_agency_type (                      -- 警种
    agency_type_sk   uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,               -- CAD 警种编码
    code             text        NOT NULL,
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
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
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
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
    name_ar          text,
    name_en          text,
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
    name_ar          text,
    name_en          text,
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
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
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
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
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
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
    has_call         boolean     NOT NULL,               -- 决定该渠道的接警单是否应有关联通话
    UNIQUE (site_sk, src_natural_key)
);
COMMENT ON COLUMN core.dim_channel.has_call IS '非电话渠道的接警单没有首呼，须从全过程时长的分母中显式排除（M-I08）';

CREATE TABLE core.dim_priority (
    priority_sk      uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
    rank_order       smallint    NOT NULL,
    UNIQUE (site_sk, src_natural_key)
);

CREATE TABLE core.dim_disposition (                      -- 处置结果 / 结案原因
    disposition_sk   uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
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
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
    UNIQUE (site_sk, src_natural_key)
);

CREATE TABLE core.dim_shift (
    shift_sk         uuid        PRIMARY KEY,
    site_sk          uuid        NOT NULL REFERENCES core.dim_site,
    src_natural_key  text        NOT NULL,
    code             text        NOT NULL,
    name_ar          text        NOT NULL,
    name_en          text        NOT NULL,
    start_time       time        NOT NULL,
    end_time         time        NOT NULL,
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz NOT NULL DEFAULT 'infinity',
    is_current       boolean     GENERATED ALWAYS AS (valid_to = 'infinity') STORED,
    UNIQUE (site_sk, src_natural_key, valid_from)
);
COMMENT ON TABLE core.dim_shift IS '班次。计划排班为旁路数据源（M-10 人工维护或文件导入），实际在岗由 ICP 登录/登出事件推算（R-22）';
