-- =====================================================================
-- OSS911 BI/DW  Structural change 0013  Mart read rules for the service layer
-- Basis: DD-99 (2026-11-16 review, item seven; Mac side 2026-11-16: marts.yaml synced into a read-only meta table,
--        the service layer reads the database) · LD-04 3.5 · LD-07 2.2, 2.3
--
-- run_as = owner.
-- =====================================================================

-- ---------------------------------------------------------------------
-- meta.mart_rule: one row per mart table the query API may read -- the read-only copy of metrics/marts.yaml,
-- written only by 46-metrics (infra/files/metric_sync.sql) as the owner, like meta.metric_registry. A mart table
-- without a row here is not read by the query API (DD-99). The service layer reads it with oss911_query_svc
-- (SELECT on meta through the default privileges of 0002).
-- ---------------------------------------------------------------------
CREATE TABLE meta.mart_rule (
    mart_table          text        PRIMARY KEY,
    grain               text        NOT NULL,
    roles               text[]      NOT NULL,
    row_filter          text        NOT NULL,
    self_column         text,
    dimensions          jsonb       NOT NULL,              -- dimension name -> column
    required_dimensions text[]      NOT NULL DEFAULT '{}',
    attributes          jsonb       NOT NULL DEFAULT '{}', -- dimension name -> [columns shown with a member]
    sensitive_columns   jsonb       NOT NULL DEFAULT '{}', -- column -> [roles that may see it]
    rules_sha256        text        NOT NULL,              -- of the rules above, to report what a sync changed
    synced_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_mart_rule_table CHECK (mart_table ~ '^mart\.[a-z][a-z0-9_]*$'),
    CONSTRAINT ck_mart_rule_roles CHECK (cardinality(roles) > 0
        AND roles <@ ARRAY['call_taker', 'dispatcher', 'supervisor', 'admin']),
    CONSTRAINT ck_mart_rule_row_filter CHECK (row_filter IN ('none', 'department', 'self')),
    CONSTRAINT ck_mart_rule_self CHECK ((row_filter = 'self') = (self_column IS NOT NULL)),
    CONSTRAINT ck_mart_rule_dimensions CHECK (jsonb_typeof(dimensions) = 'object' AND dimensions <> '{}'::jsonb)
);
COMMENT ON TABLE meta.mart_rule IS
    'Read rules of the mart tables (metrics/marts.yaml), synced by 46-metrics only. A mart table not listed here is not read by the query API (DD-99)';
COMMENT ON COLUMN meta.mart_rule.row_filter IS
    'none · department (the user''s department, LD-07 2.3) · self (only the user''s own rows, matched on self_column with the token''s agent number across every dimension version, DD-99 review item two)';
COMMENT ON COLUMN meta.mart_rule.required_dimensions IS
    'Dimensions every query of the table must output: a column repeated on each of their members (a day total) is wrong when summed across them';
