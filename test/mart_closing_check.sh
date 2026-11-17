#!/bin/bash
# =====================================================================
# Check of the closing boundary (src/dbt/macros/mart.sql, mart_final_days · DD-97 note 2026-11-17)
#
# Which report days count as final, for fixture re-sweeps of 2030-01 (far from any real data), with the first
# re-sweep day 2030-01-15:
#   source day 2030-01-15  re-swept by a successful run, but one of its batches has not reached core (pending)
#   source day 2030-01-16  re-swept, every batch in core
# The report day (Asia/Riyadh) overlaps two source days (UTC): report day D covers source days D-1 and D.
#   phase 1: only 2030-01-14 is final (source days 13 and 14 lie before the first re-sweep day). 15 and 16 are not:
#            source day 15 is pending. Derived from the successful re-sweeps instead, the boundary would be the 16th
#            and both would pass for history -- the defect this guards against
#   phase 2: the pending batch is discarded in quarantine -> 14, 15 and 16 are final; 17 is not (source day 17 has
#            no re-sweep)
# Also: without var resweep_from the closing refuses to compile.
#
# Everything is one transaction rolled back at the end: no trace in the database.
#
# Usage:
#   DBT="dbt --project-dir src/dbt --profiles-dir ..." bash test/mart_closing_check.sh <psql connection arguments>
# DBT: the dbt command for compiling (any target whose database has the meta tables); the psql arguments: a login
# allowed to insert into meta (a superuser on a scratch cluster).
# =====================================================================
set -euo pipefail
: "${DBT:?set DBT to the dbt command (with --project-dir and --profiles-dir)}"
PSQL=(psql -X -q -At -v ON_ERROR_STOP=1 "$@")
DAYS="(select d::integer as date_key from unnest(array[20300114, 20300115, 20300116, 20300117]) d)"
INLINE="{{ mart_final_days('icp_cdr_voice', '$DAYS') }} order by 1"

if $DBT compile --quiet --inline "$INLINE" >/dev/null 2>&1; then
    echo "FAIL: closing compiled without var resweep_from"; exit 1
fi
SQL=$($DBT compile --quiet --inline "$INLINE" --vars '{"resweep_from": {"icp_cdr_voice": "2030-01-15"}}')

out=$("${PSQL[@]}" <<EOF
BEGIN;
INSERT INTO meta.run_log (run_id, source_id, contract_version, started_at, finished_at, outcome)
VALUES ('00000000-0000-0000-0000-00000000c015', 'icp_cdr_voice', 1, '2030-01-16 02:00Z', '2030-01-16 02:05Z', 'success'),
       ('00000000-0000-0000-0000-00000000c016', 'icp_cdr_voice', 1, '2030-01-17 02:00Z', '2030-01-17 02:05Z', 'success');
INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to, row_count, rows_written,
                               contract_version)
VALUES ('00000000-0000-0000-0000-00000000c015', 1, 'icp_cdr_voice', 'resweep', '2030-01-14 23:45:00', '2030-01-15 12:00:00', 5, 5, 1),
       ('00000000-0000-0000-0000-00000000c015', 2, 'icp_cdr_voice', 'resweep', '2030-01-15 12:00:00', '2030-01-16 00:00:00', 5, 5, 1),
       ('00000000-0000-0000-0000-00000000c016', 1, 'icp_cdr_voice', 'resweep', '2030-01-15 23:45:00', '2030-01-17 00:00:00', 5, 5, 1);
INSERT INTO meta.staging_batch_state (model, run_id, batch_seq)
VALUES ('fact_call_leg', '00000000-0000-0000-0000-00000000c015', 1),
       ('fact_call_leg', '00000000-0000-0000-0000-00000000c016', 1);
INSERT INTO meta.quarantine_batch (run_id, batch_seq, failure_reason, row_count)
VALUES ('00000000-0000-0000-0000-00000000c015', 2, 'closing check fixture', 5);
SELECT 'phase1:' || coalesce(string_agg(date_key::text, ',' ORDER BY date_key), '') FROM ($SQL) f;
UPDATE meta.quarantine_batch SET state = 'discarded', handled_by = 'closing-check', handled_at = now(),
       handling_reason = 'closing check fixture'
 WHERE run_id = '00000000-0000-0000-0000-00000000c015' AND batch_seq = 2;
SELECT 'phase2:' || coalesce(string_agg(date_key::text, ',' ORDER BY date_key), '') FROM ($SQL) f;
ROLLBACK;
EOF
)
expected=$'phase1:20300114\nphase2:20300114,20300115,20300116'
if [ "$out" != "$expected" ]; then
    printf 'FAIL: final days\n  got:\n%s\n  expected:\n%s\n' "$out" "$expected"; exit 1
fi
echo "ok: the 15th pending holds the 15th and the 16th; the boundary is the configured first re-sweep day"
