{#- Reference data from ICP snapshots into core dimensions and meta.reference_value (contract if03d · DD-94 · DD-12).

    Which snapshot is in force, per scope (ingest_batch.snapshot_scope, e.g. agents@1/1):
      - a processable snapshot this model has not applied yet, newer than every one it has applied: the oldest such
        one is applied in this run ("new") -- one version per run, oldest first, so a rename at v14 undone at v15 is
        kept as two versions rather than skipped
      - otherwise the newest one applied ("in force, not new"): nothing from the source changes, but an M-10 role
        mark set since is still applied (DD-94 item six)
    A released snapshot older than one already applied is registered and never applied (DD-82 item four).

    How a dimension changes (SCD2, DD-12): the current version is closed (valid_to) and a new one inserted; rows are
    never rewritten or deleted (change 0007 allows dbt nothing else). The time of a change:
      - an entity seen for the first time ever: valid from the sentinel 1970-01-01 00:00 UTC -- the first version
        covers the time before it was observed (DD-94 item five)
      - a change from a new snapshot: the gateway sync's endTime (stg_icp_reference.synced_at)
      - a change from an M-10 role mark: the mark's set_at; a cleared mark: now()
      always later than the previous version's valid_from, so versions never overlap or share a valid_from.
    An entity missing from a NEW snapshot is closed at that snapshot's sync time; a snapshot already applied never
    closes anything.

    The pieces below are used by a model and its hooks with the same arguments, so the pre-hook (close) and the model
    (insert) compute the same change time: both read "the latest version" (current or closed), which the pre-hook's
    closing does not move.
-#}

{#- Snapshots of a dataset a model is to use now: run_id, batch_seq, snapshot_scope, snapshot_version, synced_at,
    is_new. Only batches staging has taken (registered for stg_icp_reference) count. -#}
{% macro ref_snapshot_in_force(dataset, model_name) -%}
    with cand as (
        select b.run_id, b.batch_seq, b.snapshot_scope, b.snapshot_version,
               coalesce(b.snapshot_synced_at, b.committed_at) as synced_at,
               exists (select 1 from meta.staging_batch_state s
                        where s.model = '{{ model_name }}' and s.run_id = b.run_id and s.batch_seq = b.batch_seq) as applied
          from meta.ingest_batch b
         where b.source_id = 'icp_reference' and b.mode = 'snapshot'
           and split_part(b.snapshot_scope, '@', 1) = '{{ dataset }}'
           and not exists (select 1 from meta.quarantine_batch q
                            where q.run_id = b.run_id and q.batch_seq = b.batch_seq and q.state in ('pending', 'discarded'))
           and exists (select 1 from meta.staging_batch_state s
                        where s.model = 'stg_icp_reference' and s.run_id = b.run_id and s.batch_seq = b.batch_seq)
    ),
    done as (
        select snapshot_scope, max(snapshot_version) as v from cand where applied group by snapshot_scope
    ),
    pending as (
        select distinct on (c.snapshot_scope) c.run_id, c.batch_seq, c.snapshot_scope, c.snapshot_version, c.synced_at
          from cand c left join done d on d.snapshot_scope = c.snapshot_scope
         where not c.applied and c.snapshot_version > coalesce(d.v, -1)
         order by c.snapshot_scope, c.snapshot_version, c.batch_seq
    ),
    current_applied as (
        select distinct on (c.snapshot_scope) c.run_id, c.batch_seq, c.snapshot_scope, c.snapshot_version, c.synced_at
          from cand c
         where c.applied and not exists (select 1 from pending p where p.snapshot_scope = c.snapshot_scope)
         order by c.snapshot_scope, c.snapshot_version desc, c.batch_seq desc
    )
    select p.*, true as is_new from pending p
    union all
    select a.*, false as is_new from current_applied a
{%- endmacro %}

{#- Post-hook: register the snapshot applied in this run, and every older processable one not applied (a late
    release); newer ones wait for the next runs. -#}
{% macro register_snapshot_batches(dataset) -%}
    insert into meta.staging_batch_state (model, run_id, batch_seq)
    select '{{ this.identifier }}', b.run_id, b.batch_seq
      from meta.ingest_batch b
      join ({{ ref_snapshot_in_force(dataset, this.identifier) }}) f on f.snapshot_scope = b.snapshot_scope
     where b.source_id = 'icp_reference' and b.mode = 'snapshot'
       and b.snapshot_version <= f.snapshot_version
       and not exists (select 1 from meta.quarantine_batch q
                        where q.run_id = b.run_id and q.batch_seq = b.batch_seq and q.state in ('pending', 'discarded'))
       and exists (select 1 from meta.staging_batch_state s
                    where s.model = 'stg_icp_reference' and s.run_id = b.run_id and s.batch_seq = b.batch_seq)
       and not exists (select 1 from meta.staging_batch_state s
                        where s.model = '{{ this.identifier }}' and s.run_id = b.run_id and s.batch_seq = b.batch_seq)
{%- endmacro %}

{#- Per natural key: what is to happen to the dimension. desired: the model's SELECT of src_natural_key, is_new,
    synced_at, override_at and the attribute columns; snap: ref_snapshot_in_force(...); override_attrs: the attributes
    set in M-10 rather than by the source (dim_agent: role_code). Output columns: nk, action (new / change / gone /
    same), change_at, current_sk, plus the desired columns (NULL for a gone entity).

    change_at, compared with the entity's latest version (current, or closed -- by an earlier run, or by this run's
    pre-hook, which leaves valid_from alone):
      never seen before                                      the sentinel (DD-94 item five)
      gone from a new snapshot                               that snapshot's sync time
      back in a new snapshot after being gone                that snapshot's sync time
      a source attribute differs in a new snapshot           that snapshot's sync time
      otherwise (only an M-10 attribute differs)             the mark's set_at, or now() for a cleared mark
    never earlier than the latest version's valid_from + 1 second. -#}
{% macro scd2_changes(desired, snap, attrs, sk, override_attrs=[]) -%}
    {%- set source_attrs = attrs | reject('in', override_attrs) | list -%}
    with d as ({{ desired }}),
    cur as (
        select * from {{ this }}
         where site_sk = core.site_namespace('{{ var("site_code") }}') and valid_to = 'infinity'
    ),
    lat as (
        select distinct on (src_natural_key) *
          from {{ this }}
         where site_sk = core.site_namespace('{{ var("site_code") }}')
         order by src_natural_key, valid_from desc
    ),
    fresh as (
        select max(synced_at) as synced_at from ({{ snap }}) f where f.is_new
    )
    select coalesce(d.src_natural_key, c.src_natural_key)                       as nk,
           case when d.src_natural_key is null then 'gone'
                when c.src_natural_key is null then 'new'
                when row({% for a in attrs %}d.{{ a }}{{ ", " if not loop.last }}{% endfor %})
                     is distinct from
                     row({% for a in attrs %}c.{{ a }}{{ ", " if not loop.last }}{% endfor %}) then 'change'
                else 'same' end                                                  as action,
           case when l.src_natural_key is null then '1970-01-01 00:00:00+00'::timestamptz
                else greatest(
                    case when d.src_natural_key is null then (select synced_at from fresh)
                         when l.valid_to <> 'infinity' and l.built_at < now() then d.synced_at
                         when d.is_new and row({% for a in source_attrs %}d.{{ a }}{{ ", " if not loop.last }}{% endfor %})
                              is distinct from
                              row({% for a in source_attrs %}l.{{ a }}{{ ", " if not loop.last }}{% endfor %}) then d.synced_at
                         else coalesce(d.override_at, now()) end,
                    l.valid_from + interval '1 second') end                      as change_at,
           c.{{ sk }}                                                            as current_sk,
           d.*
      from d
      full join cur c on c.src_natural_key = d.src_natural_key
      left join lat l on l.src_natural_key = coalesce(d.src_natural_key, c.src_natural_key)
     -- an entity missing from the snapshot is gone only if that snapshot is new
     where d.src_natural_key is not null or (select synced_at from fresh) is not null
{%- endmacro %}

{#- Pre-hook: close the current version of every changed or gone entity. -#}
{% macro scd2_close(desired, snap, attrs, sk, override_attrs=[]) -%}
    update {{ this }} t
       set valid_to = x.change_at, built_at = now()
      from ({{ scd2_changes(desired, snap, attrs, sk, override_attrs) }}) x
     where t.{{ sk }} = x.current_sk and x.action in ('change', 'gone')
{%- endmacro %}

{#- Post-hook: one audit row per changed field (LD-01 9.3 · DD-94 item eleven). This run's rows are the ones with
    built_at = now(): dbt runs a model's hooks and its insert in one transaction. -#}
{% macro scd2_log(attrs, sk) -%}
    with ins as (
        select * from {{ this }} where built_at = now() and valid_to = 'infinity'
    ),
    closed as (
        select * from {{ this }} where built_at = now() and valid_to <> 'infinity'
    )
    insert into audit.reference_data_change_log (target_table, target_key, field_name, old_value, new_value,
                                                 changed_by, reason)
    {% for a in attrs %}
    select '{{ this.schema }}.{{ this.identifier }}', core.nk(i.src_natural_key), '{{ a }}', p.{{ a }}::text,
           i.{{ a }}::text, 'icp_sync', 'new version valid from ' || i.valid_from
      from ins i
      left join closed p on p.src_natural_key = i.src_natural_key
     where (p.{{ sk }} is null and i.{{ a }} is not null)
        or (p.{{ sk }} is not null and p.{{ a }} is distinct from i.{{ a }})
    union all
    {% endfor %}
    select '{{ this.schema }}.{{ this.identifier }}', core.nk(p.src_natural_key), 'is_current', 'true', 'false', 'icp_sync',
           'no longer in the source snapshot; closed at ' || p.valid_to
      from closed p
     where not exists (select 1 from ins i where i.src_natural_key = p.src_natural_key)
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
