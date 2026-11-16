{#- Input of mart.calls__day__repeat_failed: day x site, M-C15 (Mac side 2026-11-16, window decided by Gavin).
    The window is the calendar day in the report time zone (date_key). A failed call is a complete inbound call whose
    result is abandoned or unanswered_system; a caller is a repeat caller of a day when it has at least two failed
    calls that day, at that site (var repeat_min_failed, M-C15's params
    min_failed; 46 checks they are equal). Calls without caller_number take no part (they cannot be grouped).
      repeat_callers       callers with at least repeat_min_failed failed calls
      repeat_failed_calls  the failed calls of those callers -- M-C15's column
    Grouped by caller_number inside the database; no number leaves it, only the counts (DD-96 item ten · DD-92).
    Every day x site with a complete inbound call has a row, zeros included, so "none" is a value, not a gap.
    Summed over several days the counts are per day: a caller failing once on each of two days is not counted. -#}
{{ config(materialized='view') }}

with inbound as (
    select f.date_key, f.site_sk, f.caller_number,
           r.code in ('abandoned', 'unanswered_system') as failed
      from {{ ref('fact_call') }} f
      left join {{ source('core', 'dim_call_result') }} r on r.call_result_sk = f.call_result_sk
     where f.is_complete and f.direction = 'inbound'
),
per_caller as (
    select date_key, site_sk, caller_number, count(*) as failed_calls
      from inbound
     where failed and caller_number is not null
     group by date_key, site_sk, caller_number
    having count(*) >= {{ var('repeat_min_failed') }}
),
days as (
    select distinct date_key, site_sk from inbound
)
select
    d.date_key,
    d.site_sk,
    count(p.caller_number)                                   as repeat_callers,
    coalesce(sum(p.failed_calls), 0)::bigint                 as repeat_failed_calls
  from days d
  left join per_caller p on p.date_key = d.date_key and p.site_sk = d.site_sk
 group by d.date_key, d.site_sk
