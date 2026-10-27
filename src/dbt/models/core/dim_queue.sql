{#- core.dim_queue from ICP's skills snapshot: SCD2, one version per change (DD-94 · DD-12).
    What each attribute is: macros/reference_models.sql (queue_desired); how a snapshot is applied: macros/reference.sql.
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ lock_call_leg_links() }}", "{{ scd2_close(queue_desired(), queue_snapshot(), queue_attrs(), 'queue_sk') }}"],
    post_hook=["{{ scd2_log(queue_attrs(), 'queue_sk') }}", "{{ flag_renamed_labels('name_en') }}",
               "{{ relink_call_legs('queue_sk', 1) }}", "{{ register_snapshot_batches('skills') }}"]
) }}

select core.sk_scd('{{ var("site_code") }}', 'icp', x.nk, x.change_at)  as queue_sk,
       core.site_namespace('{{ var("site_code") }}')                     as site_sk,
       x.nk                                                              as src_natural_key,
       x.code,
       x.name_ar,
       x.name_en,
       x.skill_group_code,
       x.media_type_code,
       x.change_at                                                       as valid_from,
       'infinity'::timestamptz                                           as valid_to,
       'icp'::text                                                       as src_system,
       x.run_id,
       x.contract_version,
       now()                                                             as built_at
  from ({{ scd2_changes(queue_desired(), queue_snapshot(), queue_attrs(), 'queue_sk') }}) x
 where x.action in ('new', 'change')
