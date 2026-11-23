-- Day totals against the call records (DD-100 item five, review note item two): per agent and closed day, the
-- platform's inbound_calls against the agent's answered legs in core -- legs of device type 2 (agent) with that
-- agent's ID, a call_begin (answered), not outbound or internal, on the same day (date_key, the day of wait_begin),
-- and ended by the day row's src_confirmed_at. The cut-off is the last time the platform confirmed the row: the
-- day's final value lacks the minutes after its last observation, and so do the legs counted against it.
-- Only days whose legs are closed (mart.legs__day_agent__handling has a closed version): the call records of such a
-- day are final, re-sweep included. Over the closed days of the last var agent_reconcile.days days.
-- A day differs when |inbound_calls - legs| > max(min_calls, ratio x legs) (var agent_reconcile). A day total that
-- is null (no such day, Q36) is not compared.
-- WARN and notify only (daily digest): the request time is not the platform's report cut-off (Q36), and the day
-- totals may count calls differently from the CDRs -- a difference is a question to the platform, not a reason to
-- hold a closing. References no model, so the builds do not select it: maintenance_checks runs it nightly.
-- One row per agent and day that differs.
{{ config(severity='warn') }}
{%- set rc = var('agent_reconcile') %}
with days as (
    select date_key
      from meta.mart_day_publication
     where mart_table = 'mart.legs__day_agent__handling' and close_version >= 1
       and date_key >= to_char((now() at time zone '{{ var("report_timezone") }}')::date - {{ rc.days | int }},
                               'YYYYMMDD')::integer
     group by date_key
),
agent_days as (
    select d.agent_no, d.date_key, d.inbound_calls, d.src_confirmed_at
      from core.fact_agent_day d
      join days using (date_key)
     where d.inbound_calls is not null
),
counted as (
    select a.agent_no, a.date_key, a.inbound_calls, a.src_confirmed_at, count(l.call_leg_sk) as legs
      from agent_days a
      left join core.fact_call_leg l
        on l.date_key = a.date_key
       and l.device_type_code = 2
       and l.device_no = a.agent_no
       and l.call_begin is not null
       and l.call_end <= a.src_confirmed_at
       and l.direction is distinct from 'outbound'
       and l.direction is distinct from 'internal'
     group by a.agent_no, a.date_key, a.inbound_calls, a.src_confirmed_at
)
select agent_no, date_key, src_confirmed_at, inbound_calls, legs, inbound_calls - legs as difference
  from counted
 where abs(inbound_calls - legs) > greatest({{ rc.min_calls | int }}, {{ rc.ratio | float }} * legs)
