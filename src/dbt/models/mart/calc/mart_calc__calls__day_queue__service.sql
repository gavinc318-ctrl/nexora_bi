{#- Input of mart.calls__day_queue__service: day x site x first queue, complete inbound calls that reached a queue.
    A call whose queue is not in the configuration any more is kept under queue_key 'unlinked' with its source code
    (DD-95 item eleven), so M-03 can drill into it.
      answered_within_x  answered with a queue wait of at most var service_level_seconds (M-C05's x_seconds,
                         metrics.yaml params; a different X is a new metric version and a new column)
      wait_seconds_sum   queue waits of all queued calls, abandoned included: M-C06's numerator over queued_calls -#}
{{ config(materialized='view') }}

select
    f.date_key,
    f.site_sk,
    coalesce(f.first_queue_sk::text, 'unlinked')                                                  as queue_key,
    f.first_queue_code                                                                            as queue_code,
    count(*)                                                                                      as queued_calls,
    count(*) filter (where r.code = 'answered')                                                   as answered_calls,
    count(*) filter (where r.code = 'answered'
                       and f.wait_seconds <= {{ var('service_level_seconds') }})                  as answered_within_x,
    count(*) filter (where r.code = 'abandoned')                                                  as abandoned_calls,
    coalesce(sum(f.wait_seconds), 0)                                                              as wait_seconds_sum
  from {{ ref('fact_call') }} f
  left join {{ source('core', 'dim_call_result') }} r on r.call_result_sk = f.call_result_sk
 where f.is_complete and f.direction = 'inbound' and f.first_queue_code is not null
 group by f.date_key, f.site_sk, coalesce(f.first_queue_sk::text, 'unlinked'), f.first_queue_code
