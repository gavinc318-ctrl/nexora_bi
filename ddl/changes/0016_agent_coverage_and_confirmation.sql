-- =====================================================================
-- OSS911 BI/DW  Structural change 0016  Agent observations: what each request returned, and the last confirmation
-- Basis: DD-98 review note (2026-11-22) items one and two · ICP question Q40 · DD-101 (open intervals)
--
-- run_as = owner. Default privileges of 0002 give ingest SELECT and INSERT on the new raw table and transform SELECT
-- on it; transform already has SELECT, INSERT, UPDATE, DELETE on core. Nothing else is granted here.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. raw.icp_agent_coverage: one row per hindex/agent request (DD-98 review note item one)
-- An agent asked for but not in the response is registered here, not quarantined: the request includes agents removed
-- during the day, which the platform may legitimately leave out (Q40), and quarantining would stop every batch. The
-- batch is ingested with the records that did come back. An empty response array is "every agent missing". A response
-- that is not an array of records is still quarantined (the batch), as before -- its returned set is then empty.
-- Append only, like the rest of raw. requested and returned are the agent IDs as text, sorted, without duplicates.
-- ---------------------------------------------------------------------
CREATE TABLE raw.icp_agent_coverage (
    source_id        text        NOT NULL CHECK (source_id IN ('icp_agent_session', 'icp_agent_day')),
    cc_id            text        NOT NULL,
    vdn_id           text        NOT NULL,
    date_key         integer     NOT NULL,     -- the observation's day (YYYYMMDD, the contract's day_timezone)
    observed_at      timestamptz NOT NULL,     -- the same time as the batch's raw rows
    requested        text[]      NOT NULL,
    returned         text[]      NOT NULL,
    missing_count    integer     NOT NULL CHECK (missing_count >= 0),
    run_id           uuid        NOT NULL,
    batch_seq        integer     NOT NULL,
    contract_version integer     NOT NULL,
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_icp_agent_coverage_batch
        FOREIGN KEY (run_id, batch_seq) REFERENCES meta.ingest_batch (run_id, batch_seq)
) PARTITION BY RANGE (ingested_at);
COMMENT ON TABLE raw.icp_agent_coverage IS
    'Which agents each hindex/agent request asked for and which came back (DD-98 review note). The basis of '
    'src_confirmed_at in core and of the "asked for but not returned" monitoring signal. Append only';
COMMENT ON COLUMN raw.icp_agent_coverage.returned IS
    'Agent IDs that have a record in the response, whatever its values (all null included)';
COMMENT ON COLUMN raw.icp_agent_coverage.missing_count IS 'How many requested IDs are not in returned';

CREATE INDEX ix_icp_agent_coverage_ingested ON raw.icp_agent_coverage USING brin (ingested_at) WITH (autosummarize = on);
CREATE INDEX ix_icp_agent_coverage_batch    ON raw.icp_agent_coverage (run_id, batch_seq);
CREATE INDEX ix_icp_agent_coverage_day      ON raw.icp_agent_coverage (source_id, date_key, observed_at);

-- ---------------------------------------------------------------------
-- 2. src_confirmed_at: the last time the platform confirmed a row (DD-98 review note item two)
-- src_observed_at stays what it is, the time the row's content last changed. An agent still signed in may be returned
-- every ten minutes with the same content: raw writes nothing, src_observed_at stands still, and an open interval
-- counted up to it would be short. src_confirmed_at is the observation time of the last processable batch (not
-- quarantined pending, not discarded) whose response held the agent, for the row's day; dbt derives it from
-- raw.icp_agent_coverage. Everything that needs "data as of when" reads it: reconciliation cut-offs, open intervals
-- in the agent marts (DD-101). Rows existing before this change start at src_observed_at.
-- ---------------------------------------------------------------------
ALTER TABLE core.fact_agent_session ADD COLUMN src_confirmed_at timestamptz;
UPDATE core.fact_agent_session SET src_confirmed_at = src_observed_at;
ALTER TABLE core.fact_agent_session ALTER COLUMN src_confirmed_at SET NOT NULL;
COMMENT ON COLUMN core.fact_agent_session.src_confirmed_at IS
    'Last observation whose response held this agent, on a day from the interval''s sign-in day to the day of '
    'src_observed_at (an interval not returned on later days is not confirmed by them, Q38). Never earlier than '
    'src_observed_at. While signout_at is NULL the interval counts up to this time (DD-101)';
-- The confirmation update looks rows up by agent and day
CREATE INDEX ix_agentsess_agent_day ON core.fact_agent_session (agent_no, date_key);

ALTER TABLE core.fact_agent_day ADD COLUMN src_confirmed_at timestamptz;
UPDATE core.fact_agent_day SET src_confirmed_at = src_observed_at;
ALTER TABLE core.fact_agent_day ALTER COLUMN src_confirmed_at SET NOT NULL;
COMMENT ON COLUMN core.fact_agent_day.src_confirmed_at IS
    'Last observation of the day whose response held this agent: the day''s values are confirmed up to this time '
    '(the reconciliation cut-off, DD-100). Never earlier than src_observed_at';
