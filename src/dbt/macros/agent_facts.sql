{#- Pieces of core.fact_agent_session and core.fact_agent_day (contracts if03b / if03c · DD-98 · DD-95 · DD-82).

    Both are written like fact_call_leg (macros/fact_call_leg.sql): per key the newest new observation, written only
    when core has no row for the key, or when its content differs and the observation is not older than the one the
    row was built from (src_observed_at). Only changed rows are written (DD-78).

    The agent link (DD-98 item six):
      sign-in interval   the dim_agent version valid at signin_at, as for call legs
      day total          the version valid at the end of the day; for an agent removed during the day, its last version
                         valid that day -- both are "the latest version that overlaps the day", versions never overlap
    An agent no version covers stays unlinked (agent_sk NULL, agent_no kept), one clarification item per ID (DD-95);
    dim_agent relinks both tables when its versions change, like the call legs (relink_agent_facts).
-#}

{#- The day totals: index -> column of core.fact_agent_day, as contract if03c's columns say. The test
    agent_day_columns_match_contract compares the two. -#}
{% macro agent_day_columns() -%}
    {{ return([
        ('IDX_04_02_001', 'signin_count'), ('IDX_04_02_002', 'signin_seconds'),
        ('IDX_04_02_003', 'inbound_calls'), ('IDX_04_02_004', 'inbound_seconds'),
        ('IDX_04_02_006', 'outbound_calls'), ('IDX_04_02_007', 'outbound_seconds'),
        ('IDX_04_02_010', 'acw_seconds'), ('IDX_04_02_013', 'rest_seconds'),
        ('IDX_04_02_016', 'hold_seconds'), ('IDX_04_02_019', 'busy_seconds'),
        ('IDX_04_02_021', 'talk_seconds'), ('IDX_04_02_022', 'idle_seconds'),
        ('IDX_04_02_023', 'internal_xfer_count'), ('IDX_04_02_024', 'transfer_out_count'),
    ]) }}
{%- endmacro %}

{#- Derived content of a row, as one hash; lineage and bookkeeping are not content (as leg_content_hash). -#}
{% macro agent_session_content_hash(a) -%}
    md5(row({{a}}.date_key, {{a}}.site_sk, {{a}}.agent_sk, {{a}}.agent_no, {{a}}.signin_at, {{a}}.signout_at,
            {{a}}.duration_seconds)::text)
{%- endmacro %}
{% macro agent_day_content_hash(a) -%}
    md5(row({{a}}.date_key, {{a}}.site_sk, {{a}}.agent_sk, {{a}}.agent_no
            {%- for _, col in agent_day_columns() %}, {{a}}.{{ col }}{% endfor %}, {{a}}.source_kind)::text)
{%- endmacro %}

{#- New observations of a source for a core model: staging rows of batches the model has not registered. Rows that
    cannot be placed -- no agent ID, no sign-in time -- exist only in batches released from quarantine and stay
    visible in staging. -#}
{% macro agent_observations(stg_model, source_id, model_name, key_not_null) -%}
    select s.*
      from {{ ref(stg_model) }} s
      join ({{ unprocessed_batches(source_id, model_name) }}) b
        on b.run_id = s.run_id and b.batch_seq = s.batch_seq
     where s.agent_no is not null and {{ key_not_null }}
{%- endmacro %}

{#- The version of agent no valid at time at (sign-in intervals) -#}
{% macro agent_at(no, at) -%}
    (select d.agent_sk from core.dim_agent d
      where d.site_sk = core.site_namespace('{{ var("site_code") }}') and d.src_natural_key = {{ no }}
        and {{ at }} >= d.valid_from and {{ at }} < d.valid_to)
{%- endmacro %}

{#- The latest version of agent no that overlaps day date_key in the report time zone (day totals) -#}
{% macro agent_on_day(no, date_key) -%}
    (select d.agent_sk from core.dim_agent d
      where d.site_sk = core.site_namespace('{{ var("site_code") }}') and d.src_natural_key = {{ no }}
        and d.valid_from < ((to_date({{ date_key }}::text, 'YYYYMMDD') + 1)::timestamp
                            at time zone '{{ var("report_timezone") }}')
        and d.valid_to > (to_date({{ date_key }}::text, 'YYYYMMDD')::timestamp at time zone '{{ var("report_timezone") }}')
      order by d.valid_from desc limit 1)
{%- endmacro %}

{#- Pre-hook: monthly partitions for the days the new observations fall on (DD-77), as for the call legs.
    kind: session (the day of signin_at) or day (date_key). -#}
{% macro ensure_agent_partitions(kind) -%}
    {%- if kind == 'session' -%}
    select meta.ensure_monthly_partitions('core.fact_agent_session',
               min((o.signin_at at time zone '{{ var("report_timezone") }}')::date),
               max((o.signin_at at time zone '{{ var("report_timezone") }}')::date))
      from ({{ agent_observations('stg_icp_agent_session', 'icp_agent_session', 'fact_agent_session',
                                  's.signin_at is not null') }}) o
    {%- else -%}
    select meta.ensure_monthly_partitions('core.fact_agent_day',
               min(to_date(o.date_key::text, 'YYYYMMDD')), max(to_date(o.date_key::text, 'YYYYMMDD')))
      from ({{ agent_observations('stg_icp_agent_day', 'icp_agent_day', 'fact_agent_day', 's.date_key is not null') }}) o
    {%- endif -%}
{%- endmacro %}

{#- Post-hook of the two models: register the staging batches taken into core, and the staged batches that wrote no
    row to raw (rows_written = 0, as register_core_batches does for the call legs). -#}
{% macro register_agent_batches(stg_model, source_id) -%}
    insert into meta.staging_batch_state (model, run_id, batch_seq)
    select '{{ this.identifier }}', x.run_id, x.batch_seq
      from (select distinct s.run_id, s.batch_seq from {{ ref(stg_model) }} s
            union
            select b.run_id, b.batch_seq
              from meta.ingest_batch b
              join meta.staging_batch_state t
                on t.model = '{{ stg_model }}' and t.run_id = b.run_id and t.batch_seq = b.batch_seq
             where b.source_id = '{{ source_id }}' and b.rows_written = 0) x
     where not exists (select 1 from meta.staging_batch_state r
                        where r.model = '{{ this.identifier }}' and r.run_id = x.run_id and r.batch_seq = x.batch_seq)
{%- endmacro %}

{#- Post-hook of the two models and of dim_agent: one open clarification item per agent ID that no dimension version
    covers in the agent facts (DD-95 · DD-98 item five), not raised again while one is open. Silent while dim_agent is
    empty. Read through the partial indexes on agent_sk IS NULL (change 0015). -#}
{% macro raise_unlinked_agent_fact_items() -%}
    {%- for table, what in [('core.fact_agent_session', 'sign-in intervals, not in the configuration at sign-in'),
                            ('core.fact_agent_day', 'day totals, not in the configuration that day')] %}
    insert into meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
    select 'icp_reference', 'agentNo of {{ what }} (DD-95)', f.agent_no, count(*)
      from {{ table }} f
     where f.agent_sk is null
       and exists (select 1 from core.dim_agent)
       and not exists (select 1 from meta.clarification_queue q
                        where q.source_id = 'icp_reference' and q.src_value = f.agent_no
                          and q.src_column = 'agentNo of {{ what }} (DD-95)' and q.status in ('open', 'asked'))
     group by f.agent_no;
    {%- endfor %}
{%- endmacro %}

{#- Post-hook of dim_agent: rows of the two agent fact tables point to the version their rule picks. Only rows of the
    agents this run touched (all_keys=false), and only where the link really changes (DD-78). -#}
{% macro relink_agent_facts(all_keys=false) -%}
    {%- set touched = "and f.agent_no in (select src_natural_key from core.dim_agent where built_at = now())"
                      if not all_keys else "" -%}
    update core.fact_agent_session f
       set agent_sk = x.new_sk, last_change_at = now(), built_at = now()
      from (select g.agent_session_sk, g.date_key, {{ agent_at('g.agent_no', 'g.signin_at') }} as new_sk
              from core.fact_agent_session g
             where true {{ touched | replace('f.', 'g.') }}) x
     where f.agent_session_sk = x.agent_session_sk and f.date_key = x.date_key
       and f.agent_sk is distinct from x.new_sk;
    update core.fact_agent_day f
       set agent_sk = x.new_sk, last_change_at = now(), built_at = now()
      from (select g.agent_day_sk, g.date_key, {{ agent_on_day('g.agent_no', 'g.date_key') }} as new_sk
              from core.fact_agent_day g
             where true {{ touched | replace('f.', 'g.') }}) x
     where f.agent_day_sk = x.agent_day_sk and f.date_key = x.date_key
       and f.agent_sk is distinct from x.new_sk
{%- endmacro %}
