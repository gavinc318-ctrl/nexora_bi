{#- Pieces of core.fact_call_leg used by the model and its hooks (DD-91 items 7, 8, 9). -#}

{#- The derived content of a call-leg row, as one hash. Lineage (run_id, contract_version, src_watermark,
    src_observed_at) and bookkeeping (correction_seq, last_change_at, built_at) are not content: a newer observation
    with the same content does not rewrite the row (DD-82 item 4), and a contract version bump alone rewrites nothing.
    Applied to the candidate and to the existing core row with the same column types, so equal content gives an
    equal hash. -#}
{% macro leg_content_hash(a) -%}
    md5(row({{a}}.date_key, {{a}}.site_sk, {{a}}.call_sk, {{a}}.src_natural_key, {{a}}.call_id, {{a}}.leg_seq,
            {{a}}.is_last_leg, {{a}}.device_type_code, {{a}}.device_no, {{a}}.device_in_code, {{a}}.agent_sk,
            {{a}}.queue_sk, {{a}}.queue_code, {{a}}.call_type_code, {{a}}.direction, {{a}}.caller_number,
            {{a}}.caller_number_hash, {{a}}.callee_number, {{a}}.wait_begin, {{a}}.wait_end, {{a}}.ack_begin,
            {{a}}.ack_end, {{a}}.call_begin, {{a}}.call_end, {{a}}.wait_seconds, {{a}}.talk_seconds,
            {{a}}.wait_cause_code, {{a}}.release_cause_code, {{a}}.release_source, {{a}}.enter_reason_code,
            {{a}}.leave_reason_code, {{a}}.source_tz_offset)::text)
{%- endmacro %}

{#- Observations core has not taken yet: staging rows of batches not registered for fact_call_leg. Rows without a
    complete key or without WAITBEGIN cannot be placed (core.nk() raises on a NULL part by design, LD-01 2.2; date_key
    needs WAITBEGIN); they exist only in batches released from quarantine, and stay visible in staging. -#}
{% macro leg_observations() -%}
    select s.*,
           core.nk(s.call_id, s.leg_seq::text)                                       as src_natural_key
      from {{ ref('stg_icp_cdr_voice') }} s
      join ({{ unprocessed_batches('icp_cdr_voice', 'fact_call_leg') }}) b
        on b.run_id = s.run_id and b.batch_seq = s.batch_seq
     where s.call_id is not null and s.leg_seq is not null and s.wait_begin is not null
{%- endmacro %}

{#- The 24-hour guard (Mac side 2026-10-13 · DD-91 items 8, 9): the same (CALLID, CALLIDNUM) seen with WAITBEGIN more
    than 24 hours apart -- among the new observations, or against the row already in core -- is a key that may repeat
    (CALLID's uniqueness scope is unanswered, ICP question Q20), not a new version of one leg. Not written to core;
    raised as a clarification item. Without the guard two different calls would silently merge into one. -#}
{% macro leg_guard_keys() -%}
    select o.src_natural_key, count(*) as observations
      from ({{ leg_observations() }}) o
      left join {{ this }} e
        on e.call_leg_sk = core.sk('{{ var("site_code") }}', 'icp', o.src_natural_key)
     group by o.src_natural_key
    having greatest(max(o.wait_begin), max(e.wait_begin)) - least(min(o.wait_begin), min(e.wait_begin))
           > interval '24 hours'
{%- endmacro %}

{#- Post-hook: one open clarification item per suspicious key (the same rule as ingestion's: an item is raised once,
    its count is the one first seen). -#}
{% macro raise_leg_guard_items() -%}
    insert into meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
    select 'icp_cdr_voice', 'CALLID|CALLIDNUM (WAITBEGIN more than 24h apart)', g.src_natural_key, g.observations
      from ({{ leg_guard_keys() }}) g
     where not exists (select 1 from meta.clarification_queue q
                        where q.source_id = 'icp_cdr_voice' and q.src_value = g.src_natural_key
                          and q.src_column = 'CALLID|CALLIDNUM (WAITBEGIN more than 24h apart)'
                          and q.status in ('open', 'asked'))
{%- endmacro %}

{#- Post-hook: register the staging batches this run has taken into core. Exact because dbt runs one at a time
    (pool dbt_build, one slot, DD-85): nothing writes staging between this model's read and this hook. -#}
{% macro register_core_batches() -%}
    insert into meta.staging_batch_state (model, run_id, batch_seq)
    select distinct '{{ this.identifier }}', s.run_id, s.batch_seq
      from {{ ref('stg_icp_cdr_voice') }} s
     where not exists (select 1 from meta.staging_batch_state r
                        where r.model = '{{ this.identifier }}' and r.run_id = s.run_id and r.batch_seq = s.batch_seq)
{%- endmacro %}

{#- Pre-hook: monthly partitions for every day the new observations fall on (DD-77 · DD-91 item 11), so a backfill of
    an old month does not fail on a missing partition. The daily maintenance creates the months ahead. -#}
{% macro ensure_leg_partitions() -%}
    select meta.ensure_monthly_partitions('core.fact_call_leg',
               min((o.wait_begin at time zone '{{ var("report_timezone") }}')::date),
               max((o.wait_begin at time zone '{{ var("report_timezone") }}')::date))
      from ({{ leg_observations() }}) o
{%- endmacro %}
