-- =====================================================================
-- Metric sync: write metrics/metrics.yaml into meta.metric_registry (DD-97 · LD-03 chapters 5, 6)
--
-- Called by infra/playbooks/46-metrics.yml with psql -X -q -1 -v ON_ERROR_STOP=1 -v pre_production=<true|false>,
-- the script on stdin preceded by one line that sets oss911.metrics to the JSON object from metrics_to_json.py
-- ({metrics, marts}; as a dollar-quoted literal: it is too large for a command-line argument as metrics grow).
-- One transaction (-1): any failure leaves the registry exactly as it was. Runs as the owner role, like a
-- structural change: the registry is read-only for everyone else.
--
-- Per metric, comparing metrics.yaml with the latest version in the registry:
--   not in the registry          -> insert; effective_from as given, or -infinity (the first version covers history)
--   same version, same definition-> update what may change without a new version: names, domain, dimensions,
--                                   source tables, depends_unverified, published, the mart mapping (LD-03 chapter 6:
--                                   a new dimension or a rewrite with the same result is not a new version)
--   same version, new definition -> before go-live (pre_production = true): update in place, "redefined" (DD-97
--                                   item five: changes before production do not bump the version);
--                                   after go-live: FAIL -- the definition changed without a new version
--   higher version               -> insert a new row; older rows stay (DD-97 item two). effective_from as given,
--                                   or the sync day (Asia/Riyadh)
--   lower version                -> FAIL: the registry is ahead of this release (forward only)
-- A confirmed Arabic name (name_ar_state = confirmed, set through M-10, DD-56) is never overwritten by a draft.
-- Then, over the whole registry: every row that names a mart table must find the table in schema mart and its
-- columns in it -- older versions included, so a release that drops an old version's column fails (DD-97 item two:
-- old columns are kept). Metrics without mart_table are synced and reported as not implemented (DD-97 item four).
-- metrics/marts.yaml, checked against the database the same way, then replaces meta.mart_rule (change 0013): one
-- row per declared table; a table no longer declared is removed -- the query API stops reading it (DD-99).
-- reports/*.yaml (checked in metrics_to_json.py against the metrics and marts of the same release) then replaces
-- meta.report_definition (change 0018) the same way: a report no longer in reports/ is removed (DD-102).
-- Concurrent deployments are serialized by a transaction-level advisory lock.
--
-- Output: "<code>|<inserted|updated|unchanged|redefined|new_version>|<version>|<implemented|not_implemented>" per
-- metric, then "<code>|not_in_release|<version>|" for registry metrics this release does not list (kept), then
-- "<mart table>|rule_inserted|rule_updated|rule_unchanged|rule_removed" per mart table, then
-- "<report id>|report_inserted|report_updated|report_unchanged|report_removed" per report.
-- =====================================================================
\set QUIET on
\pset tuples_only on
\pset format unaligned

SELECT set_config('oss911.pre_production', :'pre_production', true) \g /dev/null
SELECT pg_advisory_xact_lock(hashtext('oss911.metric_sync')) \g /dev/null
SET LOCAL ROLE oss911_owner;

CREATE TEMP TABLE metric_sync_result (code text, action text, version integer, implemented text) ON COMMIT DROP;
CREATE TEMP TABLE mart_rule_result (mart_table text, action text) ON COMMIT DROP;
CREATE TEMP TABLE report_result (report_id text, action text) ON COMMIT DROP;

DO $$
DECLARE
    m        jsonb;
    cur      meta.metric_registry%ROWTYPE;
    ver      integer;
    pre_prod boolean := current_setting('oss911.pre_production')::boolean;
    action   text;
    n        integer;
BEGIN
    FOR m IN SELECT jsonb_array_elements(current_setting('oss911.metrics')::jsonb -> 'metrics') LOOP
        ver := (m->>'version')::integer;
        SELECT * INTO cur FROM meta.metric_registry
         WHERE metric_code = m->>'code' ORDER BY metric_version DESC LIMIT 1 FOR UPDATE;

        IF NOT FOUND OR ver > cur.metric_version THEN
            action := CASE WHEN FOUND THEN 'new_version' ELSE 'inserted' END;
            INSERT INTO meta.metric_registry (
                metric_code, metric_version, subject_domain, name_zh, name_en, name_ar, name_ar_state, definition,
                grain, aggregation, measure, entity_date, retrospective, params, dimensions, source_tables,
                depends_unverified, is_published, mart_table, mart_column, mart_numerator, mart_denominator,
                effective_from, definition_sha256)
            VALUES (
                m->>'code', ver, m->>'domain', m->>'name_zh', m->>'name_en', m->>'name_ar', m->>'name_ar_state',
                m->>'definition', m->>'grain', m->>'aggregation', m->>'measure', m->>'entity_date',
                (m->>'retrospective')::boolean, m->'params',
                ARRAY(SELECT jsonb_array_elements_text(m->'dimensions')),
                ARRAY(SELECT jsonb_array_elements_text(m->'source_tables')),
                (m->>'depends_unverified')::boolean, (m->>'published')::boolean,
                m->>'mart_table', m->>'mart_column', m->>'mart_numerator', m->>'mart_denominator',
                coalesce((m->>'effective_from')::date,
                         CASE WHEN action = 'inserted' THEN '-infinity'::date
                              ELSE (now() AT TIME ZONE 'Asia/Riyadh')::date END),
                m->>'definition_sha256');

        ELSIF ver < cur.metric_version THEN
            RAISE EXCEPTION 'Metric %: metrics.yaml has version %, the registry already has version %. Versions only go forward',
                m->>'code', ver, cur.metric_version;

        ELSE
            IF cur.definition_sha256 <> m->>'definition_sha256' AND NOT pre_prod THEN
                RAISE EXCEPTION 'Metric % version %: the definition changed (definition, grain, aggregation, measure, entity_date, params or retrospective) without a new version. Bump version in metrics.yaml (LD-03 chapter 6)',
                    m->>'code', ver;
            END IF;
            UPDATE meta.metric_registry r
               SET subject_domain = m->>'domain', name_zh = m->>'name_zh', name_en = m->>'name_en',
                   name_ar = CASE WHEN r.name_ar_state = 'confirmed' THEN r.name_ar ELSE m->>'name_ar' END,
                   name_ar_state = CASE WHEN r.name_ar_state = 'confirmed' THEN 'confirmed' ELSE m->>'name_ar_state' END,
                   definition = m->>'definition', grain = m->>'grain', aggregation = m->>'aggregation',
                   measure = m->>'measure', entity_date = m->>'entity_date',
                   retrospective = (m->>'retrospective')::boolean, params = m->'params',
                   dimensions = ARRAY(SELECT jsonb_array_elements_text(m->'dimensions')),
                   source_tables = ARRAY(SELECT jsonb_array_elements_text(m->'source_tables')),
                   depends_unverified = (m->>'depends_unverified')::boolean,
                   is_published = (m->>'published')::boolean,
                   mart_table = m->>'mart_table', mart_column = m->>'mart_column',
                   mart_numerator = m->>'mart_numerator', mart_denominator = m->>'mart_denominator',
                   effective_from = coalesce((m->>'effective_from')::date, r.effective_from),
                   definition_sha256 = m->>'definition_sha256',
                   updated_at = now()
             WHERE r.metric_code = cur.metric_code AND r.metric_version = cur.metric_version
               AND (r.subject_domain, r.name_zh, r.name_en,
                    CASE WHEN r.name_ar_state = 'confirmed' THEN NULL ELSE r.name_ar END,
                    CASE WHEN r.name_ar_state = 'confirmed' THEN NULL ELSE r.name_ar_state END,
                    r.dimensions, r.source_tables, r.depends_unverified, r.is_published,
                    r.mart_table, r.mart_column, r.mart_numerator, r.mart_denominator, r.definition_sha256,
                    r.effective_from)
                   IS DISTINCT FROM
                   (m->>'domain', m->>'name_zh', m->>'name_en',
                    CASE WHEN r.name_ar_state = 'confirmed' THEN NULL ELSE m->>'name_ar' END,
                    CASE WHEN r.name_ar_state = 'confirmed' THEN NULL ELSE m->>'name_ar_state' END,
                    ARRAY(SELECT jsonb_array_elements_text(m->'dimensions')),
                    ARRAY(SELECT jsonb_array_elements_text(m->'source_tables')),
                    (m->>'depends_unverified')::boolean, (m->>'published')::boolean,
                    m->>'mart_table', m->>'mart_column', m->>'mart_numerator', m->>'mart_denominator',
                    m->>'definition_sha256', coalesce((m->>'effective_from')::date, r.effective_from));
            GET DIAGNOSTICS n = ROW_COUNT;
            action := CASE WHEN n = 0 THEN 'unchanged'
                           WHEN cur.definition_sha256 <> m->>'definition_sha256' THEN 'redefined'
                           ELSE 'updated' END;
        END IF;

        INSERT INTO metric_sync_result
        VALUES (m->>'code', action, ver,
                CASE WHEN m->>'mart_table' IS NULL THEN 'not_implemented' ELSE 'implemented' END);
    END LOOP;
END $$;

-- Every mart mapping in the registry, older versions included, must point at existing columns
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(format('%s v%s: %s', r.metric_code, r.metric_version, x.missing), '; '
                      ORDER BY r.metric_code, r.metric_version)
      INTO bad
      FROM meta.metric_registry r
      CROSS JOIN LATERAL (
          SELECT CASE WHEN to_regclass(r.mart_table) IS NULL
                           OR (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                WHERE c.oid = to_regclass(r.mart_table)) <> 'mart'
                      THEN 'table ' || r.mart_table || ' does not exist in schema mart'
                      ELSE (SELECT 'column(s) ' || string_agg(col, ', ') || ' missing in ' || r.mart_table
                              FROM unnest(ARRAY[r.mart_column, r.mart_numerator, r.mart_denominator]) col
                             WHERE col IS NOT NULL
                               AND NOT EXISTS (SELECT 1 FROM pg_attribute a
                                                WHERE a.attrelid = to_regclass(r.mart_table)
                                                  AND a.attname = col AND a.attnum > 0 AND NOT a.attisdropped))
                 END AS missing) x
     WHERE r.mart_table IS NOT NULL AND x.missing IS NOT NULL;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Metrics point at mart columns that do not exist (build the mart models first, 50-dags; old versions keep their columns, DD-97): %', bad;
    END IF;
END $$;

-- Every table metrics/marts.yaml declares must exist in schema mart with the columns its rules name (dimensions,
-- attributes, sensitive columns, the self column): the service layer reads them by those names (DD-99)
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(format('%s: %s', t.key, x.missing), '; ' ORDER BY t.key)
      INTO bad
      FROM jsonb_each(current_setting('oss911.metrics')::jsonb -> 'marts') t
      CROSS JOIN LATERAL (
          SELECT CASE WHEN to_regclass(t.key) IS NULL
                      THEN 'table does not exist'
                      ELSE (SELECT 'column(s) ' || string_agg(col, ', ') || ' missing'
                              FROM jsonb_array_elements_text(t.value -> 'columns') col
                             WHERE NOT EXISTS (SELECT 1 FROM pg_attribute a
                                                WHERE a.attrelid = to_regclass(t.key)
                                                  AND a.attname = col AND a.attnum > 0 AND NOT a.attisdropped))
                 END AS missing) x
     WHERE x.missing IS NOT NULL;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'metrics/marts.yaml names mart tables or columns that do not exist: %', bad;
    END IF;
END $$;

-- The read rules of the declared tables into meta.mart_rule (change 0013); undeclared tables are removed
WITH m AS (
    SELECT t.key AS mart_table, t.value AS r
      FROM jsonb_each(current_setting('oss911.metrics')::jsonb -> 'marts') t
),
removed AS (
    DELETE FROM meta.mart_rule x WHERE x.mart_table NOT IN (SELECT mart_table FROM m)
    RETURNING x.mart_table
),
upserted AS (
    INSERT INTO meta.mart_rule AS x (mart_table, grain, roles, row_filter, self_column, dimensions, required_dimensions,
                                     attributes, sensitive_columns, rules_sha256)
    SELECT m.mart_table, m.r->>'grain', ARRAY(SELECT jsonb_array_elements_text(m.r->'roles')), m.r->>'row_filter',
           m.r->>'self_column', m.r->'dimensions', ARRAY(SELECT jsonb_array_elements_text(m.r->'required_dimensions')),
           m.r->'attributes', m.r->'sensitive_columns', m.r->>'rules_sha256'
      FROM m
    ON CONFLICT (mart_table) DO UPDATE
       SET grain = excluded.grain, roles = excluded.roles, row_filter = excluded.row_filter,
           self_column = excluded.self_column, dimensions = excluded.dimensions,
           required_dimensions = excluded.required_dimensions, attributes = excluded.attributes,
           sensitive_columns = excluded.sensitive_columns, rules_sha256 = excluded.rules_sha256, synced_at = now()
     WHERE x.rules_sha256 IS DISTINCT FROM excluded.rules_sha256
    RETURNING x.mart_table, (xmax = 0) AS inserted
)
INSERT INTO mart_rule_result
SELECT mart_table, 'rule_removed' FROM removed
UNION ALL
SELECT mart_table, CASE WHEN inserted THEN 'rule_inserted' ELSE 'rule_updated' END FROM upserted
UNION ALL
SELECT m.mart_table, 'rule_unchanged' FROM m WHERE m.mart_table NOT IN (SELECT mart_table FROM upserted);

-- The report definitions into meta.report_definition (change 0018); reports no longer in reports/ are removed
WITH m AS (
    SELECT t.key AS report_id, t.value AS d
      FROM jsonb_each(coalesce(current_setting('oss911.metrics')::jsonb -> 'reports', '{}')) t
),
removed AS (
    DELETE FROM meta.report_definition x WHERE x.report_id NOT IN (SELECT report_id FROM m)
    RETURNING x.report_id
),
upserted AS (
    INSERT INTO meta.report_definition AS x (report_id, kind, roles, published, definition, definition_sha256)
    SELECT m.report_id, m.d->>'kind', ARRAY(SELECT jsonb_array_elements_text(m.d->'roles')),
           (m.d->>'published')::boolean, m.d - 'definition_sha256', m.d->>'definition_sha256'
      FROM m
    ON CONFLICT (report_id) DO UPDATE
       SET kind = excluded.kind, roles = excluded.roles, published = excluded.published,
           definition = excluded.definition, definition_sha256 = excluded.definition_sha256, synced_at = now()
     WHERE x.definition_sha256 IS DISTINCT FROM excluded.definition_sha256
    RETURNING x.report_id, (xmax = 0) AS inserted
)
INSERT INTO report_result
SELECT report_id, 'report_removed' FROM removed
UNION ALL
SELECT report_id, CASE WHEN inserted THEN 'report_inserted' ELSE 'report_updated' END FROM upserted
UNION ALL
SELECT m.report_id, 'report_unchanged' FROM m WHERE m.report_id NOT IN (SELECT report_id FROM upserted);

SELECT code || '|' || action || '|' || version || '|' || implemented FROM metric_sync_result ORDER BY code;
SELECT r.metric_code || '|not_in_release|' || max(r.metric_version) || '|'
  FROM meta.metric_registry r
 WHERE r.metric_code NOT IN (SELECT jsonb_array_elements(current_setting('oss911.metrics')::jsonb -> 'metrics')->>'code')
 GROUP BY r.metric_code ORDER BY 1;
SELECT mart_table || '|' || action FROM mart_rule_result ORDER BY mart_table;
SELECT report_id || '|' || action FROM report_result ORDER BY report_id;
