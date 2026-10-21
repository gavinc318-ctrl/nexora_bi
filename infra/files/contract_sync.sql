-- =====================================================================
-- Contract sync: write the released contracts into meta.source_contract (DD-88 · LD-01 2.5)
--
-- Called by infra/playbooks/45-contracts.yml:
--   psql -X -q -1 -v ON_ERROR_STOP=1 -v contracts=<JSON array from contracts_to_json.py> \
--        -v overrides=<JSON object {source_id: {param: value}}> -f contract_sync.sql
-- One transaction (-1): any failure leaves the database exactly as it was.
--
-- The contract rules are NOT here. Every contract has been checked by `oss911-ingest validate-contract
-- --with-overrides` just before, against the hot-tuned values in the database at that moment (DD-88 item
-- fourteen: one set of rules). "overrides" is what that check saw. This script only makes sure the database
-- still holds exactly those values: if someone changed one in M-01 between the check and this transaction,
-- the check no longer covers it, and deployment fails with "rerun" instead of syncing past it. The comparison
-- is made while holding the contract row FOR UPDATE; M-01 holds it FOR SHARE while it checks and writes a value
-- (LD-06 2.4), so the two wait for each other instead of interleaving.
--
-- Runs as the owner role, like a structural change: the contract copy is read-only for everyone else
-- (admin_svc may change "enabled" only, 0004).
--
-- Per contract, comparing the file with the copy in the database:
--   not in the database            -> insert, "enabled" taken from the file (the only time it is)
--   same version, same content     -> leave alone
--   same version, different content-> FAIL: the contract changed without bumping contract_version
--   file version lower             -> FAIL: the database is ahead of this release (forward only)
--   file version higher            -> update definition, contract_version, target_raw, connector. Whether the
--                                     hot-tuned values still fit the new contract was decided by the check;
--                                     values that do not fit fail the check, and are cleared by an operator
--                                     in M-01 (audited), never silently by deployment
-- "enabled" and meta.source_runtime_param are never touched. Content is compared as jsonb, which
-- ignores key order and formatting -- a reformatted YAML file is the same content.
-- Every target_raw must exist as a table in schema raw: a contract released before its raw table is a
-- release error.
-- Concurrent deployments are serialized by a transaction-level advisory lock (DD-88 item sixteen): without it two
-- runs could both read version 1 and both write, and "versions only go forward" would depend on timing.
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
SELECT set_config('oss911.contracts', :'contracts', true),
       set_config('oss911.overrides', :'overrides', true) \g /dev/null
SELECT pg_advisory_xact_lock(hashtext('oss911.contract_sync')) \g /dev/null
SET LOCAL ROLE oss911_owner;

CREATE TEMP TABLE contract_sync_result (source_id text, action text, contract_version integer) ON COMMIT DROP;

DO $$
DECLARE
    c        jsonb;
    cur      meta.source_contract%ROWTYPE;
    ver      integer;
    raw_ok   boolean;
    checked  jsonb;
    now_held jsonb;
    exists_  boolean;
    n        integer;
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

        -- Row lock first, comparison second (DD-88 item seventeen). M-01 takes FOR SHARE on this row before it
        -- checks and writes a hot-tuned value, so from here until commit no value can change underneath; and
        -- under READ COMMITTED the read below sees everything committed before the lock was granted. Compared
        -- the other way round, a value written between the comparison and the UPDATE would slip through.
        -- A source not in the database yet has no row to lock, and cannot have hot-tuned values either (foreign
        -- key); the advisory lock above serializes deployments
        SELECT * INTO cur FROM meta.source_contract WHERE source_id = c->>'source_id' FOR UPDATE;
        exists_ := FOUND;     -- captured now: every later SELECT ... INTO resets FOUND

        -- The hot-tuned values must be the ones the check saw. A missing entry means the check saw none
        checked := coalesce(current_setting('oss911.overrides')::jsonb -> (c->>'source_id'), '{}');
        SELECT coalesce(jsonb_object_agg(p.param_name, p.param_value), '{}') INTO now_held
          FROM meta.source_runtime_param p WHERE p.source_id = c->>'source_id';
        IF checked <> now_held THEN
            RAISE EXCEPTION 'Contract % (%): hot-tuned values changed after they were checked (checked %, now %). Run the deployment again',
                c->>'source_id', c->>'file', checked, now_held;
        END IF;

        IF NOT exists_ THEN
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
            UPDATE meta.source_contract
               SET definition = c->'definition', contract_version = ver,
                   target_raw = c->>'target_raw', connector = c->>'connector', synced_at = now()
             WHERE source_id = c->>'source_id';
            GET DIAGNOSTICS n = ROW_COUNT;
            IF n <> 1 THEN
                RAISE EXCEPTION 'Contract %: expected to update one row, updated %', c->>'source_id', n;
            END IF;
            INSERT INTO contract_sync_result VALUES (c->>'source_id', 'updated', ver);
        END IF;
    END LOOP;

    INSERT INTO contract_sync_result
    SELECT s.source_id, 'not_in_release', s.contract_version
      FROM meta.source_contract s
     WHERE s.source_id NOT IN (SELECT r.source_id FROM contract_sync_result r);
END $$;

SELECT source_id || '|' || action || '|' || contract_version FROM contract_sync_result ORDER BY source_id;
