-- Every agent leg points to the agent version valid at its start, and every queue leg to the queue version
-- (DD-12 · DD-91 item 3 · DD-94): NULL only when no version covers that time. Legs written before their dimension
-- arrived, or whose dimension changed later, are relinked by the dimension models (macros/reference.sql,
-- relink_call_legs); a row here means a relink was missed. Repair: dbt run-operation relink_all_call_legs (runbook 14).
select f.call_leg_sk, f.device_type_code, f.device_no, f.wait_begin
  from {{ ref('fact_call_leg') }} f
  left join {{ ref('dim_agent') }} d
    on f.device_type_code = 2 and d.site_sk = f.site_sk and d.src_natural_key = f.device_no
   and f.wait_begin >= d.valid_from and f.wait_begin < d.valid_to
  left join {{ ref('dim_queue') }} q
    on f.device_type_code = 1 and q.site_sk = f.site_sk and q.src_natural_key = f.device_no
   and f.wait_begin >= q.valid_from and f.wait_begin < q.valid_to
 where (f.device_type_code = 2 and f.agent_sk is distinct from d.agent_sk)
    or (f.device_type_code = 1 and f.queue_sk is distinct from q.queue_sk)
    or (f.device_type_code not in (1, 2) and (f.agent_sk is not null or f.queue_sk is not null))
