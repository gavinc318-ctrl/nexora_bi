-- =====================================================================
-- cad_mirror  Structural change 0000  The structural change registry
--
-- Same shape as meta.ddl_change in oss911 (one row per executed change), in its own schema "ddl": the DS writer
-- gets USAGE on cad and cad_meta only, so it can neither read nor write this registry. (cad_meta would not do:
-- the writer may INSERT into every table there.)
-- The executor writes the registry row at the end of the same transaction; this file does not register itself.
-- =====================================================================
CREATE SCHEMA ddl;
REVOKE ALL ON SCHEMA ddl FROM PUBLIC;

CREATE TABLE ddl.ddl_change (
    change_id        text        PRIMARY KEY CHECK (change_id ~ '^[0-9]{4}$'),
    files            text[]      NOT NULL,
    content_sha256   text        NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    summary          text        NOT NULL,
    applied_at       timestamptz NOT NULL DEFAULT now(),
    applied_by       text        NOT NULL DEFAULT session_user,
    release_version  text        -- empty in development; the release registry lives in oss911
);
COMMENT ON TABLE ddl.ddl_change IS
    'Executed structural changes of cad_mirror, one row per change (DD-73). See ddl/cad_mirror/changes.yml';
