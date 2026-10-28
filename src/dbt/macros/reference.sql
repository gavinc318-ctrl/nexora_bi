{#- Reference data from ICP snapshots into core dimensions and meta.reference_value (contract if03d · DD-94 · DD-12).

    Only the snapshots of the contract's site_vdn are used (DD-94 item seventeen): the scope of a dataset is
    "<dataset>@<ccId>/<vdnId>" ("vdns@<ccId>" for the CTI-level dataset); snapshots of any other CTI or VDN stay in
    raw and in the clarification queue (ingestion raises it).

    A snapshot is pending for a model when it is processable, staged, not applied by the model yet, and newer than
    every one the model has applied. A released snapshot older than one already applied is registered and never
    applied (DD-82 item four).

    The dimensions (dim_agent, dim_queue) apply every pending snapshot in one run, and cut versions at the union of
    the change points of two sources (LD-02 chapter 7, third rule · DD-94 item sixteen):
      - each pending snapshot, at its gateway sync time (endTime; DD-94 item five)
      - each start and end of an M-10 role mark (meta.agent_role_override, dim_agent only), once it is not in the future
    At each point the state of an entity is evaluated -- the source attributes of the newest snapshot synced by
    then (or the latest version's, if none), the role of the mark in force then (call_taker without one) -- and a
    version starts wherever the state differs from the one before: one version per change point, never two changes
    of different times merged into one. Absent from a snapshot = gone: the version is closed at that point.
    Rows are only inserted and closed (change 0007). So nothing is cut before the entity's latest version: a point
    earlier than that (a mark entered with a past valid_from, a snapshot synced before a mark already applied) takes
    effect one second after the latest version's valid_from -- the "catch-up" point every entity is evaluated at.
    An entity seen for the first time ever starts at the sentinel 1970-01-01 00:00 UTC (DD-94 item five).

    meta.reference_value is not SCD2 and still applies one pending snapshot per run, oldest first (ref_snapshot_in_force).
-#}

{#- The site's scope of a dataset, from the contract (endpoint.site_vdn). -#}
{% macro site_scope(dataset) -%}
    ('{{ dataset }}@' || {{ contract_text('icp_reference', 'endpoint,site_vdn,cc_id') }}
     {%- if dataset != 'vdns' %} || '/' || {{ contract_text('icp_reference', 'endpoint,site_vdn,vdn_id') }}{% endif %})
{%- endmacro %}

{#- Processable, staged snapshots of the site's scope of a dataset: run_id, batch_seq, snapshot_version, synced_at,
    applied (registered for model_name). -#}
{% macro ref_snapshot_candidates(dataset, model_name) -%}
    select b.run_id, b.batch_seq, b.snapshot_scope, b.snapshot_version,
           coalesce(b.snapshot_synced_at, b.committed_at) as synced_at,
           exists (select 1 from meta.staging_batch_state s
                    where s.model = '{{ model_name }}' and s.run_id = b.run_id and s.batch_seq = b.batch_seq) as applied
      from meta.ingest_batch b
     where b.source_id = 'icp_reference' and b.mode = 'snapshot'
       and b.snapshot_scope = {{ site_scope(dataset) }}
       and not exists (select 1 from meta.quarantine_batch q
                        where q.run_id = b.run_id and q.batch_seq = b.batch_seq and q.state in ('pending', 'discarded'))
       and exists (select 1 from meta.staging_batch_state s
                    where s.model = 'stg_icp_reference' and s.run_id = b.run_id and s.batch_seq = b.batch_seq)
{%- endmacro %}

{#- Every pending snapshot, oldest first. -#}
{% macro ref_snapshots_pending(dataset, model_name) -%}
    with cand as ({{ ref_snapshot_candidates(dataset, model_name) }})
    select c.run_id, c.batch_seq, c.snapshot_version, c.synced_at
      from cand c
     where not c.applied
       and c.snapshot_version > coalesce((select max(a.snapshot_version) from cand a where a.applied), -1)
{%- endmacro %}

{#- meta.reference_value: the one snapshot in force -- the oldest pending one ("new"), or else the newest applied. -#}
{% macro ref_snapshot_in_force(dataset, model_name) -%}
    with cand as ({{ ref_snapshot_candidates(dataset, model_name) }}),
    pending as (
        select c.run_id, c.batch_seq, c.snapshot_scope, c.snapshot_version, c.synced_at
          from cand c
         where not c.applied
           and c.snapshot_version > coalesce((select max(a.snapshot_version) from cand a where a.applied), -1)
         order by c.snapshot_version, c.batch_seq
         limit 1
    ),
    current_applied as (
        select c.run_id, c.batch_seq, c.snapshot_scope, c.snapshot_version, c.synced_at
          from cand c
         where c.applied and not exists (select 1 from pending)
         order by c.snapshot_version desc, c.batch_seq desc
         limit 1
    )
    select p.*, true as is_new from pending p
    union all
    select a.*, false as is_new from current_applied a
{%- endmacro %}

{#- Post-hook: register what this run applied -- every pending snapshot (all_pending, the dimensions) or the one in
    force (meta.reference_value) -- and every older processable one not applied (a late release). -#}
{% macro register_snapshot_batches(dataset, all_pending=false) -%}
    insert into meta.staging_batch_state (model, run_id, batch_seq)
    select '{{ this.identifier }}', c.run_id, c.batch_seq
      from ({{ ref_snapshot_candidates(dataset, this.identifier) }}) c
     where not c.applied
       and c.snapshot_version <= (select max(f.snapshot_version) from (
               {%- if all_pending %}{{ ref_snapshots_pending(dataset, this.identifier) }}
               {%- else %}{{ ref_snapshot_in_force(dataset, this.identifier) }}{% endif %}) f)
{%- endmacro %}

{#- The changes of a dimension in this run, one row per change point of an entity (see the header).
      dataset       the snapshot dataset
      source_sql    SELECT of nk, run_id, batch_seq, contract_version and the source attributes, from staging
      src_attrs     the source attributes
      mark_sql      SELECT of nk, valid_from, valid_to and the mark attributes (or none)
      mark_attrs    [(attribute, value without a mark)]
    Output: nk, at (the change point), next_at (the entity's next change point in this run, or NULL), present,
    prev_present, the attributes and prev_<attribute>, is_first, base_sk / base_open (the entity's latest version
    and whether it is the current one), run_id, contract_version, reason.
    Computed the same in the pre-hooks and in the model: "the latest version" is read whether current or closed, and
    the pre-hook's closing (built_at = now()) is recognised, so the close does not change the result. -#}
{% macro scd2_timeline(dataset, source_sql, src_attrs, sk, mark_sql=none, mark_attrs=[]) -%}
    {%- set m_names = mark_attrs | map(attribute=0) | list -%}
    {%- set attrs = src_attrs + m_names -%}
    with pend as (
        select p.run_id, p.batch_seq, p.snapshot_version,
               max(p.synced_at) over (order by p.snapshot_version, p.batch_seq) as at
          from ({{ ref_snapshots_pending(dataset, this.identifier) }}) p
    ),
    src as (
        select x.*, p.snapshot_version
          from ({{ source_sql }}) x
          join pend p on p.run_id = x.run_id and p.batch_seq = x.batch_seq
    ),
    lat as (
        select distinct on (src_natural_key) src_natural_key as nk, {{ sk }} as sk, valid_from, valid_to,
               (valid_to = 'infinity' or built_at = now()) as present,
               {% for a in attrs %}{{ a }}, {% endfor %}run_id, contract_version
          from {{ this }}
         where site_sk = core.site_namespace('{{ var("site_code") }}')
         order by src_natural_key, valid_from desc
    ),
    base as (
        select e.nk, l.sk, (l.nk is null) as is_new, coalesce(l.present, false) as present,
               (l.valid_to = 'infinity') as open,
               coalesce(l.valid_from, '1970-01-01 00:00:00+00'::timestamptz - interval '1 second') as l_from,
               {% for a in attrs %}case when l.present then l.{{ a }} end as {{ a }}, {% endfor %}
               l.run_id, l.contract_version
          from (select nk from lat union select nk from src) e
          left join lat l on l.nk = e.nk
    ),
    -- per entity and pending snapshot: in it or not, from when (an entity never seen before: its first snapshot
    -- holds from the sentinel)
    st as (
        select b.nk, p.snapshot_version, p.run_id as snap_run_id,
               case when b.is_new and p.snapshot_version = (select min(s2.snapshot_version) from src s2 where s2.nk = b.nk)
                    then '1970-01-01 00:00:00+00'::timestamptz else p.at end as eff,
               (s.nk is not null) as present,
               {% for a in src_attrs %}s.{{ a }}, {% endfor %}s.run_id, s.contract_version
          from base b
          cross join pend p
          left join src s on s.nk = b.nk and s.snapshot_version = p.snapshot_version
    ),
    {% if mark_sql %}
    marks as ({{ mark_sql }}),
    {% endif %}
    pts as (
        select b.nk, b.l_from + interval '1 second' as p from base b
        union
        select st.nk, st.eff from st join base b on b.nk = st.nk where st.eff > b.l_from + interval '1 second'
        {% if mark_sql %}
        union
        select m.nk, m.valid_from from marks m join base b on b.nk = m.nk
         where m.valid_from > b.l_from + interval '1 second' and m.valid_from <= now()
        union
        select m.nk, m.valid_to from marks m join base b on b.nk = m.nk
         where m.valid_to > b.l_from + interval '1 second' and m.valid_to <= now()
        {% endif %}
    ),
    state as (
        select pt.nk, pt.p,
               coalesce(ss.present, b.present) as present,
               {% for a in src_attrs %}case when ss.nk is null then b.{{ a }} when ss.present then ss.{{ a }} end as {{ a }},
               {% endfor %}
               {% for a, dflt in mark_attrs %}coalesce(mm.{{ a }}, {{ dflt }}) as {{ a }},
               {% endfor %}
               coalesce(ss.run_id, ss.snap_run_id, b.run_id) as run_id, coalesce(ss.contract_version, b.contract_version) as contract_version,
               case when ss.eff = pt.p or (ss.nk is not null and pt.p = b.l_from + interval '1 second')
                    then 'snapshot v' || ss.snapshot_version end as by_snapshot,
               {% if mark_sql %}case when mm.nk is not null and mm.valid_from < pt.p and pt.p = b.l_from + interval '1 second'
                    then mm.valid_from end{% else %}null::timestamptz{% endif %} as late_mark_from
          from pts pt
          join base b on b.nk = pt.nk
          left join lateral (select * from st where st.nk = pt.nk and st.eff <= pt.p
                              order by st.snapshot_version desc limit 1) ss on true
          {% if mark_sql %}
          left join lateral (select * from marks m where m.nk = pt.nk and m.valid_from <= pt.p and pt.p < m.valid_to
                              order by m.valid_from desc limit 1) mm on true
          {% endif %}
    ),
    seq as (
        select s.*,
               lag(s.present, 1, b.present) over w as prev_present,
               {% for a in attrs %}lag(s.{{ a }}, 1, b.{{ a }}) over w as prev_{{ a }},
               {% endfor %}
               b.sk as base_sk, b.open as base_open
          from state s join base b on b.nk = s.nk
        window w as (partition by s.nk order by s.p)
    ),
    changes as (
        select * from seq
         where present is distinct from prev_present
            or (present and row({% for a in attrs %}{{ a }}{{ ", " if not loop.last }}{% endfor %})
                            is distinct from
                            row({% for a in attrs %}prev_{{ a }}{{ ", " if not loop.last }}{% endfor %}))
    )
    select c.nk, c.p as at, lead(c.p) over (partition by c.nk order by c.p) as next_at,
           c.present, c.prev_present,
           {% for a in attrs %}c.{{ a }}, c.prev_{{ a }}, {% endfor %}
           row_number() over (partition by c.nk order by c.p) = 1 as is_first,
           c.base_sk, c.base_open, c.run_id, c.contract_version,
           'version valid from ' || c.p || coalesce(' (' || c.by_snapshot || ')', ' (role mark)')
           || coalesce('; mark valid from ' || c.late_mark_from || ', versions before are not rewritten', '') as reason
      from changes c
{%- endmacro %}

{#- Pre-hook: close the current version of every entity that changes, at its first change point. -#}
{% macro scd2_close(timeline, sk) -%}
    update {{ this }} t
       set valid_to = x.at, built_at = now()
      from ({{ timeline }}) x
     where x.is_first and x.base_open and t.{{ sk }} = x.base_sk and t.valid_to = 'infinity'
{%- endmacro %}

{#- Pre-hook, before the close: one audit row per changed field (LD-01 9.3 · DD-94 item eleven), from the same
    timeline the model inserts. -#}
{% macro scd2_log(timeline, attrs) -%}
    with x as ({{ timeline }})
    insert into audit.reference_data_change_log (target_table, target_key, field_name, old_value, new_value,
                                                 changed_by, reason)
    {% for a in attrs %}
    select '{{ this.schema }}.{{ this.identifier }}', core.nk(x.nk), '{{ a }}', x.prev_{{ a }}::text, x.{{ a }}::text,
           'icp_sync', x.reason
      from x
     where x.present and (not x.prev_present or x.prev_{{ a }} is distinct from x.{{ a }})
       and (x.prev_present or x.{{ a }} is not null)
    union all
    {% endfor %}
    select '{{ this.schema }}.{{ this.identifier }}', core.nk(x.nk), 'is_current',
           x.prev_present::text, x.present::text, 'icp_sync',
           case when x.present then 'back in the source snapshot; ' || x.reason
                else 'no longer in the source snapshot; closed at ' || x.at end
      from x
     where x.present <> x.prev_present and not (x.present and x.base_sk is null)
{%- endmacro %}

{#- Post-hook: a source name changed under a confirmed translation -> pending_reconfirm (DD-56 · DD-94 item eight).
    The comparison is with the source text recorded when the translation was confirmed, with no normalisation. -#}
{% macro flag_renamed_labels(name_column) -%}
    update meta.label_override o
       set state = 'pending_reconfirm', flagged_at = now()
      from {{ this }} i
     where i.built_at = now() and i.valid_to = 'infinity'
       and o.site_code = '{{ var("site_code") }}' and o.dim_name = '{{ this.identifier }}'
       and o.src_natural_key = i.src_natural_key and o.state = 'confirmed'
       and o.source_label_at_confirm is distinct from i.{{ name_column }}
{%- endmacro %}

{#- Post-hook: call legs already in core point to the dimension version valid at their start (DD-91 item 3). A leg
    written before its dimension arrived, or whose dimension changed, is relinked here -- only legs of the entities
    this run touched, and only where the key really changes (DD-78). device_type_code: 1 skill queue, 2 agent. -#}
{% macro relink_call_legs(sk, device_type, dim=none, all_keys=false) -%}
    {%- set dim = dim or this -%}
    update core.fact_call_leg f
       set {{ sk }} = x.new_sk, last_change_at = now(), built_at = now()
      from (select g.call_leg_sk, g.date_key, d.{{ sk }} as new_sk
              from core.fact_call_leg g
              left join {{ dim }} d
                on d.site_sk = g.site_sk and d.src_natural_key = g.device_no
               and g.wait_begin >= d.valid_from and g.wait_begin < d.valid_to
             where g.device_type_code = {{ device_type }}
               {% if not all_keys %}and g.device_no in (select src_natural_key from {{ dim }} where built_at = now()){% endif %}) x
     where f.call_leg_sk = x.call_leg_sk and f.date_key = x.date_key
       and f.{{ sk }} is distinct from x.new_sk
{%- endmacro %}

{#- Repair, by hand: relink every agent and queue leg in core, not only those of entities changed in this run.
    The dimension models relink in the same transaction as their own change, so the two cannot drift apart in normal
    operation; this is for when test fact_call_leg_dimension_links fails anyway (runbook 14). Writes only legs whose
    link really changes.
        dbt run-operation relink_all_call_legs -#}
{% macro relink_all_call_legs() -%}
    {% set sql %}
    {{ relink_call_legs('agent_sk', 2, 'core.dim_agent', true) }};
    {{ relink_call_legs('queue_sk', 1, 'core.dim_queue', true) }};
    {% endset %}
    {% do run_query(sql) %}
    {% do run_query('commit') %}
    {{ log('relink_all_call_legs: done', info=true) }}
{%- endmacro %}

{#- Post-hook of fact_call_leg, dim_agent and dim_queue: a call leg whose agent or queue no dimension version covers
    at its start -- removed from ICP's configuration while calls still reached it -- stays unlinked (no sentinel
    member, no closed version stretched) and is counted in an explicit unlinked bucket by the marts; each such ID gets
    one clarification item, not raised again while one is open (DD-95). Silent while the dimension is empty, or
    every leg written before if03d would be raised. Reads the unlinked legs through the partial indexes on
    (agent_sk / queue_sk, wait_begin). device_type: 1 skill queue, 2 agent. -#}
{% macro raise_unlinked_member_items(device_type) -%}
    {%- set dim, sk, label = ('core.dim_agent', 'agent_sk', 'agentNo') if device_type == 2
                             else ('core.dim_queue', 'queue_sk', 'skillId') -%}
    insert into meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
    select 'icp_reference', '{{ label }} of call legs, not in the configuration at their start (DD-95)', f.device_no,
           count(*)
      from core.fact_call_leg f
     where f.device_type_code = {{ device_type }} and f.{{ sk }} is null and f.device_no is not null
       and exists (select 1 from {{ dim }})
       and not exists (select 1 from meta.clarification_queue q
                        where q.source_id = 'icp_reference' and q.src_value = f.device_no
                          and q.src_column = '{{ label }} of call legs, not in the configuration at their start (DD-95)'
                          and q.status in ('open', 'asked'))
     group by f.device_no
{%- endmacro %}

{#- Pre-hook, first, of fact_call_leg, dim_agent and dim_queue: one at a time, also outside Airflow.
    The links between call legs and dimensions are kept right from two sides -- fact_call_leg joins the dimension
    version valid at each new leg, and a dimension model relinks the legs already in core. If the two run at the same
    time, each can miss the other's uncommitted rows: legs written by the one, dimension versions by the other, and
    neither links them. Airflow's pool dbt_build (one slot) keeps the scheduled builds apart, but a build run by hand
    bypasses the pool (seen on the real machine, 2026-10-27). A transaction-level advisory lock serialises the three
    models whoever runs them; it is released at commit, and the second model's statements then see the first's rows.
-#}
{% macro lock_call_leg_links() -%}
    select pg_advisory_xact_lock(hashtext('oss911.call_leg_links'))
{%- endmacro %}
