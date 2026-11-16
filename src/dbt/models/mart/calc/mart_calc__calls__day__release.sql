{#- Input of mart.calls__day__release: day x site x who released the call (the last leg's release class: agent ·
    caller · system · unmapped), complete inbound calls. M-C11 is each class's share: calls / day_calls, where
    day_calls is the day's (and site's) total, repeated on each of its rows. Summed over days within one class
    that is right; summed over classes it is not -- so a query on this table must keep released_by as an output
    dimension (metrics/marts.yaml required_dimensions). -#}
{{ config(materialized='view') }}

select
    f.date_key,
    f.site_sk,
    f.released_by,
    count(*)                                                                    as calls,
    sum(count(*)) over (partition by f.date_key, f.site_sk)::bigint             as day_calls
  from {{ ref('fact_call') }} f
 where f.is_complete and f.direction = 'inbound'
 group by f.date_key, f.site_sk, f.released_by
