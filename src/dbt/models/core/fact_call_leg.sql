{#- core.fact_call_leg: one row per ICP call leg (LD-02 chapter 3.3 · DD-66 · DD-91).

    The table is created by the structural changes and owned by oss911_owner; this model only writes into it
    (incremental, delete+insert by call_leg_sk). Per key it takes the newest new observation and writes it only when
      - core has no row for the key, or
      - the observation is newer than the one the row was built from AND the derived content differs (DD-82 item 4), or
      - it is the same observation and the derived content differs (a corrected classification, a dimension that
        arrived -- reprocessing after clearing this model's registrations, DD-91 items 3, 7).
    An older observation (a Monday batch released on Thursday) never overwrites a newer one. Only changed rows are
    written (DD-78). Delete+insert by key also moves a row whose date_key changed to its new partition (DD-91 item 8).
    Keys caught by the 24-hour guard are not written (macros/fact_call_leg.sql).
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='call_leg_sk',
    on_schema_change='fail',
    pre_hook="{{ ensure_leg_partitions() }}",
    post_hook=["{{ raise_leg_guard_items() }}", "{{ register_core_batches() }}"]
) }}

with obs as (
    {{ leg_observations() }}
),

latest as (
    select *
      from (select o.*, row_number() over (partition by o.src_natural_key
                                           order by o.observed_at desc, o.content_hash) as rn
              from obs o) x
     where rn = 1
       and src_natural_key not in (select g.src_natural_key from ({{ leg_guard_keys() }}) g)
),

candidate as (
    select
        core.sk('{{ var("site_code") }}', 'icp', l.src_natural_key)                        as call_leg_sk,
        to_char(l.wait_begin at time zone '{{ var("report_timezone") }}', 'YYYYMMDD')::integer as date_key,
        core.site_namespace('{{ var("site_code") }}')                                    as site_sk,
        core.sk('{{ var("site_code") }}', 'icp', core.nk(l.call_id))                      as call_sk,
        l.src_natural_key,
        l.call_id,
        l.leg_seq,
        l.is_last_leg,
        coalesce(l.device_type_code, 0)::smallint                                        as device_type_code,
        l.device_no,
        l.device_in_code,
        da.agent_sk,
        dq.queue_sk,
        case when l.device_type_code = 1 then l.device_no end                            as queue_code,
        l.call_type_code,
        l.direction,
        l.caller_number,
        null::text                                                                       as caller_number_hash,  -- DD-92, not yet
        l.callee_number,
        l.wait_begin,
        l.wait_end,
        l.ack_begin,
        l.ack_end,
        l.call_begin,
        l.call_end,
        extract(epoch from l.wait_end - l.wait_begin)::integer                          as wait_seconds,
        extract(epoch from l.call_end - l.call_begin)::integer                          as talk_seconds,
        l.wait_cause_code,
        l.release_cause_code,
        l.release_source,
        l.enter_reason_code,
        l.leave_reason_code,
        l.source_tz_offset,
        'icp'::text                                                                      as src_system,
        l.run_id,
        l.contract_version,
        l.src_watermark,
        0::smallint                                                                      as correction_seq,
        now()                                                                            as last_change_at,
        now()                                                                            as built_at,
        l.observed_at                                                                    as src_observed_at
      from latest l
      -- dimensions: the version valid at the leg's start (DD-12); empty until if03d is ingested (DD-91 item 3)
      left join core.dim_agent da
        on l.device_type_code = 2 and da.site_sk = core.site_namespace('{{ var("site_code") }}')
       and da.src_natural_key = l.device_no and l.wait_begin >= da.valid_from and l.wait_begin < da.valid_to
      left join core.dim_queue dq
        on l.device_type_code = 1 and dq.site_sk = core.site_namespace('{{ var("site_code") }}')
       and dq.src_natural_key = l.device_no and l.wait_begin >= dq.valid_from and l.wait_begin < dq.valid_to
)

select c.*
  from candidate c
  left join {{ this }} e on e.call_leg_sk = c.call_leg_sk
 where e.call_leg_sk is null
    or ({{ leg_content_hash('c') }} <> {{ leg_content_hash('e') }}
        and c.src_observed_at >= e.src_observed_at)
