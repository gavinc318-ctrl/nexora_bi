-- Processable ICP CDR batches committed more than a day ago that core.fact_call_leg has not registered: they never
-- reach core, and a re-sweep batch among them stops mart closing for good (macros/mart.sql: a day closes once its
-- re-sweep is in core). Quarantined batches still pending are left out: they wait for a person in M-03, by design;
-- discarded ones are never processed. References no model, so the builds do not select it: maintenance_checks
-- runs it nightly. One row per batch.
select b.run_id, b.batch_seq, b.mode, b.window_from, b.window_to, b.row_count, b.rows_written, b.committed_at,
       exists (select 1 from meta.staging_batch_state s
                where s.model = 'stg_icp_cdr_voice' and s.run_id = b.run_id and s.batch_seq = b.batch_seq) as staged
  from meta.ingest_batch b
 where b.source_id = 'icp_cdr_voice'
   and b.committed_at < now() - interval '1 day'
   and not exists (select 1 from meta.quarantine_batch q
                    where q.run_id = b.run_id and q.batch_seq = b.batch_seq and q.state in ('pending', 'discarded'))
   and not exists (select 1 from meta.staging_batch_state s
                    where s.model = 'fact_call_leg' and s.run_id = b.run_id and s.batch_seq = b.batch_seq)
