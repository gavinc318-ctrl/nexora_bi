{#- meta.reference_value from ICP's small code tables (DD-94 items seven, nine · LD-02 chapter 3 · LD-01 9.2).

    Four datasets, one domain each (macros/reference_models.sql); the source's text goes to src_label, never over
    M-10's translations (name_ar / name_en, which dbt cannot write -- change 0007). Not SCD2 (the table's own rule): a
    code missing from a NEW snapshot is set is_current = false, never deleted, because history still refers to it.
    Only rows that change are written (DD-78): a new code is inserted (this model); a changed label or a code that
    comes back is updated, with last_seen_at = the time of that change; an unchanged code is not touched. Every
    change is logged field by field (LD-01 9.3), before it is made.
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ refvalue_log_changes() }}", "{{ refvalue_update_changed() }}", "{{ refvalue_close_gone() }}"],
    post_hook=["{{ refvalue_log_new() }}", "{{ refvalue_register() }}"]
) }}

select d.domain, 'icp'::text as src_system, d.code, null::text as name_ar, null::text as name_en, true as is_current,
       now() as first_seen_at, now() as last_seen_at, d.contract_version, d.src_label
  from ({{ refvalue_desired() }}) d
 where not exists (select 1 from {{ this }} t where t.domain = d.domain and t.src_system = 'icp' and t.code = d.code)
