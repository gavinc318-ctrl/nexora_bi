{#- core.dim_agent from ICP's agents snapshot: SCD2, one version per change (DD-94 · DD-12).
    What each attribute is: macros/reference_models.sql (agent_desired); how a snapshot is applied: macros/reference.sql.
    Rows are only inserted and closed (change 0007). The post-hooks log every changed field, flag renamed
    translations, relink the call legs already in core, and register the snapshot.
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ lock_call_leg_links() }}", "{{ scd2_close(agent_desired(), agent_snapshot(), agent_attrs(), 'agent_sk', ['role_code']) }}"],
    post_hook=["{{ scd2_log(agent_attrs(), 'agent_sk') }}", "{{ flag_renamed_labels('name_en') }}",
               "{{ relink_call_legs('agent_sk', 2) }}", "{{ register_snapshot_batches('agents') }}"]
) }}

select core.sk_scd('{{ var("site_code") }}', 'icp', x.nk, x.change_at)  as agent_sk,
       core.site_namespace('{{ var("site_code") }}')                     as site_sk,
       x.nk                                                              as src_natural_key,
       null::text                                                        as login_id,
       x.name_ar,
       x.name_en,
       x.role_code,
       null::uuid                                                        as agency_type_sk,   -- a call taker has none (DD-41)
       x.team_code,
       x.change_at                                                       as valid_from,
       'infinity'::timestamptz                                           as valid_to,
       'icp'::text                                                       as src_system,
       x.run_id,
       x.contract_version,
       now()                                                             as built_at
  from ({{ scd2_changes(agent_desired(), agent_snapshot(), agent_attrs(), 'agent_sk', ['role_code']) }}) x
 where x.action in ('new', 'change')
