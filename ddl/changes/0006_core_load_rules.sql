-- =====================================================================
-- OSS911 BI/DW  Structural change 0006  What the core load of ICP call legs needs (DD-91)
-- Basis: DD-91 items 2, 4, 7, 9, 12 · DD-06 · DD-82 item 4 · LD-02 chapters 4, 6
--
-- run_as = owner.
--
-- Correction to a header that cannot be edited: ddl/02_core_dimensions.sql (part of change 0001, executed, its
-- checksum registered) says every core table carries the lineage column ingested_at. The fact tables do not have
-- it. The time an observation was received is carried, where core needs it, by src_observed_at (section 2 below).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. The site (DD-91 item 2)
-- A deployment constant, not ingested data. site_sk is the site's namespace UUID itself (core.site_namespace): the
-- same value core.sk() derives every other key of this site from, so it is reproducible from the code alone.
-- Names: Gavin, 2026-10-27.
-- ---------------------------------------------------------------------
INSERT INTO core.dim_site (site_sk, site_code, name_ar, name_en)
VALUES (core.site_namespace('MKK'), 'MKK', 'مكة المكرمة', 'Makkah');

-- ---------------------------------------------------------------------
-- 2. The observation a core row was built from (DD-91 item 7 · DD-82 item 4)
-- DD-82 lets core take a newer observation only. ICP call legs carry no version of their own (contract if03a,
-- core_version: observed_at), so the version is the time the observation was RECEIVED: raw.ingested_at of the row it
-- came from -- not the time staging or core processed it. A batch ingested on Monday and released from quarantine on
-- Thursday must not overwrite what arrived on Tuesday.
-- The table is empty at this point (nothing loads core before this change), so NOT NULL needs no default.
-- ---------------------------------------------------------------------
ALTER TABLE core.fact_call_leg ADD COLUMN src_observed_at timestamptz NOT NULL;
COMMENT ON COLUMN core.fact_call_leg.src_observed_at IS
    'raw.ingested_at of the observation this row was built from: the version for DD-82 (newer observation only). '
    'A row is rewritten when its observation is newer and its derived content differs, or when the same observation '
    'derives different content (corrected classification, dimension arrived) -- DD-91 item 7';

-- ---------------------------------------------------------------------
-- 3. "Unmapped" is a value of its own (DD-91 item 4 · DD-06)
-- The CHECKs allowed only the mapped values, so an unknown code had nowhere to go but a mapped bucket -- exactly the
-- silent "other" DD-06 forbids. Now:
--   direction       a CALLTYPE in the contract's value table but in neither the inbound nor the outbound set ->
--                   internal; a CALLTYPE not in the value table at all -> unmapped
--   release_source  a RELEASECAUSE not in the contract's classification -> unmapped
-- fact_call derives direction and released_by from its legs, so its two columns follow.
-- The generated constraint names are looked up, and exactly one must be found per column.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t  text;
    c  text;
    found text[];
BEGIN
    FOR t, c IN VALUES ('core.fact_call_leg', 'direction'), ('core.fact_call_leg', 'release_source'),
                       ('core.fact_call', 'direction'), ('core.fact_call', 'released_by') LOOP
        SELECT array_agg(conname) INTO found
          FROM pg_constraint
         WHERE conrelid = t::regclass AND contype = 'c' AND conislocal
           AND pg_get_constraintdef(oid) ~ ('\m' || c || '\M');
        IF coalesce(array_length(found, 1), 0) <> 1 THEN
            RAISE EXCEPTION 'Expected exactly one CHECK on %.%, found: %', t, c, found;
        END IF;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', t, found[1]);
    END LOOP;
END $$;

ALTER TABLE core.fact_call_leg
    ADD CONSTRAINT ck_fact_call_leg_direction
        CHECK (direction IN ('inbound', 'outbound', 'internal', 'unmapped')),
    ADD CONSTRAINT ck_fact_call_leg_release_source
        CHECK (release_source IN ('agent', 'caller', 'system', 'unmapped'));
ALTER TABLE core.fact_call
    ADD CONSTRAINT ck_fact_call_direction
        CHECK (direction IN ('inbound', 'outbound', 'internal', 'unmapped')),
    ADD CONSTRAINT ck_fact_call_released_by
        CHECK (released_by IN ('agent', 'caller', 'system', 'unmapped'));

-- ---------------------------------------------------------------------
-- 4. The offset is a convention, not a source fact (DD-91 item 12 · LD-02 chapter 6 · DD-60)
-- ---------------------------------------------------------------------
COMMENT ON COLUMN core.fact_call_leg.source_tz_offset IS
    'Offset of the source''s time zone. For ICP it comes from the contract (window.source_timezone), NOT from the '
    'source: CDR times carry no offset. If the contract''s time zone is ever corrected, this column and every time '
    'column are recomputed (DD-91 item 12)';
COMMENT ON COLUMN core.fact_call.source_tz_offset IS
    'As core.fact_call_leg.source_tz_offset: from the contract''s window.source_timezone, not from the source (DD-91 item 12)';

-- ---------------------------------------------------------------------
-- 5. dbt raises clarification items (DD-91 item 9)
-- The 24-hour guard (Mac side, 2026-10-13): two observations of the same (CALLID, CALLIDNUM) whose WAITBEGIN differ by
-- more than 24 hours are a key that may repeat, not a new version -- they go to the clarification queue and are not
-- written to core. Only INSERT: an item is raised, never changed, by dbt.
-- ---------------------------------------------------------------------
GRANT INSERT ON meta.clarification_queue TO oss911_transform;
