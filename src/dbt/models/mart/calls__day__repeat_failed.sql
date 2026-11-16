{#- mart.calls__day__repeat_failed: published by day from staging.mart_calc__calls__day__repeat_failed (macros/mart.sql: provisional, closed and corrected
    versions; metrics/marts.yaml: who may read it and through which dimensions). -#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook=["{{ mart_plan(ref('mart_calc__calls__day__repeat_failed'), 'icp_cdr_voice') }}"],
    post_hook=["{{ mart_register() }}"]
) }}

{{ mart_rows(ref('mart_calc__calls__day__repeat_failed')) }}
