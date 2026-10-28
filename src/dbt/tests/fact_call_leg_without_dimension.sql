-- Agent and queue legs that no dimension version covers: the device number is not in the configuration at the
-- leg's start -- the agent or queue was removed from ICP's configuration while calls still reached it, or the
-- configuration was changed before the calls of the old one had ended (DD-94). The link is correctly NULL (a closed
-- version is not stretched), and fact_call_leg_dimension_links cannot see it: the leg and the dimension agree on
-- "nothing". A warning, so that someone looks: how such legs count in the metrics is not decided yet (NOTES
-- 2026-10-28). Silent until the dimension has any row, or every leg written before if03d would be listed.
-- One row per device: how many legs, first and last.
{{ config(severity='warn') }}
select f.device_type_code, f.device_no, count(*) as legs, min(f.wait_begin) as first_leg, max(f.wait_begin) as last_leg
  from {{ ref('fact_call_leg') }} f
 where (f.device_type_code = 2 and f.agent_sk is null and exists (select 1 from {{ ref('dim_agent') }}))
    or (f.device_type_code = 1 and f.queue_sk is null and exists (select 1 from {{ ref('dim_queue') }}))
 group by 1, 2
