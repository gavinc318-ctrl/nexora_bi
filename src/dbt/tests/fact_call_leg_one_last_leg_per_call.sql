-- At most one final leg (CALLIDNUM = -1) per call (contract if03a batch_checks 1 · AS-33)
select call_id, count(*) as last_legs
  from {{ ref('fact_call_leg') }}
 where is_last_leg
 group by call_id
having count(*) > 1
