-- =====================================================================
-- OSS911 BI/DW  Structural change 0010  What core.fact_call needs (DD-96)
-- Basis: DD-96 · LD-03 2.4 · LD-02 chapter 3 (2026-10-30 note) · DD-66 · DD-78
--
-- run_as = owner. fact_call and dim_call_result have had no writer before this change; both must be empty.
-- (fact_call.released_by already allows 'unmapped': change 0006, section 3.)
-- =====================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM core.fact_call) OR EXISTS (SELECT 1 FROM core.dim_call_result) THEN
        RAISE EXCEPTION 'core.fact_call and core.dim_call_result must be empty: nothing wrote them before 0010';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 1. Complete or not (DD-96 items one, two)
-- A call row exists once its first leg (CALLIDNUM 1) has arrived; its last leg (-1) may still be missing while the
-- call is in progress or the leg is late. Published metrics count complete calls only; the others are "in progress".
-- ---------------------------------------------------------------------
ALTER TABLE core.fact_call ADD COLUMN is_complete boolean NOT NULL;
COMMENT ON COLUMN core.fact_call.is_complete IS
    'The last leg (CALLIDNUM -1) has arrived. Published metrics count complete calls only; incomplete ones are shown '
    'as in progress, and their call_result, ended_at and released_by are NULL (DD-96 items two, nine)';

-- ---------------------------------------------------------------------
-- 2. The four call results (DD-96 item nine)
-- Who released the call is looked at before whether it reached an agent: a caller who hangs up has abandoned the
-- call, in the queue or while an agent's phone rang. transferred and malicious are flags (is_transferred,
-- is_malicious), not results. Keys from the site's namespace with src_system 'oss911': the values are this system's
-- classification, not a source's.
-- Arabic names: proposed by the development side, to be confirmed (NOTES 2026-10-30); a later change corrects them.
-- ---------------------------------------------------------------------
ALTER TABLE core.dim_call_result
    ADD CONSTRAINT ck_dim_call_result_code
        CHECK (code IN ('answered', 'abandoned', 'unanswered_system', 'ivr_only')),
    ADD CONSTRAINT ck_dim_call_result_key CHECK (src_natural_key = code);
COMMENT ON COLUMN core.dim_call_result.code IS
    'answered · abandoned · unanswered_system · ivr_only, decided in that order (LD-03 2.4 item nine · DD-96)';

INSERT INTO core.dim_call_result (call_result_sk, site_sk, src_natural_key, code, name_ar, name_en)
SELECT core.sk('MKK', 'oss911', v.code), core.site_namespace('MKK'), v.code, v.code, v.name_ar, v.name_en
  FROM (VALUES ('answered',          'مُجابة',                   'Answered'),
               ('abandoned',         'متروكة',                   'Abandoned'),
               ('unanswered_system', 'غير مُجابة (أنهاها النظام)', 'Unanswered (released by the system)'),
               ('ivr_only',          'الرد الآلي فقط',            'IVR only')) AS v (code, name_ar, name_en);

-- ---------------------------------------------------------------------
-- 3. Which calls to rebuild
-- fact_call is rebuilt for the calls whose legs changed since its last write: a leg is rewritten by fact_call_leg
-- or relinked by a dimension model, both setting last_change_at. Without an index every build reads the whole leg
-- table. Written on every changed leg, so B-tree, not BRIN (DD-78: index by write pattern).
-- ---------------------------------------------------------------------
CREATE INDEX ix_leg_changed ON core.fact_call_leg (last_change_at);
