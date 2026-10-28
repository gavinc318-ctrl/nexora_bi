{#- core.dim_queue from ICP's skills snapshots: SCD2, one version per change point (DD-94 · DD-12). What each
    attribute is: macros/reference_models.sql (queue_source); how versions are cut: macros/reference.sql.
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ lock_call_leg_links() }}", "{{ scd2_log(queue_timeline(), queue_attrs()) }}",
              "{{ scd2_close(queue_timeline(), 'queue_sk') }}"],
    post_hook=["{{ flag_renamed_labels('name_en') }}", "{{ relink_call_legs('queue_sk', 1) }}",
               "{{ raise_unlinked_member_items(1) }}", "{{ register_snapshot_batches('skills', true) }}"]
) }}

select core.sk_scd('{{ var("site_code") }}', 'icp', x.nk, x.at)       as queue_sk,
       core.site_namespace('{{ var("site_code") }}')                     as site_sk,
       x.nk                                                              as src_natural_key,
       x.code,
       x.name_ar,
       x.name_en,
       x.skill_group_code,
       x.media_type_code,
       x.at                                                              as valid_from,
       coalesce(x.next_at, 'infinity')                                   as valid_to,
       'icp'::text                                                       as src_system,
       x.run_id,
       x.contract_version,
       now()                                                             as built_at
  from ({{ queue_timeline() }}) x
 where x.present
