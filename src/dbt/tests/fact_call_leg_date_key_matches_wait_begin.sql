-- date_key is the day of wait_begin in the reporting time zone (LD-02 chapter 6 · DD-79); a stale date_key would put
-- the leg in the wrong partition and the wrong day's metrics
select call_leg_sk
  from {{ ref('fact_call_leg') }}
 where date_key <> to_char(wait_begin at time zone '{{ var("report_timezone") }}', 'YYYYMMDD')::integer
