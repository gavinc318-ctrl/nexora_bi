{#- core.fact_call from core.fact_call_leg (DD-66 · DD-96 · LD-03 2.4). See models/core/fact_call.sql. -#}

{#- The calls to rebuild: those with a leg written or relinked since fact_call last wrote. last_change_at is the
    start of the writing transaction, not its commit, so a leg build that started before fact_call's last write but
    committed after it carries an earlier time; the day of margin covers that (a build does not wait a day for the
    lock). Correctness comes from comparing content (DD-78); the time only narrows the scan (as DD-89). -#}
{% macro calls_to_rebuild() -%}
    select distinct l.call_sk
      from {{ ref('fact_call_leg') }} l
     where l.last_change_at > (select coalesce(max(f.built_at), '-infinity'::timestamptz) from {{ this }} f)
                              - interval '1 day'
{%- endmacro %}

{#- The call-level columns derived from the legs; lineage (run_id, contract_version, src_watermark) and build times
    are not content. -#}
{% macro call_content_hash(a) -%}
    md5(row({{a}}.date_key, {{a}}.site_sk, {{a}}.src_natural_key, {{a}}.call_type_code, {{a}}.direction,
            {{a}}.caller_number, {{a}}.caller_number_hash, {{a}}.first_queue_sk, {{a}}.first_queue_code, {{a}}.agent_sk,
            {{a}}.call_result_sk, {{a}}.offered_at, {{a}}.answered_at, {{a}}.ended_at, {{a}}.wait_seconds,
            {{a}}.talk_seconds, {{a}}.leg_count, {{a}}.agent_leg_count, {{a}}.answered_leg_count, {{a}}.ring_count,
            {{a}}.released_by, {{a}}.is_transferred, {{a}}.is_malicious, {{a}}.source_tz_offset, {{a}}.is_complete)::text)
{%- endmacro %}

{#- Pre-hook: monthly partitions for the days the rebuilt calls fall on (their first leg's day). -#}
{% macro ensure_call_partitions() -%}
    select meta.ensure_monthly_partitions('core.fact_call',
               min((l.wait_begin at time zone '{{ var("report_timezone") }}')::date),
               max((l.wait_begin at time zone '{{ var("report_timezone") }}')::date))
      from {{ ref('fact_call_leg') }} l
     where l.leg_seq = 1 and l.call_sk in ({{ calls_to_rebuild() }})
{%- endmacro %}

{#- Post-hook: the legs of one CALLID disagree on CALLTYPE or direction (DD-96 item four) -- the call takes its first
    leg's; one clarification item per CALLID, not raised again while open. Checked over every call this run looked
    at, written or not: a later leg that disagrees leaves the call's own columns unchanged. -#}
{% macro raise_call_type_items() -%}
    insert into meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
    select 'icp_cdr_voice', 'CALLTYPE / direction (legs of one CALLID disagree)', f.src_natural_key, count(*)
      from {{ this }} f
      join {{ ref('fact_call_leg') }} l on l.call_sk = f.call_sk
     where f.call_sk in ({{ calls_to_rebuild() }})
       and not exists (select 1 from meta.clarification_queue q
                        where q.source_id = 'icp_cdr_voice' and q.src_value = f.src_natural_key
                          and q.src_column = 'CALLTYPE / direction (legs of one CALLID disagree)'
                          and q.status in ('open', 'asked'))
     group by f.src_natural_key
    having count(distinct l.call_type_code) > 1 or count(distinct l.direction) > 1
        or (count(l.call_type_code) > 0 and count(l.call_type_code) < count(*))
{%- endmacro %}
