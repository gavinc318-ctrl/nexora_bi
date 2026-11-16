{#- mart.legs__day_agent__handling: published by day from staging.mart_calc__legs__day_agent__handling (macros/mart.sql: provisional, closed and corrected
    versions; metrics/marts.yaml: who may read it and through which dimensions). -#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ mart_plan(ref('mart_calc__legs__day_agent__handling'), 'icp_cdr_voice') }}"],
    post_hook=["{{ mart_register() }}"]
) }}

{{ mart_rows(ref('mart_calc__legs__day_agent__handling')) }}
