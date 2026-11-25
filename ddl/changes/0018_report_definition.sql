-- =====================================================================
-- OSS911 BI/DW  Structural change 0018  Report definitions for the portal
-- Basis: DD-102 (2026-11-24, item four; note 2026-11-25, item one) · LD-04 3.5 · LD-05 chapter 2
--
-- run_as = owner.
-- =====================================================================

-- ---------------------------------------------------------------------
-- meta.report_definition: one row per report or dashboard -- the read-only copy of reports/*.yaml, written only by
-- 46-metrics (infra/files/metric_sync.sql) as the owner, like meta.metric_registry and meta.mart_rule. The service
-- layer reads it with oss911_query_svc (SELECT on meta through the default privileges of 0002) for GET /v1/reports
-- and GET /v1/reports/{id}. A definition says what to query and how to lay it out; the data still goes through
-- POST /v1/query under the same permissions -- holding a definition is not holding its data (LD-04 3.5).
-- A report removed from reports/ is removed here; published = false keeps it out of the catalogue and refuses it.
-- ---------------------------------------------------------------------
CREATE TABLE meta.report_definition (
    report_id         text        PRIMARY KEY,
    kind              text        NOT NULL,
    roles             text[]      NOT NULL,
    published         boolean     NOT NULL,
    definition        jsonb       NOT NULL,              -- the checked definition (metrics_to_json.py)
    definition_sha256 text        NOT NULL,              -- to report what a sync changed
    synced_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_report_definition_id CHECK (report_id ~ '^[RD]-[0-9]{2}$'),
    CONSTRAINT ck_report_definition_kind CHECK ((kind = 'report') = (report_id LIKE 'R-%')
        AND kind IN ('report', 'dashboard')),
    CONSTRAINT ck_report_definition_roles CHECK (cardinality(roles) > 0
        AND roles <@ ARRAY['call_taker', 'dispatcher', 'supervisor', 'admin']),
    CONSTRAINT ck_report_definition_body CHECK (jsonb_typeof(definition) = 'object'
        AND definition ->> 'id' = report_id)
);
COMMENT ON TABLE meta.report_definition IS
    'Report and dashboard definitions (reports/*.yaml), synced by 46-metrics only. Visibility is decided by the service layer from roles (DD-102)';
COMMENT ON COLUMN meta.report_definition.roles IS
    'Roles that see the report in the catalogue and may read its definition; 46 refuses roles beyond those of the mart tables it reads';
