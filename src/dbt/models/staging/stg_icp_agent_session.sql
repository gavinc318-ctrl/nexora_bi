{#- ICP agent sign-in intervals, typed -- one row per observation in raw (contract if03b · DD-98 · DD-89).

    Rows: every raw row of the processable batches this model has not registered yet (macros/batches.sql). Which
    observation of an interval wins is decided in core: staging keeps them all.

    Typing: inTime / outTime are the source's notation read in the contract's window.source_timezone; an empty outTime
    is an interval still open (NULL). duration is the source's own, "n" or "n.0" (DD-98 item four); an empty one is
    NULL. A value present that does not parse becomes NULL and typed_ok is false (a released batch may carry one).
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook="{{ clear_registrations_on_rebuild() }}",
    post_hook="{{ register_staged_batches('icp_agent_session') }}"
) }}

with batches as (
    {{ unprocessed_batches('icp_agent_session', this.identifier) }}
),

typed as (
    select nullif(r.id, '')                                          as agent_no,
           r.intime                                                  as signin_text,
           staging.try_timestamp(nullif(r.intime, ''))               as signin_local,
           staging.try_timestamp(nullif(r.outtime, ''))              as signout_local,
           staging.try_whole(nullif(r.duration, ''))                 as duration_seconds,
           (nullif(r.intime, '') is not null and staging.try_timestamp(r.intime) is null)
             or (nullif(r.outtime, '') is not null and staging.try_timestamp(r.outtime) is null)
             or (nullif(r.duration, '') is not null and staging.try_whole(r.duration) is null) as has_unparseable,
           r.cc_id,
           r.vdn_id,
           r.run_id,
           r.batch_seq,
           r.contract_version,
           encode(r.content_hash, 'hex')                             as content_hash,
           r.observed_at
      from raw.icp_agent_session r
      join batches b on b.run_id = r.run_id and b.batch_seq = r.batch_seq
),

contract as (
    select {{ contract_text('icp_agent_session', 'window,source_timezone') }} as tz
)

select t.agent_no,
       t.signin_text,
       t.signin_local  at time zone c.tz                             as signin_at,
       t.signout_local at time zone c.tz                             as signout_at,
       t.duration_seconds,
       not t.has_unparseable                                         as typed_ok,
       t.cc_id,
       t.vdn_id,
       t.run_id,
       t.batch_seq,
       t.contract_version,
       t.content_hash,
       t.observed_at,
       now()                                                         as staged_at
  from typed t
 cross join contract c
