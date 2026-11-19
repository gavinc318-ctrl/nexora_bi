-- Closed sign-in intervals whose source duration differs from signout_at - signin_at by more than a minute (contract
-- if03b quality). The source's duration is kept as it is and never recomputed: the difference is the platform's own
-- and is reported, not smoothed (DD-98 item eight). A warning; the minute is a first value. One row per interval.
{{ config(severity='warn') }}
select f.agent_no, f.signin_at, f.signout_at, f.duration_seconds,
       extract(epoch from f.signout_at - f.signin_at)::integer as from_times
  from {{ ref('fact_agent_session') }} f
 where f.signout_at is not null and f.duration_seconds is not null
   and abs(f.duration_seconds - extract(epoch from f.signout_at - f.signin_at)) > 60
