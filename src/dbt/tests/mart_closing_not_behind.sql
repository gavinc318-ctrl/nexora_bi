-- Mart tables whose latest closed day is more than closing_lag_days (dbt variable, 3) behind today (report time zone),
-- or that have no closed day at all (Mac side 2026-11-16). Normally the nightly publication closes up to the day
-- before yesterday; a larger lag means the re-sweep has not reached core for some day (a failed re-sweep, a batch
-- still pending in quarantine, a batch that never reached core -- see batches_reach_core) or nightly itself is not
-- running. ERROR. The alert OSS911ClosingBehind reports the same lag as it happens, against closing_lag_days of
-- contracts/monitoring.yml; this test is its nightly backstop, and 55-monitor refuses a release where the two numbers
-- differ. References no model, so the builds do not select it: maintenance_checks runs it nightly. One row per table.
with tables as (
    select mart_table,
           max(date_key) filter (where close_version >= 1) as last_closed
      from meta.mart_day_publication
     group by mart_table
)
select mart_table, last_closed,
       (now() at time zone '{{ var("report_timezone") }}')::date
         - to_date(last_closed::text, 'YYYYMMDD')                    as days_behind
  from tables
 where last_closed is null
    or to_date(last_closed::text, 'YYYYMMDD') < (now() at time zone '{{ var("report_timezone") }}')::date
                                                          - {{ var("closing_lag_days") | int }}
