{#- ICP reference data, one row per record of every snapshot in raw (contract if03d · DD-94 · DD-89).

    Rows: every raw row of the processable batches this model has not registered yet (macros/batches.sql), with the
    snapshot it belongs to -- dataset, scope, the source's version, the sync time -- from meta.ingest_batch. Staging
    keeps every snapshot; which one is in force is decided by the core models (macros/reference.sql).

    No typing of the payload here beyond the record's id: the eight datasets have eight shapes, and each consuming
    model reads the fields it needs from payload (as received, DD-94 item fourteen: an empty or repeated name is
    taken as it is).
-#}
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    on_schema_change='fail',
    pre_hook="{{ clear_registrations_on_rebuild() }}",
    post_hook="{{ register_staged_batches('icp_reference') }}"
) }}

with batches as (
    {{ unprocessed_batches('icp_reference', this.identifier) }}
)

select
    r.dataset,
    r.cc_id,
    r.vdn_id,
    nullif(r.src_natural_key, '')                                   as src_natural_key,
    b.snapshot_scope,
    r.snapshot_version,
    -- When this version took effect at the gateway (DD-94 item five); the batch's arrival if the source gave no time
    coalesce(b.snapshot_synced_at, b.committed_at)                  as synced_at,
    r.payload,
    r.content_hash,
    r.run_id,
    r.batch_seq,
    r.contract_version,
    r.ingested_at
  from raw.icp_reference r
  join batches x on x.run_id = r.run_id and x.batch_seq = r.batch_seq
  join meta.ingest_batch b on b.run_id = r.run_id and b.batch_seq = r.batch_seq
