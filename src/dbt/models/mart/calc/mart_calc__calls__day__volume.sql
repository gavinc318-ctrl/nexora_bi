{#- Input of mart.calls__day__volume: every day, all of it (a view; the mart model reads the days it publishes).
    Grain: day x site. The four call results and the counts the metrics divide are columns, not a result
    dimension: the service layer computes a ratio from two columns of the same rows (DD-97 item three), so
    answered / offered must sit side by side. Inbound calls only (M-C01: CALLTYPE in the inbound set); the others
    are counted in non_inbound_calls, for reconciliation, not published.
    Complete calls only (DD-96 item two); incomplete_calls is the "in progress" count while the day is open, and
    what was still incomplete when the day was closed. -#}
{{ config(materialized='view') }}

select
    f.date_key,
    f.site_sk,
    count(*) filter (where f.is_complete and f.direction = 'inbound')                              as offered_calls,
    count(*) filter (where f.is_complete and f.direction = 'inbound' and r.code = 'answered')      as answered_calls,
    count(*) filter (where f.is_complete and f.direction = 'inbound' and r.code = 'abandoned')     as abandoned_calls,
    count(*) filter (where f.is_complete and f.direction = 'inbound'
                       and r.code = 'unanswered_system')                                            as unanswered_system_calls,
    count(*) filter (where f.is_complete and f.direction = 'inbound' and r.code = 'ivr_only')      as ivr_only_calls,
    count(*) filter (where f.is_complete and f.direction = 'inbound' and f.is_transferred)         as transferred_calls,
    count(*) filter (where not f.is_complete and f.direction = 'inbound')                          as incomplete_calls,
    count(*) filter (where f.is_complete and f.direction <> 'inbound')                             as non_inbound_calls
  from {{ ref('fact_call') }} f
  left join {{ source('core', 'dim_call_result') }} r on r.call_result_sk = f.call_result_sk
 group by f.date_key, f.site_sk
