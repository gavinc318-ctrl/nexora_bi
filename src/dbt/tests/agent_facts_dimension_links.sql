-- Every agent sign-in interval points to the agent version valid at its sign-in, and every day total to the latest
-- version that overlaps its day (DD-98 item six · macros/agent_facts.sql): NULL only when no version qualifies. Rows
-- written before their dimension arrived, or whose dimension changed later, are relinked by dim_agent
-- (relink_agent_facts); a row here means a relink was missed. Repair: dbt run-operation relink_all_call_legs.
select 'fact_agent_session' as model, f.agent_no, f.date_key, f.agent_sk, {{ agent_at('f.agent_no', 'f.signin_at') }} as expected
  from {{ ref('fact_agent_session') }} f
 where f.agent_sk is distinct from {{ agent_at('f.agent_no', 'f.signin_at') }}
union all
select 'fact_agent_day', f.agent_no, f.date_key, f.agent_sk, {{ agent_on_day('f.agent_no', 'f.date_key') }}
  from {{ ref('fact_agent_day') }} f
 where f.agent_sk is distinct from {{ agent_on_day('f.agent_no', 'f.date_key') }}
