-- Calls with legs in core but no first leg (CALLIDNUM 1): no fact_call row is built for them (DD-96 item one), so
-- they are missing from every call-level metric. A monitoring item, not a failure: the first leg may be late. If a
-- call with a single leg turns out to be numbered -1 rather than 1 (interface document: "1 the first CDR, -1 the
-- last"; NOTES 2026-10-30), such calls show here permanently. One row: how many calls, and the oldest.
{{ config(severity='warn') }}
select count(*) as calls, min(first_wait_begin) as oldest
  from (select l.call_sk, min(l.wait_begin) as first_wait_begin
          from {{ ref('fact_call_leg') }} l
         group by l.call_sk
        having not bool_or(l.leg_seq = 1)) x
having count(*) > 0
