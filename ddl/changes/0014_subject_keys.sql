-- =====================================================================
-- 0014  Users are identified by the token's sub, not their user name (DD-99 note 2026-11-17, LD-07 chapter 6)
--
-- A user name can be changed, and a freed one given to someone else; sub, under the one trusted issuer the
-- service verifies (svc.conf SVC_OIDC_ISSUER), never is. So:
--
-- meta.user_department_map is keyed by subject (the sub). The user name moves to column username, kept only to
-- show who a row was meant for. Rows written before this change carry a user name and no subject: they are kept
-- but never match anyone -- the service looks up by subject only, so a re-used user name cannot inherit an old
-- department. Until such a row is given its subject, its user gets department_unmapped where a department filter
-- applies (refused, never an empty result). How the old rows get their subject is open (NOTES 2026-11-17). Every
-- row written or changed from now on must carry a subject (the check is NOT VALID: it holds for new and updated
-- rows, not for the old ones).
--
-- audit.access_log.user_principal now records the sub; the new column user_display the user name at the time.
-- Rows written before this change hold the user name in user_principal and no user_display: the audit is
-- append-only, they are not rewritten.
-- =====================================================================

ALTER TABLE meta.user_department_map DROP CONSTRAINT user_department_map_pkey;
ALTER TABLE meta.user_department_map RENAME COLUMN user_principal TO username;
ALTER TABLE meta.user_department_map ALTER COLUMN username DROP NOT NULL;
ALTER TABLE meta.user_department_map ADD COLUMN subject text;
ALTER TABLE meta.user_department_map ADD COLUMN map_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY;
CREATE UNIQUE INDEX ux_user_department_map_subject ON meta.user_department_map (subject);
ALTER TABLE meta.user_department_map
    ADD CONSTRAINT ck_user_department_map_subject CHECK (subject IS NOT NULL AND subject <> '') NOT VALID;
COMMENT ON COLUMN meta.user_department_map.subject IS
    'The user''s sub under the trusted issuer: what the query API matches on (DD-99 note 2026-11-17). NULL only on '
    'rows written before change 0014, which match no one';
COMMENT ON COLUMN meta.user_department_map.username IS
    'The user name when the row was written, for display only; never used to match';

ALTER TABLE audit.access_log ADD COLUMN user_display text;
COMMENT ON COLUMN audit.access_log.user_principal IS
    'The sub of the token (the trusted issuer''s stable user id) from change 0014 on; the user name before it';
COMMENT ON COLUMN audit.access_log.user_display IS
    'The user name (preferred_username) at the time of the access, for display; NULL before change 0014';
