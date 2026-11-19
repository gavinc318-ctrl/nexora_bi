-- Day totals whose state durations add up to more than the signed-in time (contract if03c quality): reported, never
-- adjusted -- the platform's figures are kept as they are (DD-65). A warning. One row per agent and day.
{{ config(severity='warn') }}
select f.agent_no, f.date_key, f.signin_seconds,
       coalesce(f.talk_seconds, 0) + coalesce(f.acw_seconds, 0) + coalesce(f.rest_seconds, 0)
         + coalesce(f.busy_seconds, 0) + coalesce(f.hold_seconds, 0) + coalesce(f.idle_seconds, 0) as states_seconds
  from {{ ref('fact_agent_day') }} f
 where f.signin_seconds is not null
   and coalesce(f.talk_seconds, 0) + coalesce(f.acw_seconds, 0) + coalesce(f.rest_seconds, 0)
         + coalesce(f.busy_seconds, 0) + coalesce(f.hold_seconds, 0) + coalesce(f.idle_seconds, 0) > f.signin_seconds
