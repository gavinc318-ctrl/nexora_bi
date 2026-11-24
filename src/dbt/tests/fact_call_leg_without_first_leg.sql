-- Calls whose CALLIDNUM 1 leg is missing while a later positive leg is in core: the call starts from that later leg
-- (DD-96 item one), so its offered_at, call type and first queue may be wrong until CALLIDNUM 1 arrives. A monitoring
-- item, not a failure: the first leg may be late. Calls with their -1 leg only are not counted here: a single-CDR call
-- may be numbered -1 (ICP Q39); core.fact_call.first_leg_seq = -1 shows those. One row per call, so the failures
-- recorded in meta.check_result count calls and the monitoring channel sees them grow (one summary row before
-- 2026-11-24 stayed at 1 whatever the number).
{{ config(severity='warn') }}
select call_sk, first_leg_seq, offered_at
  from {{ ref('fact_call') }}
 where first_leg_seq > 1
