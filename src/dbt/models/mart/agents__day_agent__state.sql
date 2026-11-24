{#- mart.agents__day_agent__state: published by day from staging.mart_calc__agents__day_agent__state
    (macros/mart.sql). Closed by observation, not by re-sweep: the agent sources cannot be re-swept (DD-101 item five,
    mart_observed_days). Who may read it: metrics/marts.yaml. -#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ mart_plan(ref('mart_calc__agents__day_agent__state'), observed=['icp_agent_session', 'icp_agent_day']) }}"],
    post_hook=["{{ mart_register() }}"]
) }}

{{ mart_rows(ref('mart_calc__agents__day_agent__state')) }}
