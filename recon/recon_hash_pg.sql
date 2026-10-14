-- ---------------------------------------------------------------------------
-- Rolling-window reconciliation: source-side aggregate query template (PostgreSQL)
-- LD-01 8.3 · DD-61
--
-- Issued by us against the DS presentation database with a read-only account. The computation runs inside the
-- source database engine and only aggregate values come back; the data itself never leaves the source. DS installs or runs nothing.
--
-- Placeholders are filled in by the connector from the contract:
--   :src_table    fully qualified source table
--   :ts_col       time column used for slicing
--   :from / :to   window start and end
--   The column list and serialisation expressions are generated in the order declared in
--   meta.source_contract.definition, never in table definition order -- ALTER TABLE changes the latter.
--
-- Serialisation rules (independent of any session default, see LD-01 8.3):
--   numbers    to_char(v,'FM...0.000') with an explicit scale; bare ::text is forbidden
--   timestamps AT TIME ZONE 'UTC', then a fixed format string
--   NULL       the '\N' sentinel, distinct from the empty string
--   aggregate  two 32-bit slices of md5 summed separately, independent of row order; string_agg is forbidden
-- ---------------------------------------------------------------------------

SELECT
    (:ts_col AT TIME ZONE 'Asia/Riyadh')::date                      AS day_key,
    count(*)                                                        AS row_count,
    sum(('x' || substr(md5(row_sig), 1, 8))::bit(32)::int::bigint)  AS hash_hi,
    sum(('x' || substr(md5(row_sig), 9, 8))::bit(32)::int::bigint)  AS hash_lo
FROM (
    SELECT
        :ts_col,
        concat_ws('|',
            -- generated below in the column order declared by the contract; example shape:
            coalesce(id::text,                                          '\N'),
            coalesce(to_char(ts AT TIME ZONE 'UTC',
                             'YYYY-MM-DD HH24:MI:SS.US'),               '\N'),
            coalesce(to_char(amount, 'FM9999999999990.000'),            '\N'),
            coalesce(note,                                              '\N'),
            coalesce(flag::text,                                        '\N')
        ) AS row_sig
    FROM :src_table
    WHERE :ts_col >= :from
      AND :ts_col <  :to
) s
GROUP BY 1
ORDER BY 1;

-- On our side exactly the same serialisation and aggregation runs over the raw copy, sharing the code rather than
-- writing it twice: two implementations drift sooner or later, and after that every difference is a false one.
