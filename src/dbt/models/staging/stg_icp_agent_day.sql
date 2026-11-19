{#- ICP agent day totals, typed -- one row per observation in raw (contract if03c · DD-98 · DD-65 · DD-89).

    Rows: every raw row of the processable batches this model has not registered yet. Which observation of a day wins
    is decided in core (the latest): staging keeps them all.

    Typing: each index of idxs becomes its column (macros/agent_facts.sql, agent_day_columns), "n" or "n.0" an
    integer (DD-98 item four). null stays NULL -- "no such day", never 0. A value present that does not parse becomes
    NULL and typed_ok is false (a released batch may carry one).
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook="{{ clear_registrations_on_rebuild() }}",
    post_hook="{{ register_staged_batches('icp_agent_day') }}"
) }}

with batches as (
    {{ unprocessed_batches('icp_agent_day', this.identifier) }}
),

rows_ as (
    select r.*,
           (select jsonb_object_agg(x ->> 'id', x -> 'val') from jsonb_array_elements(r.idxs) x
             where jsonb_typeof(x) = 'object' and x ? 'id')                      as v
      from raw.icp_agent_day r
      join batches b on b.run_id = r.run_id and b.batch_seq = r.batch_seq
)

select nullif(r.id, '')                                              as agent_no,
       r.date_key,
       {%- for idx, col in agent_day_columns() %}
       staging.try_whole(r.v ->> '{{ idx }}')                        as {{ col }},
       {%- endfor %}
       not (false
       {%- for idx, col in agent_day_columns() %}
            or (r.v ->> '{{ idx }}' is not null and staging.try_whole(r.v ->> '{{ idx }}') is null)
       {%- endfor %})                                                as typed_ok,
       r.cc_id,
       r.vdn_id,
       r.run_id,
       r.batch_seq,
       r.contract_version,
       encode(r.content_hash, 'hex')                                 as content_hash,
       r.observed_at,
       now()                                                         as staged_at
  from rows_ r
