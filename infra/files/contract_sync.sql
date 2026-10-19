-- =====================================================================
-- Contract sync: write the released contracts into meta.source_contract (DD-88 · LD-01 2.5)
--
-- Called by infra/playbooks/45-contracts.yml:
--   psql -X -q -1 -v ON_ERROR_STOP=1 -v contracts=<JSON array from contracts_to_json.py> -f contract_sync.sql
-- One transaction (-1): any failure leaves the database exactly as it was.
--
-- Runs as the owner role, like a structural change: the contract copy is read-only for everyone else
-- (admin_svc may change "enabled" only, 0004).
--
-- Per contract, comparing the file with the copy in the database:
--   not in the database            -> insert, "enabled" taken from the file (the only time it is)
--   same version, same content     -> leave alone
--   same version, different content-> FAIL: the contract changed without bumping contract_version
--   file version lower             -> FAIL: the database is ahead of this release (forward only)
--   file version higher            -> check the hot-tuned values against the new contract first; any
--                                     that no longer apply -> FAIL, listing them. They are cleared by an
--                                     operator in M-01 (audited), never silently by deployment. Then update
--                                     definition, contract_version, target_raw, connector
-- "enabled" and meta.source_runtime_param are never touched. Content is compared as jsonb, which
-- ignores key order and formatting -- a reformatted YAML file is the same content.
-- Every target_raw must exist as a table in schema raw: a contract released before its raw table is a
-- release error.
--
-- Output: one line per contract "<source_id>|<inserted|updated|unchanged>|<version>", then one
-- "<source_id>|not_in_release|<version>" line for each contract in the database that this release
-- does not list (reported, not removed).
-- =====================================================================
\set QUIET on
\pset tuples_only on
\pset format unaligned

-- psql does not substitute variables inside dollar-quoted bodies; hand the JSON over via a
-- transaction-local setting instead
SELECT set_config('oss911.contracts', :'contracts', true) \g /dev/null
SET LOCAL ROLE oss911_owner;

CREATE TEMP TABLE contract_sync_result (source_id text, action text, contract_version integer) ON COMMIT DROP;

DO $$
DECLARE
    c        jsonb;
    cur      meta.source_contract%ROWTYPE;
    ver      integer;
    raw_ok   boolean;
    stale    text;
BEGIN
    FOR c IN SELECT jsonb_array_elements(current_setting('oss911.contracts')::jsonb) LOOP
        ver := (c->>'contract_version')::integer;

        SELECT EXISTS (SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE t.relkind IN ('r', 'p') AND n.nspname = 'raw'
                          AND format('%I.%I', n.nspname, t.relname) = c->>'target_raw')
          INTO raw_ok;
        IF NOT raw_ok THEN
            RAISE EXCEPTION 'Contract % (%): target_raw % is not a table in schema raw. Its structural change must be executed before the contract is released',
                c->>'source_id', c->>'file', c->>'target_raw';
        END IF;

        SELECT * INTO cur FROM meta.source_contract WHERE source_id = c->>'source_id';

        IF NOT FOUND THEN
            INSERT INTO meta.source_contract (source_id, connector, target_raw, contract_version, definition, enabled)
            VALUES (c->>'source_id', c->>'connector', c->>'target_raw', ver, c->'definition', (c->>'enabled')::boolean);
            INSERT INTO contract_sync_result VALUES (c->>'source_id', 'inserted', ver);

        ELSIF ver = cur.contract_version THEN
            IF cur.definition = c->'definition' AND cur.connector = c->>'connector'
               AND cur.target_raw = c->>'target_raw' THEN
                INSERT INTO contract_sync_result VALUES (c->>'source_id', 'unchanged', ver);
            ELSE
                RAISE EXCEPTION 'Contract % (%): content differs from the copy in the database at the same contract_version %. A changed contract must bump contract_version',
                    c->>'source_id', c->>'file', ver;
            END IF;

        ELSIF ver < cur.contract_version THEN
            RAISE EXCEPTION 'Contract % (%): file version % is lower than the database version %. Deployment goes forward only',
                c->>'source_id', c->>'file', ver, cur.contract_version;

        ELSE
            -- Each whitelisted parameter (0004's CHECK) overrides one setting of the contract; it still applies only
            -- if the new contract still has that setting. Keep this mapping in step with the CHECK.
            SELECT string_agg(p.param_name || '=' || p.param_value, ', ' ORDER BY p.param_name)
              INTO stale
              FROM meta.source_runtime_param p
             WHERE p.source_id = c->>'source_id'
               AND (CASE p.param_name
                        WHEN 'lookback_minutes' THEN c->'definition' #> '{window,lookback_minutes}'
                    END) IS NULL;
            IF stale IS NOT NULL THEN
                RAISE EXCEPTION 'Contract % (%): version % no longer has a setting for these hot-tuned values: %. Clear them in M-01 (audited), then deploy again',
                    c->>'source_id', c->>'file', ver, stale;
            END IF;
            UPDATE meta.source_contract
               SET definition = c->'definition', contract_version = ver,
                   target_raw = c->>'target_raw', connector = c->>'connector', synced_at = now()
             WHERE source_id = c->>'source_id';
            INSERT INTO contract_sync_result VALUES (c->>'source_id', 'updated', ver);
        END IF;
    END LOOP;

    INSERT INTO contract_sync_result
    SELECT s.source_id, 'not_in_release', s.contract_version
      FROM meta.source_contract s
     WHERE s.source_id NOT IN (SELECT r.source_id FROM contract_sync_result r);
END $$;

SELECT source_id || '|' || action || '|' || contract_version FROM contract_sync_result ORDER BY source_id;
