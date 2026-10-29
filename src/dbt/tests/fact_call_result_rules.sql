-- The call result (DD-96 items two, nine · LD-03 2.4): a complete call has exactly one of the four results, an
-- incomplete one none (nor ended_at or released_by); answered means at least one answered agent leg, and the others
-- follow the order of the rule. One row per call that breaks a rule, with the rule.
with c as (
    select f.*, r.code,
           exists (select 1 from {{ ref('fact_call_leg') }} l where l.call_sk = f.call_sk and l.device_type_code = 1) as has_queue_leg
      from {{ ref('fact_call') }} f
      left join {{ source('core', 'dim_call_result') }} r on r.call_result_sk = f.call_result_sk
)
select call_sk, src_natural_key, code, 'complete call without a result' as rule from c where is_complete and code is null
union all
select call_sk, src_natural_key, code, 'incomplete call with a result, ended_at or released_by' from c
 where not is_complete and (call_result_sk is not null or ended_at is not null or released_by is not null)
union all
select call_sk, src_natural_key, code, 'answered without an answered agent leg' from c
 where code = 'answered' and answered_leg_count < 1
union all
select call_sk, src_natural_key, code, 'answered agent leg but not answered' from c
 where code <> 'answered' and answered_leg_count >= 1
union all
select call_sk, src_natural_key, code, 'abandoned: no queue or agent leg, or not released by the caller' from c
 where code = 'abandoned' and (released_by is distinct from 'caller' or (agent_leg_count = 0 and not has_queue_leg))
union all
select call_sk, src_natural_key, code, 'unanswered_system: no agent leg, or released by the caller' from c
 where code = 'unanswered_system' and (agent_leg_count = 0 or released_by = 'caller')
union all
select call_sk, src_natural_key, code, 'ivr_only with an agent leg, or a queue leg released by the caller' from c
 where code = 'ivr_only' and (agent_leg_count > 0 or (has_queue_leg and released_by = 'caller'))
