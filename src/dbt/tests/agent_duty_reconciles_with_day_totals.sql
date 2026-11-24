-- Sign-in intervals against the platform's day totals (DD-101 item seven): per agent and closed day, the duty mart's
-- session_count and on_duty_seconds (the intervals cut at midnight) against fact_agent_day's signin_count and
-- signin_seconds (IDX_04_02_001 / 002). The two come from two interfaces of the same platform: a difference is the
-- platform's own and is reported, never smoothed (DD-65 · fact_agent_day.signin_seconds).
-- Only days the duty mart has closed, its latest closed version; over the closed days of the last
-- var agent_duty_reconcile.days days. A day differs when the counts differ, or when
-- |on_duty_seconds - signin_seconds| > max(min_seconds, ratio x signin_seconds) (var agent_duty_reconcile). A day
-- total that is null is not compared on that column; an agent with intervals but no day total row, or the other way
-- round with signed-in time, is reported as missing on one side.
-- WARN and notify only (daily digest): how the platform counts an interval past midnight, and up to when its day
-- total runs, are open (Q36 to Q38). References no model, so the builds do not select it: maintenance_checks runs it
-- nightly. One row per agent and day that differs.
{{ config(severity='warn') }}
{%- set rc = var('agent_duty_reconcile') %}
with days as (
    select date_key, max(close_version) as close_version
      from meta.mart_day_publication
     where mart_table = 'mart.agents__day_agent__duty' and close_version >= 1
       and date_key >= to_char((now() at time zone '{{ var("report_timezone") }}')::date - {{ rc.days | int }},
                               'YYYYMMDD')::integer
     group by date_key
),
duty as (
    select m.date_key, m.agent_no, m.session_count, m.on_duty_seconds
      from mart.agents__day_agent__duty m
      join days d on d.date_key = m.date_key and d.close_version = m.close_version
),
platform as (
    select f.date_key, f.agent_no, f.signin_count, f.signin_seconds
      from core.fact_agent_day f
      join days d on d.date_key = f.date_key
)
select coalesce(u.date_key, p.date_key)                                       as date_key,
       coalesce(u.agent_no, p.agent_no)                                       as agent_no,
       u.session_count, p.signin_count, u.on_duty_seconds, p.signin_seconds,
       case when u.agent_no is null then 'day total without intervals'
            when p.agent_no is null then 'intervals without a day total'
            when p.signin_count is distinct from u.session_count and p.signin_count is not null
                 then 'sign-in count differs'
            else 'signed-in time differs' end                                 as problem
  from duty u
  full join platform p on p.date_key = u.date_key and p.agent_no = u.agent_no
 where (u.agent_no is null and coalesce(p.signin_seconds, 0) > 0)
    or (p.agent_no is null and u.on_duty_seconds > 0)
    or (u.agent_no is not null and p.agent_no is not null
        and ((p.signin_count is not null and p.signin_count <> u.session_count)
             or (p.signin_seconds is not null
                 and abs(u.on_duty_seconds - p.signin_seconds)
                     > greatest({{ rc.min_seconds | int }}, {{ rc.ratio | float }} * p.signin_seconds))))
