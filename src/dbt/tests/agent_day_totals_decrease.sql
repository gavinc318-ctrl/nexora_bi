-- Day totals that go down: an agent's cumulative value for one day smaller than at the previous observation of the
-- same day (DD-98 review note (a)). Cumulative totals of "today so far" only grow, so a drop means the observation
-- was attributed to the wrong day -- the platform cuts its day in another time zone than window.day_timezone, or
-- its report batch was later than midnight_skip_minutes -- and the day's values mix two days. ERROR, and a page
-- through the monitoring channel: raw keeps every observation, so staging can be rebuilt once the rule is fixed.
-- Observations in the midnight skip window are never made, so they cannot cause a row here. A value turning null
-- is not counted (null is "no such day", Q36). Over observations of the last two days; every day with var
-- full_check (maintenance_checks), from var agent_day_totals_checked_from on. One row per agent, day, observation
-- and column.
-- Runs after core.fact_agent_day, not between staging and core: a failure reports, it does not stop the day totals
-- from loading (dbt build skips the children of a failed test's models; fact_agent_day has none in the build).
-- depends_on: {{ ref('fact_agent_day') }}
{%- set days = none if var('full_check', false) else 2 %}
with obs as (
    select s.agent_no, s.date_key, s.observed_at, s.run_id, s.batch_seq
           {%- for _, col in agent_day_columns() %}, s.{{ col }}{% endfor %}
      from {{ ref('stg_icp_agent_day') }} s
     where s.agent_no is not null
       and s.date_key >= {{ var('agent_day_totals_checked_from') }}
       {%- if days %}
       and s.date_key >= to_char((now() at time zone '{{ var("report_timezone") }}')::date - {{ days }}, 'YYYYMMDD')::integer
       {%- endif %}
),
steps as (
    select o.*
           {%- for _, col in agent_day_columns() %},
           lag(o.{{ col }}) over w as prev_{{ col }}
           {%- endfor %},
           lag(o.observed_at) over w as prev_observed_at
      from obs o
    window w as (partition by o.agent_no, o.date_key order by o.observed_at, o.run_id, o.batch_seq)
)
{%- for _, col in agent_day_columns() %}
select agent_no, date_key, '{{ col }}' as column_name, prev_observed_at, prev_{{ col }} as previous_value,
       observed_at, {{ col }} as value, run_id, batch_seq
  from steps
 where {{ col }} < prev_{{ col }}
{%- if not loop.last %}
union all
{%- endif %}
{%- endfor %}
