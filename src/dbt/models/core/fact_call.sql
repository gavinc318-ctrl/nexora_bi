{#- core.fact_call: one row per call, aggregated from its legs in core.fact_call_leg (DD-66 · DD-96 · LD-03 2.4).

    Legs run IVR -> skill queue (waiting for a free agent) -> agent (wait_begin = ringing, call_begin = answered).
      - No row until the first leg (CALLIDNUM 1) has arrived: without it there is no offered_at (item one).
      - is_complete once the last leg (-1) has arrived; until then call_result, ended_at and released_by are NULL and
        the call is "in progress" (items two, nine).
      - offered_at = first leg's wait_begin, date_key its day; answered_at and agent_sk = the earliest answered agent
        leg; ended_at = last leg's call_end, or its wait_end when it was not answered (item three).
      - call_type_code, direction, caller_number from the first leg; legs that disagree raise a clarification item
        (item four).
      - first queue = earliest queue leg; wait_seconds = queue legs' waits, talk_seconds = agent legs' talk; agent and
        answered leg counts (items five, six). released_by = last leg's release_source (item seven).
      - is_transferred = two or more answered agent legs; the reason-code path waits for ICP (item eight).
      - call_result: answered -> abandoned (any queue or agent leg, last leg released by the caller) ->
        unanswered_system (agent legs, none answered) -> ivr_only (item nine).
    Rebuilt for the calls whose legs changed (macros/fact_call.sql); written only when the derived content differs
    (DD-78), delete+insert by call_sk so a call whose first leg moved day changes partition.
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='call_sk',
    on_schema_change='fail',
    pre_hook=["{{ lock_call_leg_links() }}", "{{ ensure_call_partitions() }}"],
    post_hook=["{{ raise_call_type_items() }}"]
) }}

-- After the dimension models when they are in the same build: they relink legs, which changes a call's agent and
-- first queue (seen on the scratch database: run before them, the build left the relinks to the next one).
-- depends_on: {{ ref('dim_agent') }}
-- depends_on: {{ ref('dim_queue') }}

with legs as (
    select l.*,
           case when l.leg_seq = -1 then 2147483647 else l.leg_seq end as leg_order
      from {{ ref('fact_call_leg') }} l
     where l.call_sk in ({{ calls_to_rebuild() }})
),

agg as (
    select call_sk,
           count(*)                                                                     as leg_count,
           bool_or(is_last_leg)                                                         as is_complete,
           count(*) filter (where device_type_code = 1)                                 as queue_leg_count,
           count(*) filter (where device_type_code = 2)                                 as agent_leg_count,
           count(*) filter (where device_type_code = 2 and call_begin is not null)      as answered_leg_count,
           sum(wait_seconds) filter (where device_type_code = 1)                        as wait_seconds,
           sum(talk_seconds) filter (where device_type_code = 2)                        as talk_seconds,
           (array_agg(run_id order by src_observed_at desc, leg_order))[1]              as run_id,
           (array_agg(contract_version order by src_observed_at desc, leg_order))[1]    as contract_version,
           (array_agg(src_watermark order by src_observed_at desc, leg_order))[1]       as src_watermark
      from legs
     group by call_sk
),

first_leg as (
    select * from legs where leg_seq = 1
),

last_leg as (
    select * from legs where is_last_leg
),

answered as (
    select distinct on (call_sk) call_sk, agent_sk, call_begin
      from legs
     where device_type_code = 2 and call_begin is not null
     order by call_sk, call_begin, leg_order
),

first_queue as (
    select distinct on (call_sk) call_sk, queue_sk, queue_code
      from legs
     where device_type_code = 1
     order by call_sk, wait_begin, leg_order
),

calls as (
    select
        f.call_sk,
        to_char(f.wait_begin at time zone '{{ var("report_timezone") }}', 'YYYYMMDD')::integer as date_key,
        f.site_sk,
        f.call_id                                                                         as src_natural_key,
        f.call_type_code,
        f.direction,
        f.caller_number,
        null::text                                                                        as caller_number_hash,  -- DD-92, not yet
        q.queue_sk                                                                        as first_queue_sk,
        q.queue_code                                                                      as first_queue_code,
        an.agent_sk,
        case
            when not a.is_complete                                                       then null
            when a.answered_leg_count >= 1                                               then 'answered'
            when a.queue_leg_count + a.agent_leg_count > 0 and z.release_source = 'caller' then 'abandoned'
            when a.agent_leg_count > 0                                                   then 'unanswered_system'
            else 'ivr_only'
        end                                                                               as call_result_code,
        f.wait_begin                                                                      as offered_at,
        an.call_begin                                                                     as answered_at,
        case when a.is_complete then coalesce(z.call_end, z.wait_end) end                 as ended_at,
        a.wait_seconds::integer                                                           as wait_seconds,
        a.talk_seconds::integer                                                           as talk_seconds,
        a.leg_count::smallint                                                             as leg_count,
        a.agent_leg_count::smallint                                                       as agent_leg_count,
        a.answered_leg_count::smallint                                                    as answered_leg_count,
        null::smallint                                                                    as ring_count,          -- not in the CDR (column comment)
        case when a.is_complete then z.release_source end                                 as released_by,
        a.answered_leg_count >= 2                                                         as is_transferred,
        false                                                                             as is_malicious,        -- IR-46
        f.source_tz_offset,
        'icp'::text                                                                       as src_system,
        a.run_id,
        a.contract_version,
        a.src_watermark,
        0::smallint                                                                       as correction_seq,
        now()                                                                             as last_change_at,
        now()                                                                             as built_at,
        a.is_complete
      from first_leg f
      join agg a on a.call_sk = f.call_sk
      left join last_leg z on z.call_sk = f.call_sk
      left join answered an on an.call_sk = f.call_sk
      left join first_queue q on q.call_sk = f.call_sk
),

candidate as (
    select c.call_sk, c.date_key, c.site_sk, c.src_natural_key, c.call_type_code, c.direction, c.caller_number,
           c.caller_number_hash, c.first_queue_sk, c.first_queue_code, c.agent_sk, r.call_result_sk, c.offered_at,
           c.answered_at, c.ended_at, c.wait_seconds, c.talk_seconds, c.leg_count, c.agent_leg_count,
           c.answered_leg_count, c.ring_count, c.released_by, c.is_transferred, c.is_malicious, c.source_tz_offset,
           c.src_system, c.run_id, c.contract_version, c.src_watermark, c.correction_seq, c.last_change_at,
           c.built_at, c.is_complete
      from calls c
      left join {{ source('core', 'dim_call_result') }} r
        on r.site_sk = c.site_sk and r.code = c.call_result_code
)

select c.*
  from candidate c
  left join {{ this }} e on e.call_sk = c.call_sk
 where e.call_sk is null
    or {{ call_content_hash('c') }} <> {{ call_content_hash('e') }}
