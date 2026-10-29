{#- core.dim_agent from ICP's agents snapshots and M-10's role marks: SCD2, one version per change point (DD-94 ·
    DD-12 · LD-02 chapter 7). What each attribute is: macros/reference_models.sql (agent_source, agent_marks); how
    versions are cut: macros/reference.sql (scd2_timeline). Rows are only inserted and closed (change 0007). The
    pre-hooks fix this build's snapshots and timeline (DD-94 item twenty), log every changed field and close the
    current versions; the post-hooks flag renamed translations,
    relink the call legs already in core, raise an item for agents of call legs that no version covers (DD-95), and
    register the snapshots.
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ lock_call_leg_links() }}", "{{ fix_snapshot_set('agents', true) }}",
              "{{ fix_timeline(agent_timeline()) }}",
              "{{ scd2_log(agent_attrs(), agent_mark_attrs() | map(attribute=0) | list) }}",
              "{{ scd2_close('agent_sk') }}"],
    post_hook=["{{ flag_renamed_labels('name_en') }}", "{{ relink_call_legs('agent_sk', 2) }}",
               "{{ raise_unlinked_member_items(2) }}", "{{ register_snapshot_batches('agents') }}"]
) }}

select core.sk_scd('{{ var("site_code") }}', 'icp', x.nk, x.at)       as agent_sk,
       core.site_namespace('{{ var("site_code") }}')                     as site_sk,
       x.nk                                                              as src_natural_key,
       null::text                                                        as login_id,
       x.name_ar,
       x.name_en,
       x.role_code,
       null::uuid                                                        as agency_type_sk,   -- a call taker has none (DD-41)
       x.team_code,
       x.at                                                              as valid_from,
       coalesce(x.next_at, 'infinity')                                   as valid_to,
       'icp'::text                                                       as src_system,
       x.run_id,
       x.contract_version,
       now()                                                             as built_at
  from {{ timeline_table() }} x
 where x.present
