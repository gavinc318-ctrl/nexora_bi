-- Every derived column of core.fact_call recomputed from its legs another way -- one correlated subquery per column,
-- not the model's aggregation -- and compared (DD-66 table comment: each column must be recomputable from the legs;
-- DD-96 · LD-03 2.4). One row per call that differs, with both versions.
with l as (
    select x.*, case when x.leg_seq = -1 then 2147483647 else x.leg_seq end as leg_order
      from {{ ref('fact_call_leg') }} x
),
r as (
    select c.call_sk,
           (select count(*) from l where l.call_sk = c.call_sk)                                          as leg_count,
           exists (select 1 from l where l.call_sk = c.call_sk and l.leg_seq = -1)                       as is_complete,
           (select l.wait_begin from l where l.call_sk = c.call_sk and l.leg_seq = 1)                     as offered_at,
           (select l.call_type_code from l where l.call_sk = c.call_sk and l.leg_seq = 1)                 as call_type_code,
           (select l.direction from l where l.call_sk = c.call_sk and l.leg_seq = 1)                      as direction,
           (select min(l.call_begin) from l where l.call_sk = c.call_sk and l.device_type_code = 2)       as answered_at,
           (select l.agent_sk from l where l.call_sk = c.call_sk and l.device_type_code = 2 and l.call_begin is not null
             order by l.call_begin, l.leg_order limit 1)                                                 as agent_sk,
           (select l.queue_code from l where l.call_sk = c.call_sk and l.device_type_code = 1
             order by l.wait_begin, l.leg_order limit 1)                                                 as first_queue_code,
           (select coalesce(l.call_end, l.wait_end) from l where l.call_sk = c.call_sk and l.leg_seq = -1) as ended_at,
           (select l.release_source from l where l.call_sk = c.call_sk and l.leg_seq = -1)                as released_by,
           (select sum(l.wait_seconds) from l where l.call_sk = c.call_sk and l.device_type_code = 1)     as wait_seconds,
           (select sum(l.talk_seconds) from l where l.call_sk = c.call_sk and l.device_type_code = 2)     as talk_seconds,
           (select count(*) from l where l.call_sk = c.call_sk and l.device_type_code = 2)                as agent_leg_count,
           (select count(*) from l where l.call_sk = c.call_sk and l.device_type_code = 2
               and l.call_begin is not null)                                                              as answered_leg_count
      from {{ ref('fact_call') }} c
),
mine as (
    select c.call_sk, c.leg_count, c.is_complete, c.offered_at, c.call_type_code, c.direction, c.answered_at, c.agent_sk,
           c.first_queue_code, c.ended_at, c.released_by, c.wait_seconds, c.talk_seconds, c.agent_leg_count,
           c.answered_leg_count, c.is_transferred, c.date_key
      from {{ ref('fact_call') }} c
)
select m.*, r.*
  from mine m join r on r.call_sk = m.call_sk
 where row(m.leg_count, m.is_complete, m.offered_at, m.call_type_code, m.direction, m.answered_at, m.agent_sk,
           m.first_queue_code, m.wait_seconds, m.talk_seconds, m.agent_leg_count, m.answered_leg_count,
           m.is_transferred,
           to_char(m.offered_at at time zone '{{ var("report_timezone") }}', 'YYYYMMDD')::integer)
       is distinct from
       row(r.leg_count::smallint, r.is_complete, r.offered_at, r.call_type_code, r.direction, r.answered_at, r.agent_sk,
           r.first_queue_code, r.wait_seconds::integer, r.talk_seconds::integer, r.agent_leg_count::smallint,
           r.answered_leg_count::smallint, r.answered_leg_count >= 2, m.date_key)
    or (m.is_complete and row(m.ended_at, m.released_by) is distinct from row(r.ended_at, r.released_by))
