-- Agent sign-ins and day totals of an agent ID that no dimension version covers (DD-95 · DD-98 item five): kept in
-- core with agent_sk NULL and the source's agent_no, and one clarification item per ID (macros/agent_facts.sql).
-- agent_facts_dimension_links cannot see them: the row and the dimension agree on "nothing". A warning, so that
-- someone looks. Silent until dim_agent has any row. One row per table and agent ID.
{{ config(severity='warn') }}
select 'fact_agent_session' as model, f.agent_no, count(*) as rows_, min(f.date_key) as first_day, max(f.date_key) as last_day
  from {{ ref('fact_agent_session') }} f
 where f.agent_sk is null and exists (select 1 from {{ ref('dim_agent') }})
 group by f.agent_no
union all
select 'fact_agent_day', f.agent_no, count(*), min(f.date_key), max(f.date_key)
  from {{ ref('fact_agent_day') }} f
 where f.agent_sk is null and exists (select 1 from {{ ref('dim_agent') }})
 group by f.agent_no
