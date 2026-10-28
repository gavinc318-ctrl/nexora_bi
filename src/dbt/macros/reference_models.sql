{#- The source side of each reference model: what the snapshot in force says each entity should look like
    (macros/reference.sql explains how it is applied). Kept here, not in the model files, because the models' hooks
    need them too, and hooks are rendered when they run, not when the project is parsed. -#}

{#- core.dim_agent (DD-94): agentName as received in both name columns (items eight, fourteen); team = agent group
    (item seven); role call_taker unless an M-10 mark is in force (items six, sixteen, AS-36). -#}
{% macro agent_source() -%}
    select s.src_natural_key as nk, s.run_id, s.batch_seq, s.contract_version,
           s.payload ->> 'agentName'                                   as name_ar,
           s.payload ->> 'agentName'                                   as name_en,
           s.payload ->> 'groupId'                                     as team_code
      from {{ ref('stg_icp_reference') }} s
     where s.dataset = 'agents' and s.src_natural_key is not null
{%- endmacro %}
{% macro agent_marks() -%}
    select o.agent_no as nk, o.valid_from, o.valid_to, o.role_code
      from meta.agent_role_override o
     where o.site_code = '{{ var("site_code") }}' and o.src_system = 'icp'
{%- endmacro %}
{% macro agent_src_attrs() -%}{{ return(['name_ar', 'name_en', 'team_code']) }}{%- endmacro %}
{% macro agent_mark_attrs() -%}{{ return([('role_code', "'call_taker'::text")]) }}{%- endmacro %}
{% macro agent_attrs() -%}{{ return(agent_src_attrs() + ['role_code']) }}{%- endmacro %}
{% macro agent_timeline() -%}
    {{ scd2_timeline('agents', agent_source(), agent_src_attrs(), 'agent_sk', agent_marks(), agent_mark_attrs()) }}
{%- endmacro %}

{#- core.dim_queue (DD-94): every skill (item thirteen); skillName as received in both NOT NULL name columns (item
    eight); skill_group_code NULL, skills and skill groups being many-to-many (item nine). -#}
{% macro queue_source() -%}
    select s.src_natural_key as nk, s.run_id, s.batch_seq, s.contract_version,
           s.src_natural_key                                           as code,
           coalesce(s.payload ->> 'skillName', '')                     as name_ar,
           coalesce(s.payload ->> 'skillName', '')                     as name_en,
           staging.try_int(s.payload ->> 'mediaType')::smallint        as media_type_code,
           null::text                                                  as skill_group_code
      from {{ ref('stg_icp_reference') }} s
     where s.dataset = 'skills' and s.src_natural_key is not null
{%- endmacro %}
{% macro queue_attrs() -%}{{ return(['code', 'name_ar', 'name_en', 'media_type_code', 'skill_group_code']) }}{%- endmacro %}
{% macro queue_timeline() -%}
    {{ scd2_timeline('skills', queue_source(), queue_attrs(), 'queue_sk') }}
{%- endmacro %}

{#- meta.reference_value: four datasets, one domain each, the source's text into src_label (DD-94 items seven, nine). -#}
{% macro refvalue_domains() -%}
    {{ return([('groups', 'agent_group', 'groupName'), ('skillgroups', 'skill_group', 'skillGroupName'),
               ('restreasons', 'rest_reason', 'reasonInfo'), ('servertypes', 'service_type', 'serviceName')]) }}
{%- endmacro %}
{% macro refvalue_desired() -%}
    {% for ds, domain, label in refvalue_domains() %}
    select '{{ domain }}'::text as domain, s.src_natural_key as code, s.payload ->> '{{ label }}' as src_label,
           f.is_new, s.contract_version
      from ({{ ref_snapshot_in_force(ds, 'reference_value') }}) f
      join {{ ref('stg_icp_reference') }} s on s.run_id = f.run_id and s.batch_seq = f.batch_seq
     where s.src_natural_key is not null
    {{ "union all" if not loop.last }}
    {% endfor %}
{%- endmacro %}
{#- Domains whose snapshot is new in this run: only those can have codes that are gone. -#}
{% macro refvalue_fresh_domains() -%}
    {% for ds, domain, label in refvalue_domains() %}
    select '{{ domain }}'::text from ({{ ref_snapshot_in_force(ds, 'reference_value') }}) f where f.is_new
    {{ "union all" if not loop.last }}
    {% endfor %}
{%- endmacro %}

{% macro refvalue_log_changes() -%}
    insert into audit.reference_data_change_log (target_table, target_key, field_name, old_value, new_value, changed_by, reason)
    select 'meta.reference_value', core.nk(t.domain, t.code), 'src_label', t.src_label, d.src_label, 'icp_sync', 'new snapshot'
      from meta.reference_value t join ({{ refvalue_desired() }}) d on d.domain = t.domain and d.code = t.code
     where t.src_system = 'icp' and d.is_new and t.src_label is distinct from d.src_label
    union all
    select 'meta.reference_value', core.nk(t.domain, t.code), 'is_current', 'false', 'true', 'icp_sync', 'back in the snapshot'
      from meta.reference_value t join ({{ refvalue_desired() }}) d on d.domain = t.domain and d.code = t.code
     where t.src_system = 'icp' and d.is_new and not t.is_current
    union all
    select 'meta.reference_value', core.nk(t.domain, t.code), 'is_current', 'true', 'false', 'icp_sync', 'no longer in the snapshot'
      from meta.reference_value t
     where t.src_system = 'icp' and t.is_current and t.domain in ({{ refvalue_fresh_domains() }})
       and not exists (select 1 from ({{ refvalue_desired() }}) d where d.domain = t.domain and d.code = t.code)
{%- endmacro %}

{#- A changed label, or a code that is back: updated, last_seen_at = now. Unchanged codes are not touched (DD-78). -#}
{% macro refvalue_update_changed() -%}
    update meta.reference_value t
       set src_label = d.src_label, is_current = true, last_seen_at = now(), contract_version = d.contract_version
      from ({{ refvalue_desired() }}) d
     where d.domain = t.domain and d.code = t.code and t.src_system = 'icp' and d.is_new
       and (t.src_label is distinct from d.src_label or not t.is_current)
{%- endmacro %}

{% macro refvalue_close_gone() -%}
    update meta.reference_value t
       set is_current = false, last_seen_at = now()
     where t.src_system = 'icp' and t.is_current and t.domain in ({{ refvalue_fresh_domains() }})
       and not exists (select 1 from ({{ refvalue_desired() }}) d where d.domain = t.domain and d.code = t.code)
{%- endmacro %}

{% macro refvalue_log_new() -%}
    insert into audit.reference_data_change_log (target_table, target_key, field_name, old_value, new_value, changed_by, reason)
    select 'meta.reference_value', core.nk(t.domain, t.code), 'src_label', null, t.src_label, 'icp_sync', 'new code'
      from meta.reference_value t
     where t.src_system = 'icp' and t.first_seen_at = now()
{%- endmacro %}

{% macro refvalue_register() -%}
    {% for ds, domain, label in refvalue_domains() %}
    {{ register_snapshot_batches(ds) }};
    {% endfor %}
{%- endmacro %}
