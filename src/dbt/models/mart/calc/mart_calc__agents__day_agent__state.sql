{#- Input of mart.agents__day_agent__state: day x site x agent, the platform's day totals (DD-101 item three ·
    DD-65): the seconds by state and the calls, as core.fact_agent_day has them. A value the platform sent as null
    stays NULL, never 0.
      M-R10 to M-R15 = each state's seconds / that state's agent days (talk_agent_days ...): the agent days whose
              value is not null (DD-101 note 2026-11-25). A null leaves the agent day out of both the numerator and
              the denominator; a shared agent_days denominator would count it as 0 seconds and pull the mean down.
              (M-R03, the state durations before the split, is no longer published.)
      M-R04 = occupied_seconds / occupancy_base_seconds: talk + after-call work over signed-in time. Both columns are
              set only when all three inputs are, and are NULL together otherwise -- numerator and denominator come
              from the same rows (a sum over agents would otherwise divide one set of agents by another)
    One row per agent number and day (the key of fact_agent_day); agent_key and the names are the row's link, the
    latest dimension version overlapping the day, unlinked when none (DD-95). -#}
{{ config(materialized='view') }}

select f.date_key,
       f.site_sk,
       coalesce(f.agent_sk::text, 'unlinked')                                                 as agent_key,
       f.agent_no,
       a.name_ar                                                                              as agent_name_ar,
       a.name_en                                                                              as agent_name_en,
       f.signin_seconds,
       f.talk_seconds,
       f.acw_seconds,
       f.rest_seconds,
       f.busy_seconds,
       f.hold_seconds,
       f.idle_seconds,
       f.inbound_calls,
       f.outbound_calls,
       1::integer                                                                             as agent_days,
{%- for st in ['talk', 'acw', 'rest', 'busy', 'hold', 'idle'] %}
       (f.{{ st }}_seconds is not null)::integer                                              as {{ st }}_agent_days,
{%- endfor %}
       case when f.talk_seconds is not null and f.acw_seconds is not null and f.signin_seconds is not null
            then f.talk_seconds + f.acw_seconds end                                           as occupied_seconds,
       case when f.talk_seconds is not null and f.acw_seconds is not null and f.signin_seconds is not null
            then f.signin_seconds end                                                         as occupancy_base_seconds
  from {{ ref('fact_agent_day') }} f
  left join {{ ref('dim_agent') }} a on a.agent_sk = f.agent_sk
