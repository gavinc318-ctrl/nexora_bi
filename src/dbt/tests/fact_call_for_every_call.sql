-- Every call with a leg in core has its row in core.fact_call (DD-96 item one, 2026-11-16: the first leg is the lowest
-- positive CALLIDNUM, or -1 when it is the only leg, so every call has one): a call left behind by the rebuild
-- selection (macros/fact_call.sql, calls_to_rebuild) shows here.
select l.call_sk, min(l.call_id) as call_id, min(l.wait_begin) as wait_begin
  from {{ ref('fact_call_leg') }} l
 where not exists (select 1 from {{ ref('fact_call') }} c where c.call_sk = l.call_sk)
 group by l.call_sk
