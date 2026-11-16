-- =====================================================================
-- OSS911 BI/DW  Structural change 0011  Which leg starts a call (DD-96, 2026-11-16 answer)
-- Basis: DD-96 items one, nine (corrected 2026-11-16) · LD-03 2.4 · ICP question Q39 · change 0010
--
-- run_as = owner.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. The first leg is the one with the lowest positive CALLIDNUM; a call with only its -1 leg starts with that leg.
-- A call with a single CDR may be numbered -1 (Q39 open), and such calls were left out of core.fact_call by the rule
-- of change 0010 ("no row until CALLIDNUM 1"). Kept per call so a call that did not start with CALLIDNUM 1 can be
-- counted: 1 normally, -1 for a single-CDR call numbered -1, 2 or more while the earlier legs are missing or late.
-- ---------------------------------------------------------------------
-- The rows built under the 0010 rules are removed: the calls without a CALLIDNUM 1 leg have no row, and calls with
-- queue legs only, released by the system, carry ivr_only (now unanswered_system). With the table empty, the next
-- build rebuilds every call (macros/fact_call.sql: nothing written yet means every leg is new). Nothing reads
-- fact_call yet (no mart before step 8 part 2). The NOT NULL column without a default also stops the previous model
-- from writing between this change and the release of the new one (dbt fails the build: schemas out of sync).
TRUNCATE core.fact_call;

ALTER TABLE core.fact_call
    ADD COLUMN first_leg_seq smallint NOT NULL,
    ADD CONSTRAINT ck_fact_call_first_leg_seq CHECK (first_leg_seq >= 1 OR first_leg_seq = -1);
COMMENT ON COLUMN core.fact_call.first_leg_seq IS
    'CALLIDNUM of the leg the call starts with: the lowest positive one, or -1 when only the last leg exists '
    '(DD-96 item one, 2026-11-16). Not 1 means the call did not start with CALLIDNUM 1: -1 a single-CDR call '
    '(ICP Q39), 2 or more while earlier legs are missing';

-- ---------------------------------------------------------------------
-- 2. unanswered_system also covers calls that reached a skill queue and were released by the system (queue timeout,
-- for instance); ivr_only is left with the calls that reached neither a queue nor an agent (DD-96 item nine,
-- 2026-11-16). The codes are unchanged; only the comment.
-- ---------------------------------------------------------------------
COMMENT ON COLUMN core.dim_call_result.code IS
    'answered · abandoned · unanswered_system · ivr_only, decided in that order (LD-03 2.4 item nine · DD-96): '
    'unanswered_system = queue or agent legs, none answered, not released by the caller; ivr_only = no queue or '
    'agent leg';
