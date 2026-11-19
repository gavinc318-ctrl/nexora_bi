-- =====================================================================
-- OSS911 BI/DW  Structural change 0015  ICP agent metrics: sign-in intervals (if03b) and day totals (if03c)
-- Basis: DD-98 items one to nine · DD-95 · DD-82 · DD-88 · LD-01 4.1 · contracts if03b / if03c v1
--
-- run_as = owner. Default privileges of 0002 give ingest SELECT and INSERT on the new raw tables, transform SELECT
-- on them, and transform SELECT, INSERT, UPDATE, DELETE on core; nothing else is granted here.
--
-- Both sources come from the same interface, hindex/agent, which has no date parameter (DD-98 item one): every run is
-- one observation of "today so far". Nothing is in these tables yet on any machine (no contract of the two has been
-- released), so the core columns below can be NOT NULL without a default.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. An observation batch (DD-98 item one)
-- A run of these sources has no time window to cut and no watermark to move: it asks for the current values of a set
-- of agents, one batch per request of at most 2000 agents. window_from holds the day the observation belongs to
-- (YYYY-MM-DD in the contract's day_timezone), window_to the observation time in the source's notation.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    found text[];
BEGIN
    SELECT array_agg(conname) INTO found
      FROM pg_constraint
     WHERE conrelid = 'meta.ingest_batch'::regclass AND contype = 'c'
       AND pg_get_constraintdef(oid) LIKE '%mode%incremental%backfill%resweep%snapshot%';
    IF coalesce(array_length(found, 1), 0) <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one CHECK on meta.ingest_batch.mode, found: %', found;
    END IF;
    EXECUTE format('ALTER TABLE meta.ingest_batch DROP CONSTRAINT %I', found[1]);
END $$;
ALTER TABLE meta.ingest_batch ADD CONSTRAINT ck_ingest_batch_mode
    CHECK (mode IN ('incremental', 'backfill', 'resweep', 'snapshot', 'observation'));
COMMENT ON COLUMN meta.ingest_batch.mode IS
    'incremental: from the watermark, moves it. backfill: an explicit window, by hand. resweep: the daily re-sweep of '
    'one day. snapshot: one dataset at one version (0007). observation: the current values of a set of agents, '
    'interfaces without a date parameter (0015; window_from = the day, window_to = the observation time)';

-- ---------------------------------------------------------------------
-- 2. raw.icp_agent_session: one row per sign-in interval and observation that differs (DD-98 item seven)
-- The interval's fields as received (text), the agent's ID, and the interval object itself in payload. An interval
-- still open is observed again and again (duration grows, outTime is filled in at sign-out); only an observation that
-- differs from the key's previous one is written (contract raw_write).
-- ---------------------------------------------------------------------
CREATE TABLE raw.icp_agent_session (
    cc_id            text        NOT NULL,     -- the request's ccId (contract site_vdn)
    vdn_id           text        NOT NULL,
    id               text,                     -- agent ID as received (typed int, sent as a string)
    intime           text,
    outtime          text,
    duration         text,
    payload          jsonb       NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    observed_at      timestamptz NOT NULL,     -- when the request was sent: the observation's version (core_version)
    run_id           uuid        NOT NULL,
    batch_seq        integer     NOT NULL,
    contract_version integer     NOT NULL,
    content_hash     bytea       NOT NULL CHECK (octet_length(content_hash) = 32),
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_icp_agent_session_batch
        FOREIGN KEY (run_id, batch_seq) REFERENCES meta.ingest_batch (run_id, batch_seq)
) PARTITION BY RANGE (ingested_at);
COMMENT ON TABLE raw.icp_agent_session IS
    'ICP agent sign-in intervals as received (contract if03b, IDX_04_03_001): one row per interval and changed '
    'observation. Append only: ingest has SELECT and INSERT only (DD-02 · DD-98)';
COMMENT ON COLUMN raw.icp_agent_session.payload IS
    'The interval object as received, with the agent''s id added under "id" (the interval itself does not carry it)';

CREATE INDEX ix_icp_agent_session_ingested ON raw.icp_agent_session USING brin (ingested_at) WITH (autosummarize = on);
CREATE INDEX ix_icp_agent_session_batch    ON raw.icp_agent_session (run_id, batch_seq);
CREATE INDEX ix_icp_agent_session_key      ON raw.icp_agent_session (id, intime, observed_at);

-- ---------------------------------------------------------------------
-- 3. raw.icp_agent_day: one row per agent, day and observation that differs (DD-98 items two, four, seven)
-- date_key is not in the response: the ingestion program derives it from the observation time and the contract's
-- day_timezone, and skips observations made shortly after midnight. idxs is the response's array as received --
-- values are strings, "n.0" included, and null for "no such day", which must never become 0.
-- ---------------------------------------------------------------------
CREATE TABLE raw.icp_agent_day (
    cc_id            text        NOT NULL,
    vdn_id           text        NOT NULL,
    id               text,                     -- agent ID as received
    date_key         integer     NOT NULL,     -- derived by ingestion: the observation's day (YYYYMMDD, day_timezone)
    idxs             jsonb       NOT NULL CHECK (jsonb_typeof(idxs) = 'array'),
    payload          jsonb       NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    observed_at      timestamptz NOT NULL,
    run_id           uuid        NOT NULL,
    batch_seq        integer     NOT NULL,
    contract_version integer     NOT NULL,
    content_hash     bytea       NOT NULL CHECK (octet_length(content_hash) = 32),
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_icp_agent_day_batch
        FOREIGN KEY (run_id, batch_seq) REFERENCES meta.ingest_batch (run_id, batch_seq)
) PARTITION BY RANGE (ingested_at);
COMMENT ON TABLE raw.icp_agent_day IS
    'ICP agent day totals as received (contract if03c, IDX_04_02_*): one row per agent, day and changed observation. '
    'Append only (DD-02 · DD-98)';
COMMENT ON COLUMN raw.icp_agent_day.date_key IS
    'Not sent by the source: the day of the observation time in the contract''s day_timezone (DD-98 item two, open '
    'until ICP question Q36). Observations within midnight_skip_minutes after midnight are not written at all';

CREATE INDEX ix_icp_agent_day_ingested ON raw.icp_agent_day USING brin (ingested_at) WITH (autosummarize = on);
CREATE INDEX ix_icp_agent_day_batch    ON raw.icp_agent_day (run_id, batch_seq);
CREATE INDEX ix_icp_agent_day_key      ON raw.icp_agent_day (id, date_key, observed_at);

-- ---------------------------------------------------------------------
-- 4. Hot-tuned: minutes after midnight in which a day-total observation is not used (DD-98 item two)
-- Same type guard as before plus the new name; the rules (it can only be raised) live in contract.py.
-- ---------------------------------------------------------------------
ALTER TABLE meta.source_runtime_param DROP CONSTRAINT ck_source_runtime_param_whitelist;
ALTER TABLE meta.source_runtime_param ADD CONSTRAINT ck_source_runtime_param_whitelist CHECK (
    CASE param_name
        WHEN 'lookback_minutes' THEN
            CASE WHEN param_value ~ '^[0-9]{1,4}$'
                 THEN param_value::integer BETWEEN 1 AND 1440
                 ELSE false END
        WHEN 'version_stale_days' THEN
            CASE WHEN param_value ~ '^[0-9]{1,3}$'
                 THEN param_value::integer BETWEEN 1 AND 365
                 ELSE false END
        WHEN 'midnight_skip_minutes' THEN
            CASE WHEN param_value ~ '^[0-9]{1,3}$'
                 THEN param_value::integer BETWEEN 1 AND 720
                 ELSE false END
        ELSE false
    END);

-- ---------------------------------------------------------------------
-- 5. Agents missing from the configuration stay in core (DD-98 item five · DD-95)
-- agent_sk NOT NULL meant an agent removed from ICP's configuration lost its sign-ins and day totals for the days it
-- still worked; DD-95 forbids a sentinel member. So agent_sk may be empty (the foreign key stays), the source's agent
-- ID is always kept (agent_no) for relinking and reconciliation, and an unlinked ID becomes one clarification item.
-- src_observed_at is the observation a row was built from (core_version, DD-82): a row is only replaced by a newer one.
-- ---------------------------------------------------------------------
ALTER TABLE core.fact_agent_session ALTER COLUMN agent_sk DROP NOT NULL;
ALTER TABLE core.fact_agent_session ADD COLUMN agent_no text NOT NULL;
ALTER TABLE core.fact_agent_session ADD COLUMN src_observed_at timestamptz NOT NULL;
COMMENT ON COLUMN core.fact_agent_session.agent_sk IS
    'The dim_agent version valid at signin_at; NULL while no version covers it (removed from the configuration, or '
    'if03d not yet ingested) -- relinked by dim_agent when one arrives (DD-98 items five, six)';
COMMENT ON COLUMN core.fact_agent_session.agent_no IS 'The agent''s ID as the source sent it (if03b id)';
COMMENT ON COLUMN core.fact_agent_session.src_observed_at IS
    'Observation time of the raw row this row was built from (raw.icp_agent_session.observed_at): the version for '
    'DD-82. While signout_at is NULL the interval counts up to this time in reports (DD-98 item eight)';
CREATE INDEX ix_agentsess_unlinked ON core.fact_agent_session (agent_no, signin_at) WHERE agent_sk IS NULL;

ALTER TABLE core.fact_agent_day ALTER COLUMN agent_sk DROP NOT NULL;
ALTER TABLE core.fact_agent_day ADD COLUMN agent_no text NOT NULL;
ALTER TABLE core.fact_agent_day ADD COLUMN src_observed_at timestamptz NOT NULL;
ALTER TABLE core.fact_agent_day ADD COLUMN last_change_at timestamptz NOT NULL DEFAULT now();
COMMENT ON COLUMN core.fact_agent_day.agent_sk IS
    'The dim_agent version valid at the end of the day; for an agent removed during the day, its last version valid '
    'that day. NULL while none covers the day -- relinked by dim_agent when one arrives (DD-98 items five, six)';
COMMENT ON COLUMN core.fact_agent_day.agent_no IS 'The agent''s ID as the source sent it (if03c id)';
COMMENT ON COLUMN core.fact_agent_day.src_observed_at IS
    'Observation time of the raw row this row was built from (raw.icp_agent_day.observed_at): the version for DD-82. '
    'The day''s values are those of its last usable observation; there is no backfill (DD-98 item one)';
-- One row per agent and day: by the source's ID now, since agent_sk may be empty
DROP INDEX core.ux_agentday;
CREATE UNIQUE INDEX ux_agentday ON core.fact_agent_day (agent_no, date_key);
CREATE INDEX ix_agentday_unlinked ON core.fact_agent_day (agent_no, date_key) WHERE agent_sk IS NULL;
