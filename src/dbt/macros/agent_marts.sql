{#- Pieces of the agent marts (DD-101 · LD-03 chapter 5, last part).

    The sign-in intervals of core.fact_agent_session, cut at every boundary of step (day or hour) in the report time
    zone: an interval past midnight is cut there, each piece belongs to its own day -- a day's total can be added up
    and is never more than 24 hours. An interval not signed out yet counts up to src_confirmed_at, the last time the
    platform returned the agent (DD-98 review note item two), and is open. One row per piece:
      agent_no, site_sk, date_key, hour_of_day (step hour only), piece_from, piece_to, seconds,
      signin_at, signout_at, is_open, is_signin_piece (the piece the interval begins with)
    A piece of zero length is kept only when it is the interval's first: an interval seen at the moment it began
    still counts as a sign-in that day. -#}
{% macro agent_session_pieces(step) -%}
{%- set tz = var('report_timezone') -%}
{%- if step not in ('day', 'hour') -%}
    {{ exceptions.raise_compiler_error("agent_session_pieces: step is day or hour, got " ~ step) }}
{%- endif -%}
    select x.agent_no,
           x.site_sk,
           to_char(b.local_from, 'YYYYMMDD')::integer                                         as date_key,
           {%- if step == 'hour' %}
           extract(hour from b.local_from)::integer                                           as hour_of_day,
           {%- endif %}
           greatest(x.signin_at, b.local_from at time zone '{{ tz }}')                         as piece_from,
           least(x.ends_at, (b.local_from + interval '1 {{ step }}') at time zone '{{ tz }}')  as piece_to,
           extract(epoch from least(x.ends_at, (b.local_from + interval '1 {{ step }}') at time zone '{{ tz }}')
                              - greatest(x.signin_at, b.local_from at time zone '{{ tz }}'))   as seconds,
           x.signin_at,
           x.signout_at,
           x.is_open,
           b.local_from = date_trunc('{{ step }}', x.signin_at at time zone '{{ tz }}')       as is_signin_piece
      from (select s.agent_no, s.site_sk, s.signin_at, s.signout_at, s.signout_at is null as is_open,
                   -- never before the sign-in: an outTime before inTime is the platform's (test
                   -- agent_session_duration_differs reports it) and counts as zero here, not as negative time
                   greatest(s.signin_at, coalesce(s.signout_at, s.src_confirmed_at))       as ends_at
              from {{ ref('fact_agent_session') }} s) x
     cross join lateral generate_series(date_trunc('{{ step }}', x.signin_at at time zone '{{ tz }}'),
                                        date_trunc('{{ step }}', x.ends_at at time zone '{{ tz }}'),
                                        interval '1 {{ step }}') as b(local_from)
     where (b.local_from + interval '1 {{ step }}') at time zone '{{ tz }}' > x.signin_at
       and (b.local_from at time zone '{{ tz }}' < x.ends_at
            or b.local_from = date_trunc('{{ step }}', x.signin_at at time zone '{{ tz }}'))
{%- endmacro %}

{#- The agent key and names of agent_no on day date_key: the latest dim_agent version that overlaps the day, as the
    day totals are linked (agent_on_day, macros/agent_facts.sql) -- one row per agent and day across versions; an
    agent no version covers that day is unlinked (DD-95). Use as: left join lateral ( ... ) a on true -#}
{% macro agent_of_day(no, date_key) -%}
    select coalesce(d.agent_sk::text, 'unlinked') as agent_key, d.name_ar as agent_name_ar, d.name_en as agent_name_en
      from (select 1) one
      left join {{ ref('dim_agent') }} d on d.agent_sk = {{ agent_on_day(no, date_key) }}
{%- endmacro %}
