{#- Input of mart.agents__day_agent__duty: day x site x agent, from the sign-in intervals (DD-101 item one). Pieces
    as macros/agent_marts.sql cuts them: past midnight each day keeps its own part, an interval not signed out counts
    up to the last confirmation.
      session_count       intervals signed in that day (the platform's signin_count, IDX_04_02_001, is the
                          reconciliation partner)
      on_duty_seconds     the day's pieces, in seconds -- M-R02 = on_duty_seconds / agent_days
      open_session_count  of the day's pieces, those whose interval is not signed out yet: on_duty_seconds counts them
                          up to the last confirmation, not to a sign-out
      agent_days          always 1, the denominator of averages over agents and days
      first_signin_at     the day's first sign-in, last_signout_at its last sign-out: for display by day, no metric
    One row per agent number across dimension versions; agent_key and the names are those of the latest version that
    overlaps the day, unlinked when none does (DD-95). The names are sensitive columns (metrics/marts.yaml). -#}
{{ config(materialized='view') }}

with pieces as (
    {{ agent_session_pieces('day') }}
),
per_day as (
    select date_key,
           site_sk,
           agent_no,
           count(*) filter (where is_signin_piece)                                           as session_count,
           round(sum(seconds))::bigint                                                       as on_duty_seconds,
           count(*) filter (where is_open)                                                   as open_session_count,
           min(signin_at) filter (where is_signin_piece)                                     as first_signin_at,
           max(signout_at) filter (where not is_open and piece_to = signout_at)              as last_signout_at
      from pieces
     group by date_key, site_sk, agent_no
)
select p.date_key,
       p.site_sk,
       a.agent_key,
       p.agent_no,
       a.agent_name_ar,
       a.agent_name_en,
       p.session_count,
       p.on_duty_seconds,
       p.open_session_count,
       1::integer                                                                             as agent_days,
       p.first_signin_at,
       p.last_signout_at
  from per_day p
  left join lateral ({{ agent_of_day('p.agent_no', 'p.date_key') }}) a on true
