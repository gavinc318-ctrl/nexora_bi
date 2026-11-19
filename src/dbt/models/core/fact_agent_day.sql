{#- core.fact_agent_day: one row per agent and day, the platform's day totals (contract if03c · DD-98 · DD-65).

    Owned by oss911_owner (structural changes); this model only writes into it (incremental, delete+insert by key).
    Key: the agent's ID and date_key, the day ingestion assigned to the observation (DD-98 item two). The day's values
    are those of its latest usable observation -- there is no backfill (hindex/agent has no date parameter), so the
    last observation of the day is its final value, and it may lack the day's last minutes (DD-98 item one). Written
    only when the content differs and the observation is not older than the row's (macros/agent_facts.sql). A value
    the platform sends as null stays NULL: no such day, never 0.
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='agent_day_sk',
    on_schema_change='fail',
    pre_hook=["{{ lock_call_leg_links() }}",
              "{{ ensure_agent_partitions('day') }}"],
    post_hook=["{{ raise_unlinked_agent_fact_items() }}",
               "{{ register_agent_batches('stg_icp_agent_day', 'icp_agent_day') }}"]
) }}

with obs as (
    {{ agent_observations('stg_icp_agent_day', 'icp_agent_day', this.identifier, 's.date_key is not null') }}
),

latest as (
    select *
      from (select o.*, row_number() over (partition by o.agent_no, o.date_key
                                           order by o.observed_at desc, o.content_hash) as rn
              from obs o) x
     where rn = 1
),

candidate as (
    select core.sk('{{ var("site_code") }}', 'icp', core.nk(l.agent_no, l.date_key::text))    as agent_day_sk,
           l.date_key,
           core.site_namespace('{{ var("site_code") }}')                                      as site_sk,
           {{ agent_on_day('l.agent_no', 'l.date_key') }}                                      as agent_sk,
           {%- for _, col in agent_day_columns() %}
           l.{{ col }},
           {%- endfor %}
           'platform_index'::text                                                             as source_kind,
           'icp'::text                                                                        as src_system,
           l.run_id,
           l.contract_version,
           now()                                                                              as built_at,
           l.agent_no,
           l.observed_at                                                                      as src_observed_at,
           now()                                                                              as last_change_at
      from latest l
)

select c.*
  from candidate c
  left join {{ this }} e on e.agent_day_sk = c.agent_day_sk
 where e.agent_day_sk is null
    or ({{ agent_day_content_hash('c') }} <> {{ agent_day_content_hash('e') }}
        and c.src_observed_at >= e.src_observed_at)
