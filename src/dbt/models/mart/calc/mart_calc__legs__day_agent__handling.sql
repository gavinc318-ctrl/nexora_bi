{#- Input of mart.legs__day_agent__handling: day x site x agent, the agent legs of complete inbound calls (a leg's
    day is its own wait_begin's). agent_no is the source agent number: the service layer's "self" filter matches it
    across every version of the agent (DD-99 correction two). A leg whose agent is not in the configuration any more
    is kept under agent_key 'unlinked' with its agent number (DD-95). Names are the agent version's; they are
    sensitive columns (metrics/marts.yaml).
      presented_legs  M-C09 · answered_legs M-C10 · missed_legs M-C12 · talk_seconds_sum / answered_legs M-C07 -#}
{{ config(materialized='view') }}

select
    l.date_key,
    l.site_sk,
    coalesce(l.agent_sk::text, 'unlinked')                                                        as agent_key,
    l.device_no                                                                                   as agent_no,
    a.name_ar                                                                                     as agent_name_ar,
    a.name_en                                                                                     as agent_name_en,
    count(*)                                                                                      as presented_legs,
    count(*) filter (where l.call_begin is not null)                                              as answered_legs,
    count(*) filter (where l.call_begin is null)                                                  as missed_legs,
    coalesce(sum(l.talk_seconds) filter (where l.call_begin is not null), 0)                      as talk_seconds_sum
  from {{ ref('fact_call_leg') }} l
  join {{ ref('fact_call') }} f on f.call_sk = l.call_sk
  left join {{ ref('dim_agent') }} a on a.agent_sk = l.agent_sk
 where l.device_type_code = 2 and f.is_complete and f.direction = 'inbound'
 group by l.date_key, l.site_sk, coalesce(l.agent_sk::text, 'unlinked'), l.device_no, a.name_ar, a.name_en
