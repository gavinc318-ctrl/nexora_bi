-- The day totals' index -> column list of the models (macros/agent_facts.sql, agent_day_columns) and contract if03c's
-- columns in the database (target fact_agent_day.<column>) are the same: a contract that adds, drops or remaps an
-- index without the models following would load it into the wrong column or not at all. One row per difference.
with models as (
    select * from (values
    {%- for idx, col in agent_day_columns() %}
        ('{{ idx }}', 'fact_agent_day.{{ col }}'){% if not loop.last %},{% endif %}
    {%- endfor %}) v(src, target)
),
contract as (
    select c ->> 'src' as src, c ->> 'target' as target
      from meta.source_contract s, jsonb_array_elements(s.definition -> 'columns') c
     where s.source_id = 'icp_agent_day' and c ->> 'src' like 'IDX\_%'
)
select coalesce(m.src, c.src) as src, m.target as in_models, c.target as in_contract
  from models m full join contract c on c.src = m.src
 where m.target is distinct from c.target
   and exists (select 1 from meta.source_contract where source_id = 'icp_agent_day')
