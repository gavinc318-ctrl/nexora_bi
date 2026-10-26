{#- ICP call legs, typed and mapped -- one row per observation in raw (LD-02 chapter 9 · DD-89 · DD-91).

    Rows: every raw row of the processable batches this model has not registered yet (macros/batches.sql). Which
    observation of a leg wins is decided in core, not here: staging keeps them all.

    Typing: empty strings are NULL (the CSV has no other way to say "no value"); times are the source's own notation
    read in the contract's window.source_timezone -- not the session's -- and become timestamptz; a value that does
    not parse becomes NULL (macros/safe_casts.sql) and is visible in typed_ok.

    Mapping (staging's job, LD-02 chapter 9), from the contract in the database:
      direction       CALLTYPE in enums.call_type.inbound -> inbound, in .outbound -> outbound, in the value table but
                      in neither -> internal, not in the value table (or empty) -> unmapped (DD-91 item 4 · DD-06)
      release_source  only on the last leg (CALLIDNUM = -1): RELEASECAUSE is meaningful there only (interface document;
                      ICP question Q23). Its class from enums.release_cause.classes, or unmapped. Other legs: NULL -- not
                      applicable, and not counted as unmapped
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook="{{ clear_registrations_on_rebuild() }}",
    post_hook="{{ register_staged_batches('icp_cdr_voice') }}"
) }}

with batches as (
    {{ unprocessed_batches('icp_cdr_voice', this.identifier) }}
),

raw_rows as (
    select r.*
      from raw.icp_cdr_voice r
      join batches b on b.run_id = r.run_id and b.batch_seq = r.batch_seq
),

typed as (
    select
        nullif(callid, '')                                           as call_id,
        staging.try_int(nullif(callidnum, ''))                       as leg_seq,
        nullif(callerno, '')                                         as caller_number,
        nullif(calleeno, '')                                         as callee_number,
        staging.try_timestamp(nullif(waitbegin, ''))                 as wait_begin_local,
        staging.try_timestamp(nullif(waitend, ''))                   as wait_end_local,
        staging.try_timestamp(nullif(ackbegin, ''))                  as ack_begin_local,
        staging.try_timestamp(nullif(ackend, ''))                    as ack_end_local,
        staging.try_timestamp(nullif(callbegin, ''))                 as call_begin_local,
        staging.try_timestamp(nullif(callend, ''))                   as call_end_local,
        staging.try_int(nullif(devicetype, ''))                      as device_type_code,
        nullif(deviceno, '')                                         as device_no,
        nullif(devicein, '')                                         as device_in_code,
        nullif(waitcause, '')                                        as wait_cause_code,
        nullif(releasecause, '')                                     as release_cause_code,
        staging.try_int(nullif(calltype, ''))                        as call_type_code,
        nullif(ucenterreason, '')                                    as enter_reason_code,
        nullif(ucleavereason, '')                                    as leave_reason_code,
        -- values that were present but did not parse; a released batch may carry them
        (nullif(callidnum, '') is not null and staging.try_int(callidnum) is null)
          or (nullif(waitbegin, '') is not null and staging.try_timestamp(waitbegin) is null)
          or (nullif(waitend, '')   is not null and staging.try_timestamp(waitend)   is null)
          or (nullif(callbegin, '') is not null and staging.try_timestamp(callbegin) is null)
          or (nullif(callend, '')   is not null and staging.try_timestamp(callend)   is null)
          or (nullif(devicetype, '') is not null and staging.try_int(devicetype) is null)
          or (nullif(calltype, '')  is not null and staging.try_int(calltype)  is null)   as has_unparseable,
        run_id,
        batch_seq,
        contract_version,
        src_watermark,
        encode(content_hash, 'hex')                                  as content_hash,
        ingested_at                                                  as observed_at
      from raw_rows
),

contract as (
    select {{ contract_text('icp_cdr_voice', 'window,source_timezone') }}  as tz,
           {{ contract_path('icp_cdr_voice', 'enums,call_type') }}         as call_type,
           {{ contract_path('icp_cdr_voice', 'enums,release_cause,classes') }} as release_classes
)

select
    t.call_id,
    t.leg_seq,
    t.leg_seq = -1                                                   as is_last_leg,
    t.caller_number,
    t.callee_number,
    t.wait_begin_local  at time zone c.tz                            as wait_begin,
    t.wait_end_local    at time zone c.tz                            as wait_end,
    t.ack_begin_local   at time zone c.tz                            as ack_begin,
    t.ack_end_local     at time zone c.tz                            as ack_end,
    t.call_begin_local  at time zone c.tz                            as call_begin,
    t.call_end_local    at time zone c.tz                            as call_end,
    -- the offset the source's notation is read with: from the contract, not from the source (DD-91 item 12)
    (t.wait_begin_local at time zone c.tz) - (t.wait_begin_local at time zone 'UTC') as source_tz_offset,
    t.device_type_code::smallint                                     as device_type_code,
    t.device_no,
    t.device_in_code,
    t.wait_cause_code,
    t.release_cause_code,
    t.call_type_code::smallint                                       as call_type_code,
    case
        when t.call_type_code is null                                          then 'unmapped'
        when c.call_type -> 'inbound'  @> to_jsonb(t.call_type_code)           then 'inbound'
        when c.call_type -> 'outbound' @> to_jsonb(t.call_type_code)           then 'outbound'
        when c.call_type -> 'values'   @> to_jsonb(t.call_type_code)           then 'internal'
        else 'unmapped'
    end                                                              as direction,
    case
        when t.leg_seq is distinct from -1                                     then null
        when c.release_classes -> 'agent'  @> to_jsonb(staging.try_int(t.release_cause_code)) then 'agent'
        when c.release_classes -> 'caller' @> to_jsonb(staging.try_int(t.release_cause_code)) then 'caller'
        when c.release_classes -> 'system' @> to_jsonb(staging.try_int(t.release_cause_code)) then 'system'
        else 'unmapped'
    end                                                              as release_source,
    t.enter_reason_code,
    t.leave_reason_code,
    not t.has_unparseable                                            as typed_ok,
    t.run_id,
    t.batch_seq,
    t.contract_version,
    t.src_watermark,
    t.content_hash,
    t.observed_at,
    now()                                                            as staged_at
  from typed t
  cross join contract c
