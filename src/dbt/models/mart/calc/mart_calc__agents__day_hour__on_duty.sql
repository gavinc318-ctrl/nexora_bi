{#- Input of mart.agents__day_hour__on_duty: day x site x hour (0-23, report time zone), from the sign-in intervals
    (DD-101 item two). Every hour of a day that has any piece has its row, zero included.
      agents_on_duty   different agent numbers with a piece of that hour. A distinct count: it cannot be added up
                       over hours (metrics/marts.yaml required_dimensions: hour)
      day_count        always 1: M-R01 = sum(agents_on_duty) / sum(day_count), by hour -- one day gives that day's
                       count, a week the daily average of each hour (DD-101 note 2026-11-24)
      on_duty_seconds  the hour's pieces, in seconds; over a day it is the duty table's on_duty_seconds (up to the
                       rounding of each row) -#}
{{ config(materialized='view') }}

-- pieces is read once and only grouped: the publication's filter on date_key then reaches it before anything is
-- kept (a CTE read twice would be materialized over the whole history at every hourly run)
with per_hour as (
    select date_key, site_sk, hour_of_day,
           count(distinct agent_no) filter (where seconds > 0)                               as agents_on_duty,
           round(sum(seconds))::bigint                                                       as on_duty_seconds
      from ({{ agent_session_pieces('hour') }}) pieces
     group by date_key, site_sk, hour_of_day
),
per_day as (
    select date_key, site_sk,
           array_agg(hour_of_day order by hour_of_day)                                       as hours,
           array_agg(agents_on_duty order by hour_of_day)                                    as agents,
           array_agg(on_duty_seconds order by hour_of_day)                                   as seconds
      from per_hour
     group by date_key, site_sk
)
select d.date_key,
       d.site_sk,
       h.hour_of_day,
       coalesce(d.agents[array_position(d.hours, h.hour_of_day)], 0)::bigint                  as agents_on_duty,
       coalesce(d.seconds[array_position(d.hours, h.hour_of_day)], 0)::bigint                 as on_duty_seconds,
       1::integer                                                                             as day_count
  from per_day d
 cross join generate_series(0, 23) as h(hour_of_day)
