-- Every derived column of core.fact_call recomputed from its legs another way and compared (DD-66 table comment:
-- each column must be recomputable from the legs; DD-96 · LD-03 2.4). One row per call that differs, with both.
--
-- Another way than the model's: the model picks the first leg, the first answered agent leg and the first queue
-- leg with DISTINCT ON and aggregates with array_agg; here each leg is ranked with window functions once and the
-- call's values are aggregates over the ranked legs (FILTER on the rank).
--
-- One pass over the legs of the checked calls, joined and grouped by hash, so its time grows with the number of
-- legs and does not depend on the planner's statistics. (The first version ran one correlated subquery per column
-- and call: right after a large load into a new month's partition, before autovacuum analysed it, the planner
-- took the partition for empty and scanned it in full for every subquery -- nine minutes on the scratch database,
-- 2026-11-16.)
--
-- Over the calls of calls_to_check (macros/fact_call.sql; every call with var full_check), each with all of its
-- legs: the legs are not filtered by time.
with checked as ({{ calls_to_check() }}),
l as (
    select x.*,
           x.device_type_code = 2 and x.call_begin is not null                                   as is_answered_agent,
           row_number() over (partition by x.call_sk
                              order by case when x.leg_seq = -1 then 2147483647 else x.leg_seq end)  as rank_first,
           row_number() over (partition by x.call_sk, (x.device_type_code = 2 and x.call_begin is not null)
                              order by x.call_begin,
                                       case when x.leg_seq = -1 then 2147483647 else x.leg_seq end)  as rank_answered,
           row_number() over (partition by x.call_sk, x.device_type_code
                              order by x.wait_begin,
                                       case when x.leg_seq = -1 then 2147483647 else x.leg_seq end)  as rank_device
      from {{ ref('fact_call_leg') }} x
     where x.call_sk in (select call_sk from checked)
),
r as (
    select call_sk,
           count(*)                                                                    as leg_count,
           bool_or(leg_seq = -1)                                                       as is_complete,
           max(leg_seq) filter (where rank_first = 1)                                  as first_leg_seq,
           max(wait_begin) filter (where rank_first = 1)                               as offered_at,
           max(call_type_code) filter (where rank_first = 1)                           as call_type_code,
           max(direction) filter (where rank_first = 1)                                as direction,
           min(call_begin) filter (where device_type_code = 2)                         as answered_at,
           max(agent_sk::text) filter (where is_answered_agent and rank_answered = 1)  as agent_sk,
           max(queue_code) filter (where device_type_code = 1 and rank_device = 1)     as first_queue_code,
           max(coalesce(call_end, wait_end)) filter (where leg_seq = -1)               as ended_at,
           max(release_source) filter (where leg_seq = -1)                             as released_by,
           sum(wait_seconds) filter (where device_type_code = 1)                       as wait_seconds,
           sum(talk_seconds) filter (where device_type_code = 2)                       as talk_seconds,
           count(*) filter (where device_type_code = 2)                                as agent_leg_count,
           count(*) filter (where is_answered_agent)                                   as answered_leg_count
      from l
     group by call_sk
),
mine as (
    select c.call_sk, c.leg_count, c.is_complete, c.first_leg_seq, c.offered_at, c.call_type_code, c.direction,
           c.answered_at, c.agent_sk, c.first_queue_code, c.ended_at, c.released_by, c.wait_seconds, c.talk_seconds,
           c.agent_leg_count, c.answered_leg_count, c.is_transferred, c.date_key
      from {{ ref('fact_call') }} c
     where c.call_sk in (select call_sk from checked)
)
select m.*, r.*
  from mine m join r on r.call_sk = m.call_sk
 where row(m.leg_count, m.is_complete, m.first_leg_seq, m.offered_at, m.call_type_code, m.direction, m.answered_at,
           m.agent_sk::text, m.first_queue_code, m.wait_seconds, m.talk_seconds, m.agent_leg_count,
           m.answered_leg_count, m.is_transferred,
           to_char(m.offered_at at time zone '{{ var("report_timezone") }}', 'YYYYMMDD')::integer)
       is distinct from
       row(r.leg_count::smallint, r.is_complete, r.first_leg_seq::smallint, r.offered_at, r.call_type_code,
           r.direction, r.answered_at, r.agent_sk, r.first_queue_code, r.wait_seconds::integer,
           r.talk_seconds::integer, r.agent_leg_count::smallint, r.answered_leg_count::smallint,
           r.answered_leg_count >= 2, m.date_key)
    or (m.is_complete and row(m.ended_at, m.released_by) is distinct from row(r.ended_at, r.released_by))
