-- Every call whose first leg (CALLIDNUM 1) is in core has its row in core.fact_call (DD-96 item one): a call left
-- behind by the rebuild selection (macros/fact_call.sql, calls_to_rebuild) shows here.
select l.call_sk, l.call_id, l.wait_begin
  from {{ ref('fact_call_leg') }} l
 where l.leg_seq = 1
   and not exists (select 1 from {{ ref('fact_call') }} c where c.call_sk = l.call_sk)
