{#- core.fact_agent_session: one row per agent sign-in interval (contract if03b · DD-98 · DD-53).

    Owned by oss911_owner (structural changes); this model only writes into it (incremental, delete+insert by key).
    Key: the agent's ID and the interval's inTime as received. An interval is observed again and again until its
    sign-out (duration grows, outTime is filled in): per key the newest new observation is taken, and written only
    when it differs and is not older than the row's (macros/agent_facts.sql). date_key is the day of signin_at in the
    report time zone -- an interval past midnight belongs to its sign-in day. While signout_at is NULL the interval
    counts up to src_observed_at in reports (DD-98 item eight).
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='agent_session_sk',
    on_schema_change='fail',
    pre_hook=["{{ lock_call_leg_links() }}",
              "{{ ensure_agent_partitions('session') }}"],
    post_hook=["{{ raise_unlinked_agent_fact_items() }}",
               "{{ confirm_agent_facts('session') }}",
               "{{ register_agent_batches('stg_icp_agent_session', 'icp_agent_session') }}"]
) }}

with obs as (
    {{ agent_observations('stg_icp_agent_session', 'icp_agent_session', this.identifier, 's.signin_at is not null') }}
),

latest as (
    select *
      from (select o.*, row_number() over (partition by o.agent_no, o.signin_text
                                           order by o.observed_at desc, o.content_hash) as rn
              from obs o) x
     where rn = 1
),

candidate as (
    select core.sk('{{ var("site_code") }}', 'icp', core.nk(l.agent_no, l.signin_text))        as agent_session_sk,
           to_char(l.signin_at at time zone '{{ var("report_timezone") }}', 'YYYYMMDD')::integer as date_key,
           core.site_namespace('{{ var("site_code") }}')                                      as site_sk,
           {{ agent_at('l.agent_no', 'l.signin_at') }}                                         as agent_sk,
           l.signin_at,
           l.signout_at,
           l.duration_seconds,
           'icp'::text                                                                        as src_system,
           l.run_id,
           l.contract_version,
           0::smallint                                                                        as correction_seq,
           now()                                                                              as last_change_at,
           now()                                                                              as built_at,
           l.agent_no,
           l.observed_at                                                                      as src_observed_at,
           -- never back: a newer content keeps the confirmation already reached (confirm_agent_facts moves it on)
           greatest(l.observed_at, (select e2.src_confirmed_at from {{ this }} e2
                                     where e2.agent_session_sk = core.sk('{{ var("site_code") }}', 'icp',
                                                                           core.nk(l.agent_no, l.signin_text))
                                       and e2.date_key = to_char(l.signin_at at time zone '{{ var("report_timezone") }}',
                                                                 'YYYYMMDD')::integer))  as src_confirmed_at
      from latest l
)

select c.*
  from candidate c
  left join {{ this }} e on e.agent_session_sk = c.agent_session_sk
 where e.agent_session_sk is null
    or ({{ agent_session_content_hash('c') }} <> {{ agent_session_content_hash('e') }}
        and c.src_observed_at >= e.src_observed_at)
